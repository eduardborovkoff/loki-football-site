#!/usr/bin/env python3
"""Multi-sport parser (k1123-E2, T032) — ESPN public API (без ключа).

Sources:
1. NBA: site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard
2. ATP/WTA: site.api.espn.com/apis/site/v2/sports/tennis/{atp,wta}/scoreboard
3. Offline fixture (для тестов/демо).

Rate limit: 1 req/sec, cache TTL 1h в LOKI_HOME/work/football/cache/multisport/.
User-Agent обязателен — Akamai блокирует curl/urllib по умолчанию (403).

Output (JSON):
  <LOKI_HOME>/work/football/data/<sport>/teams.json
  <LOKI_HOME>/work/football/data/<sport>/fixtures.json

CLI:
  python3 tools/multisport_parser.py --sport nba
  python3 tools/multisport_parser.py --sport nba --offline
  python3 tools/multisport_parser.py --sport tennis-atp --offline
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

# sport -> ESPN path
SPORTS = {
    "nba": "basketball/nba",
    "tennis-atp": "tennis/atp",
    "tennis-wta": "tennis/wta",
}

CACHE_TTL_S = 3600
MIN_INTERVAL_S = 1.0


def _home() -> Path:
    return Path(os.environ.get("LOKI_HOME", "/data/asgard")).expanduser()


def _cache_dir() -> Path:
    p = _home() / "work" / "football" / "cache" / "multisport"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _out_dir(sport: str) -> Path:
    p = _home() / "work" / "football" / "data" / sport
    p.mkdir(parents=True, exist_ok=True)
    return p


class _Rate:
    def __init__(self, interval):
        self.interval = interval
        self._last = 0.0
    def wait(self):
        now = time.monotonic()
        d = now - self._last
        if d < self.interval:
            time.sleep(self.interval - d)
        self._last = time.monotonic()


_RATE = _Rate(MIN_INTERVAL_S)


def fetch_espn(sport: str) -> dict:
    path = SPORTS.get(sport)
    if not path:
        raise ValueError(f"unknown sport {sport}; supported: {list(SPORTS)}")
    url = f"{ESPN_BASE}/{path}/scoreboard"
    cache = _cache_dir() / (sport + "_" + time.strftime("%Y-%m-%d-%H") + ".json")
    if cache.exists():
        age = time.time() - cache.stat().st_mtime
        if age < CACHE_TTL_S:
            try:
                return json.loads(cache.read_text(encoding="utf-8"))
            except Exception:
                pass
    _RATE.wait()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json", "Referer": "https://www.espn.com/"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read()
        cache.write_bytes(data)
        return json.loads(data)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        print(f"fetch {sport}: {e}", file=sys.stderr)
        return {}


def parse_espn(sport: str, obj: dict) -> tuple[list[dict], list[dict]]:
    """ESPN scoreboard -> (teams, fixtures)."""
    teams: dict[str, dict] = {}
    fixtures: list[dict] = []
    for ev in obj.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        competitors = comp.get("competitors") or []
        if len(competitors) < 2:
            continue
        # ESPN: competitors[0].homeAway = "home"/"away"
        home = None
        away = None
        for c in competitors:
            name = (c.get("team") or {}).get("displayName") or (c.get("athlete") or {}).get("displayName")
            if not name:
                continue
            teams.setdefault(name, {"name": name, "sport": sport})
            if c.get("homeAway") == "home":
                home = name
            elif c.get("homeAway") == "away":
                away = name
        if not home or not away:
            continue
        # score
        hs = as_ = None
        for c in competitors:
            name = (c.get("team") or {}).get("displayName") or (c.get("athlete") or {}).get("displayName")
            score = c.get("score")
            if name == home:
                hs = score
            elif name == away:
                as_ = score
        status = (ev.get("status") or {}).get("type", {}).get("name", "scheduled")
        fixtures.append({
            "home": home,
            "away": away,
            "date": ev.get("date", ""),
            "status": status,
            "home_score": hs,
            "away_score": as_,
        })
    return list(teams.values()), fixtures


def offline_fixture(sport: str) -> tuple[list[dict], list[dict]]:
    if sport == "nba":
        names = ["Lakers", "Celtics", "Warriors", "Bucks", "Nuggets", "Heat",
                 "Suns", "76ers", "Knicks", "Mavericks"]
    elif sport.startswith("tennis"):
        names = ["Djokovic N.", "Alcaraz C.", "Sinner J.", "Medvedev D.",
                 "Zverev A.", "Rune H.", "Fritz T.", "Rublev A."]
    else:
        names = ["Team A", "Team B"]
    teams = [{"name": n, "sport": sport} for n in names]
    fixtures = [
        {"home": names[0], "away": names[1], "date": "2026-11-15",
         "status": "scheduled", "home_score": None, "away_score": None},
        {"home": names[2], "away": names[3], "date": "2026-11-16",
         "status": "scheduled", "home_score": None, "away_score": None},
    ]
    return teams, fixtures


def _write(sport: str, teams: list[dict], fixtures: list[dict]) -> None:
    out = _out_dir(sport)
    (out / "teams.json").write_text(
        json.dumps(teams, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "fixtures.json").write_text(
        json.dumps(fixtures, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(teams)} teams, {len(fixtures)} fixtures -> {out}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="nba", choices=list(SPORTS) + ["tennis-wta"])
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()
    if args.offline:
        teams, fixtures = offline_fixture(args.sport)
    else:
        obj = fetch_espn(args.sport)
        if not obj:
            print("ESPN fetch failed; pass --offline", file=sys.stderr)
            return 1
        teams, fixtures = parse_espn(args.sport, obj)
        if not teams:
            print("no events parsed; pass --offline", file=sys.stderr)
            return 1
    _write(args.sport, teams, fixtures)
    return 0


if __name__ == "__main__":
    sys.exit(main())