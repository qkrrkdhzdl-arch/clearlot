"""Tamper-evident handoff receipts.

A receipt is not a screenshot: it is a canonical JSON body whose hash chains to
the previous receipt, plus an HMAC under a key the database does not hold. Change
one bottle row and the hash no longer matches; change the whole chain and the
HMAC no longer matches unless you also hold the key.

This is deliberately *not* a blockchain. It is the smallest mechanism that lets a
beneficiation plant answer "was this crate really the one you recorded?" without
trusting the operator's app.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets

KEY_PATH = os.environ.get("CLEARLOT_KEY_PATH", os.path.join("data", "receipt.key"))
GENESIS = "0" * 64


def _key() -> bytes:
    path = KEY_PATH
    if os.path.exists(path):
        return open(path, "rb").read()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    k = secrets.token_bytes(32)
    with open(path, "wb") as f:
        f.write(k)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return k


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(body: dict) -> str:
    return hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


def mac(body_hash: str) -> str:
    return hmac.new(_key(), body_hash.encode("utf-8"), hashlib.sha256).hexdigest()


def build_body(*, receipt_id: str, lot: dict, spec: dict, bottles: list[dict],
               checks: list[dict], acceptances: list[dict]) -> dict:
    """Assemble the exact document a receiver can re-derive from the crate."""
    colours = ["flint", "green", "amber"]
    counted = [b for b in bottles if b["final_color"] in colours]
    composition = {c: sum(1 for b in counted if b["final_color"] == c) for c in colours}
    target = spec["target_color"]
    purity = (composition[target] / len(counted)) if counted else 0.0
    return {
        "schema": "clearlot.receipt/1",
        "receipt_id": receipt_id,
        "issued_at": lot["closed_at"],
        "lot": {"id": lot["id"], "operator": lot["operator"], "created_at": lot["created_at"]},
        "spec": {
            "id": spec["id"], "version": spec["version"], "name": spec["name"],
            "receiver": spec["receiver"], "target_color": target,
            "min_purity": spec["min_purity"], "min_bottles": spec["min_bottles"],
        },
        "result": {
            "bottles_counted": len(counted),
            "bottles_excluded": len(bottles) - len(counted),
            "composition": composition,
            "target_purity": round(purity, 4),
            "meets_spec": bool(purity >= spec["min_purity"] and len(counted) >= spec["min_bottles"]),
        },
        "checks": [{"key": c["item_key"], "answer": c["answer"], "actor": c["actor"], "at": c["created_at"]} for c in checks],
        "bottles": [
            {
                "seq": b["seq"],
                "image_sha256": b["image_sha256"],
                "model": {"version": b["model_version"], "suggested": b["suggested_color"],
                          "confidence": b["confidence"], "margin": b["margin"],
                          "probabilities": json.loads(b["probabilities"])},
                "decision": {"final_color": b["final_color"], "source": b["label_source"],
                             "review_reason": b["review_reason"]},
                "captured_at": b["created_at"],
            }
            for b in sorted(bottles, key=lambda x: x["seq"])
        ],
        "acceptances": acceptances,
        "trail": {
            "note": "Prevailing tier is recorded separately from eligibility.",
            "tiers": ["projected", "recorded", "verified"],
        },
    }


def issue(con, lot_id: str, body: dict) -> dict:
    row = con.execute("SELECT sha256, seq FROM receipts ORDER BY seq DESC LIMIT 1").fetchone()
    prev = row["sha256"] if row else GENESIS
    seq = (row["seq"] + 1) if row else 1
    h = digest(body)
    rid = body["receipt_id"]
    con.execute(
        "INSERT INTO receipts (id, lot_id, seq, prev_hash, body, sha256, hmac, created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (rid, lot_id, seq, prev, canonical(body), h, mac(h), body["issued_at"]),
    )
    return {"id": rid, "sha256": h, "prev_hash": prev, "seq": seq, "hmac": mac(h)}


def verify(con, receipt_id: str) -> dict:
    row = con.execute("SELECT * FROM receipts WHERE id = ?", (receipt_id,)).fetchone()
    if not row:
        return {"found": False}
    stored = row["sha256"]
    recomputed = digest(json.loads(row["body"]))
    prev = con.execute(
        "SELECT sha256 FROM receipts WHERE seq = ?", (row["seq"] - 1,)
    ).fetchone() if row["seq"] > 1 else None
    expected_prev = prev["sha256"] if prev else GENESIS
    return {
        "found": True,
        "hash_matches": recomputed == stored,
        "hmac_matches": hmac.compare_digest(mac(recomputed), row["hmac"]),
        "chain_linked": row["prev_hash"] == expected_prev,
        "seq": row["seq"],
        "sha256": stored,
    }
