#!/usr/bin/env python3
"""Football model (k1121, M051) — вероятности П1/Х/П2: Пуассон + Dixon-Coles.

Вход: teams.json (от football_parser.py, T032) + fixtures.json.
Выход: probs.json — P(П1)/P(Х)/P(П2), λ_home, λ_away, edge vs букмекер.

Чистый Python (math, json). Без ML. Без сети.

Модель:
  λ_H = avg_home_gf[home] * avg_away_ga[away] / avg_league_goals
  λ_A = avg_away_gf[away] * avg_home_ga[home] / avg_league_goals
  P(i:j) = Poisson(λ_H, i) * Poisson(λ_A, j),  i+j <= max_goals
  P(П1)=Σ(i>j), P(Х)=Σ(i=j), P(П2)=Σ(i<j)

Dixon-Coles поправка τ(i,j) на низкие счета (0:0,1:0,0:1,1:1):
  τ(0,0)=1-λH*λA*ρ, τ(0,1)=1+λH*ρ, τ(1,0)=1+λA*ρ, τ(1,1)=1-ρ
  (ρ<0 усиливает 0:0 и 1:1, ослабляет 1:0/0:1)

CLI:
  --teams F --fixtures F --out probs.json [--rho -0.05] [--max-goals 10]
  --backtest probs.json --results F   # Brier + калибровка
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path


def poisson_pmf(lam: float, k: int) -> float:
    """P(X=k) ~ Poisson(lam)."""
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def dixon_coles_tau(i: int, j: int, lam_h: float, lam_a: float, rho: float) -> float:
    """Dixon-Coles поправка на низкие счета."""
    if rho == 0.0:
        return 1.0
    if i == 0 and j == 0:
        return 1.0 - lam_h * lam_a * rho
    if i == 0 and j == 1:
        return 1.0 + lam_h * rho
    if i == 1 and j == 0:
        return 1.0 + lam_a * rho
    if i == 1 and j == 1:
        return 1.0 - rho
    return 1.0


def score_matrix(lam_h: float, lam_a: float, max_goals: int = 10, rho: float = 0.0) -> list[list[float]]:
    """Матрица P(i:j) с Dixon-Coles поправкой, нормированная."""
    m: list[list[float]] = []
    total = 0.0
    for i in range(max_goals + 1):
        row: list[float] = []
        for j in range(max_goals + 1):
            p = poisson_pmf(lam_h, i) * poisson_pmf(lam_a, j) * dixon_coles_tau(i, j, lam_h, lam_a, rho)
            row.append(p)
            total += p
        m.append(row)
    if total > 0:
        for i in range(len(m)):
            for j in range(len(m[i])):
                m[i][j] /= total
    return m


def outcome_probs(lam_h: float, lam_a: float, max_goals: int = 10, rho: float = 0.0) -> tuple[float, float, float]:
    """(p_home, p_draw, p_away)."""
    m = score_matrix(lam_h, lam_a, max_goals, rho)
    p_home = p_draw = p_away = 0.0
    for i in range(len(m)):
        for j in range(len(m[i])):
            p = m[i][j]
            if i > j:
                p_home += p
            elif i == j:
                p_draw += p
            else:
                p_away += p
    s = p_home + p_draw + p_away
    if s > 0:
        p_home, p_draw, p_away = p_home / s, p_draw / s, p_away / s
    return p_home, p_draw, p_away


def _team_stats(teams: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for t in teams:
        out[t.get("team") or t.get("name")] = t
    return out


def _league_avg(teams: list[dict]) -> float:
    """Среднее голов за игру (по всем командам, all matches)."""
    total_gf = sum(t.get("goals_for", 0) for t in teams)
    total_matches = sum(t.get("matches_played", 0) for t in teams)
    if total_matches == 0:
        return 1.35  # дефолт среднего по лиге
    return total_gf / total_matches


def _safe_avg(total: float, n: float, default: float) -> float:
    return total / n if n > 0 else default


def lambdas(home: dict, away: dict, league_avg: float) -> tuple[float, float]:
    """λ_home, λ_away через attack/defense strength."""
    # дефолты, если мало матчей
    def _hf(t: dict) -> float:
        mp = t.get("matches_played", 0)
        return _safe_avg(t.get("home_goals_for", 0), mp, league_avg)

    def _ha(t: dict) -> float:
        mp = t.get("matches_played", 0)
        return _safe_avg(t.get("home_goals_against", 0), mp, league_avg)

    def _af(t: dict) -> float:
        mp = t.get("matches_played", 0)
        return _safe_avg(t.get("away_goals_for", 0), mp, league_avg)

    def _aa(t: dict) -> float:
        mp = t.get("matches_played", 0)
        return _safe_avg(t.get("away_goals_against", 0), mp, league_avg)

    la = league_avg if league_avg > 0 else 1.35
    lam_h = _hf(home) * _aa(away) / la
    lam_a = _af(away) * _ha(home) / la
    # нижняя граница 0.1, чтобы не было 0
    return max(lam_h, 0.1), max(lam_a, 0.1)


def predict(teams: list[dict], fixtures: list[dict], rho: float = 0.0, max_goals: int = 10) -> list[dict]:
    stats = _team_stats(teams)
    la = _league_avg(teams)
    out: list[dict] = []
    for fx in fixtures:
        home = stats.get(fx["home"])
        away = stats.get(fx["away"])
        if not home or not away:
            out.append({**fx, "error": "team not found"})
            continue
        lam_h, lam_a = lambdas(home, away, la)
        p1, px, p2 = outcome_probs(lam_h, lam_a, max_goals, rho)
        rec = {
            "home": fx["home"], "away": fx["away"],
            "lambda_home": round(lam_h, 3), "lambda_away": round(lam_a, 3),
            "p_home": round(p1, 4), "p_draw": round(px, 4), "p_away": round(p2, 4),
        }
        # edge vs букмекер, если есть odds
        for side, p in (("home", p1), ("draw", px), ("away", p2)):
            odd_key = f"odds_{side}"
            if fx.get(odd_key):
                implied = 1.0 / float(fx[odd_key])
                rec[f"edge_{side}"] = round(p - implied, 4)
                rec[f"value_{side}"] = (p - implied) > 0.03
        out.append(rec)
    return out


def brier(probs: list[dict], results: list[dict]) -> dict:
    """Brier score (мультикласс) + калибровка по бинам.

    results: [{"home","away","result":"home|draw|away"}]
    """
    idx = {(r["home"], r["away"]): r["result"] for r in results}
    pairs: list[tuple[tuple[float, float, float], str]] = []
    for p in probs:
        key = (p.get("home"), p.get("away"))
        actual = idx.get(key)
        if not actual:
            continue
        vec = (p.get("p_home", 0), p.get("p_draw", 0), p.get("p_away", 0))
        pairs.append((vec, actual))
    if not pairs:
        return {"n": 0, "brier": None}
    bs = 0.0
    bins: dict[int, list[tuple[float, int]]] = {i: [] for i in range(10)}
    for vec, actual in pairs:
        for i, p in enumerate(vec):
            target = 1 if (i == 0 and actual == "home") or (i == 1 and actual == "draw") or (i == 2 and actual == "away") else 0
            bs += (p - target) ** 2
            b = min(int(p * 10), 9)
            bins[b].append((p, target))
    n_events = len(pairs) * 3
    calib = []
    for b in range(10):
        if not bins[b]:
            continue
        ps = [x[0] for x in bins[b]]
        ts = [x[1] for x in bins[b]]
        calib.append({
            "bin": f"{b*10}-{(b+1)*10}%",
            "predicted": round(sum(ps) / len(ps), 3),
            "actual": round(sum(ts) / len(ts), 3),
            "n": len(ts),
        })
    return {"n": len(pairs), "brier": round(bs / n_events, 4), "calibration": calib}


def main() -> None:
    ap = argparse.ArgumentParser(description="Football model (k1121, M051)")
    ap.add_argument("--teams")
    ap.add_argument("--fixtures")
    ap.add_argument("--out")
    ap.add_argument("--rho", type=float, default=-0.05)
    ap.add_argument("--max-goals", type=int, default=10)
    ap.add_argument("--backtest", help="probs.json для бэктеста")
    ap.add_argument("--results", help="results.json для бэктеста")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.backtest:
        if not args.results:
            print("FAIL: --backtest требует --results", file=sys.stderr)
            sys.exit(2)
        probs = json.loads(Path(args.backtest).read_text(encoding="utf-8"))
        results = json.loads(Path(args.results).read_text(encoding="utf-8"))
        rep = brier(probs, results)
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return

    if not (args.teams and args.fixtures and args.out):
        print("FAIL: --teams --fixtures --out обязательны", file=sys.stderr)
        sys.exit(2)

    teams = json.loads(Path(args.teams).read_text(encoding="utf-8"))
    fixtures = json.loads(Path(args.fixtures).read_text(encoding="utf-8"))
    probs = predict(teams, fixtures, rho=args.rho, max_goals=args.max_goals)
    Path(args.out).write_text(json.dumps(probs, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(probs, ensure_ascii=False, indent=2))
    else:
        value = sum(1 for p in probs if any(p.get(f"value_{s}") for s in ("home", "draw", "away")))
        print(f"Матчей: {len(probs)}, value-bets: {value}, out: {args.out}")


if __name__ == "__main__":
    main()