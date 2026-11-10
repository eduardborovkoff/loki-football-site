#!/usr/bin/env python3
"""Football data parser (k1120-E1, T032) — сбор статистики/матчей/коэффициентов.

MVP: OFFLINE-режим (фикстура) + интерфейс для будущих источников:
- football-data.org (free-tier, 10 req/min, нужен API-ключ)
- FBref / Understat (scraping, без ключа)
- Ручной JSON от Эда

Выход: SQLite stats.db в LOKI_HOME/work/football/.
Схема: teams, matches, odds, h2h — совместимо с M051 paper-трекером.

Не делает ставок. Только читает данные. Публикация на сайте (k1120-E5) — отдельно.

Режимы:
  --seed          заполнить фикстурой (2 команды, 1 матч)
  --fetch SOURCE  попытка API (пока NotImplemented)
  --stats         показать содержимое stats.db
  --export-json   выгрузить в matches.json для sport_analyst.py (M051)
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any


def _home() -> Path:
    return Path(os.environ.get("LOKI_HOME", "/data/asgard")).expanduser()


def _db_path() -> Path:
    p = _home() / "work" / "football" / "stats.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    league TEXT,
    elo REAL DEFAULT 1500.0,
    wins INTEGER DEFAULT 0,
    draws INTEGER DEFAULT 0,
    losses INTEGER DEFAULT 0,
    goals_for INTEGER DEFAULT 0,
    goals_against INTEGER DEFAULT 0,
    updated_ts REAL
);

CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    home_id INTEGER NOT NULL REFERENCES teams(id),
    away_id INTEGER NOT NULL REFERENCES teams(id),
    league TEXT,
    kickoff_ts REAL,
    home_goals INTEGER,
    away_goals INTEGER,
    status TEXT DEFAULT 'scheduled',
    updated_ts REAL
);

CREATE TABLE IF NOT EXISTS odds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL REFERENCES matches(id),
    bookmaker TEXT,
    p1 REAL,
    px REAL,
    p2 REAL,
    captured_ts REAL
);

CREATE TABLE IF NOT EXISTS h2h (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_a_id INTEGER NOT NULL REFERENCES teams(id),
    team_b_id INTEGER NOT NULL REFERENCES teams(id),
    last_wins_a INTEGER DEFAULT 0,
    last_wins_b INTEGER DEFAULT 0,
    last_draws INTEGER DEFAULT 0,
    last_n INTEGER DEFAULT 0,
    updated_ts REAL
);

CREATE INDEX IF NOT EXISTS idx_matches_kickoff ON matches(kickoff_ts);
CREATE INDEX IF NOT EXISTS idx_odds_match ON odds(match_id);
"""


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def seed_fixture(conn: sqlite3.Connection) -> None:
    """MVP-фикстура: 2 команды, 1 матч с коэффициентами."""
    import time as _t
    now = _t.time()
    conn.execute(
        "INSERT OR IGNORE INTO teams (name, league, elo, wins, draws, losses, "
        "goals_for, goals_against, updated_ts) VALUES (?,?,?,?,?,?,?,?,?)",
        ("Команда А", "АПЛ", 1600.0, 10, 5, 3, 30, 15, now),
    )
    conn.execute(
        "INSERT OR IGNORE INTO teams (name, league, elo, wins, draws, losses, "
        "goals_for, goals_against, updated_ts) VALUES (?,?,?,?,?,?,?,?,?)",
        ("Команда Б", "АПЛ", 1500.0, 7, 6, 5, 22, 20, now),
    )
    ha = conn.execute("SELECT id FROM teams WHERE name='Команда А'").fetchone()[0]
    hb = conn.execute("SELECT id FROM teams WHERE name='Команда Б'").fetchone()[0]
    cur = conn.execute(
        "INSERT INTO matches (home_id, away_id, league, kickoff_ts, status, updated_ts) "
        "VALUES (?,?,?,?,?,?)",
        (ha, hb, "АПЛ", now + 86400, "запланирован", now),
    )
    mid = cur.lastrowid
    conn.execute(
        "INSERT INTO odds (match_id, bookmaker, p1, px, p2, captured_ts) "
        "VALUES (?,?,?,?,?,?)",
        (mid, "fixture-bk", 2.10, 3.40, 3.60, now),
    )
    conn.commit()


def fetch_source(conn: sqlite3.Connection, source: str) -> None:
    """Заглушка — реализации в E2 (football-data.org / FBref / Understat)."""
    print(f"fetch_source '{source}': NotImplemented (E2). Ключи/парсер — TBD.",
          file=sys.stderr)
    sys.exit(2)


def stats(conn: sqlite3.Connection) -> None:
    teams = conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
    matches = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    odds = conn.execute("SELECT COUNT(*) FROM odds").fetchone()[0]
    print(f"teams={teams} matches={matches} odds={odds}")
    for row in conn.execute(
        "SELECT m.id, th.name, ta.name, m.kickoff_ts, m.status "
        "FROM matches m JOIN teams th ON m.home_id=th.id "
        "JOIN teams ta ON m.away_id=ta.id ORDER BY m.kickoff_ts LIMIT 10"
    ):
        print(f"  match {row[0]}: {row[1]} vs {row[2]} [{row[4]}]")
    for row in conn.execute(
        "SELECT o.match_id, o.bookmaker, o.p1, o.px, o.p2 FROM odds o LIMIT 5"
    ):
        print(f"  odds match {row[0]} ({row[1]}): p1={row[2]} px={row[3]} p2={row[4]}")


def export_json(conn: sqlite3.Connection, path: Path) -> None:
    """Экспорт в формат sport_analyst.py (M051)."""
    rows = conn.execute(
        "SELECT m.id, th.name, ta.name, o.p1, o.px, o.p2, m.kickoff_ts "
        "FROM matches m JOIN teams th ON m.home_id=th.id "
        "JOIN teams ta ON m.away_id=ta.id "
        "LEFT JOIN odds o ON o.match_id=m.id WHERE m.status IN ('запланирован','scheduled')"
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        mid, home, away, p1, px, p2, ts = r
        out.append({
            "match_id": mid,
            "match": f"{home} vs {away}",
            "home": home,
            "away": away,
            "odds_p1": p1,
            "odds_px": px,
            "odds_p2": p2,
            "kickoff_ts": ts,
        })
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print(f"exported {len(out)} matches -> {path}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", action="store_true",
                    help="заполнить фикстурой")
    ap.add_argument("--fetch", metavar="SOURCE",
                    help="fetch из источника (E2)")
    ap.add_argument("--stats", action="store_true",
                    help="показать содержимое")
    ap.add_argument("--export-json", metavar="PATH",
                    help="экспорт для M051 sport_analyst")
    args = ap.parse_args()

    db = _db_path()
    conn = sqlite3.connect(str(db))
    try:
        init_db(conn)
        if args.seed:
            seed_fixture(conn)
            print(f"seeded {db}")
        if args.fetch:
            fetch_source(conn, args.fetch)
        if args.stats:
            stats(conn)
        if args.export_json:
            export_json(conn, Path(args.export_json))
        if not (args.seed or args.fetch or args.stats or args.export_json):
            ap.print_help()
            return 1
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())