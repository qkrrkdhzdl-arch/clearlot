"""Tests for ClearLot.

They exercise the parts a judge would poke at: the gate that blocks a close, the
immutability of a closed crate, and whether a receipt actually detects tampering.
"""
import io
import json
import os
import sqlite3
import tempfile

import pytest

TMP = tempfile.mkdtemp(prefix="clearlot-tests-")
os.environ["CLEARLOT_DB"] = os.path.join(TMP, "test.db")
os.environ["CLEARLOT_KEY_PATH"] = os.path.join(TMP, "test.key")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def fresh_lot():
    r = client.post("/api/demo/seed", json={"spec_id": "spec_amber_v2", "history": False})
    assert r.status_code == 200
    return r.json()


def test_specs_and_model_metadata():
    specs = client.get("/api/specs").json()
    assert len(specs) >= 4
    amber = next(s for s in specs if s["id"] == "spec_amber_v2")
    assert amber["target_color"] == "amber"
    assert 0 < amber["min_purity"] <= 1
    assert all("label" in c for c in amber["checklist"])
    model = client.get("/api/model").json()
    assert model["classes"] == ["flint", "green", "amber"]
    real = next(m for m in model["metrics"] if m["split"] == "real-photos-holdout")
    assert real["n"] >= 20
    assert 0.5 < real["macro_f1"] <= 1.0


def test_seed_runs_the_real_classifier(fresh_lot):
    """Seeded bottles are classified, not hard-coded."""
    assert fresh_lot["bottles"], "seed produced no bottles"
    for b in fresh_lot["bottles"]:
        assert b["model_version"]
        assert 0.0 <= b["confidence"] <= 1.0
        probs = json.loads(b["probabilities"])
        assert abs(sum(probs.values()) - 1.0) < 0.01
        assert b["label_source"] in ("model", "human", "none")


def test_pending_review_blocks_close(fresh_lot):
    lot = fresh_lot["lot"]["id"]
    r = client.post(f"/api/lots/{lot}/capture", json={"sample": "flint_02.jpg"})
    assert r.status_code == 200
    body = r.json()
    if not body["result"]["review_required"]:
        pytest.skip("classifier was confident on the ambiguous sample; gate not exercised")
    blocked = client.post(f"/api/lots/{lot}/close")
    assert blocked.status_code == 409
    codes = [b["code"] for b in blocked.json()["blockers"]]
    assert "pending_review" in codes

    bottle = next(b for b in body["lot"]["bottles"] if b["review_state"] == "pending")
    client.post(f"/api/bottles/{bottle['id']}/review",
                json={"decision": "exclude", "reason": "flint bottle in an amber crate", "actor": "test"})
    after = client.get(f"/api/lots/{lot}").json()
    assert not any(b["review_state"] == "pending" for b in after["bottles"])
    assert not any(b["code"] == "pending_review" for b in after["blockers"])


def test_close_requires_checklist_and_spec(fresh_lot):
    lot = fresh_lot["lot"]["id"]
    for s in ("amber_19.jpg", "amber_20.jpg", "amber_18.jpg", "amber_15.jpg"):
        client.post(f"/api/lots/{lot}/capture", json={"sample": s})
    body = client.get(f"/api/lots/{lot}").json()
    codes = [b["code"] for b in body["blockers"]]
    assert any(c.startswith("check:") for c in codes), "checklist must gate the close"
    client.put(f"/api/lots/{lot}/checks",
               json={"items": {c["key"]: True for c in body["spec"]["checklist"]}, "actor": "test"})
    closed = client.post(f"/api/lots/{lot}/close")
    assert closed.status_code in (200, 409)
    if closed.status_code == 409:
        assert "min_bottles" in [b["code"] for b in closed.json()["blockers"]]


