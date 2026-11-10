#!/usr/bin/env python3
import argparse
import csv
import os
import sqlite3
import sys
import time
from pathlib import Path

# helpers from football_odds_ingest
try:
    from football_odds_ingest import _team_id, match_id, parse_date
except Exception as e:
    sys.stderr.write(f"Failed to import helpers from football_odds_ingest: {e}\n")
    sys.exit(1)


TABLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS match_stats (
    match_id      INTEGER PRIMARY KEY REFERENCES matches(id),
    referee       TEXT,
    hs            INTEGER,
    as            INTEGER,
    hst           INTEGER,
    ast           INTEGER,
    hf            INTEGER,
    af            INTEGER,
    hc            INTEGER,
    ac            INTEGER,
    hy            INTEGER,
    ay            INTEGER,
    hr            INTEGER,
    ar            INTEGER,
    b365_over25   REAL,
    b365_under25  REAL,
    avg_over25    REAL,
    avg_under25   REAL,
    ah_line       REAL,
    b365_ahh      REAL,
    b365_aha      REAL,
    closing_b365h REAL,
    closing_b365d REAL,
    closing_b365a REAL,
    updated_ts    REAL
);
"""


def _to_int(value):
    try:
        return int(value) if value != "" else None
    except (ValueError, TypeError):
        return None


def _to_float(value):
    try:
        return float(value) if value != "" else None
    except (ValueError, TypeError):
        return None


def season_to_suffix(season: str) -> str:
    """
    Convert season string like '2024-25' to suffix used in filenames: '2425'
    """
    start, end = season.split("-")
    return f"{start[-2:]}{end[-2:]}"


def process_file(csv_path: Path, conn: sqlite3.Connection):
    inserted = 0
    skipped = 0

    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # expected team names columns
            home_name = row.get("HomeTeam") or row.get("Home") or row.get("HomeTeamName")
            away_name = row.get("AwayTeam") or row.get("Away") or row.get("AwayTeamName")
            date_str = row.get("Date") or row.get("Kickoff") or row.get("MatchDate")

            if not home_name or not away_name or not date_str:
                skipped += 1
                continue

            home_id = _team_id(home_name)
            away_id = _team_id(away_name)
            kickoff_ts = parse_date(date_str)
            if kickoff_ts is None:
                skipped += 1
                continue

            m_id = match_id(home_id, away_id, kickoff_ts)
            if m_id is None:
                skipped += 1
                continue

            # map CSV columns to DB fields
            data = {
                "match_id": m_id,
                "referee": row.get("Referee") or None,
                "hs": _to_int(row.get("HS")),
                "as": _to_int(row.get("AS")),
                "hst": _to_int(row.get("HST")),
                "ast": _to_int(row.get("AST")),
                "hf": _to_int(row.get("HF")),
                "af": _to_int(row.get("AF")),
                "hc": _to_int(row.get("HC")),
                "ac": _to_int(row.get("AC")),
                "hy": _to_int(row.get("HY")),
                "ay": _to_int(row.get("AY")),
                "hr": _to_int(row.get("HR")),
                "ar": _to_int(row.get("AR")),
                "b365_over25": _to_float(row.get("B365>2.5")),
                "b365_under25": _to_float(row.get("B365<2.5")),
                "avg_over25": _to_float(row.get("Avg>2.5")),
                "avg_under25": _to_float(row.get("Avg<2.5")),
                "ah_line": _to_float(row.get("AHh")),
                "b365_ahh": _to_float(row.get("B365AHH")),
                "b365_aha": _to_float(row.get("B365AHA")),
                "closing_b365h": _to_float(row.get("B365CH")),
                "closing_b365d": _to_float(row.get("B365CD")),
                "closing_b365a": _to_float(row.get("B365CA")),
                "updated_ts": time.time(),
            }

            placeholders = ", ".join("?" for _ in data)
            columns = ", ".join(data.keys())
            sql = f"INSERT OR REPLACE INTO match_stats ({columns}) VALUES ({placeholders})"

            try:
                conn.execute(sql, tuple(data.values()))
                inserted += 1
            except Exception:
                skipped += 1
                continue

    conn.commit()
    return inserted, skipped


def main():
    parser = argparse.ArgumentParser(description="Ingest football match statistics into SQLite")
    parser.add_argument(
        "--leagues",
        required=True,
        help="Comma‑separated list of league codes, e.g. EPL,LALIGA,SERIEA",
    )
    parser.add_argument(
        "--seasons",
        required=True,
        help="Comma‑separated list of seasons, e.g. 2024-25,2025-26",
    )
    parser.add_argument(
        "--db",
        default="/data/loki/fb_site_deploy/work/football/stats.db",
        help="Path to SQLite database",
    )
    args = parser.parse_args()

    leagues = [l.strip() for l in args.leagues.split(",") if l.strip()]
    seasons = [s.strip() for s in args.seasons.split(",") if s.strip()]

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute(TABLE_SCHEMA)

    total_inserted = 0
    total_skipped = 0

    for league in leagues:
        league_inserted = 0
        league_skipped = 0
        for season in seasons:
            suffix = season_to_suffix(season)
            filename = f"{league}_{suffix}.csv"
            csv_path = Path("/data/asgard/work/football/cache/footballdata") / filename
            if not csv_path.is_file():
                sys.stderr.write(f"CSV file not found: {csv_path}\n")
                continue

            ins, skp = process_file(csv_path, conn)
            league_inserted += ins
            league_skipped += skp

        print(f"{league}: inserted={league_inserted}, skipped={league_skipped}")
        total_inserted += league_inserted
        total_skipped += league_skipped

    # final summary
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM match_stats")
    total_rows = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM match_stats WHERE referee IS NOT NULL")
    referee_not_null = cur.fetchone()[0]
    print(f"Total match_stats rows: {total_rows}")
    print(f"Rows with referee NOT NULL: {referee_not_null}")

    conn.close()


if __name__ == "__main__":
    main()
