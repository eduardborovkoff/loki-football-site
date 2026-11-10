#!/usr/bin/env python3
"""Football odds ingest from football-data.co.uk CSV (free, no key) -> stats.db odds.

Source: http://www.football-data.co.uk/mmz4281/{season}/{league}.csv
  season: 2425 for 2024/25, 2526 for 2025/26
  league: E0 (EPL), SP1 (La Liga), I1 (Serie A), D1 (Bundesliga), F1 (Ligue 1)

Parses 1X2 odds columns (B365H/D/A fallback AvgH/D/A etc.) and upserts into odds.
Match by (home_id, away_id, kickoff_ts ±1 day). Team names fuzzy-match (difflib, cutoff 0.75).

Idempotent: unique index (match_id, bookmaker), INSERT OR REPLACE.
"""
import argparse
import csv
import io
import os
import sqlite3
import sys
import time
import urllib.request
from difflib import get_close_matches
from pathlib import Path

LEAGUE_MAP = {
    "EPL": "E0",
    "LALIGA": "SP1",
    "SERIEA": "I1",
    "BUNDESLIGA": "D1",
    "LIGUE1": "F1",
}
SEASON_MAP = {"2024-25": "2425", "2025-26": "2526"}
BASE_URL = "https://football-data.co.uk/mmz4281/{season}/{league}.csv"
UA = "Mozilla/5.0 (X11; Linux x86_64) LokiOddsIngest/1.0"


def _home() -> Path:
    return Path(os.environ.get("LOKI_HOME", "/data/asgard")).expanduser()


def _cache_dir() -> Path:
    p = _home() / "work" / "football" / "cache" / "footballdata"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _db_path() -> Path:
    env = os.environ.get("LOKI_FB_DB")
    if env:
        return Path(env)
    return _home() / "work" / "football" / "stats.db"


