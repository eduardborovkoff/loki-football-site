#!/usr/bin/env python3
"""Football results ingest (k1119-E3, T033) — real finished matches -> stats.db.

Source: openfootball (github raw JSON, no key, TTL-cache 24h).
Real format (verified iter17):
  {"matches": [{"date":"2025-08-15","team1":"Liverpool FC","team2":"AFC Bournemouth",
                "score":{"ft":[4,2],"ht":[1,0]}, "round":"Matchday 1"}]}

Writes to stats.db (compat with M052 football_model backtest):
  - upsert teams (name UNIQUE)
  - upsert matches (home_id, away_id, league, kickoff_ts) status='finished'
  - recompute teams.wins/draws/losses/goals_for/goals_against from matches
Idempotent: INSERT match only if (home_id,away_id,kickoff_ts) not present.

CLI:
  python3 tools/football_results_ingest.py --leagues EPL,LALIGA --seasons 2024-25,2025-26
  python3 tools/football_results_ingest.py --leagues EPL --seasons 2025-26 --db work/football/stats.db
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

LEAGUE_SLUG = {
    "EPL": "en.1",
    "LALIGA": "es.1",
    "SERIEA": "it.1",
    "BUNDESLIGA": "de.1",
    "LIGUE1": "fr.1",
}
OF_BASE = "https://raw.githubusercontent.com/openfootball/football.json/master"
UA = "Mozilla/5.0 LokiFootballIngest/1.0"
CACHE_TTL_S = 24 * 3600

SCHEMA_EXTRA = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_matches_home_away_kick
  ON matches(home_id, away_id, kickoff_ts);
"""


def _home() -> Path:
    return Path(os.environ.get("LOKI_HOME", "/data/asgard")).expanduser()


def _default_db() -> Path:
    env = os.environ.get("LOKI_FB_DB")
    if env:
        return Path(env)
    p = _home() / "work" / "football" / "stats.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _cache_dir() -> Path:
    p = _home() / "work" / "football" / "cache" / "openfootball"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _fetch(url: str) -> bytes | None:
    cache = _cache_dir() / (url.rsplit("/", 1)[-1].replace("/", "_") + ".cache")
    if cache.exists() and (time.time() - cache.stat().st_mtime) < CACHE_TTL_S:
        try:
            return cache.read_bytes()
        except OSError:
            pass
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=25) as r:
            data = r.read()
        cache.write_bytes(data)
        return data
    except Exception as e:
        print(f"fetch fail {url}: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def _date_to_ts(s: str) -> float | None:
    try:
        import datetime as dt
        d = dt.datetime.strptime(s, "%Y-%m-%d")
        return d.timestamp()
    except Exception:
        return None


def _get_or_create_team(conn: sqlite3.Connection, name: str, league: str) -> int:
    row = conn.execute("SELECT id FROM teams WHERE name=?", (name,)).fetchone()
    if row:
        return int(row[0])
    cur = conn.execute(
        "INSERT INTO teams (name, league, elo, wins, draws, losses, "
        "goals_for, goals_against, updated_ts) VALUES (?,?,?,0,0,0,0,0,?)",
        (name, league, 1500.0, time.time()),
    )
    return int(cur.lastrowid)


def _ingest_league(conn: sqlite3.Connection, league: str, season: str) -> tuple[int, int]:
    """Returns (inserted, skipped)."""
    slug = LEAGUE_SLUG.get(league.upper())
    if not slug:
        print(f"unknown league {league}", file=sys.stderr)
        return 0, 0
    url = f"{OF_BASE}/{season}/{slug}.json"
    raw = _fetch(url)
    if not raw:
        return 0, 0
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"bad json {url}: {e}", file=sys.stderr)
        return 0, 0
    matches = obj.get("matches", [])
    inserted = skipped = 0
    for m in matches:
        h_name = m.get("team1")
        a_name = m.get("team2")
        if not h_name or not a_name:
            continue
        score = m.get("score") or {}
        ft = score.get("ft")
        if not (isinstance(ft, list) and len(ft) == 2):
            continue
        try:
            hg, ag = int(ft[0]), int(ft[1])
        except (TypeError, ValueError):
            continue
        kickoff = _date_to_ts(str(m.get("date", "")))
        if kickoff is None:
            continue
        hid = _get_or_create_team(conn, h_name, league)
        aid = _get_or_create_team(conn, a_name, league)
        exists = conn.execute(
            "SELECT 1 FROM matches WHERE home_id=? AND away_id=? AND kickoff_ts=?",
            (hid, aid, kickoff),
        ).fetchone()
        if exists:
            skipped += 1
            continue
        conn.execute(
            "INSERT INTO matches (home_id, away_id, league, kickoff_ts, "
            "home_goals, away_goals, status, updated_ts) VALUES (?,?,?,?,?,?,?,?)",
            (hid, aid, league, kickoff, hg, ag, "finished", time.time()),
        )
        inserted += 1
    conn.commit()
    return inserted, skipped


