#!/usr/bin/env python3
"""Patch captive_portal.py splash screen and oled_display.py filtering."""
import re

# ── 1. PATCH SPLASH in captive_portal.py ──

CP_PATH = "/home/pi/timing-sender/captive_portal.py"
with open(CP_PATH) as f:
    code = f.read()

# ── Replace splash CSS ──
old_splash_css = """#splash{
  position:fixed;inset:0;z-index:100;
  display:flex;align-items:center;justify-content:center;
  background:
    radial-gradient(circle at 18% 18%, rgba(255,48,79,0.26), transparent 28%),
    radial-gradient(circle at 82% 20%, rgba(51,87,255,0.28), transparent 26%),
    linear-gradient(160deg,#030611 0%,#071023 38%,#091732 72%,#040814 100%);
  animation:splashOut 0.75s cubic-bezier(.68,-0.2,.32,1) 1.8s forwards;
  overflow:hidden;
}
#splash::before{
  content:'';position:absolute;inset:-20%;
  background:
    repeating-linear-gradient(115deg, transparent 0 42px, rgba(255,255,255,0.035) 42px 44px),
    linear-gradient(90deg, transparent 0%, rgba(255,255,255,0.08) 50%, transparent 100%);
  opacity:0.45;
  transform:perspective(700px) rotateX(73deg) translateY(28%);
  transform-origin:center bottom;
  animation:gridDrift 10s linear infinite;
}
#splash::after{
  content:'';position:absolute;inset:0;
  background:linear-gradient(90deg, transparent 0%, rgba(255,209,102,0.9) 45%, transparent 100%);
  width:45%;filter:blur(10px);transform:translateX(-160%) skewX(-18deg);
  animation:lightSweep 2.1s cubic-bezier(.32,.02,.12,1) 0.55s forwards;
}
.splash-inner{
  position:relative;z-index:1;width:min(96vw,680px);padding:2.15rem 1.35rem 1.6rem;
  text-align:center;animation:splashLift 1s cubic-bezier(.22,.9,.32,1) 0.15s both;
}
.splash-badge{
  display:inline-flex;align-items:center;gap:0.5rem;
  margin-bottom:1rem;padding:0.35rem 0.8rem;border-radius:999px;
  border:1px solid rgba(255,255,255,0.14);background:rgba(255,255,255,0.06);
  color:var(--dim);font-size:0.66rem;font-weight:800;letter-spacing:0.22em;text-transform:uppercase;
  backdrop-filter:blur(10px);
}
.splash-badge::before{
  content:'';width:8px;height:8px;border-radius:50%;
  background:linear-gradient(180deg,var(--gold),#fff0b0);box-shadow:0 0 14px rgba(255,209,102,0.8);
}
.splash-logo-frame{
  position:relative;margin:0 auto 1.25rem;padding:1.05rem 1.05rem;width:min(92vw,540px);
  background:linear-gradient(180deg, rgba(255,255,255,0.18), rgba(255,255,255,0.04));
  border:1px solid rgba(255,255,255,0.14);border-radius:28px;
  box-shadow:0 22px 60px rgba(0,0,0,0.42), inset 0 1px 0 rgba(255,255,255,0.22);
  overflow:hidden;backdrop-filter:blur(14px);
}
.splash-logo-frame::before,
.splash-logo-frame::after{
  content:'';position:absolute;top:12px;bottom:12px;width:3px;border-radius:999px;
  background:linear-gradient(180deg, transparent, var(--accent), transparent);
  opacity:0.85;
}
.splash-logo-frame::before{left:12px;}
.splash-logo-frame::after{right:12px;background:linear-gradient(180deg, transparent, var(--blue), transparent);}
.splash-logo{
  display:block;width:100%;height:auto;object-fit:contain;filter:drop-shadow(0 18px 30px rgba(0,0,0,0.28));
  animation:logoPulse 1.9s ease-in-out 0.35s both;
}
.splash-wordmark{
  margin-top:0.95rem;font-family:var(--display);font-size:clamp(2.7rem,10vw,5rem);
  letter-spacing:0.22em;text-transform:uppercase;line-height:0.95;color:#fff;
  text-shadow:0 12px 30px rgba(0,0,0,0.45);
}
.splash-wordmark strong{
  display:inline-block;color:var(--gold);font-weight:900;
  text-shadow:0 0 26px rgba(255,209,102,0.28);
}
.splash-sub{
  margin-top:0.7rem;color:#d7e0ff;font-size:0.9rem;font-weight:700;
  letter-spacing:0.34em;text-transform:uppercase;opacity:0.9;
}
.splash-track{
  position:relative;display:grid;grid-template-columns:repeat(3,1fr);gap:0.6rem;
  width:min(82vw,360px);margin:1.1rem auto 0;
}
.splash-track span{
  position:relative;height:6px;border-radius:999px;overflow:hidden;
  background:rgba(255,255,255,0.08);box-shadow:inset 0 0 0 1px rgba(255,255,255,0.05);
}
.splash-track span::after{
  content:'';position:absolute;inset:0 auto 0 -65%;width:65%;
  background:linear-gradient(90deg, transparent, var(--gold), #fff, transparent);
  animation:laneDash 1.05s cubic-bezier(.52,.03,.36,.98) infinite;
}
.splash-track span:nth-child(2)::after{animation-delay:0.15s;}
.splash-track span:nth-child(3)::after{animation-delay:0.3s;}
@keyframes splashLift{from{opacity:0;transform:translateY(18px) scale(0.96);}to{opacity:1;transform:translateY(0) scale(1);}}
@keyframes splashOut{to{opacity:0;visibility:hidden;pointer-events:none;}}
@keyframes gridDrift{from{transform:perspective(700px) rotateX(73deg) translateY(28%) translateX(0);}to{transform:perspective(700px) rotateX(73deg) translateY(28%) translateX(-60px);}}
@keyframes lightSweep{to{transform:translateX(280%) skewX(-18deg);}}
@keyframes logoPulse{0%{opacity:0;transform:scale(0.9) translateY(8px);}55%{opacity:1;transform:scale(1.03) translateY(0);}100%{opacity:1;transform:scale(1);}}
@keyframes laneDash{to{transform:translateX(255%);}}"""

