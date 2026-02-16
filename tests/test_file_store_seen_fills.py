from __future__ import annotations

import json
from pathlib import Path

from src.storage.files import FileStore


def test_load_seen_fills_reads_existing_entries(tmp_path: Path) -> None:
    store = FileStore(str(tmp_path))
    rows = [
        {"order_id": "oid1", "contracts": 1},
        {"order_id": "oid2", "contracts": 3},
    ]
    with store.fills_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row))
            f.write("\n")

    seen = store.load_seen_fills()
    assert "oid1:1" in seen
    assert "oid2:3" in seen
