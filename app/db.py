"""SQLite persistence for ClearLot.

The audit trail is the product, so the schema is the contract: every bottle row
keeps the model's output *and* the human decision separately, every lot is
closed exactly once, and a closed lot rejects further mutation at the database
level (triggers), not only in application code.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid

DB_PATH = os.environ.get("CLEARLOT_DB", os.path.join("data", "clearlot.db"))

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS specs (
    id           TEXT PRIMARY KEY,
    version      INTEGER NOT NULL,
    name         TEXT NOT NULL,
    receiver     TEXT NOT NULL,
    target_color TEXT NOT NULL,
    min_purity   REAL NOT NULL,
    min_bottles  INTEGER NOT NULL,
    checklist    TEXT NOT NULL,          -- JSON [{key,label,required}]
    note         TEXT,
    active       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS lots (
    id         TEXT PRIMARY KEY,
    spec_id    TEXT NOT NULL REFERENCES specs(id),
    operator   TEXT NOT NULL,
    status     TEXT NOT NULL CHECK (status IN ('open','closed')),
    created_at TEXT NOT NULL,
    closed_at  TEXT
);

CREATE TABLE IF NOT EXISTS bottles (
    id             TEXT PRIMARY KEY,
    lot_id         TEXT NOT NULL REFERENCES lots(id),
    seq            INTEGER NOT NULL,
    created_at     TEXT NOT NULL,
    image_sha256   TEXT NOT NULL,
    image_path     TEXT NOT NULL,
    model_version  TEXT NOT NULL,
    suggested_color TEXT,
    confidence     REAL,
    margin         REAL,
    probabilities  TEXT NOT NULL,
    features       TEXT NOT NULL,
    review_state   TEXT NOT NULL CHECK (review_state IN ('auto','pending','human')),
    review_reason  TEXT,
    final_color    TEXT,
    label_source   TEXT NOT NULL CHECK (label_source IN ('model','human','none')),
    note           TEXT,
    UNIQUE (lot_id, seq)
);

CREATE TABLE IF NOT EXISTS reviews (
    id         TEXT PRIMARY KEY,
    bottle_id  TEXT NOT NULL UNIQUE REFERENCES bottles(id),
    decision   TEXT NOT NULL,           -- flint|green|amber|exclude
    reason     TEXT,
    actor      TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS checks (
    lot_id     TEXT NOT NULL REFERENCES lots(id),
    item_key   TEXT NOT NULL,
    answer     TEXT NOT NULL,
    actor      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (lot_id, item_key)
);

CREATE TABLE IF NOT EXISTS receipts (
    id         TEXT PRIMARY KEY,
    lot_id     TEXT NOT NULL UNIQUE REFERENCES lots(id),
    seq        INTEGER NOT NULL,
    prev_hash  TEXT NOT NULL,
    body       TEXT NOT NULL,
    sha256     TEXT NOT NULL,
    hmac       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS acceptances (
    id         TEXT PRIMARY KEY,
    receipt_id TEXT NOT NULL REFERENCES receipts(id),
    tier       TEXT NOT NULL CHECK (tier IN ('projected','recorded','verified')),
    actor      TEXT NOT NULL,
    evidence   TEXT,
    created_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS bottles_immutable_on_closed
BEFORE INSERT ON bottles
WHEN (SELECT status FROM lots WHERE id = NEW.lot_id) = 'closed'
BEGIN SELECT RAISE(ABORT, 'lot is closed'); END;

CREATE TRIGGER IF NOT EXISTS bottles_update_blocked_on_closed
BEFORE UPDATE ON bottles
WHEN (SELECT status FROM lots WHERE id = OLD.lot_id) = 'closed'
BEGIN SELECT RAISE(ABORT, 'lot is closed'); END;

CREATE TRIGGER IF NOT EXISTS reviews_blocked_on_closed
BEFORE INSERT ON reviews
WHEN (SELECT l.status FROM lots l JOIN bottles b ON b.lot_id = l.id WHERE b.id = NEW.bottle_id) = 'closed'
BEGIN SELECT RAISE(ABORT, 'lot is closed'); END;

CREATE TRIGGER IF NOT EXISTS receipts_immutable
BEFORE UPDATE ON receipts
BEGIN SELECT RAISE(ABORT, 'receipts are append-only'); END;

CREATE TRIGGER IF NOT EXISTS receipts_no_delete
BEFORE DELETE ON receipts
BEGIN SELECT RAISE(ABORT, 'receipts are append-only'); END;
"""


