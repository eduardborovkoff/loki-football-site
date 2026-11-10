#!/usr/bin/env python3
"""Patch fb_server.py (add /backtest endpoint) + fb_index.html (real backtest section)."""
from pathlib import Path

# ---------- A. fb_server.py: add /backtest endpoint ----------
srv_path = Path("/data/loki/fb_site_deploy/fb_server.py")
src = srv_path.read_text(encoding="utf-8")

endpoint = (
    '            if path == "/backtest":\n'
    '                import json as _json\n'
    '                bets_path = Path(__file__).resolve().parent / "work" / "football" / "walkforward_bets.jsonl"\n'
    '                bets = []\n'
    '                if bets_path.exists():\n'
    '                    try:\n'
    '                        for _l in bets_path.read_text(encoding="utf-8").splitlines():\n'
    '                            if _l.strip():\n'
    '                                bets.append(_json.loads(_l))\n'
    '                    except Exception:\n'
    '                        bets = []\n'
    '                n_bets = len(bets)\n'
    '                wins = sum(1 for b in bets if b.get("win"))\n'
    '                total_pnl = sum(float(b.get("pnl", 0)) for b in bets)\n'
    '                total_stake = sum(float(b.get("stake", 0)) for b in bets)\n'
    '                winrate = (wins / n_bets * 100.0) if n_bets else 0.0\n'
    '                roi_pct = (total_pnl / total_stake * 100.0) if total_stake else 0.0\n'
    '                cum = peak = mdd = 0.0\n'
    '                for b in bets:\n'
    '                    cum += float(b.get("pnl", 0))\n'
    '                    if cum > peak:\n'
    '                        peak = cum\n'
    '                    dd = peak - cum\n'
    '                    if dd > mdd:\n'
    '                        mdd = dd\n'
    '                mdd_pct = (mdd / total_stake * 100.0) if total_stake else 0.0\n'
    '                buckets = {"edge<0.1": [0, 0, 0.0, 0.0],\n'
    '                           "edge0.1-0.2": [0, 0, 0.0, 0.0],\n'
    '                           "edge>=0.2": [0, 0, 0.0, 0.0]}\n'
    '                for b in bets:\n'
    '                    e = float(b.get("edge", 0))\n'
    '                    k = "edge<0.1" if e < 0.1 else ("edge0.1-0.2" if e < 0.2 else "edge>=0.2")\n'
    '                    buckets[k][0] += 1\n'
    '                    buckets[k][1] += 1 if b.get("win") else 0\n'
    '                    buckets[k][2] += float(b.get("pnl", 0))\n'
    '                    buckets[k][3] += float(b.get("stake", 0))\n'
    '                by_edge = []\n'
    '                for k, v in buckets.items():\n'
    '                    w = (v[1] / v[0] * 100.0) if v[0] else 0.0\n'
    '                    r = (v[2] / v[3] * 100.0) if v[3] else 0.0\n'
    '                    by_edge.append({"label": k, "n": v[0],\n'
    '                                    "winrate_pct": round(w, 2),\n'
    '                                    "roi_pct": round(r, 2)})\n'
    '                summary = {"n_bets": n_bets, "wins": wins,\n'
    '                           "winrate_pct": round(winrate, 2),\n'
    '                           "total_pnl": round(total_pnl, 2),\n'
    '                           "total_stake": round(total_stake, 2),\n'
    '                           "roi_pct": round(roi_pct, 2),\n'
    '                           "max_drawdown_abs": round(mdd, 2),\n'
    '                           "max_drawdown_pct": round(mdd_pct, 2)}\n'
    '                self._send_json(200, {"summary": summary, "by_edge": by_edge, "n": n_bets})\n'
    '                return\n'
)

anchor = '            if path == "/cycles":\n                rows = []\n'
assert anchor in src, "anchor /cycles not found in fb_server.py"
src = src.replace(anchor, endpoint + anchor, 1)
srv_path.write_text(src, encoding="utf-8")
print("fb_server.py: /backtest endpoint added")
import ast
ast.parse(src)
print("fb_server.py: AST OK")

# ---------- B. fb_index.html: replace tracker section ----------
idx_path = Path("/data/loki/fb_site_deploy/fb_index.html")
html = idx_path.read_text(encoding="utf-8")

# Найдём блок секции tracker (между <section ... id="tracker"> и </section>)
import re
m = re.search(r'  <section class="card" id="tracker">.*?</section>\n', html, re.DOTALL)
assert m, "tracker section not found in fb_index.html"