new_splash_css = """#splash{
  position:fixed;inset:0;z-index:100;
  display:flex;align-items:center;justify-content:center;flex-direction:column;
  background:#0a0a0a;
  animation:splashOut 0.6s ease 2.4s forwards;
  overflow:hidden;
}
#splash::before{
  content:'';position:absolute;inset:0;
  background:
    radial-gradient(ellipse 80% 50% at 50% 0%, rgba(255,255,255,0.03), transparent),
    radial-gradient(ellipse 60% 40% at 50% 100%, rgba(255,255,255,0.02), transparent);
}
.splash-inner{
  position:relative;z-index:1;width:min(92vw,520px);
  text-align:center;
  animation:splashLift 0.8s cubic-bezier(.16,1,.3,1) 0.1s both;
}
.splash-badge{display:none;}
.splash-logo-frame{
  position:relative;margin:0 auto 2rem;padding:1.2rem;width:min(80vw,420px);
  background:transparent;border:none;border-radius:0;box-shadow:none;
  overflow:visible;backdrop-filter:none;
}
.splash-logo-frame::before,.splash-logo-frame::after{display:none;}
.splash-logo{
  display:block;width:100%;height:auto;object-fit:contain;
  filter:drop-shadow(0 0 40px rgba(255,255,255,0.08));
  animation:logoReveal 1.2s cubic-bezier(.16,1,.3,1) 0.2s both;
}
.splash-wordmark{
  margin-top:0;font-family:var(--sans);font-size:clamp(1.6rem,5vw,2.4rem);
  letter-spacing:0.28em;text-transform:uppercase;line-height:1;color:rgba(255,255,255,0.9);
  font-weight:300;
}
.splash-wordmark strong{
  display:inline;color:#fff;font-weight:700;
  text-shadow:none;
}
.splash-sub{
  margin-top:0.6rem;color:rgba(255,255,255,0.35);font-size:0.7rem;font-weight:500;
  letter-spacing:0.3em;text-transform:uppercase;
}
.splash-track{
  position:relative;display:block;
  width:min(60vw,280px);height:2px;margin:2.5rem auto 0;
  background:rgba(255,255,255,0.06);border-radius:999px;overflow:hidden;
}
.splash-track span{display:none;}
.splash-track span:first-child{
  display:block;position:absolute;inset:0;border-radius:999px;overflow:hidden;
}
.splash-track span:first-child::after{
  content:'';position:absolute;top:0;bottom:0;left:0;width:30%;
  background:linear-gradient(90deg, transparent, rgba(255,255,255,0.7), transparent);
  border-radius:999px;
  animation:barSlide 1.4s cubic-bezier(.4,0,.2,1) infinite;
}
@keyframes splashLift{from{opacity:0;transform:translateY(12px);}to{opacity:1;transform:translateY(0);}}
@keyframes splashOut{to{opacity:0;visibility:hidden;pointer-events:none;}}
@keyframes logoReveal{from{opacity:0;transform:scale(0.95);}to{opacity:1;transform:scale(1);}}
@keyframes barSlide{0%{left:-30%;}100%{left:100%;}}"""

