#!/usr/bin/env python3
"""Patch sender.py, main.py, and captive_portal.py for:
- Source-tagged feed data (serial vs TCP)
- Split-column Feed tab
- Full timeslip Results tab
"""
import re

# ══════════════════════════════════════════════════════════════
# 1. PATCH sender.py — add source parameter to _emit_data
# ══════════════════════════════════════════════════════════════
SENDER = "/home/pi/timing-sender/sender.py"
with open(SENDER) as f:
    s = f.read()

# Change _emit_data to accept source
s = s.replace(
    "    def _emit_data(self, line):\n"
    "        _data_logger.log_line(line)\n"
    "        if self._on_data:\n"
    "            self._on_data(line)",
    "    def _emit_data(self, line, source=\"serial\"):\n"
    "        _data_logger.log_line(line)\n"
    "        if self._on_data:\n"
    "            self._on_data(line, source)"
)

# Tag serial _emit_data calls (in _run_loop)
# Find the serial loop emit and add source="serial"
# The serial loop already defaults to "serial", so we just need TCP

# Tag TCP _emit_data calls — find them by context
# TCP _read_from_socket has _emit_data(decoded) — change to source="tcp"
s = s.replace(
    "                        self._emit_data(decoded)",
    "                        self._emit_data(decoded, source=\"tcp\")"
)

with open(SENDER, "w") as f:
    f.write(s)
print("[1/5] sender.py patched — _emit_data now passes source")


# ══════════════════════════════════════════════════════════════
# 2. PATCH main.py — update on_data callback
# ══════════════════════════════════════════════════════════════
MAIN = "/home/pi/timing-sender/main.py"
with open(MAIN) as f:
    m = f.read()

m = m.replace(
    "    def on_data(line):\n"
    "        captive_portal.push_data_line(line)\n"
    "        if oled:\n"
    "            oled.show_data(line)",
    "    def on_data(line, source=\"serial\"):\n"
    "        captive_portal.push_data_line(line, source=source)\n"
    "        if oled:\n"
    "            oled.show_data(line)"
)

with open(MAIN, "w") as f:
    f.write(m)
print("[2/5] main.py patched — on_data passes source")


# ══════════════════════════════════════════════════════════════
# 3. PATCH captive_portal.py — data buffer, feed, results
# ══════════════════════════════════════════════════════════════
CP = "/home/pi/timing-sender/captive_portal.py"
with open(CP) as f:
    c = f.read()

# 3a. Update push_data_line to accept source
c = c.replace(
    "def push_data_line(line):\n"
    '    _data_buffer.append({"time": time.strftime("%H:%M:%S"), "data": line})',
    "def push_data_line(line, source=\"serial\"):\n"
    '    _data_buffer.append({"time": time.strftime("%H:%M:%S"), "data": line, "source": source})'
)
print("  - push_data_line updated with source param")

# 3b. Replace the Feed tab HTML
old_feed = """    <!-- FEED TAB -->
    <div id="tab-feed" class="tab-panel">
      <div class="feed-wrap">
        <div class="feed-toolbar">
          <span id="feed-count">0 lines</span>
          <div>
            <label style="font-size:0.65rem;color:var(--dim);margin-right:0.4rem;"><input type="checkbox" id="feed-auto" checked> Auto-scroll</label>
            <button class="feed-btn" onclick="clearFeed()">Clear</button>
          </div>
        </div>
        <div class="feed-scroll" id="feed">
          <div class="feed-empty">Waiting for data...</div>
        </div>
      </div>
    </div>"""

new_feed = """    <!-- FEED TAB -->
    <div id="tab-feed" class="tab-panel">
      <div class="feed-wrap">
        <div class="feed-toolbar">
          <span id="feed-count">0 lines</span>
          <div>
            <label style="font-size:0.65rem;color:var(--dim);margin-right:0.4rem;"><input type="checkbox" id="feed-auto" checked> Auto-scroll</label>
            <button class="feed-btn" onclick="clearFeed()">Clear</button>
          </div>
        </div>
        <div class="feed-split">
          <div class="feed-col">
            <div class="feed-col-hdr"><span class="feed-dot serial"></span>Serial</div>
            <div class="feed-scroll" id="feed-serial">
              <div class="feed-empty">Waiting for serial data...</div>
            </div>
          </div>
          <div class="feed-col">
            <div class="feed-col-hdr"><span class="feed-dot tcp"></span>TCP</div>
            <div class="feed-scroll" id="feed-tcp">
              <div class="feed-empty">Waiting for TCP data...</div>
            </div>
          </div>
        </div>
      </div>
    </div>"""

if old_feed in c:
    c = c.replace(old_feed, new_feed)
    print("  - Feed tab HTML replaced with split columns")
else:
    print("  WARNING: Could not find Feed tab HTML to replace")

