#!/usr/bin/env python3
"""Replace Results tab with spreadsheet-style table layout."""

CP = "/home/pi/timing-sender/captive_portal.py"
with open(CP) as f:
    c = f.read()

# 1. Replace the result timeslip CSS with table CSS
old_result_css = """/* Result timeslip cards */
.result-slip{position:relative;background:linear-gradient(180deg, rgba(13,21,43,0.92), rgba(8,13,28,0.98));border:1px solid rgba(255,255,255,0.1);border-radius:16px;overflow:hidden;margin-bottom:0.75rem;box-shadow:0 8px 24px rgba(0,0,0,0.3);}
.result-slip::before{content:'';position:absolute;left:0;right:0;top:0;height:2px;background:linear-gradient(90deg,var(--accent),var(--gold),var(--blue));}
.result-slip .slip-header{padding:0.55rem 0.7rem 0.5rem;background:linear-gradient(135deg,rgba(255,48,79,0.12),rgba(51,87,255,0.1));border-bottom:1px solid rgba(255,255,255,0.07);}
.result-slip .race-category{font-size:1rem;}
.result-slip .race-round{font-size:0.62rem;}
.result-slip .slip-drivers{display:grid;grid-template-columns:minmax(0,1fr) auto minmax(0,1fr);border-bottom:1px solid rgba(255,255,255,0.05);}
.result-slip .slip-driver{padding:0.45rem 0.55rem 0.4rem;}
.result-slip .slip-driver-name{font-size:0.85rem;}
.result-slip .slip-driver-meta{font-size:0.58rem;}
.result-slip .slip-grid{padding:0.25rem 0.2rem 0.2rem;}
.result-slip .slip-row{display:grid;grid-template-columns:minmax(0,1fr) 60px minmax(0,1fr);align-items:center;padding:0.25rem 0.3rem;border-bottom:1px solid rgba(255,255,255,0.03);}
.result-slip .slip-row:last-child{border-bottom:none;}
.result-slip .slip-cell{font-family:var(--mono);font-size:0.88rem;font-weight:700;text-align:center;}
.result-slip .slip-cell.et-val{font-size:1.05rem;}
.result-slip .slip-cell.speed-sub{font-size:0.62rem;color:var(--dim);font-weight:500;margin-top:0.04rem;}
.result-slip .slip-lbl{text-align:center;font-size:0.55rem;color:var(--dim);text-transform:uppercase;letter-spacing:0.14em;white-space:nowrap;}
.result-slip .slip-row.dial-row{background:rgba(255,209,102,0.06);}
.result-slip .slip-row.et-row{background:linear-gradient(90deg,rgba(255,48,79,0.06),rgba(51,87,255,0.06));}
.result-slip .slip-row.mov-row{background:rgba(255,209,102,0.06);border-top:1px solid var(--gold-dim);}
.result-slip .slip-cell.gold{color:var(--gold);}
body[data-theme='light'] .result-slip{background:linear-gradient(180deg,rgba(255,255,255,0.98),rgba(244,247,252,0.96));border-color:rgba(16,38,77,0.12);}
body[data-theme='light'] .result-slip .slip-header{background:linear-gradient(135deg,rgba(207,47,94,0.06),rgba(36,87,214,0.06));}"""