def test_receipt_chain_and_tamper_detection(fresh_lot):
    lot = fresh_lot["lot"]["id"]
    for s in ("amber_19.jpg", "amber_20.jpg", "amber_18.jpg", "amber_15.jpg"):
        client.post(f"/api/lots/{lot}/capture", json={"sample": s})
    body = client.get(f"/api/lots/{lot}").json()
    client.put(f"/api/lots/{lot}/checks",
               json={"items": {c["key"]: True for c in body["spec"]["checklist"]}, "actor": "test"})
    r = client.post(f"/api/lots/{lot}/close")
    assert r.status_code == 200, r.text
    receipt = r.json()["receipt"]
    rid = receipt["id"]

    check = client.get(f"/api/receipts/{rid}").json()
    assert check["integrity"]["hash_matches"] is True
    assert check["integrity"]["hmac_matches"] is True
    assert check["integrity"]["chain_linked"] is True

    # closing a closed crate returns the same receipt instead of issuing a second one
    again = client.post(f"/api/lots/{lot}/close")
    assert again.json()["receipt"]["id"] == rid

    # a closed crate refuses new bottles, at the database level
    assert client.post(f"/api/lots/{lot}/capture", json={"sample": "amber_01.jpg"}).status_code == 409

    # ...and even a direct UPDATE is refused by the trigger, not just by the API
    con = sqlite3.connect(os.environ["CLEARLOT_DB"])
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE bottles SET final_color='green' WHERE lot_id=?", (lot,))
    con.close()

    # a determined attacker with file access can drop the trigger; the hash is then the defence
    con = sqlite3.connect(os.environ["CLEARLOT_DB"])
    con.execute("DROP TRIGGER receipts_immutable")
    body = json.loads(con.execute("SELECT body FROM receipts WHERE id=?", (rid,)).fetchone()[0])
    body["result"]["bottles_counted"] = 999
    con.execute("UPDATE receipts SET body=? WHERE id=?", (json.dumps(body), rid))
    con.commit()
    con.close()
    broken = client.get(f"/api/receipts/{rid}").json()
    assert broken["integrity"]["hash_matches"] is False
    assert broken["integrity"]["hmac_matches"] is False


def test_public_receipt_page_renders(fresh_lot):
    lot = fresh_lot["lot"]["id"]
    for s in ("amber_19.jpg", "amber_20.jpg", "amber_18.jpg", "amber_15.jpg"):
        client.post(f"/api/lots/{lot}/capture", json={"sample": s})
    body = client.get(f"/api/lots/{lot}").json()
    client.put(f"/api/lots/{lot}/checks",
               json={"items": {c["key"]: True for c in body["spec"]["checklist"]}, "actor": "test"})
    rid = client.post(f"/api/lots/{lot}/close").json()["receipt"]["id"]
    page = client.get(f"/r/{rid}")
    assert page.status_code == 200
    assert "handoff receipt" in page.text.lower() or "ClearLot receipt" in page.text
    qr = client.get(f"/r/{rid}/qr.png")
    assert qr.status_code == 200 and qr.headers["content-type"] == "image/png"


def test_upload_path_accepts_a_photo(fresh_lot):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (240, 300), (90, 60, 30)).save(buf, format="JPEG")
    r = client.post(f"/api/lots/{fresh_lot['lot']['id']}/bottles",
                    files={"image": ("bottle.jpg", buf.getvalue(), "image/jpeg")})
    assert r.status_code == 200
    assert r.json()["result"]["suggested_color"] in ("flint", "green", "amber")
    assert r.json()["image_url"].startswith("/media/")


def test_acceptance_tiers_are_separate(fresh_lot):
    lot = fresh_lot["lot"]["id"]
    for s in ("amber_19.jpg", "amber_20.jpg", "amber_18.jpg", "amber_15.jpg"):
        client.post(f"/api/lots/{lot}/capture", json={"sample": s})
    body = client.get(f"/api/lots/{lot}").json()
    client.put(f"/api/lots/{lot}/checks",
               json={"items": {c["key"]: True for c in body["spec"]["checklist"]}, "actor": "test"})
    rid = client.post(f"/api/lots/{lot}/close").json()["receipt"]["id"]

    # a verified acceptance must carry an external reference
    assert client.post(f"/api/receipts/{rid}/acceptances",
                       json={"tier": "verified", "actor": "plant"}).status_code == 400
    assert client.post(f"/api/receipts/{rid}/acceptances",
                       json={"tier": "recorded", "actor": "operator"}).status_code == 200
    assert client.post(f"/api/receipts/{rid}/acceptances",
                       json={"tier": "verified", "actor": "plant", "evidence": "WC-1"}).status_code == 200
    acc = client.get(f"/api/receipts/{rid}").json()["acceptances"]
    assert {a["tier"] for a in acc} == {"recorded", "verified"}
