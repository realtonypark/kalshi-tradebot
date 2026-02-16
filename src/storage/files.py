from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class FileStore:
    def __init__(self, data_dir: str) -> None:
        self.base = Path(data_dir)
        self.base.mkdir(parents=True, exist_ok=True)
        self.fills_path = self.base / "fills.jsonl"
        self.orders_path = self.base / "orders.jsonl"
        self.positions_path = self.base / "positions.json"
        self.pnl_path = self.base / "pnl_timeseries.csv"
        self.health_path = self.base / "health.json"
        self._init_csv()

    def _init_csv(self) -> None:
        if self.pnl_path.exists():
            return
        with self.pnl_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["ts", "realized_usd", "unrealized_usd", "net_usd", "gross_exposure_usd"])

    def append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        row = dict(payload)
        row.setdefault("ts", utc_now_iso())
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, separators=(",", ":"), sort_keys=True))
            f.write("\n")

    def append_order(self, payload: dict[str, Any]) -> None:
        self.append_jsonl(self.orders_path, payload)

    def append_fill(self, payload: dict[str, Any]) -> None:
        self.append_jsonl(self.fills_path, payload)

    def load_seen_fills(self) -> set[str]:
        seen: set[str] = set()
        if not self.fills_path.exists():
            return seen
        with self.fills_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                order_id = str(row.get("order_id", ""))
                contracts = row.get("contracts")
                if not order_id or contracts is None:
                    continue
                try:
                    count = int(contracts)
                except (TypeError, ValueError):
                    continue
                seen.add(f"{order_id}:{count}")
        return seen

    def write_positions(self, payload: dict[str, Any]) -> None:
        tmp = self.positions_path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(payload, f, sort_keys=True, separators=(",", ":"))
        tmp.replace(self.positions_path)

    def append_pnl(
        self,
        realized_usd: float,
        unrealized_usd: float,
        net_usd: float,
        gross_exposure_usd: float,
    ) -> None:
        with self.pnl_path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    utc_now_iso(),
                    f"{realized_usd:.4f}",
                    f"{unrealized_usd:.4f}",
                    f"{net_usd:.4f}",
                    f"{gross_exposure_usd:.4f}",
                ]
            )

    def write_health(self, payload: dict[str, Any]) -> None:
        tmp = self.health_path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(payload, f, sort_keys=True, separators=(",", ":"))
        tmp.replace(self.health_path)
