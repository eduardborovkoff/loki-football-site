#!/usr/bin/env python3
"""Sport analyst (k960, M051) — READ-ONLY paper trading.

Ограничение (осознанно): НЕ делаем реальных ставок и НЕ парсим букмекеров
автоматически. Источник данных — JSON от Эда/сессии (матчи + коэффициенты),
кладётся вручную. Модуль делает рутину:
- расчёт implied probability и EV (expected value) для прогноза;
- ведение журнала виртуальных ставок (JSONL) и ROI/винрейта;
- отчёт в Telegram через `loki notify` (опционально).

Формат входа (--in matches.json):
[
  {"match": "A vs B", "pick": "A", "coef": 1.85,
   "prob": 0.60,  # субъективная вероятность победы (0..1)
   "stake": 1000, "league": "EPL", "ts": "2026-11-10T20:00"}
]

Модель: EV = prob * (coef - 1) * stake - (1 - prob) * stake
       (положительный EV → value bet)

Журнал: LOKI_HOME/work/sport_analyst/journal.jsonl (append-only).
ROI = сумма(PnL) / сумма(stake).

Режимы:
  --in F.json        матчи (обязателен)
  --min-ev N         порог EV (по умолчанию 0 — только value > 0)
  --log-result ID:WIN|LOSS   записать результат ставки
  --roi              показать ROI/винрейт по журналу
  --notify           отправить отчёт в Telegram (loki notify)
  --json             машинный вывод
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _home() -> Path:
    return Path(os.environ.get("LOKI_HOME", "/data/asgard")).expanduser()


def _journal_path() -> Path:
    p = _home() / "work" / "sport_analyst" / "journal.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def ev(prob: float, coef: float, stake: float) -> float:
    """EV ставки: prob*(coef-1)*stake - (1-prob)*stake."""
    return prob * (coef - 1.0) * stake - (1.0 - prob) * stake


def implied_prob(coef: float) -> float:
    """Вероятность, заложенная в коэффициент (1/coef)."""
    return 1.0 / coef if coef > 0 else 0.0


def analyze(matches: list[dict], min_ev: float = 0.0) -> list[dict]:
    out: list[dict] = []
    for m in matches:
        try:
            coef = float(m["coef"])
            prob = float(m["prob"])
            stake = float(m.get("stake", 1000))
        except (KeyError, TypeError, ValueError):
            continue
        e = ev(prob, coef, stake)
        out.append({
            **m,
            "implied_prob": round(implied_prob(coef), 4),
            "ev": round(e, 2),
            "value": e >= min_ev,
        })
    return out


def log_bet(match: dict) -> None:
    p = _journal_path()
    rec = {
        "ts": time.time(),
        "match": match.get("match"),
        "pick": match.get("pick"),
        "coef": match.get("coef"),
        "prob": match.get("prob"),
        "stake": match.get("stake", 1000),
        "ev": match.get("ev"),
        "result": None,  # WIN/LOSS
    }
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def log_result(bet_id: str, result: str) -> bool:
    """Записать результат в журнал по индексу строки (0-based)."""
    p = _journal_path()
    if not p.exists():
        return False
    lines = p.read_text(encoding="utf-8").splitlines()
    try:
        idx = int(bet_id)
    except ValueError:
        return False
    if idx < 0 or idx >= len(lines):
        return False
    rec = json.loads(lines[idx])
    rec["result"] = result.upper()
    lines[idx] = json.dumps(rec, ensure_ascii=False)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def roi() -> dict:
    """ROI/винрейт по закрытым ставкам журнала."""
    p = _journal_path()
    if not p.exists():
        return {"bets": 0, "wins": 0, "winrate": 0.0, "pnl": 0.0, "stake": 0.0, "roi": 0.0}
    bets = wins = 0
    pnl = stake_total = 0.0
    for ln in p.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r.get("result") not in ("WIN", "LOSS"):
            continue
        bets += 1
        stake = float(r.get("stake", 0))
        stake_total += stake
        if r["result"] == "WIN":
            wins += 1
            pnl += stake * (float(r.get("coef", 1.0)) - 1.0)
        else:
            pnl -= stake
    return {
        "bets": bets,
        "wins": wins,
        "winrate": round(wins / bets, 4) if bets else 0.0,
        "pnl": round(pnl, 2),
        "stake": round(stake_total, 2),
        "roi": round(pnl / stake_total, 4) if stake_total else 0.0,
    }


def _notify(text: str) -> None:
    try:
        subprocess.run(["loki", "notify", text[:3500]], check=False, timeout=15)
    except Exception:
        pass


def main() -> None:
    ap = argparse.ArgumentParser(description="Sport analyst (read-only, k960)")
    ap.add_argument("--in", dest="inp", help="JSON матчей")
    ap.add_argument("--min-ev", type=float, default=0.0)
    ap.add_argument("--log-result", help="ID:WIN|LOSS — записать результат ставки")
    ap.add_argument("--roi", action="store_true", help="показать ROI/винрейт")
    ap.add_argument("--notify", action="store_true", help="Telegram")
    ap.add_argument("--json", action="store_true", help="машинный вывод")
    args = ap.parse_args()

    if args.log_result:
        try:
            bid, res = args.log_result.split(":", 1)
        except ValueError:
            print("FAIL: --log-result формат ID:WIN|LOSS", file=sys.stderr)
            sys.exit(2)
        ok = log_result(bid, res)
        print(json.dumps({"logged": ok, "id": bid, "result": res}, ensure_ascii=False))
        return

    if args.roi:
        print(json.dumps(roi(), ensure_ascii=False))
        return

    if not args.inp:
        print("FAIL: --in F.json или --roi или --log-result", file=sys.stderr)
        sys.exit(2)

    matches = json.loads(Path(args.inp).read_text(encoding="utf-8"))
    analyzed = analyze(matches, min_ev=args.min_ev)
    value_bets = [a for a in analyzed if a.get("value")]

    if args.json:
        print(json.dumps({"analyzed": analyzed, "value_bets": value_bets}, ensure_ascii=False, indent=2))
    else:
        print(f"Матчей: {len(analyzed)}, value-bets: {len(value_bets)}")
        for a in value_bets:
            print(f"  {a.get('match')}: pick={a.get('pick')} coef={a.get('coef')} "
                  f"prob={a.get('prob')} EV={a.get('ev')}")

    if args.notify:
        lines = [f"[k960] Sport analyst: value-bets {len(value_bets)}/{len(analyzed)}"]
        for a in value_bets[:10]:
            lines.append(f"  {a.get('match')}: {a.get('pick')} @{a.get('coef')} EV={a.get('ev')}")
        _notify("\n".join(lines))

    # авто-запись value-bets в журнал
    for a in value_bets:
        log_bet(a)
    print(f"journal: {_journal_path()}")


if __name__ == "__main__":
    main()