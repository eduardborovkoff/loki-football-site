#!/usr/bin/env python3
"""Football stats API server (k1120-E2, T032) — stdlib http.server MVP.

Почему stdlib: fastapi/uvicorn нет в venv, не ставим пакеты в этой сессии
(риск окружения). Для E2 достаточно доказать архитектуру.

Endpoints:
  GET /health       -> {"ok": true, "db": "..."}
  GET /teams        -> список команд
  GET /matches      -> список матчей (с odds)
  GET /predictions  -> прогнозы (пока пусто; заполнит M051-математика)

Запуск:
  python3 tools/fb_server.py --port 8765
  python3 tools/fb_server.py --port 8765 --bind 127.0.0.1  # default
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse


def _home() -> Path:
    return Path(os.environ.get("LOKI_HOME", "/data/asgard")).expanduser()


def _db_path() -> Path:
    return _home() / "work" / "football" / "stats.db"


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


class FbHandler(BaseHTTPRequestHandler):
    server_version = "LokiFb/0.1"

    def log_message(self, fmt, *args):
        # тихий лог
        return

    def _send_json(self, code: int, obj) -> None:
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _query(self, sql: str, args: tuple = ()) -> list[dict]:
        db = _db_path()
        if not db.exists():
            return []
        conn = sqlite3.connect(str(db))
        try:
            conn.row_factory = sqlite3.Row
            return [_row_to_dict(r) for r in conn.execute(sql, args).fetchall()]
        finally:
            conn.close()

    def _read_body_json(self):
        try:
            n = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            n = 0
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == "/cycles":
                body = self._read_body_json()
                # ожидаем: {algo_version, sport, roi, winrate, brier, logloss, max_dd, n_bets}
                db = _db_path()
                conn = sqlite3.connect(str(db))
                try:
                    # ensure table cycles (idempotent)
                    conn.executescript(
                        "CREATE TABLE IF NOT EXISTS cycles ("
                        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                        " algo_version TEXT, sport TEXT,"
                        " roi REAL, winrate REAL, brier REAL, logloss REAL,"
                        " max_dd REAL, n_bets INTEGER,"
                        " ts REAL DEFAULT (strftime('%s','now')));"
                    )
                    conn.execute(
                        "INSERT INTO cycles (algo_version, sport, roi, winrate, "
                        "brier, logloss, max_dd, n_bets) VALUES (?,?,?,?,?,?,?,?)",
                        (body.get("algo_version"), body.get("sport"),
                         body.get("roi"), body.get("winrate"),
                         body.get("brier"), body.get("logloss"),
                         body.get("max_dd"), body.get("n_bets")),
                    )
                    conn.commit()
                finally:
                    conn.close()
                self._send_json(200, {"ok": True, "inserted": 1})
                return
            self._send_json(404, {"error": "not found", "path": path})
        except Exception as e:
            self._send_json(500, {"error": f"{type(e).__name__}: {e}"})

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/" or path == "/index.html":
                idx = Path(__file__).resolve().parent / "fb_index.html"
                if not idx.exists():
                    self._send_json(404, {"error": "index not found"})
                    return
                body = idx.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/health":
                self._send_json(200, {"ok": True, "db": str(_db_path())})
                return
            if path == "/teams":
                rows = self._query(
                    "SELECT id, name, league, elo, wins, draws, losses, "
                    "goals_for, goals_against FROM teams ORDER BY id"
                )
                self._send_json(200, {"teams": rows, "n": len(rows)})
                return
            if path == "/matches":
                rows = self._query(
                    "SELECT m.id, th.name AS home, ta.name AS away, m.league, "
                    "m.kickoff_ts, m.home_goals, m.away_goals, m.status, "
                    "o.p1, o.px, o.p2, o.bookmaker "
                    "FROM matches m "
                    "JOIN teams th ON m.home_id=th.id "
                    "JOIN teams ta ON m.away_id=ta.id "
                    "LEFT JOIN odds o ON o.match_id=m.id "
                    "ORDER BY m.kickoff_ts"
                )
                # k1120-i18n: переводим status/league на русский
                _status_ru = {"scheduled": "запланирован",
                              "finished": "завершён",
                              "live": "в игре", "postponed": "отложен"}
                _league_ru = {"EPL": "АПЛ", "LALIGA": "Ла Лига",
                              "SERIEA": "Серия А", "BUNDESLIGA": "Бундеслига",
                              "LIGUE1": "Лига 1"}
                for r in rows:
                    s = r.get("status")
                    if s in _status_ru:
                        r["status"] = _status_ru[s]
                    lg = r.get("league")
                    if lg in _league_ru:
                        r["league"] = _league_ru[lg]
                self._send_json(200, {"matches": rows, "n": len(rows)})
                return
            if path == "/tracker" or path == "/tracker.html":
                idx = Path(__file__).resolve().parent / "fb_tracker.html"
                if not idx.exists():
                    self._send_json(404, {"error": "tracker not found"})
                    return
                body = idx.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/cycles":
                rows = []
                db = _db_path()
                if db.exists():
                    conn = sqlite3.connect(str(db))
                    try:
                        conn.row_factory = sqlite3.Row
                        try:
                            rows = [_row_to_dict(r) for r in conn.execute(
                                "SELECT * FROM cycles ORDER BY ts"
                            ).fetchall()]
                        except sqlite3.OperationalError:
                            rows = []
                    finally:
                        conn.close()
                self._send_json(200, {"cycles": rows, "n": len(rows)})
                return
            if path == "/predictions":
                # заглушка: таблицу predictions заполнит M051-математика
                rows = []
                db = _db_path()
                if db.exists():
                    conn = sqlite3.connect(str(db))
                    try:
                        conn.row_factory = sqlite3.Row
                        try:
                            rows = [_row_to_dict(r) for r in conn.execute(
                                "SELECT * FROM predictions ORDER BY match_id"
                            ).fetchall()]
                        except sqlite3.OperationalError:
                            rows = []  # таблицы ещё нет
                    finally:
                        conn.close()
                self._send_json(200, {"predictions": rows, "n": len(rows)})
                return
            self._send_json(404, {"error": "not found", "path": path})
        except Exception as e:
            self._send_json(500, {"error": f"{type(e).__name__}: {e}"})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--bind", default="127.0.0.1")
    args = ap.parse_args()
    srv = HTTPServer((args.bind, args.port), FbHandler)
    print(f"listening {args.bind}:{args.port} db={_db_path()}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())