def _fetch_csv(url: str, cache_name: str) -> str | None:
    cache = _cache_dir() / cache_name
    if cache.exists() and (time.time() - cache.stat().st_mtime) < 86400:
        try:
            return cache.read_text(encoding="latin-1")
        except OSError:
            pass
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read().decode("latin-1")
        cache.write_text(data, encoding="latin-1")
        return data
    except Exception as e:
        print(f"fetch fail {url}: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def _parse_date(s: str) -> float | None:
    s = (s or "").strip()
    if not s:
        return None
    import datetime as dt
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return dt.datetime.strptime(s, fmt).timestamp()
        except ValueError:
            continue
    return None


_ALIAS = {
    "man united": "manchester united fc",
    "man utd": "manchester united fc",
    "man city": "manchester city fc",
    "newcastle": "newcastle united fc",
    "nott'm forest": "nottingham forest fc",
    "nottingham": "nottingham forest fc",
    "tottenham": "tottenham hotspur fc",
    "spurs": "tottenham hotspur fc",
    "sheffield utd": "sheffield united fc",
    "west ham": "west ham united fc",
    "brighton": "brighton & hove albion fc",
    "wolves": "wolverhampton wanderers fc",
    "leicester": "leicester city fc",
    "ipswich": "ipswich town fc",
    "southampton": "southampton fc",
    "leeds": "leeds united fc",
    "athletic": "athletic club",
    "ath bilbao": "athletic club",
    "real betis": "real betis balompie",
    "betis": "real betis balompie",
    "real madrid": "real madrid cf",
    "barcelona": "fc barcelona",
    "barca": "fc barcelona",
    "atletico": "atletico de madrid",
    "atletico madrid": "atletico de madrid",
    "sociedad": "real sociedad",
    "villarreal": "villarreal cf",
    "inter": "inter milan",
    "internazionale": "inter milan",
    "ac milan": "ac milan",
    "milan": "ac milan",
    "juventus": "juventus fc",
    "juve": "juventus fc",
    "napoli": "ssc napoli",
    "roma": "as roma",
    "lazio": "ss lazio",
    "fiorentina": "acf fiorentina",
    "atalanta": "atalanta bc",
    "bologna": "bologna fc",
    "torino": "torino fc",
    "udinese": "udinese calcio",
    "sampdoria": "uc sampdoria",
    "genoa": "genoa cfc",
    "cagliari": "cagliari calcio",
    "lecce": "us lecce",
    "verona": "hellas verona",
    "empoli": "empoli fc",
    "sassuolo": "us sassuolo",
    "monza": "ac monza",
    "como": "como 1907",
    "venezia": "venezia fc",
    "parma": "parma calcio 1913",
    "salernitana": "us salernitana",
    "frosinone": "frosinone calcio",
}

_SUFFIXES = {"fc", "afc", "cf", "sad", "calcio", "ac", "sc", "ss", "ssc",
             "as", "us", "ud", "bc", "acf", "cfc", "uc", "1913", "1907", "balompie"}


def _norm(name: str) -> str:
    s = (name or "").lower().strip()
    s = s.replace("&", "and").replace(".", "").replace("-", " ").replace("'", "")
    toks = [t for t in s.split() if t and t not in _SUFFIXES]
    return " ".join(toks)


def _team_id(conn: sqlite3.Connection, name: str) -> int | None:
    raw = (name or "").strip()
    if not raw:
        return None
    # 1. Точное
    row = conn.execute("SELECT id FROM teams WHERE name=?", (raw,)).fetchone()
    if row:
        return int(row[0])
    # 2. Alias
    alias_target = _ALIAS.get(raw.lower())
    if alias_target:
        r = conn.execute("SELECT id FROM teams WHERE LOWER(name)=?", (alias_target,)).fetchone()
        if r:
            return int(r[0])
    # 3. Нормализованное совпадение / substring
    norm_csv = _norm(raw)
    all_rows = conn.execute("SELECT id, name FROM teams").fetchall()
    for tid, db_name in all_rows:
        norm_db = _norm(db_name)
        if norm_csv == norm_db:
            return int(tid)
        if norm_csv and norm_db and (norm_csv in norm_db or norm_db in norm_csv):
            return int(tid)
    # 4. Fuzzy по нормализованным
    by_norm = {_norm(n): i for i, n in all_rows}
    close = get_close_matches(norm_csv, list(by_norm.keys()), n=1, cutoff=0.55)
    if close:
        return int(by_norm[close[0]])
    return None


def _match_id(conn: sqlite3.Connection, home_id: int, away_id: int, kickoff: float) -> int | None:
    lo, hi = kickoff - 86400, kickoff + 86400
    row = conn.execute(
        "SELECT id FROM matches WHERE home_id=? AND away_id=? AND kickoff_ts BETWEEN ? AND ?",
        (home_id, away_id, lo, hi),
    ).fetchone()
    return int(row[0]) if row else None


def _ensure_odds_unique(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_odds_match_book ON odds(match_id, bookmaker)")
        conn.commit()
    except sqlite3.OperationalError as e:
        print(f"warn: odds unique index: {e}", file=sys.stderr)


def _ingest_league_season(conn: sqlite3.Connection, league: str, season_code: str) -> tuple[int, int]:
    code = LEAGUE_MAP.get(league.upper())
    if not code:
        return 0, 0
    url = BASE_URL.format(season=season_code, league=code)
    text = _fetch_csv(url, f"{league}_{season_code}.csv")
    if not text:
        return 0, 0
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return 0, 0
    h_col = next((c for c in ("B365H", "AvgH", "BbAvH", "WHH", "IWH") if c in reader.fieldnames), None)
    d_col = next((c for c in ("B365D", "AvgD", "BbAvD", "WHD", "IWD") if c in reader.fieldnames), None)
    a_col = next((c for c in ("B365A", "AvgA", "BbAvA", "WHA", "IWA") if c in reader.fieldnames), None)
    if not (h_col and d_col and a_col):
        print(f"no odds columns in {url}", file=sys.stderr)
        return 0, 0
    inserted = skipped = 0
    for row in reader:
        h_name = (row.get("HomeTeam") or "").strip()
        a_name = (row.get("AwayTeam") or "").strip()
        date_str = row.get("Date") or ""
        if not (h_name and a_name and date_str):
            continue
        kickoff = _parse_date(date_str)
        if kickoff is None:
            continue
        hid = _team_id(conn, h_name)
        aid = _team_id(conn, a_name)
        if not (hid and aid):
            skipped += 1
            continue
        mid = _match_id(conn, hid, aid, kickoff)
        if not mid:
            skipped += 1
            continue
        try:
            p1 = float(row[h_col]); px = float(row[d_col]); p2 = float(row[a_col])
        except (ValueError, KeyError, TypeError):
            skipped += 1
            continue
        conn.execute(
            "INSERT OR REPLACE INTO odds (match_id, bookmaker, p1, px, p2, captured_ts) "
            "VALUES (?,?,?,?,?,?)",
            (mid, "football-data", p1, px, p2, time.time()),
        )
        inserted += 1
    conn.commit()
    return inserted, skipped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--leagues", default="EPL,LALIGA,SERIEA,BUNDESLIGA,LIGUE1")
    ap.add_argument("--seasons", default="2024-25,2025-26")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()
    leagues = [x.strip() for x in args.leagues.split(",") if x.strip()]
    seasons = [x.strip() for x in args.seasons.split(",") if x.strip()]
    db = Path(args.db) if args.db else _db_path()
    conn = sqlite3.connect(str(db))
    try:
        _ensure_odds_unique(conn)
        total_ins = total_skip = 0
        for league in leagues:
            for season in seasons:
                code = SEASON_MAP.get(season)
                if not code:
                    continue
                ins, skip = _ingest_league_season(conn, league, code)
                total_ins += ins; total_skip += skip
                print(f"{league} {season}: odds inserted={ins} skipped={skip}")
        n_odds = conn.execute("SELECT COUNT(*) FROM odds").fetchone()[0]
        n_matches_with_odds = conn.execute("SELECT COUNT(DISTINCT match_id) FROM odds").fetchone()[0]
        print(f"DB: {db} total_odds_inserted={total_ins} total_skipped={total_skip}")
        print(f"odds rows in db: {n_odds}, matches with odds: {n_matches_with_odds}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())