new_section = '''  <section class="card" id="backtest">
    <h2>Реальный walk-forward бэктест (M052, 2024-25)</h2>
    <div class="grid grid-3" style="margin-bottom:16px;">
      <div class="card" style="background:#0f172a;">
        <div class="kpi-label">Ставок</div>
        <div class="kpi" id="bt-n">…</div>
      </div>
      <div class="card" style="background:#0f172a;">
        <div class="kpi-label">Winrate</div>
        <div class="kpi" id="bt-win">…</div>
      </div>
      <div class="card" style="background:#0f172a;">
        <div class="kpi-label">ROI</div>
        <div class="kpi" id="bt-roi">…</div>
      </div>
      <div class="card" style="background:#0f172a;">
        <div class="kpi-label">Max Drawdown</div>
        <div class="kpi" id="bt-mdd">…</div>
      </div>
    </div>
    <div id="backtest-by-edge"></div>
    <div id="backtest-body" class="empty" style="margin-top:12px;">Загрузка…</div>
    <p style="font-size:12px;color:#94a3b8;margin-top:12px;">
      Walk-forward на исторических closing odds football-data.co.uk.
      Прогноз на матч только по данным ДО него. Negative ROI = базовая модель
      не бьёт букмекера — это ожидаемо (см. баннер выше).
    </p>
  </section>
'''
html = html[:m.start()] + new_section + html[m.end():]

# ---------- C. JS: renderCycles -> renderBacktest ----------
# Заменяем блок функции renderCycles на renderBacktest
m2 = re.search(r'function renderCycles\(data\)\{.*?\n\}\n', html, re.DOTALL)
assert m2, "renderCycles function not found"
new_fn = '''function renderBacktest(data){
  const s = (data && data.summary) || {};
  document.getElementById('bt-n').textContent   = s.n_bets ?? '—';
  document.getElementById('bt-win').textContent = (s.winrate_pct!=null ? (+s.winrate_pct).toFixed(1)+'%' : '—');
  document.getElementById('bt-roi').textContent = (s.roi_pct!=null ? ((+s.roi_pct>0?'+':'')+(+s.roi_pct).toFixed(2)+'%') : '—');
  document.getElementById('bt-mdd').textContent = (s.max_drawdown_pct!=null ? (+s.max_drawdown_pct).toFixed(2)+'%' : '—');
  const roiEl = document.getElementById('bt-roi');
  if (s.roi_pct != null) roiEl.style.color = s.roi_pct > 0 ? '#10b981' : '#ef4444';
  const edgeBox = document.getElementById('backtest-by-edge');
  const bodyBox = document.getElementById('backtest-body');
  if (!data || !data.by_edge || !data.by_edge.length){
    edgeBox.innerHTML = '';
    bodyBox.innerHTML = '<div class="empty">Нет данных бэктеста.</div>';
    return;
  }
  edgeBox.innerHTML = '<table><thead><tr><th>Edge bucket</th><th>n</th><th>Winrate</th><th>ROI</th></tr></thead><tbody>' +
    data.by_edge.map(b => '<tr><td>' + esc(b.label) + '</td><td>' + b.n +
      '</td><td>' + (+b.winrate_pct).toFixed(1) + '%</td>' +
      '<td style="color:' + (b.roi_pct > 0 ? '#10b981' : '#ef4444') + '">' +
      ((+b.roi_pct > 0 ? '+' : '') + (+b.roi_pct).toFixed(2)) + '%</td></tr>').join('') +
    '</tbody></table>';
  const sideNote = s.total_pnl != null
    ? '<div style="font-size:13px;color:#94a3b8;margin-top:8px;">Cum PnL: ' +
      ((+s.total_pnl).toFixed(0)) + ' / stake ' + (+s.total_stake).toFixed(0) + '</div>'
    : '';
  bodyBox.innerHTML = sideNote;
}
'''
html = html[:m2.start()] + new_fn + html[m2.end():]

# Promise.all: /cycles -> /backtest, renderCycles -> renderBacktest
html = html.replace("getJSON('/cycles')", "getJSON('/backtest')")
html = html.replace("renderCycles(cycles)", "renderBacktest(cycles)")

idx_path.write_text(html, encoding="utf-8")
print("fb_index.html: backtest section + renderBacktest OK")
print("DONE")