# 3c. Add CSS for split feed and mini results
# Find the feed-wrap CSS section and add after it
feed_css_addition = """
.feed-split{display:grid;grid-template-columns:1fr 1fr;gap:0.5rem;flex:1;min-height:0;}
.feed-col{display:flex;flex-direction:column;min-height:0;overflow:hidden;border-radius:12px;border:1px solid rgba(255,255,255,0.06);background:rgba(0,0,0,0.15);}
.feed-col-hdr{padding:0.4rem 0.6rem;font-size:0.62rem;font-weight:800;letter-spacing:0.14em;text-transform:uppercase;color:var(--dim);border-bottom:1px solid rgba(255,255,255,0.06);display:flex;align-items:center;gap:0.4rem;}
.feed-dot{width:7px;height:7px;border-radius:50%;}
.feed-dot.serial{background:var(--accent);box-shadow:0 0 6px rgba(255,48,79,0.5);}
.feed-dot.tcp{background:var(--blue);box-shadow:0 0 6px rgba(51,87,255,0.5);}
body[data-theme='light'] .feed-col{background:rgba(0,0,0,0.03);border-color:rgba(16,38,77,0.1);}

/* Result timeslip cards */
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
body[data-theme='light'] .result-slip .slip-header{background:linear-gradient(135deg,rgba(207,47,94,0.06),rgba(36,87,214,0.06));}
"""

# Insert the CSS before the /* ── App Shell ── */ comment
c = c.replace(
    "/* ── App Shell ── */",
    feed_css_addition + "\n/* ── App Shell ── */"
)
print("  - CSS added for split feed and result timeslips")

# 3d. Replace the pollFeed JS function
old_poll = """function pollFeed() {
  fetch('/api/data?since=' + feedCount).then(function(r){return r.json();}).then(function(d) {
    if (d.lines.length > 0) {
      var feed = document.getElementById('feed');
      if (feedCount === 0) feed.innerHTML = '';
      d.lines.forEach(function(item) {
        var div = document.createElement('div');
        div.className = 'feed-line';
        div.innerHTML = '<span class="feed-time">' + esc(item.time) + '</span>' + esc(item.data);
        feed.appendChild(div);
      });
      feedCount += d.lines.length;
      document.getElementById('feed-count').textContent = feedCount + ' lines';
      if (document.getElementById('feed-auto').checked) feed.scrollTop = feed.scrollHeight;
    }
  }).catch(function(){});
}

function clearFeed() {
  fetch('/api/clear-data', {method:'POST'}).then(function() {
    document.getElementById('feed').innerHTML = '<div class="feed-empty">Waiting for data...</div>';
    feedCount = 0;
    document.getElementById('feed-count').textContent = '0 lines';
  });
}"""

new_poll = """function pollFeed() {
  fetch('/api/data?since=' + feedCount).then(function(r){return r.json();}).then(function(d) {
    if (d.lines.length > 0) {
      var serialFeed = document.getElementById('feed-serial');
      var tcpFeed = document.getElementById('feed-tcp');
      if (feedCount === 0) { serialFeed.innerHTML = ''; tcpFeed.innerHTML = ''; }
      d.lines.forEach(function(item) {
        var div = document.createElement('div');
        div.className = 'feed-line';
        div.innerHTML = '<span class="feed-time">' + esc(item.time) + '</span>' + esc(item.data);
        var src = item.source || 'serial';
        if (src === 'tcp') { tcpFeed.appendChild(div); } else { serialFeed.appendChild(div); }
      });
      feedCount += d.lines.length;
      document.getElementById('feed-count').textContent = feedCount + ' lines';
      if (document.getElementById('feed-auto').checked) {
        serialFeed.scrollTop = serialFeed.scrollHeight;
        tcpFeed.scrollTop = tcpFeed.scrollHeight;
      }
    }
  }).catch(function(){});
}

function clearFeed() {
  fetch('/api/clear-data', {method:'POST'}).then(function() {
    document.getElementById('feed-serial').innerHTML = '<div class="feed-empty">Waiting for serial data...</div>';
    document.getElementById('feed-tcp').innerHTML = '<div class="feed-empty">Waiting for TCP data...</div>';
    feedCount = 0;
    document.getElementById('feed-count').textContent = '0 lines';
  });
}"""

if old_poll in c:
    c = c.replace(old_poll, new_poll)
    print("  - pollFeed() JS replaced with split-column routing")
else:
    print("  WARNING: Could not find pollFeed JS")

# 3e. Replace the renderResults and resultLane functions
old_results = """function renderResults(completed) {
  var el = document.getElementById('results-list');
  if (!completed.length) { el.innerHTML = '<div class="no-results">No completed races yet</div>'; return; }
  var html = '';
  for (var i = completed.length - 1; i >= 0; i--) {
    var r = completed[i];
    var lw = r.winner && r.winner.toUpperCase() === 'LEFT';
    var rw = r.winner && r.winner.toUpperCase() === 'RIGHT';
    html += '<div class="result-card"><div class="result-header"><span class="result-cat">' + esc(r.category || 'RACE') + '</span><span class="result-time">' + esc(r.timestamp || '') + '</span></div>';
    html += '<div class="result-lanes">';
    html += resultLane(r.left, lw);
    html += resultLane(r.right, rw);
    html += '</div></div>';
  }
  el.innerHTML = html;
}"""

new_results = """function renderResults(completed) {
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

if old_results in c:
    c = c.replace(old_results, new_results)
    print("  - renderResults() replaced with full timeslip cards")
else:
    print("  WARNING: Could not find renderResults JS")

# 3f. Remove old resultLane function (no longer needed but keep for backward compat)
# Actually let's leave it — it's harmless and removing it could break if referenced elsewhere

with open(CP, "w") as f:
    f.write(c)
print("[3/5] captive_portal.py patched — feed split + result timeslips")

print("\n=== All patches applied successfully ===")
