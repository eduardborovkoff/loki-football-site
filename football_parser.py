#!/usr/bin/env python3
"""Football data parser (k1119, T032) — read-only.

Sources (free, no paid APIs):
1. OpenFootball (github raw JSON) — no key, most reliable.
2. football-data.org (free-tier, 10 req/min) — env FOOTBALL_DATA_API_KEY.
3. the-odds-api.com (500 req/month) — env ODDS_API_KEY (заглушка в этой версии).
4. FBref — HTML-парсинг, fragile (stub в MVP).

Output:
  <LOKI_HOME>/work/football/data/<league>/<season>/teams.json
  <LOKI_HOME>/work/football/data/<league>/<season>/fixtures.json

Cache: <LOKI_HOME>/work/football/cache/<source>/<YYYY-MM-DD>.json (TTL 24h).
Rate limit: >= 6s between requests to free APIs.

CLI:
  python3 tools/football_parser.py --league EPL --season 2025-26
  python3 tools/football_parser.py --league EPL --season 2025-26 --offline
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


CACHE_TTL_S = 24 * 3600
MIN_INTERVAL_S = 6.0


def _home() -> Path:
    return Path(os.environ.get("LOKI_HOME", "/data/asgard")).expanduser()


def _cache_dir(source: str) -> Path:
    p = _home() / "work" / "football" / "cache" / source
    p.mkdir(parents=True, exist_ok=True)
    return p


def _output_dir(league: str, season: str) -> Path:
    p = _home() / "work" / "football" / "data" / league.upper() / season
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class Team:
    name: str
    matches_played: int = 0
    goals_for: int = 0
    goals_against: int = 0
    home_goals_for: int = 0
    home_goals_against: int = 0
    away_goals_for: int = 0
    away_goals_against: int = 0
    form: list = field(default_factory=list)


@dataclass
class Fixture:
    home: str
    away: str
    date: str
    odds_home: float = None
    odds_draw: float = None
    odds_away: float = None


class RateLimiter:
    def __init__(self, interval_s):
        self.interval_s = interval_s
        self._last = 0.0

    def wait(self):
        now = time.monotonic()
        delta = now - self._last
        if delta < self.interval_s:
            time.sleep(self.interval_s - delta)
        self._last = time.monotonic()


def _cached_fetch(url, source, rate, headers=None):
    cache = _cache_dir(source) / (time.strftime("%Y-%m-%d") + ".json")
    if cache.exists():
        age = time.time() - cache.stat().st_mtime
        if age < CACHE_TTL_S:
            try:
                return cache.read_bytes()
            except OSError:
                pass
    rate.wait()
    try:
        h = {"User-Agent": "LokiFootball/0.1"}
        if headers:
            h.update(headers)
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read()
        cache.write_bytes(data)
        return data
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        print(f"fetch {source}: {e}", file=sys.stderr)
        return None


def parse_openfootball(league, season):
    slug = {
        "EPL": "en.1", "LALIGA": "es.1", "SERIEA": "it.1",
        "BUNDESLIGA": "de.1", "LIGUE1": "fr.1",
    }.get(league.upper(), "en.1")
    url = (f"https://raw.githubusercontent.com/openfootball/football.json/"
           f"master/{season}/{slug}.json")
    rate = RateLimiter(MIN_INTERVAL_S)
    data = _cached_fetch(url, "openfootball", rate)
    if not data:
        return [], []
    try:
        obj = json.loads(data)
    except json.JSONDecodeError:
        return [], []
    teams, fixtures = {}, []
    for m in obj.get("matches", []):
        home_name = (m.get("team1") or {}).get("name")
        away_name = (m.get("team2") or {}).get("name")
        if not home_name or not away_name:
            continue
        for name in (home_name, away_name):
            teams.setdefault(name, Team(name=name))
        ft = (m.get("score") or {}).get("ft")
        if ft and len(ft) == 2:
            hg, ag = int(ft[0]), int(ft[1])
            th, ta = teams[home_name], teams[away_name]
            th.matches_played += 1
            ta.matches_played += 1
            th.goals_for += hg
            th.goals_against += ag
            ta.goals_for += ag
            ta.goals_against += hg
            th.home_goals_for += hg
            th.home_goals_against += ag
            ta.away_goals_for += ag
            ta.away_goals_against += hg
            th.form.append("W" if hg > ag else ("L" if hg < ag else "D"))
            ta.form.append("W" if ag > hg else ("L" if ag < hg else "D"))
        else:
            fixtures.append(Fixture(home=home_name, away=away_name,
                                     date=str(m.get("date", ""))))
    return list(teams.values()), fixtures


def parse_football_data_org(league, season):
    api_key = os.environ.get("FOOTBALL_DATA_API_KEY")
    if not api_key:
        print("FOOTBALL_DATA_API_KEY not set — skip", file=sys.stderr)
        return [], []
    comp = {
        "EPL": "PL", "LALIGA": "PD", "SERIEA": "SA",
        "BUNDESLIGA": "BL1", "LIGUE1": "FL1",
    }.get(league.upper(), "PL")
    year = season.split("-")[0] if "-" in season else season
    url = (f"https://api.football-data.org/v4/competitions/{comp}/teams"
           f"?season={year}")
    rate = RateLimiter(MIN_INTERVAL_S)
    data = _cached_fetch(url, "football-data", rate,
                         headers={"X-Auth-Token": api_key})
    if not data:
        return [], []
    try:
        obj = json.loads(data)
    except json.JSONDecodeError:
        return [], []
    teams = [Team(name=t["name"]) for t in obj.get("teams", []) if t.get("name")]
    return teams, []


def offline_fixture():
    """20 команд EPL — стабильная фикстура для тестов."""
    names = ["Arsenal", "Chelsea", "Liverpool", "Man City", "Man United",
             "Tottenham", "Newcastle", "Brighton", "Aston Villa", "West Ham",
             "Everton", "Crystal Palace", "Brentford", "Fulham", "Wolves",
             "Nottingham Forest", "Leicester", "Ipswich", "Southampton",
             "Bournemouth"]
    teams = []
    for i, name in enumerate(names):
        t = Team(name=name)
        t.matches_played = 10
        t.home_goals_for = 10 + (i % 5)
        t.away_goals_for = 8 + (i % 4)
        t.goals_for = t.home_goals_for + t.away_goals_for
        t.home_goals_against = 5 + (i % 3)
        t.away_goals_against = 6 + (i % 3)
        t.goals_against = t.home_goals_against + t.away_goals_against
        t.form = (["W", "D", "L", "W", "W"] if i % 2 == 0
                  else ["L", "W", "D", "L", "W"])
        teams.append(t)
    fixtures = [
        Fixture(home="Arsenal", away="Chelsea", date="2026-11-15",
                odds_home=1.85, odds_draw=3.60, odds_away=4.20),
        Fixture(home="Liverpool", away="Man City", date="2026-11-16",
                odds_home=2.50, odds_draw=3.30, odds_away=2.80),
    ]
    return teams, fixtures


def _write(league, season, teams, fixtures):
    out = _output_dir(league, season)
    (out / "teams.json").write_text(
        json.dumps([asdict(t) for t in teams], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out / "fixtures.json").write_text(
        json.dumps([asdict(f) for f in fixtures], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"wrote {len(teams)} teams, {len(fixtures)} fixtures -> {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", default="EPL")
    ap.add_argument("--season", default="2025-26")
    ap.add_argument("--offline", action="store_true",
                    help="offline-фикстура (без сети)")
    args = ap.parse_args()

    if args.offline:
        teams, fixtures = offline_fixture()
    else:
        teams, fixtures = parse_openfootball(args.league, args.season)
        if not teams:
            teams, fixtures = parse_football_data_org(args.league, args.season)
        if not teams:
            print("all sources failed; pass --offline for fixture",
                  file=sys.stderr)
            return 1

    _write(args.league, args.season, teams, fixtures)
    return 0


if __name__ == "__main__":
    sys.exit(main())