new_result_css = """/* Results table */
.results-wrap{position:relative;background:linear-gradient(180deg,rgba(13,21,43,0.92),rgba(8,13,28,0.98));border:1px solid rgba(255,255,255,0.1);border-radius:16px;overflow:hidden;box-shadow:0 8px 24px rgba(0,0,0,0.3);}
.results-wrap::before{content:'';position:absolute;left:0;right:0;top:0;height:2px;background:linear-gradient(90deg,var(--accent),var(--gold),var(--blue));}
.results-table{width:100%;border-collapse:collapse;font-size:0.72rem;}
.results-table th{position:sticky;top:0;z-index:2;padding:0.5rem 0.35rem;font-size:0.52rem;font-weight:800;letter-spacing:0.12em;text-transform:uppercase;color:var(--dim);background:linear-gradient(180deg,rgba(12,19,40,0.98),rgba(10,16,34,0.95));border-bottom:1px solid rgba(255,255,255,0.1);white-space:nowrap;text-align:center;}
.results-table th:first-child{text-align:left;padding-left:0.6rem;}
.results-table td{padding:0.38rem 0.35rem;font-family:var(--mono);font-weight:600;text-align:center;border-bottom:1px solid rgba(255,255,255,0.04);white-space:nowrap;}
.results-table td:first-child{text-align:left;padding-left:0.6rem;font-family:var(--sans);font-weight:700;}
.results-table tr:hover{background:rgba(255,255,255,0.03);}
.results-table tr.winner-row{background:rgba(255,209,102,0.05);}
.results-table tr.winner-row:hover{background:rgba(255,209,102,0.08);}
.results-table .win-star{color:var(--gold);font-size:0.7rem;margin-left:0.2rem;}
.results-table .cat-row td{padding:0.45rem 0.6rem;font-family:var(--display);font-size:0.78rem;font-weight:900;text-transform:uppercase;letter-spacing:0.06em;color:var(--dim);background:linear-gradient(90deg,rgba(255,48,79,0.06),rgba(51,87,255,0.06));text-align:left;border-bottom:1px solid rgba(255,255,255,0.08);}
.results-table .cat-row .cat-rnd{font-family:var(--sans);font-size:0.58rem;font-weight:500;letter-spacing:0.08em;margin-left:0.5rem;color:rgba(255,255,255,0.3);}
.results-table .td-name{max-width:100px;overflow:hidden;text-overflow:ellipsis;}
.results-table .td-et{color:var(--text);font-weight:800;font-size:0.82rem;}
.results-table .td-dial{color:var(--gold);}
.results-table .td-class{font-family:var(--sans);font-size:0.55rem;color:var(--dim);font-weight:500;}
.results-scroll{max-height:calc(100dvh - 220px);overflow-y:auto;scrollbar-width:thin;scrollbar-color:rgba(255,255,255,0.1) transparent;}
body[data-theme='light'] .results-wrap{background:linear-gradient(180deg,rgba(255,255,255,0.98),rgba(244,247,252,0.96));border-color:rgba(16,38,77,0.12);}
body[data-theme='light'] .results-table th{background:linear-gradient(180deg,rgba(255,255,255,0.98),rgba(244,247,252,0.96));border-bottom-color:rgba(16,38,77,0.1);}
body[data-theme='light'] .results-table td{border-bottom-color:rgba(16,38,77,0.06);}
body[data-theme='light'] .results-table .cat-row td{background:rgba(36,87,214,0.04);}"""

if old_result_css in c:
    c = c.replace(old_result_css, new_result_css)
    print("[1] Result CSS replaced with table styles")
else:
    print("WARNING: Could not find old result CSS")

# 2. Replace the Results tab HTML
old_results_html = """    <!-- RESULTS TAB -->
    <div id="tab-results" class="tab-panel">
      <div id="results-list">
        <div class="no-results">No completed races yet</div>
      </div>
    </div>"""

new_results_html = """    <!-- RESULTS TAB -->
    <div id="tab-results" class="tab-panel">
      <div id="results-list">
        <div class="no-results">No completed races yet</div>
      </div>
    </div>"""
# HTML stays the same — the JS fills it dynamically

# 3. Replace the renderResults JS function
old_render = """function renderResults(completed) {
  var el = document.getElementById('results-list');
  if (!completed.length) { el.innerHTML = '<div class="no-results">No completed races yet</div>'; return; }
  var html = '';
  for (var i = completed.length - 1; i >= 0; i--) {
    var r = completed[i];
    var lw = r.winner && r.winner.toUpperCase() === 'LEFT';
    var rw = r.winner && r.winner.toUpperCase() === 'RIGHT';
    var hasDial = r.left.dial_in || r.right.dial_in;
    var hasMov = r.left.mov || r.right.mov;
    html += '<div class="result-slip">';
    html += '<div class="slip-header"><div class="race-category">' + esc(r.category || 'RACE') + '</div><div class="race-round">' + esc(r.round || '') + (r.timestamp ? ' \\u00b7 ' + esc(r.timestamp) : '') + '</div></div>';
    html += '<div class="slip-drivers">';
    html += '<div class="slip-driver left"><div class="slip-driver-name">' + esc(r.left.name || '\\u2014') + '</div><div class="slip-driver-meta">';
    if (r.left.car_num) html += '<span class="car-num">#' + esc(r.left.car_num) + '</span>';
    if (r.left.class_name) html += '<span class="class-badge">' + esc(r.left.class_name) + '</span>';
    html += '</div>';
    if (lw) html += '<span class="slip-winner-tag">Winner</span>';
    html += '</div>';
    html += '<div class="slip-vs">VS</div>';
    html += '<div class="slip-driver right"><div class="slip-driver-name">' + esc(r.right.name || '\\u2014') + '</div><div class="slip-driver-meta">';
    if (r.right.class_name) html += '<span class="class-badge">' + esc(r.right.class_name) + '</span>';
    if (r.right.car_num) html += '<span class="car-num">#' + esc(r.right.car_num) + '</span>';
    html += '</div>';
    if (rw) html += '<span class="slip-winner-tag">Winner</span>';
    html += '</div></div>';
    html += '<div class="slip-grid">';
    if (hasDial) html += rsRow('Dial-in', r.left.dial_in, r.right.dial_in, 'dial-row', 'gold');
    html += rsRow('R/T', r.left.rt, r.right.rt, '', '');
    html += rsRow('60 ft', r.left.sixty, r.right.sixty, '', '');
    html += rsRow('330 ft', r.left.three30, r.right.three30, '', '');
    html += rsRow('660 ft', r.left.six60, r.right.six60, '', '');
    html += rsRow('1000 ft', r.left.thousand, r.right.thousand, '', '');
    html += rsRow('E.T.', r.left.et, r.right.et, 'et-row', 'et-val');
    html += rsRow('MPH', r.left.speed, r.right.speed, '', '');
    if (hasMov) html += rsRow('MOV', r.left.mov, r.right.mov, 'mov-row', 'gold');
    html += '</div></div>';
  }
  el.innerHTML = html;
}

function rsRow(label, lVal, rVal, rowCls, cellCls) {
  var cls = 'slip-row' + (rowCls ? ' ' + rowCls : '');
  var ccls = 'slip-cell' + (cellCls ? ' ' + cellCls : '');
  return '<div class="' + cls + '"><div><span class="' + ccls + '">' + esc(lVal || '\\u2014') + '</span></div><div class="slip-lbl">' + label + '</div><div><span class="' + ccls + '">' + esc(rVal || '\\u2014') + '</span></div></div>';
}"""

