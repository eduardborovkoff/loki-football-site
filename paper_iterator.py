"""Paper-iterator k1122 (M052 S185): модель football_model → value-bets → journal sport_analyst → ROI → POST /cycles. Симуляция 18+, без реальных денег."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from typing import Any
from urllib.request import Request, urlopen

sys.path.insert(0, '/data/loki/tools')

from football_model import predict
from sport_analyst import ev, implied_prob, _journal_path

_ = ev
_ = implied_prob

__all__ = [
    'load_json',
    'model_probs',
    'probs_to_candidates',
    'append_journal',
    'calc_roi',
    'post_cycle',
    'run_iteration',
    'main',
]


def load_json(path: str) -> list[Any]:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def model_probs(teams: list[Any], fixtures: list[Any], rho: float = -0.05) -> list[dict[str, Any]]:
    return predict(teams, fixtures, rho=rho)


def probs_to_candidates(probs: list[dict[str, Any]], min_edge: float = 0.03) -> list[dict[str, Any]]:
    sides = ('home', 'draw', 'away')
    cands: list[dict[str, Any]] = []
    for p in probs:
        best_side: str | None = None
        best_edge = float('-inf')
        for side in sides:
            value_flag = bool(p.get(f'value_{side}', False))
            edge_val = float(p.get(f'edge_{side}', float('-inf')))
            if value_flag and edge_val >= min_edge and edge_val > best_edge:
                best_edge = edge_val
                best_side = side
        if best_side is None:
            continue
        prob = float(p[f'p_{best_side}'])
        edge = float(p[f'edge_{best_side}'])
        denom = prob - edge
        coef = 1.0 / denom if denom != 0 else float('inf')
        stake = 1000.0
        ev_val = (prob * (coef - 1.0) - (1.0 - prob)) * stake
        home = str(p.get('home_team', p.get('home', 'Home')))
        away = str(p.get('away_team', p.get('away', 'Away')))
        match_name = str(p.get('match', f'{home} vs {away}'))
        cands.append({
            'match': match_name,
            'pick': best_side,
            'prob': prob,
            'coef': coef,
            'stake': stake,
            'ev': ev_val,
            'source': 'model',
        })
    return cands


def append_journal(cands: list[dict[str, Any]], journal_path: str) -> int:
    directory = os.path.dirname(os.path.abspath(journal_path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    count = 0
    with open(journal_path, 'a', encoding='utf-8') as f:
        for cand in cands:
            record: dict[str, Any] = {'ts': time.time(), **cand, 'result': None}
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
            count += 1
    return count


def calc_roi(journal_path: str) -> dict[str, Any]:
    bets = 0
    wins = 0
    pnl = 0.0
    total_stake = 0.0
    try:
        with open(journal_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get('result') not in ('WIN', 'LOSS'):
                    continue
                stake = float(rec.get('stake', 0.0))
                coef = float(rec.get('coef', 1.0))
                total_stake += stake
                bets += 1
                if rec.get('result') == 'WIN':
                    wins += 1
                    pnl += (coef - 1.0) * stake
                else:
                    pnl -= stake
    except (FileNotFoundError, OSError):
        pass
    winrate = wins / bets if bets else 0.0
    roi = pnl / total_stake if total_stake else 0.0
    return {
        'roi': roi,
        'wins': wins,
        'bets': bets,
        'pnl': pnl,
        'stake': total_stake,
        'winrate': winrate,
    }


def post_cycle(snapshot: dict[str, Any], url: str = 'http://127.0.0.1:8765/cycles') -> bool:
    try:
        data = json.dumps(snapshot).encode('utf-8')
        req = Request(url, data=data, headers={'Content-Type': 'application/json'}, method='POST')
        with urlopen(req, timeout=5) as resp:
            resp.read()
        return True
    except Exception as e:
        print(f'post_cycle error: {type(e).__name__}: {e}', file=sys.stderr)
        return False


def run_iteration(
    teams_path: str,
    fixtures_path: str,
    journal_path: str,
    rho: float = -0.05,
    min_edge: float = 0.03,
    algo_version: str = 'iter-v1',
    sport: str = 'football',
    post: bool = True,
) -> dict[str, Any]:
    teams = load_json(teams_path)
    fixtures = load_json(fixtures_path)
    probs = model_probs(teams, fixtures, rho=rho)
    cands = probs_to_candidates(probs, min_edge=min_edge)
    added = append_journal(cands, journal_path)
    roi = calc_roi(journal_path)
    snapshot: dict[str, Any] = {
        'algo_version': algo_version,
        'sport': sport,
        'roi': roi['roi'],
        'winrate': roi['winrate'],
        'brier': 0.0,
        'logloss': 0.0,
        'max_dd': 0.0,
        'n_bets': roi['bets'],
    }
    posted = post_cycle(snapshot) if post else False
    return {'added': added, 'roi': roi, 'snapshot': snapshot, 'posted': posted}


def main() -> None:
    parser = argparse.ArgumentParser(description='Paper-iterator k1122')
    parser.add_argument('--teams', default='/data/asgard/work/football/data/EPL/2025-26/teams.json')
    parser.add_argument('--fixtures', default='/data/asgard/work/football/data/EPL/2025-26/fixtures.json')
    parser.add_argument('--journal', default=_journal_path())
    parser.add_argument('--rho', type=float, default=-0.05)
    parser.add_argument('--min-edge', type=float, default=0.03)
    parser.add_argument('--algo-version', default='iter-v1')
    parser.add_argument('--sport', default='football')
    parser.add_argument('--no-post', action='store_false', dest='post', default=True)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()

    result = run_iteration(
        teams_path=args.teams,
        fixtures_path=args.fixtures,
        journal_path=args.journal,
        rho=args.rho,
        min_edge=args.min_edge,
        algo_version=args.algo_version,
        sport=args.sport,
        post=args.post,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        roi = result['roi']
        posted = 'T' if result['posted'] else 'F'
        print(
            f"iter: added={result['added']} roi={roi['roi']:.4f} "
            f"winrate={roi['winrate']:.4f} n={roi['bets']} posted={posted}"
        )


if __name__ == '__main__':
    main()