def connect(path: str | None = None) -> sqlite3.Connection:
    p = path or os.environ.get("CLEARLOT_DB", DB_PATH)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    con = sqlite3.connect(p, timeout=15, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    seed_specs(con)  # idempotent: keeps receiver specs in step with the code
    return con


def reset_database(path: str | None = None) -> None:
    """Wipe the whole station.

    Receipts are append-only, so a demo reset cannot delete them one row at a
    time — and it should not be able to. Wiping the station is a coarser, more
    visible action than dropping a receipt: the ledger file goes.
    """
    p = path or os.environ.get("CLEARLOT_DB", DB_PATH)
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(p + suffix)
        except FileNotFoundError:
            pass


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# --------------------------------------------------------------------- seed data
SPECS = [
    {
        "id": "spec_flint_v3",
        "version": 3,
        "name": "Flint crate, beneficiation grade",
        "receiver": "Busan Glass Co-op · cullet line 2",
        "target_color": "flint",
        "min_purity": 0.90,
        "min_bottles": 20,
        "checklist": [
            {"key": "ceramic_removed", "label": "No ceramics, stoneware or heat-resistant glass in the crate", "required": True},
            {"key": "caps_removed", "label": "Caps, corks and pourers removed", "required": True},
            {"key": "dry", "label": "Bottles empty and drained (no liquid, no ice)", "required": True},
            {"key": "bags_free", "label": "No plastic bags or liners left in the crate", "required": True},
        ],
        "note": "Line 2 runs flint first every morning; a 3-mix crate is diverted to aggregate and priced as such.",
    },
    {
        "id": "spec_amber_v2",
        "version": 2,
        "name": "Amber crate, beneficiation grade",
        "receiver": "Busan Glass Co-op · cullet line 2",
        "target_color": "amber",
        "min_purity": 0.85,
        "min_bottles": 16,
        "checklist": [
            {"key": "ceramic_removed", "label": "No ceramics, stoneware or heat-resistant glass in the crate", "required": True},
            {"key": "caps_removed", "label": "Caps, corks and pourers removed", "required": True},
            {"key": "dry", "label": "Bottles empty and drained (no liquid, no ice)", "required": True},
            {"key": "bags_free", "label": "No plastic bags or liners left in the crate", "required": True},
        ],
        "note": "Amber tolerates fewer strays than flint because the furnace spec fixes the final tint.",
    },
    {
        "id": "spec_green_v2",
        "version": 2,
        "name": "Green crate, beneficiation grade",
        "receiver": "Busan Glass Co-op · cullet line 1",
        "target_color": "green",
        "min_purity": 0.85,
        "min_bottles": 18,
        "checklist": [
            {"key": "ceramic_removed", "label": "No ceramics, stoneware or heat-resistant glass in the crate", "required": True},
            {"key": "caps_removed", "label": "Caps, corks and pourers removed", "required": True},
            {"key": "dry", "label": "Bottles empty and drained (no liquid, no ice)", "required": True},
            {"key": "bags_free", "label": "No plastic bags or liners left in the crate", "required": True},
        ],
        "note": "Most soju bottles in Korea are green; this is the crate the night trade fills first.",
    },
    {
        "id": "spec_artisan_v1",
        "version": 1,
        "name": "Artisan batch, intact amber bottles",
        "receiver": "Yeongdo craft workshop · kiln co-op",
        "target_color": "amber",
        "min_purity": 0.95,
        "min_bottles": 12,
        "checklist": [
            {"key": "ceramic_removed", "label": "No ceramics or heat-resistant glass in the batch", "required": True},
            {"key": "intact", "label": "Bottles intact — no chips, cracks or cut tops", "required": True},
            {"key": "labels_off", "label": "Labels and glue removed", "required": True},
            {"key": "dry", "label": "Bottles clean and dry", "required": True},
        ],
        "note": "The workshop pays per intact bottle, not per kilo: the same bottle is worth more kept whole than melted.",
    },
]


def seed_specs(con: sqlite3.Connection) -> None:
    for s in SPECS:
        con.execute(
            "INSERT OR REPLACE INTO specs (id, version, name, receiver, target_color, min_purity, min_bottles, checklist, note, active)"
            " VALUES (?,?,?,?,?,?,?,?,?,1)",
            (s["id"], s["version"], s["name"], s["receiver"], s["target_color"],
             s["min_purity"], s["min_bottles"], json.dumps(s["checklist"]), s["note"]),
        )
