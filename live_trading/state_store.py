"""JSON file persistence for positions, orders, and daily stats."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from models import LivePosition, OrderRecord

logger = logging.getLogger(__name__)


def _json_default(obj: object) -> str:
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


class JSONStateStore:
    """디렉터리가 없으면 생성하고, 파일이 없으면 빈 구조를 반환한다."""

    def __init__(self, base_path: str) -> None:
        self._root = Path(base_path)
        self._positions_path = self._root / "positions.json"
        self._orders_path = self._root / "orders.json"
        self._daily_path = self._root / "daily_stats.json"
        self._snapshot_path = self._root / "snapshot.json"

    def _ensure_dir(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)

    def save_positions(self, positions: dict[str, LivePosition]) -> None:
        self._ensure_dir()
        payload = {k: v.to_dict() for k, v in positions.items()}
        self._positions_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.debug("save_positions %s symbols", len(payload))

    def load_positions(self) -> dict[str, LivePosition]:
        if not self._positions_path.is_file():
            return {}
        raw = json.loads(self._positions_path.read_text(encoding="utf-8"))
        out: dict[str, LivePosition] = {}
        for k, v in raw.items():
            out[str(k)] = LivePosition.from_dict(v)
        return out

    def save_orders(self, orders: dict[str, OrderRecord]) -> None:
        self._ensure_dir()
        payload = {k: v.to_dict() for k, v in orders.items()}
        self._orders_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def load_orders(self) -> dict[str, OrderRecord]:
        if not self._orders_path.is_file():
            return {}
        raw = json.loads(self._orders_path.read_text(encoding="utf-8"))
        out: dict[str, OrderRecord] = {}
        for k, v in raw.items():
            out[str(k)] = OrderRecord.from_dict(v)
        return out

    def save_daily_stats(self, stats: dict[str, Any]) -> None:
        self._ensure_dir()
        self._daily_path.write_text(json.dumps(stats, indent=2, default=_json_default, ensure_ascii=False), encoding="utf-8")

    def load_daily_stats(self) -> dict[str, Any]:
        if not self._daily_path.is_file():
            return {}
        return json.loads(self._daily_path.read_text(encoding="utf-8"))

    def save_snapshot(self, payload: dict[str, Any]) -> None:
        self._ensure_dir()
        self._snapshot_path.write_text(
            json.dumps(payload, indent=2, default=_json_default, ensure_ascii=False),
            encoding="utf-8",
        )

    def load_snapshot(self) -> dict[str, Any]:
        if not self._snapshot_path.is_file():
            return {}
        return json.loads(self._snapshot_path.read_text(encoding="utf-8"))
