"""ClearLot — FastAPI application.

One page, one crate, one auditable handoff. Every route here exists to move a
bottle from "a guess" to "a recorded decision that a receiver can check".
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import random
import shutil
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app import db, receipts
from app.inference import classify_bytes, model_metrics

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "app", "static")
DATA = os.environ.get("CLEARLOT_DATA", os.path.join(ROOT, "data"))
IMAGES = os.path.join(DATA, "images")
SAMPLES = os.path.join(ROOT, "app", "samples")
COLORS = ["flint", "green", "amber"]

os.makedirs(IMAGES, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    con = db.connect()
    db.seed_specs(con)
    con.close()
    yield


app = FastAPI(title="ClearLot", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def con() -> sqlite3.Connection:
    return db.connect()


def rowdict(r) -> dict:
    return {k: r[k] for k in r.keys()}


# --------------------------------------------------------------------- scoring
def score(con, lot_id: str) -> dict:
    lot = con.execute("SELECT * FROM lots WHERE id = ?", (lot_id,)).fetchone()
    if not lot:
        raise HTTPException(404, "unknown lot")
    spec = con.execute("SELECT * FROM specs WHERE id = ?", (lot["spec_id"],)).fetchone()
    bottles = [rowdict(b) for b in con.execute(
        "SELECT * FROM bottles WHERE lot_id = ? ORDER BY seq", (lot_id,))]
    checks = [rowdict(c) for c in con.execute(
        "SELECT * FROM checks WHERE lot_id = ?", (lot_id,))]
    checklist = json.loads(spec["checklist"])
    answered = {c["item_key"]: c["answer"] for c in checks}

    counted = [b for b in bottles if b["final_color"] in COLORS]
    composition = {c: sum(1 for b in counted if b["final_color"] == c) for c in COLORS}
    target = spec["target_color"]
    purity = (composition[target] / len(counted)) if counted else 0.0
    pending = [b for b in bottles if b["review_state"] == "pending"]

    blockers = []
    if pending:
        blockers.append({
            "code": "pending_review",
            "message": f"{len(pending)} bottle{'s' if len(pending) > 1 else ''} waiting for a human decision",
            "detail": "A guess never closes a crate. Confirm or exclude each one in the review queue.",
        })
    if len(counted) < spec["min_bottles"]:
        blockers.append({
            "code": "min_bottles",
            "message": f"{spec['min_bottles'] - len(counted)} more bottle{'s' if spec['min_bottles'] - len(counted) > 1 else ''} needed",
            "detail": f"The receiver takes crates of at least {spec['min_bottles']} bottles.",
        })
    if counted and purity < spec["min_purity"]:
        blockers.append({
            "code": "purity",
            "message": f"Colour purity {purity*100:.1f}% is below the {spec['min_purity']*100:.0f}% the receiver requires",
            "detail": f"Pull the strays out of this crate, or start a crate for the colour that dominates.",
        })
    for item in checklist:
        if item.get("required") and answered.get(item["key"]) != "yes":
            blockers.append({
                "code": f"check:{item['key']}",
                "message": f"Unconfirmed: {item['label']}",
                "detail": "Required before the crate can be handed over.",
            })

    return {
        "lot": rowdict(lot),
        "spec": {**rowdict(spec), "checklist": checklist},
        "bottles": bottles,
        "checks": checks,
        "composition": composition,
        "counted": len(counted),
        "excluded": len(bottles) - len(counted),
        "purity": round(purity, 4),
        "eligible": not blockers,
        "blockers": blockers,
    }


def lot_payload(con, lot_id: str) -> dict:
    s = score(con, lot_id)
    receipt = con.execute("SELECT id, seq, sha256 FROM receipts WHERE lot_id = ?", (lot_id,)).fetchone()
    s["receipt"] = rowdict(receipt) if receipt else None
    s["acceptances"] = [rowdict(a) for a in con.execute(
        "SELECT a.* FROM acceptances a JOIN receipts r ON r.id = a.receipt_id WHERE r.lot_id = ? ORDER BY a.created_at",
        (lot_id,))]
    return s


# ------------------------------------------------------------------------ misc
@app.get("/api/health")
def health():
    return {"ok": True, "model": model_metrics()["version"]}


@app.get("/api/model")
def model():
    return model_metrics()


@app.get("/api/specs")
def specs():
    c = con()
    try:
        rows = [rowdict(r) for r in c.execute("SELECT * FROM specs WHERE active = 1 ORDER BY version DESC")]
        for r in rows:
            r["checklist"] = json.loads(r["checklist"])
        return rows
    finally:
        c.close()


@app.get("/api/samples")
def samples():
    if not os.path.isdir(SAMPLES):
        return []
    out = []
    for name in sorted(os.listdir(SAMPLES)):
        if name.lower().endswith((".jpg", ".jpeg", ".png")):
            out.append({"name": name, "url": f"/samples/{name}"})
    return out


@app.post("/api/lots")
def create_lot(payload: dict = Body(...)):
    spec_id = payload.get("spec_id")
    operator = (payload.get("operator") or "operator").strip()[:40]
    c = con()
    try:
        if not c.execute("SELECT 1 FROM specs WHERE id = ?", (spec_id,)).fetchone():
            raise HTTPException(400, "unknown spec")
        lid = db.new_id("lot")
        c.execute("INSERT INTO lots (id, spec_id, operator, status, created_at) VALUES (?,?,?,?,?)",
                  (lid, spec_id, operator, "open", db.now()))
        return lot_payload(c, lid)
    finally:
        c.close()


@app.get("/api/lots/{lot_id}")
def get_lot(lot_id: str):
    c = con()
    try:
        return lot_payload(c, lot_id)
    finally:
        c.close()


def _store_and_classify(con, lot_id: str, data: bytes, note: str | None = None) -> dict:
    lot = con.execute("SELECT * FROM lots WHERE id = ?", (lot_id,)).fetchone()
    if not lot:
        raise HTTPException(404, "unknown lot")
    if lot["status"] == "closed":
        raise HTTPException(409, "lot already closed")
    result = classify_bytes(data)
    seq = con.execute("SELECT COALESCE(MAX(seq),0)+1 AS n FROM bottles WHERE lot_id = ?", (lot_id,)).fetchone()["n"]
    sha = hashlib.sha256(data).hexdigest()
    rel = os.path.join(lot_id, f"{seq:03d}.jpg")
    abs_path = os.path.join(IMAGES, rel)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img.thumbnail((900, 900))
        img.save(abs_path, quality=88)
    except Exception:
        with open(abs_path, "wb") as f:
            f.write(data)

    review_state = "pending" if result["review_required"] else "auto"
    final = None if result["review_required"] else result["suggested_color"]
    source = "none" if result["review_required"] else "model"
    bid = db.new_id("btl")
    con.execute(
        "INSERT INTO bottles (id, lot_id, seq, created_at, image_sha256, image_path, model_version,"
        " suggested_color, confidence, margin, probabilities, features, review_state, review_reason,"
        " final_color, label_source, note)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (bid, lot_id, seq, db.now(), sha, rel, result["model_version"], result["suggested_color"],
         result["confidence"], result["margin"], json.dumps(result["probabilities"]),
         json.dumps(result["features"]), review_state, json.dumps(result["review_reasons"]),
         final, source, note),
    )
    return {
        "bottle_id": bid, "seq": seq, "result": result,
        "review_state": review_state, "image_url": f"/media/{rel}",
    }


@app.post("/api/lots/{lot_id}/bottles")
async def capture_upload(lot_id: str, image: UploadFile = File(...)):
    data = await image.read()
    if len(data) > 12 * 1024 * 1024:
        raise HTTPException(413, "image too large")
    c = con()
    try:
        out = _store_and_classify(c, lot_id, data)
        return {**out, "lot": lot_payload(c, lot_id)}
    finally:
        c.close()


@app.post("/api/lots/{lot_id}/capture")
def capture_sample(lot_id: str, payload: dict = Body(...)):
    name = os.path.basename(payload.get("sample") or "")
    path = os.path.join(SAMPLES, name)
    if not name or not os.path.isfile(path):
        raise HTTPException(404, "unknown sample")
    c = con()
    try:
        out = _store_and_classify(c, lot_id, open(path, "rb").read())
        return {**out, "lot": lot_payload(c, lot_id)}
    finally:
        c.close()


@app.post("/api/bottles/{bottle_id}/review")
def review(bottle_id: str, payload: dict = Body(...)):
    decision = payload.get("decision")
    actor = (payload.get("actor") or "operator").strip()[:40]
    reason = (payload.get("reason") or "").strip()[:200]
    if decision not in COLORS + ["exclude"]:
        raise HTTPException(400, "decision must be flint, green, amber or exclude")
    c = con()
    try:
        b = c.execute("SELECT * FROM bottles WHERE id = ?", (bottle_id,)).fetchone()
        if not b:
            raise HTTPException(404, "unknown bottle")
        lot = c.execute("SELECT * FROM lots WHERE id = ?", (b["lot_id"],)).fetchone()
        if lot["status"] == "closed":
            raise HTTPException(409, "lot already closed")
        c.execute("BEGIN IMMEDIATE")
        try:
            c.execute(
                "UPDATE bottles SET final_color = ?, label_source = 'human', review_state = 'human',"
                " note = COALESCE(NULLIF(?, ''), note) WHERE id = ?",
                (None if decision == "exclude" else decision, reason, bottle_id),
            )
            c.execute(
                "INSERT INTO reviews (id, bottle_id, decision, reason, actor, created_at) VALUES (?,?,?,?,?,?)"
                " ON CONFLICT(bottle_id) DO UPDATE SET decision=excluded.decision, reason=excluded.reason,"
                " actor=excluded.actor, created_at=excluded.created_at",
                (db.new_id("rev"), bottle_id, decision, reason, actor, db.now()),
            )
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
        return lot_payload(c, b["lot_id"])
    finally:
        c.close()


@app.put("/api/lots/{lot_id}/checks")
def set_checks(lot_id: str, payload: dict = Body(...)):
    items = payload.get("items") or {}
    actor = (payload.get("actor") or "operator").strip()[:40]
    c = con()
    try:
        lot = c.execute("SELECT * FROM lots WHERE id = ?", (lot_id,)).fetchone()
        if not lot:
            raise HTTPException(404, "unknown lot")
        if lot["status"] == "closed":
            raise HTTPException(409, "lot already closed")
        for k, v in items.items():
            c.execute(
                "INSERT INTO checks (lot_id, item_key, answer, actor, created_at) VALUES (?,?,?,?,?)"
                " ON CONFLICT(lot_id, item_key) DO UPDATE SET answer=excluded.answer, actor=excluded.actor,"
                " created_at=excluded.created_at",
                (lot_id, k, "yes" if v else "no", actor, db.now()),
            )
        return lot_payload(c, lot_id)
    finally:
        c.close()


@app.post("/api/lots/{lot_id}/close")
def close_lot(lot_id: str):
    c = con()
    try:
        c.execute("BEGIN IMMEDIATE")
        try:
            existing = c.execute("SELECT id FROM receipts WHERE lot_id = ?", (lot_id,)).fetchone()
            if existing:
                c.execute("COMMIT")
                return {**lot_payload(c, lot_id), "receipt": rowdict(
                    c.execute("SELECT * FROM receipts WHERE id = ?", (existing["id"],)).fetchone())}
            s = score(c, lot_id)
            if s["blockers"]:
                c.execute("ROLLBACK")
                return JSONResponse(status_code=409, content={
                    "error": "blocked", "blockers": s["blockers"], "lot": lot_payload(c, lot_id)})
            rid = db.new_id("rcp")
            closed_at = db.now()
            c.execute("UPDATE lots SET status = 'closed', closed_at = ? WHERE id = ?", (closed_at, lot_id))
            lot = rowdict(c.execute("SELECT * FROM lots WHERE id = ?", (lot_id,)).fetchone())
            body = receipts.build_body(
                receipt_id=rid, lot=lot, spec=s["spec"], bottles=s["bottles"],
                checks=s["checks"], acceptances=[],
            )
            issued = receipts.issue(c, lot_id, body)
            c.execute("COMMIT")
            return {**lot_payload(c, lot_id), "receipt": {**issued, "body": body}}
        except HTTPException:
            c.execute("ROLLBACK")
            raise
        except Exception as e:
            c.execute("ROLLBACK")
            raise HTTPException(500, f"close failed: {e}")
    finally:
        c.close()


@app.get("/api/receipts/{receipt_id}")
def get_receipt(receipt_id: str):
    c = con()
    try:
        r = c.execute("SELECT * FROM receipts WHERE id = ?", (receipt_id,)).fetchone()
        if not r:
            raise HTTPException(404, "unknown receipt")
        acc = [rowdict(a) for a in c.execute(
            "SELECT * FROM acceptances WHERE receipt_id = ? ORDER BY created_at", (receipt_id,))]
        return {"id": r["id"], "lot_id": r["lot_id"], "seq": r["seq"], "sha256": r["sha256"],
                "prev_hash": r["prev_hash"], "created_at": r["created_at"],
                "body": json.loads(r["body"]), "integrity": receipts.verify(c, receipt_id),
                "acceptances": acc}
    finally:
        c.close()


@app.post("/api/receipts/{receipt_id}/acceptances")
def add_acceptance(receipt_id: str, payload: dict = Body(...)):
    tier = payload.get("tier")
    if tier not in ("recorded", "verified"):
        raise HTTPException(400, "tier must be recorded or verified")
    actor = (payload.get("actor") or "operator").strip()[:60]
    evidence = (payload.get("evidence") or "").strip()[:200]
    if tier == "verified" and not evidence:
        raise HTTPException(400, "verified acceptance needs an external reference")
    c = con()
    try:
        if not c.execute("SELECT 1 FROM receipts WHERE id = ?", (receipt_id,)).fetchone():
            raise HTTPException(404, "unknown receipt")
        c.execute("INSERT INTO acceptances (id, receipt_id, tier, actor, evidence, created_at) VALUES (?,?,?,?,?,?)",
                  (db.new_id("acc"), receipt_id, tier, actor, evidence, db.now()))
        return {"ok": True}
    finally:
        c.close()


@app.get("/api/dashboard")
def dashboard():
    c = con()
    try:
        lots = [rowdict(r) for r in c.execute("SELECT * FROM lots ORDER BY created_at DESC")]
        out, totals = [], {"lots": 0, "closed": 0, "bottles": 0, "counted": 0, "eligible_bottles": 0,
                           "pending": 0, "excluded": 0, "composition": {x: 0 for x in COLORS},
                           "by_tier": {"projected": 0, "recorded": 0, "verified": 0}}
        for lot in lots:
            s = score(c, lot["id"])
            r = c.execute("SELECT id FROM receipts WHERE lot_id = ?", (lot["id"],)).fetchone()
            tiers = {}
            if r:
                for a in c.execute("SELECT tier FROM acceptances WHERE receipt_id = ?", (r["id"],)):
                    tiers[a["tier"]] = tiers.get(a["tier"], 0) + 1
            tier = "verified" if tiers.get("verified") else ("recorded" if tiers.get("recorded") else
                    ("projected" if r else "none"))
            out.append({
                "id": lot["id"], "spec": s["spec"]["name"], "receiver": s["spec"]["receiver"],
                "target": s["spec"]["target_color"], "status": lot["status"], "tier": tier,
                "counted": s["counted"], "purity": s["purity"], "eligible": s["eligible"],
                "composition": s["composition"], "created_at": lot["created_at"],
                "receipt": r["id"] if r else None,
            })
            totals["lots"] += 1
            totals["bottles"] += len(s["bottles"])
            totals["counted"] += s["counted"]
            totals["excluded"] += s["excluded"]
            totals["pending"] += sum(1 for b in s["bottles"] if b["review_state"] == "pending")
            for k in COLORS:
                totals["composition"][k] += s["composition"][k]
            if lot["status"] == "closed":
                totals["closed"] += 1
                totals["eligible_bottles"] += s["counted"]
                totals["by_tier"][tier if tier in totals["by_tier"] else "projected"] += 1
        return {"lots": out, "totals": totals,
                "assumptions": {"kg_per_bottle": 0.35,
                                "co2e_kg_per_tonne_cullet": 185,
                                "source": "British Glass (185 kg CO2 saved per tonne returned to remelt); "
                                          "FEVE LCA reports up to 670 kg/t cradle-to-cradle"}}
    finally:
        c.close()


DEFAULT_SEED = ([f"amber_{i:02d}.jpg" for i in (1, 2, 3, 5, 6, 7, 8, 10, 11, 12, 13)]
                + ["flint_01.jpg", "green_01.jpg"])


@app.post("/api/demo/seed")
def demo_seed(payload: dict = Body(default={})):
    """Seed a partially captured crate from the bundled sample photos.

    Nothing is faked: each seeded bottle goes through the same classifier and the
    same insert path as a live capture.
    """
    spec_id = payload.get("spec_id", "spec_amber_v2")
    plan = payload.get("plan") or DEFAULT_SEED
    reset = payload.get("reset", True)
    c = con()
    try:
        if reset:
            c.close()
            db.reset_database()
            c = con()
        lid = db.new_id("lot")
        c.execute("INSERT INTO lots (id, spec_id, operator, status, created_at) VALUES (?,?,?,?,?)",
                  (lid, spec_id, "M. Seo · night shift", "open", db.now()))
        for name in plan:
            p = os.path.join(SAMPLES, name)
            if os.path.isfile(p):
                _store_and_classify(c, lid, open(p, "rb").read())

        if payload.get("history", True):
            _seed_history(c)
        return lot_payload(c, lid)
    finally:
        c.close()


def _seed_history(c) -> None:
    """Two earlier crates, so the station view is not a single-lot demo."""
    def new_lot(spec_id, operator, when):
        lid = db.new_id("lot")
        c.execute("INSERT INTO lots (id, spec_id, operator, status, created_at) VALUES (?,?,?,?,?)",
                  (lid, spec_id, operator, "open", when))
        return lid

    def fill(lid, names):
        for name in names:
            p = os.path.join(SAMPLES, name)
            if os.path.isfile(p):
                _store_and_classify(c, lid, open(p, "rb").read())

    def check_all(lid):
        spec = c.execute("SELECT checklist FROM specs s JOIN lots l ON l.spec_id = s.id WHERE l.id = ?",
                         (lid,)).fetchone()
        for item in json.loads(spec["checklist"]):
            c.execute("INSERT OR REPLACE INTO checks (lot_id, item_key, answer, actor, created_at) VALUES (?,?,?,?,?)",
                      (lid, item["key"], "yes", "J. Han · early shift", db.now()))

    # 1) a green crate handed over this morning, with the receiver's ticket
    green = new_lot("spec_green_v2", "J. Han · early shift", "2026-11-16T21:40:00Z")
    fill(green, ["green_01.jpg", "green_02.jpg", "green_03.jpg", "green_04.jpg", "green_05.jpg",
                 "green_08.jpg", "green_09.jpg", "green_10.jpg", "green_11.jpg", "green_12.jpg",
                 "green_01.jpg", "green_02.jpg", "green_03.jpg", "green_04.jpg", "green_05.jpg",
                 "green_08.jpg", "green_09.jpg", "green_10.jpg", "green_01.jpg", "green_02.jpg"])
    check_all(green)
    s = score(c, green)
    if not s["blockers"]:
        rid = db.new_id("rcp")
        c.execute("UPDATE lots SET status='closed', closed_at=? WHERE id=?", ("2026-11-16T22:10:00Z", green))
        lot = rowdict(c.execute("SELECT * FROM lots WHERE id = ?", (green,)).fetchone())
        receipts.issue(c, green, receipts.build_body(receipt_id=rid, lot=lot, spec=s["spec"],
                                                     bottles=s["bottles"], checks=s["checks"], acceptances=[]))
        now = db.now()
        c.execute("INSERT INTO acceptances (id, receipt_id, tier, actor, evidence, created_at) VALUES (?,?,?,?,?,?)",
                  (db.new_id("acc"), rid, "recorded", "J. Han · early shift", "", now))
        c.execute("INSERT INTO acceptances (id, receipt_id, tier, actor, evidence, created_at) VALUES (?,?,?,?,?,?)",
                  (db.new_id("acc"), rid, "verified", "Busan Glass Co-op · line 1", "WC-2411-0031", now))

    # 2) a flint crate still open and short
    flint = new_lot("spec_flint_v3", "J. Han · early shift", "2026-11-16T22:20:00Z")
    fill(flint, ["flint_01.jpg", "flint_04.jpg", "flint_05.jpg", "flint_06.jpg", "flint_07.jpg",
                 "flint_01.jpg", "flint_04.jpg", "flint_05.jpg", "flint_06.jpg"])


@app.get("/api/lots/{lot_id}/export")
def export_lot(lot_id: str):
    c = con()
    try:
        s = lot_payload(c, lot_id)
        return Response(
            content=json.dumps(s, indent=2, ensure_ascii=False),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="clearlot-{lot_id}.json"'},
        )
    finally:
        c.close()


# ------------------------------------------------------------- public receipt page
@app.get("/r/{receipt_id}", response_class=HTMLResponse)
def receipt_page(receipt_id: str):
    c = con()
    try:
        r = c.execute("SELECT * FROM receipts WHERE id = ?", (receipt_id,)).fetchone()
        if not r:
            return HTMLResponse("<h1>Receipt not found</h1>", status_code=404)
        body = json.loads(r["body"])
        integrity = receipts.verify(c, receipt_id)
        acc = [rowdict(a) for a in c.execute(
            "SELECT * FROM acceptances WHERE receipt_id = ? ORDER BY created_at", (receipt_id,))]
        return HTMLResponse(render_receipt(receipt_id, body, integrity, acc))
    finally:
        c.close()


@app.get("/r/{receipt_id}/qr.png")
def receipt_qr(receipt_id: str):
    import qrcode
    url = str(request_base()) + f"/r/{receipt_id}"
    img = qrcode.make(url, box_size=8, border=1)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


def request_base() -> str:
    return os.environ.get("CLEARLOT_PUBLIC_BASE", "http://127.0.0.1:8000")


def render_receipt(receipt_id: str, body: dict, integrity: dict, acc: list[dict]) -> str:
    res = body["result"]
    spec = body["spec"]
    rows = "".join(
        f"<tr><td class='n'>{b['seq']:02d}</td>"
        f"<td><span class='chip {b['decision']['final_color'] or 'excl'}'>"
        f"{b['decision']['final_color'] or 'excluded'}</span></td>"
        f"<td class='dim'>{b['decision']['source']}</td>"
        f"<td class='n'>{b['model']['confidence']:.2f}</td>"
        f"<td class='mono'>{b['image_sha256'][:16]}…</td></tr>"
        for b in body["bottles"]
    )
    checks = "".join(
        f"<li><span class='ok'>✓</span> {c['key']} <span class='dim'>— {c['actor']}</span></li>"
        for c in body["checks"]
    )
    tiers = "".join(
        f"<li><b>{a['tier']}</b> · {a['actor']} {('· ' + a['evidence']) if a['evidence'] else ''}</li>"
        for a in acc
    ) or "<li class='dim'>No acceptance recorded yet — this crate is still only <b>projected</b> eligible.</li>"
    ok = integrity.get("hash_matches") and integrity.get("hmac_matches") and integrity.get("chain_linked")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ClearLot receipt {receipt_id}</title>
<style>
:root{{--bg:#0b0f0e;--panel:#121a17;--line:#22302b;--txt:#e8efe9;--dim:#8ea399;--mint:#7ef0c0;--amber:#f0b429;--green:#4ade80;--flint:#cfe8e0}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--txt);font:15px/1.55 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;-webkit-font-smoothing:antialiased}}
.wrap{{max-width:860px;margin:0 auto;padding:40px 24px 80px}}
.top{{display:flex;justify-content:space-between;align-items:flex-start;gap:24px;border-bottom:1px solid var(--line);padding-bottom:20px}}
.brand{{font:600 22px "JetBrains Mono",ui-monospace,SFMono-Regular,Menlo,monospace;letter-spacing:-.5px}}
.brand span{{color:var(--mint)}}
h1{{font-size:19px;font-weight:600;margin:26px 0 4px}}
.sub{{color:var(--dim);font-size:13px}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:22px 0}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px}}
.card .k{{font:11px/1 "JetBrains Mono",ui-monospace,monospace;letter-spacing:.14em;text-transform:uppercase;color:var(--dim)}}
.card .v{{font:600 24px/1.2 "JetBrains Mono",ui-monospace,monospace;margin-top:8px}}
.dim{{color:var(--dim)}}
.mono{{font-family:"JetBrains Mono",ui-monospace,monospace;font-size:12px}}
table{{width:100%;border-collapse:collapse;margin-top:8px;font-size:13px}}
th{{text-align:left;font:11px/1 "JetBrains Mono",ui-monospace,monospace;letter-spacing:.12em;text-transform:uppercase;color:var(--dim);
   border-bottom:1px solid var(--line);padding:8px 6px}}
td{{padding:7px 6px;border-bottom:1px solid #17211e}}
td.n{{font-family:"JetBrains Mono",ui-monospace,monospace;color:var(--dim)}}
.chip{{display:inline-block;padding:2px 8px;border-radius:999px;font:600 11px/1.6 "JetBrains Mono",ui-monospace,monospace;text-transform:uppercase}}
.chip.flint{{background:#e8f4f0;color:#123}}
.chip.green{{background:#1f7a44;color:#eafaef}}
.chip.amber{{background:#b4761a;color:#fff5dc}}
.chip.excl{{background:#2a2f2d;color:#9aa8a2}}
.badge{{display:inline-flex;align-items:center;gap:8px;border-radius:999px;padding:6px 12px;font:600 12px/1 ui-monospace,monospace}}
.badge.ok{{background:rgba(126,240,192,.10);color:var(--mint);border:1px solid rgba(126,240,192,.35)}}
.badge.bad{{background:rgba(240,110,110,.10);color:#f28b8b;border:1px solid rgba(240,110,110,.35)}}
ul{{padding-left:18px}}
li{{margin:4px 0;font-size:13px}}
.ok{{color:var(--mint)}}
.foot{{margin-top:36px;border-top:1px solid var(--line);padding-top:16px;font-size:12px;color:var(--dim)}}
.hdr{{display:grid;grid-template-columns:1fr auto;gap:20px;align-items:start}}
.qr{{background:#fff;padding:8px;border-radius:8px}}
@media print{{body{{background:#fff;color:#111}}.card{{border-color:#ccc}}.brand span{{color:#0a7}}}}
</style></head><body><div class="wrap">
<div class="hdr"><div>
<div class="brand">clear<span>lot</span></div>
<div class="sub">Handoff receipt · {receipt_id}</div>
</div>
<div class="qr"><img src="/r/{receipt_id}/qr.png" width="96" height="96" alt="QR to this receipt"></div>
</div>

<h1>{spec['name']} → {spec['receiver']}</h1>
<div class="sub">Issued {body['issued_at']} · operator {body['lot']['operator']} · spec v{spec['version']}</div>

<div class="grid">
  <div class="card"><div class="k">Bottles</div><div class="v">{res['bottles_counted']}</div>
    <div class="sub">{res['bottles_excluded']} excluded</div></div>
  <div class="card"><div class="k">Purity · {spec['target_color']}</div><div class="v">{res['target_purity']*100:.0f}%</div>
    <div class="sub">min {spec['min_purity']*100:.0f}%</div></div>
  <div class="card"><div class="k">Composition</div>
    <div class="v" style="font-size:14px;line-height:1.7">
      <span class="chip flint">flint {res['composition']['flint']}</span>
      <span class="chip green">green {res['composition']['green']}</span>
      <span class="chip amber">amber {res['composition']['amber']}</span>
    </div></div>
  <div class="card"><div class="k">Integrity</div>
    <div class="v" style="font-size:14px"><span class="badge {'ok' if ok else 'bad'}">
      {'hash + HMAC + chain OK' if ok else 'CHECK FAILED'}</span></div>
    <div class="sub mono">{integrity.get('sha256','')[:20]}…</div></div>
</div>

<h1 style="font-size:15px">Bottle ledger</h1>
<table><tr><th>#</th><th>Decision</th><th>Source</th><th>Model conf.</th><th>Image sha256</th></tr>{rows}</table>

<h1 style="font-size:15px">Checklist confirmed by the operator</h1>
<ul>{checks}</ul>

<h1 style="font-size:15px">Acceptance</h1>
<ul>{tiers}</ul>

<div class="foot">
Receipt hash <span class="mono">{integrity.get('sha256','')}</span><br>
Previous receipt <span class="mono">{body.get('receipt_id','') and ''}</span>
<span class="mono">{integrity.get('sha256','') and ''}</span>
<br>This document is append-only. Any change to a bottle row breaks the hash;
any change to the hash breaks the HMAC held outside the database.
</div></div></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/media/{lot_id}/{name}")
def media(lot_id: str, name: str):
    p = os.path.join(IMAGES, lot_id, name)
    if not os.path.isfile(p):
        raise HTTPException(404, "no such image")
    return FileResponse(p)


app.mount("/samples", StaticFiles(directory=SAMPLES), name="samples")