def _recompute_team_stats(conn: sqlite3.Connection) -> None:
    """Recompute wins/draws/losses/goals_for/against from finished matches."""
    conn.execute(
        "UPDATE teams SET wins=0, draws=0, losses=0, goals_for=0, goals_against=0"
    )
    rows = conn.execute(
        "SELECT home_id, away_id, home_goals, away_goals FROM matches WHERE status='finished'"
    ).fetchall()
    agg: dict[int, list[int]] = {}
    for hid, aid, hg, ag in rows:
        if hg is None or ag is None:
            continue
        for tid, gf, ga in ((hid, hg, ag), (aid, ag, hg)):
            cur = agg.setdefault(tid, [0, 0, 0, 0, 0])  # w,d,l,gf,ga
            cur[3] += gf
            cur[4] += ga
            if gf > ga:
                cur[0] += 1
            elif gf == ga:
                cur[1] += 1
            else:
                cur[2] += 1
    now = time.time()
    for tid, (w, d, l, gf, ga) in agg.items():
        conn.execute(
            "UPDATE teams SET wins=?, draws=?, losses=?, goals_for=?, "
            "goals_against=?, updated_ts=? WHERE id=?",
            (w, d, l, gf, ga, now, tid),
        )
    conn.commit()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS teams (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL UNIQUE,
          league TEXT, elo REAL DEFAULT 1500.0,
          wins INTEGER DEFAULT 0, draws INTEGER DEFAULT 0, losses INTEGER DEFAULT 0,
          goals_for INTEGER DEFAULT 0, goals_against INTEGER DEFAULT 0,
          updated_ts REAL
        );
        CREATE TABLE IF NOT EXISTS matches (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          home_id INTEGER NOT NULL REFERENCES teams(id),
          away_id INTEGER NOT NULL REFERENCES teams(id),
          league TEXT, kickoff_ts REAL,
          home_goals INTEGER, away_goals INTEGER,
          status TEXT DEFAULT 'scheduled', updated_ts REAL
        );
        CREATE TABLE IF NOT EXISTS odds (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          match_id INTEGER NOT NULL REFERENCES matches(id),
          bookmaker TEXT, p1 REAL, px REAL, p2 REAL, captured_ts REAL
        );
        CREATE TABLE IF NOT EXISTS h2h (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          team_a_id INTEGER NOT NULL REFERENCES teams(id),
          team_b_id INTEGER NOT NULL REFERENCES teams(id),
          last_wins_a INTEGER DEFAULT 0, last_wins_b INTEGER DEFAULT 0,
          last_draws INTEGER DEFAULT 0, last_n INTEGER DEFAULT 0, updated_ts REAL
        );
        """
    )
    try:
        conn.executescript(SCHEMA_EXTRA)
    except sqlite3.OperationalError as e:
        # unique-index may fail if duplicates already exist; non-fatal
        print(f"warn: unique idx: {e}", file=sys.stderr)
    conn.commit()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--leagues", default="EPL,LALIGA,SERIEA,BUNDESLIGA,LIGUE1")
    ap.add_argument("--seasons", default="2024-25,2025-26")
    ap.add_argument("--db", default=None)
    ap.add_argument("--stats", action="store_true", help="print summary after ingest")
    args = ap.parse_args()

    leagues = [x.strip() for x in args.leagues.split(",") if x.strip()]
    seasons = [x.strip() for x in args.seasons.split(",") if x.strip()]
    db = Path(args.db) if args.db else _default_db()
    db.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db))
    total_ins = total_skip = 0
    try:
        _ensure_schema(conn)
        for league in leagues:
            for season in seasons:
                ins, sk = _ingest_league(conn, league, season)
                total_ins += ins
                total_skip += sk
                print(f"{league} {season}: inserted={ins} skipped={sk}")
        _recompute_team_stats(conn)
        print(f"DB: {db} total_inserted={total_ins} total_skipped={total_skip}")
        if args.stats:
            n_teams = conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
            n_m = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
            n_f = conn.execute("SELECT COUNT(*) FROM matches WHERE status='finished'").fetchone()[0]
            print(f"teams={n_teams} matches={n_m} finished={n_f}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())