new_render = """function renderResults(completed) {
  var el = document.getElementById('results-list');
  if (!completed.length) { el.innerHTML = '<div class="no-results">No completed races yet</div>'; return; }
  var html = '<div class="results-wrap"><div class="results-scroll"><table class="results-table">';
  html += '<thead><tr><th>Driver</th><th>Class</th><th>Dial</th><th>R/T</th><th>60\\'</th><th>330\\'</th><th>660\\'</th><th>1000\\'</th><th>E.T.</th><th>MPH</th><th>MOV</th></tr></thead><tbody>';
  var lastCat = '';
  for (var i = completed.length - 1; i >= 0; i--) {
    var r = completed[i];
    var catKey = (r.category || '') + '|' + (r.round || '') + '|' + (r.timestamp || '');
    if (catKey !== lastCat) {
      html += '<tr class="cat-row"><td colspan="11">' + esc(r.category || 'RACE') + '<span class="cat-rnd">' + esc(r.round || '') + (r.timestamp ? ' \\u00b7 ' + esc(r.timestamp) : '') + '</span></td></tr>';
      lastCat = catKey;
    }
    var lw = r.winner && r.winner.toUpperCase() === 'LEFT';
    var rw = r.winner && r.winner.toUpperCase() === 'RIGHT';
    html += rRow(r.left, lw);
    html += rRow(r.right, rw);
  }
  html += '</tbody></table></div></div>';
  el.innerHTML = html;
}

function rRow(d, isWin) {
  var cls = isWin ? ' class="winner-row"' : '';
  var star = isWin ? '<span class="win-star">\\u2605</span>' : '';
  var h = '<tr' + cls + '>';
  h += '<td class="td-name">' + esc(d.name || '\\u2014') + star;
  if (d.car_num) h += ' <span style="color:var(--gold);font-size:0.6rem;">#' + esc(d.car_num) + '</span>';
  h += '</td>';
  h += '<td class="td-class">' + esc(d.class_name || '') + '</td>';
  h += '<td class="td-dial">' + esc(d.dial_in || '') + '</td>';
  h += '<td>' + esc(d.rt || '') + '</td>';
  h += '<td>' + esc(d.sixty || '') + '</td>';
  h += '<td>' + esc(d.three30 || '') + '</td>';
  h += '<td>' + esc(d.six60 || '') + '</td>';
  h += '<td>' + esc(d.thousand || '') + '</td>';
  h += '<td class="td-et">' + esc(d.et || '') + '</td>';
  h += '<td>' + esc(d.speed || '') + '</td>';
  h += '<td class="td-dial">' + esc(d.mov || '') + '</td>';
  h += '</tr>';
  return h;
}"""

if old_render in c:
    c = c.replace(old_render, new_render)
    print("[2] renderResults() replaced with table layout")
else:
    print("WARNING: Could not find renderResults JS")

with open(CP, "w") as f:
    f.write(c)
print("[3] captive_portal.py saved")
print("\n=== Done ===")