assert old_splash_css in code, "Could not find old splash CSS block"
code = code.replace(old_splash_css, new_splash_css)
print("Splash CSS replaced")

# ── Replace splash HTML ──
old_splash_html = """<div id="splash">
  <div class="splash-inner">
    <div class="splash-badge">Championship Data Stream</div>
    <div class="splash-logo-frame">
      <img src="/assets/nhra-logo" alt="NHRA Championship Drag Racing logo" class="splash-logo">
    </div>
    <div class="splash-wordmark">DARK <strong>MAWSON</strong></div>
    <div class="splash-sub">Race Day Timing Control</div>
    <div class="splash-track"><span></span><span></span><span></span></div>"""

# Be flexible with whitespace
if old_splash_html not in code:
    # Try to find it with a regex
    pattern = r'<div id="splash">.*?<div class="splash-track">.*?</div>'
    match = re.search(pattern, code, re.DOTALL)
    if match:
        old_splash_html = match.group(0)
        print(f"Found splash HTML via regex at pos {match.start()}")
    else:
        print("WARNING: Could not find splash HTML")
        old_splash_html = None

new_splash_html = """<div id="splash">
  <div class="splash-inner">
    <div class="splash-badge"></div>
    <div class="splash-logo-frame">
      <img src="/assets/nhra-logo" alt="NHRA logo" class="splash-logo">
    </div>
    <div class="splash-wordmark">TIMING <strong>TALK</strong></div>
    <div class="splash-sub">Live Race Data</div>
    <div class="splash-track"><span></span><span></span><span></span></div>"""

if old_splash_html:
    code = code.replace(old_splash_html, new_splash_html)
    print("Splash HTML replaced")

# ── Fix responsive splash rules ──
old_responsive = """  .splash-inner{padding-top:1.35rem;}
  .splash-logo-frame{margin-bottom:0.9rem;}
  .splash-wordmark{font-size:clamp(2.15rem,8.8vw,3.5rem);}"""

new_responsive = """  .splash-inner{padding-top:1rem;}
  .splash-logo-frame{margin-bottom:1.2rem;}
  .splash-wordmark{font-size:clamp(1.3rem,4.5vw,2rem);}"""

if old_responsive in code:
    code = code.replace(old_responsive, new_responsive)
    print("Responsive rules updated")

with open(CP_PATH, "w") as f:
    f.write(code)
print("captive_portal.py saved")


# ── 2. PATCH OLED to filter HTTP junk ──

OLED_PATH = "/home/pi/timing-sender/oled_display.py"
with open(OLED_PATH) as f:
    oled_code = f.read()

old_show_data = '''    def show_data(self, line):
        import textwrap
        with self._lock:
            # Wrap long lines so they don't get cut off on the OLED
            wrapped = textwrap.wrap(line, width=21)
            if not wrapped:
                wrapped = [""]
            for w_line in wrapped:
                self._data_lines.append(w_line)'''

new_show_data = '''    # HTTP-like patterns that should never appear on the OLED
    _JUNK_PATTERNS = (
        "User-Agent:", "Accept:", "Host:", "Content-Type:",
        "Content-Length:", "Connection:", "GET /", "POST /",
        "PUT /", "DELETE /", "HEAD /", "OPTIONS /",
        "HTTP/1.", "HTTP/2", "Authorization:",
        "Cache-Control:", "Pragma:", "Referer:",
        "X-Forwarded", "Cookie:", "Origin:",
    )

    def show_data(self, line):
        import textwrap
        # Filter out HTTP headers and request lines
        stripped = line.strip()
        if not stripped:
            return
        for pattern in self._JUNK_PATTERNS:
            if stripped.startswith(pattern):
                return
        with self._lock:
            # Wrap long lines so they don't get cut off on the OLED
            wrapped = textwrap.wrap(line, width=21)
            if not wrapped:
                wrapped = [""]
            for w_line in wrapped:
                self._data_lines.append(w_line)'''

assert old_show_data in oled_code, "Could not find old show_data method"
oled_code = oled_code.replace(old_show_data, new_show_data)
print("OLED show_data patched with HTTP filter")

with open(OLED_PATH, "w") as f:
    f.write(oled_code)
print("oled_display.py saved")

print("\n=== All patches applied ===")
