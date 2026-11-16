#!/usr/bin/env python3
"""Record the ClearLot demo video with a real browser driving the real app.

Nothing is staged: the server runs, the classifier runs on sample photographs,
the review gate blocks a real close, and the receipt is the one the app issues.

    python3 demo/record_demo.py            # writes demo/out/demo.webm (+ mp4 if ffmpeg is found)

Environment: needs Playwright's Chromium. On a normal desktop
`python3 -m playwright install chromium` is enough.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "demo", "out")
PORT = int(os.environ.get("CLEARLOT_DEMO_PORT", "8012"))
BASE = f"http://127.0.0.1:{PORT}"

CAPTION_JS = """
() => {
  const css = `
  .cap-wrap{position:fixed;left:0;right:0;bottom:0;z-index:9999;pointer-events:none;
    display:flex;justify-content:center;padding:0 0 26px;opacity:0;transition:opacity .35s ease}
  .cap-wrap.on{opacity:1}
  .cap{background:rgba(6,10,9,.92);border:1px solid #2b3d36;border-radius:12px;padding:13px 20px;
    max-width:900px;font:500 15.5px/1.45 Inter,system-ui,sans-serif;color:#e9f1ec;letter-spacing:-.005em;
    box-shadow:0 18px 50px rgba(0,0,0,.55);backdrop-filter:blur(8px)}
  .cap b{color:#7ef0c0;font-weight:600}
  .cap i{color:#dd9a2b;font-style:normal;font-weight:600}
  .cur{position:fixed;width:18px;height:18px;border-radius:50%;z-index:10000;pointer-events:none;
    background:rgba(126,240,192,.22);border:1.5px solid rgba(126,240,192,.9);
    box-shadow:0 0 0 4px rgba(126,240,192,.10), 0 2px 8px rgba(0,0,0,.5);
    transform:translate(-50%,-50%);transition:width .12s,height .12s,background .12s}
  .cur.click{width:13px;height:13px;background:rgba(126,240,192,.55)}
  .card-wrap{position:fixed;inset:0;z-index:9998;background:#090d0c;display:flex;flex-direction:column;
    justify-content:center;padding:0 110px;opacity:0;transition:opacity .45s ease;pointer-events:none}
  .card-wrap.on{opacity:1}
  .card-wrap .eyebrow{font:600 11px/1 "JetBrains Mono",ui-monospace,monospace;letter-spacing:.22em;
    text-transform:uppercase;color:#5f726b;margin-bottom:22px}
  .card-wrap h1{font:600 54px/1.08 Inter,system-ui,sans-serif;letter-spacing:-.035em;margin:0 0 20px;max-width:17ch;color:#e9f1ec}
  .card-wrap h1 em{font-style:normal;color:#7ef0c0}
  .card-wrap p{font:400 19px/1.55 Inter,sans-serif;color:#8ea399;max-width:44ch;margin:0}
  .card-wrap p b{color:#e9f1ec;font-weight:500}
  .card-wrap .rule{width:52px;height:2px;background:#7ef0c0;margin:0 0 26px;opacity:.8}
  `;
  const st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);
  const cap = document.createElement('div'); cap.className = 'cap-wrap';
  cap.innerHTML = '<div class="cap"></div>'; document.body.appendChild(cap);
  const cur = document.createElement('div'); cur.className = 'cur'; document.body.appendChild(cur);
  const card = document.createElement('div'); card.className = 'card-wrap'; document.body.appendChild(card);
  window.__cap = (html) => { cap.querySelector('.cap').innerHTML = html; cap.classList.add('on'); };
  window.__capOff = () => cap.classList.remove('on');
  window.__card = (html) => { card.innerHTML = html; card.classList.add('on'); };
  window.__cardOff = () => card.classList.remove('on');
  window.__cur = (v) => { cur.style.opacity = v ? '1' : '0'; };
  window.addEventListener('mousemove', (e) => {
    cur.style.left = e.clientX + 'px'; cur.style.top = e.clientY + 'px';
  });
  window.addEventListener('mousedown', () => cur.classList.add('click'));
  window.addEventListener('mouseup', () => cur.classList.remove('click'));
}
"""

REEL = "amber_19.jpg,amber_20.jpg,flint_02.jpg,amber_18.jpg"

CARD_INTRO = """<div class="eyebrow">Arbiter Hacks V1 · Rethinking Waste</div>
<h1>One bottle is worth more <em>kept in the loop</em> than melted into a road.</h1>
<div class="rule"></div>
<p>Glass can be remelted forever — <b>if the crate is one colour</b>.</p>"""

CARD_PROBLEM = """<div class="eyebrow">the problem</div>
<h1>Mixed-colour cullet is not bottle glass.</h1>
<div class="rule"></div>
<p>Container makers are limited in how much colour-mixed cullet they can use at all.
A crate sorted by colour becomes new bottles; the same glass, mixed, becomes road aggregate
or landfill cover. The sorting decision is made <b>at the sink, at 2am, by a person holding one bottle</b>.</p>"""

CARD_END = """<div class="eyebrow">what this is</div>
<h1>A crate that can prove it is <em>ready</em>.</h1>
<div class="rule"></div>
<p>Colour gate model · audit trail per bottle · hash-chained handoff receipt.<br>
Built solo in Python and vanilla JS. Model and limitations are in the README.</p>"""


def wait_for_server(timeout=60):
    for _ in range(timeout * 4):
        try:
            urllib.request.urlopen(BASE + "/api/health", timeout=1)
            return True
        except Exception:
            time.sleep(0.25)
    return False


def post(path, data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(BASE + path, data=body, headers={"Content-Type": "application/json"},
                                 method="POST" if data is not None else "GET")
    return json.loads(urllib.request.urlopen(req, timeout=60).read().decode())


async def record(lot_id: str):
    from playwright.async_api import async_playwright

    chrome = os.environ.get("CLEARLOT_CHROME")
    launch = {"args": ["--no-sandbox", "--force-device-scale-factor=1"]}
    if chrome:
        launch["executable_path"] = chrome

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(**launch)
        ctx = await browser.new_context(
            viewport={"width": 1440, "height": 810},
            device_scale_factor=1,
            record_video_dir=OUT,
            record_video_size={"width": 1440, "height": 810},
            reduced_motion="reduce",
        )
        page = await ctx.new_page()
        await page.goto(f"{BASE}/?lot={lot_id}&reel=" + REEL)
        await page.evaluate(CAPTION_JS)
        await page.wait_for_timeout(400)

        async def card(html, hold=4.2):
            await page.evaluate("() => window.__cur(false)")
            await page.evaluate("(h) => window.__card(h)", html)
            await page.wait_for_timeout(int(hold * 1000))
            await page.evaluate("() => window.__cardOff()")
            await page.evaluate("() => window.__cur(true)")
            await page.wait_for_timeout(320)

        async def cap(html):
            await page.evaluate("(h) => window.__cap(h)", html)

        async def capoff():
            await page.evaluate("() => window.__capOff()")

        async def beat(seconds):
            await page.wait_for_timeout(int(seconds * 1000))

        async def move_to(selector, steps=14):
            box = await page.locator(selector).first.bounding_box()
            if not box:
                return
            await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=steps)
            await page.wait_for_timeout(180)

        async def click(selector, pause=0.9):
            await move_to(selector)
            await page.locator(selector).first.click()
            await beat(pause)

        # ---------------------------------------------------------------- 1
        await card(CARD_INTRO, 4.6)
        await card(CARD_PROBLEM, 6.4)

        # ---------------------------------------------------------------- 2
        await cap("The crate comes off the bar floor unsorted: <b>13 bottles</b>, mostly amber, two strays. "
                  "<b>84.6%</b> colour purity — under the receiver's 85% floor, and three bottles short of the minimum.")
        await page.mouse.move(700, 420, steps=12)
        await beat(6.2)
        await move_to(".grade", 10)
        await beat(2.4)
        await cap("Every capture is one bottle in the guide. The model reads colour only, "
                  "and it is allowed to say <i>I don't know</i>.")
        await beat(4.4)

        # ------------------------------------------------------- hero capture
        await click("#btn-capture", 1.2)
        await cap("One capture: <b>amber, 88%</b>. Above the confidence gate, so it counts automatically.")
        await beat(5.0)
        await click("#btn-capture", 1.1)
        await cap("Another amber. Purity moves with every row — <b>this is the crate being graded</b>, not a dashboard about grading.")
        await beat(5.2)
        await click("#btn-capture", 1.2)
        await cap("Third bottle: <b>amber 53%, green 36%</b>. The top-two margin is below 45%, so the app refuses to tag it "
                  "and parks it in the review queue — a guess never closes a crate.")
        await beat(6.0)

        # ------------------------------------------------------------ review
        await cap("The review queue. One bottle, both possibilities shown, no auto-decision.")
        await click('.nav a[data-view="review"]', 1.2)
        await beat(4.0)
        await cap("It is a flint bottle dropped into an amber crate. <b>Rehome</b> takes it out of this crate "
                  "and keeps the reason in the receipt.")
        await move_to("#review-cards .choose button.ex", 12)
        await beat(2.4)
        await click("#review-cards .choose button.ex", 1.4)
        await beat(2.6)

        # ------------------------------------------------- end the crate (last bottle)
        await cap("Back at intake: 15 counted, still one bottle short of the receiver's minimum of 16.")
        await click('.nav a[data-view="intake"]', 1.0)
        await beat(2.0)
        await click("#btn-capture", 1.2)
        await cap("<b>16 of 16.</b> Purity 87.5% — over the floor, and every bottle has a decision with a source.")
        await beat(4.4)

        # --------------------------------------------------------- checklist
        await cap("Four handover checks stay human: caps off, dry, no plastic, <b>no ceramics or pyrex</b>. "
                  "The model is never asked about those.")
        checks = page.locator(".chk input")
        n = await checks.count()
        for i in range(n):
            await move_to(f".chk:nth-child({i + 1}) input", 8)
            await page.locator(".chk input").nth(i).check()
            await beat(0.85)
        await beat(1.6)
        await cap("Gate clear. <b>Eligible.</b>")
        await beat(2.6)

        # ------------------------------------------------------------- close
        await click("#btn-close", 1.8)
        await cap("Closing issues a receipt: every bottle's image hash, what the model said, what the person decided, "
                  "and the checklist, in one canonical document.")
        await beat(6.4)
        await cap("The hash chains to the previous receipt and is signed with a key the database does not hold, "
                  "so a plant can verify the crate without trusting this app.")
        await move_to(".integrity", 10)
        await beat(5.0)

        # ---------------------------------------------------- public page
        rid = await page.evaluate("() => document.querySelector('.qrbox img').src.split('/r/')[1].split('/')[0]")
        await page.goto(f"{BASE}/r/{rid}")
        await page.evaluate(CAPTION_JS)
        await cap("Scanning the QR opens the same document as a page the receiver keeps. "
                  "<b>Projected eligibility</b> is stated as projected — acceptance is recorded separately.")
        await beat(7.0)
        await page.evaluate("() => window.scrollTo({top: 520, behavior: 'instant'})")
        await beat(4.6)

        # -------------------------------------------------------- dashboard
        await page.goto(f"{BASE}/?lot={lot_id}&reel=" + REEL)
        await page.evaluate(CAPTION_JS)
        await beat(0.9)
        await click('.nav a[data-view="ledger"]', 1.2)
        await cap("Three evidence tiers, kept apart on purpose: <b>projected</b> is this app's grading, "
                  "<b>recorded</b> is the operator's word, <b>verified</b> needs a receiver reference.")
        await beat(8.4)
        await cap("<b>One crate, one colour, one receipt.</b> Same flow at 40 bars a night.")
        await beat(3.4)
        await capoff()

        await card(CARD_END, 5.2)
        await page.evaluate("() => window.__cardOff()")
        await beat(0.6)

        video = page.video
        await ctx.close()
        path = await video.path()
        target = os.path.join(OUT, "demo_raw.webm")
        if os.path.exists(target):
            os.remove(target)
        shutil.move(path, target)
        await browser.close()
        return target


def transcode(src: str) -> str | None:
    ff = os.environ.get("CLEARLOT_FFMPEG") or shutil.which("ffmpeg")
    if not ff:
        try:  # imageio-ffmpeg ships a static build with libx264
            import imageio_ffmpeg
            ff = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            ff = None
    if not ff:
        print("ffmpeg not found - webm left as is")
        return None
    dst = os.path.join(OUT, "demo.mp4")
    cmd = [ff, "-y", "-i", src, "-vf", "fps=30,scale=1440:810",
           "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", dst]
    print(" ".join(cmd))
    subprocess.run(cmd, check=True)
    return dst


def main():
    os.makedirs(OUT, exist_ok=True)
    for f in ("data/demo.db", "data/demo.db-wal", "data/demo.db-shm", "data/demo.key"):
        p = os.path.join(ROOT, f)
        if os.path.exists(p):
            os.remove(p)
    env = dict(os.environ,
               CLEARLOT_DB=os.path.join(ROOT, "data", "demo.db"),
               CLEARLOT_KEY_PATH=os.path.join(ROOT, "data", "demo.key"),
               CLEARLOT_PUBLIC_BASE=BASE)
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                            "--port", str(PORT), "--log-level", "warning"], cwd=ROOT, env=env)
    try:
        if not wait_for_server():
            raise SystemExit("server did not start")
        seeded = post("/api/demo/seed", {"spec_id": "spec_amber_v2"})
        lot = seeded["lot"]["id"]
        print("seeded", lot, seeded["counted"], seeded["purity"])
        raw = asyncio.run(record(lot))
        print("recorded", raw)
        mp4 = transcode(raw)
        if mp4:
            print("video", mp4)
    finally:
        srv.send_signal(signal.SIGINT)
        try:
            srv.wait(timeout=10)
        except subprocess.TimeoutExpired:
            srv.kill()


if __name__ == "__main__":
    main()
