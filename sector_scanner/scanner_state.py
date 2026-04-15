"""JSON 기반 스캐너 상태 저장."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from .models import ScanResult, model_to_dict

logger = logging.getLogger(__name__)


def _ts_serialize(obj: Any) -> Any:
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


class ScannerStateStore:
    """최근 스캔 결과 및 런타임 상태."""

    def __init__(self, state_file: str) -> None:
        self._path = Path(state_file)

    def save_last_result(self, result: ScanResult) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = model_to_dict(result)
        text = json.dumps(payload, ensure_ascii=False, indent=2, default=_ts_serialize)
        self._path.write_text(text, encoding="utf-8")
        logger.info("ScannerStateStore saved ScanResult to %s", self._path)

    def load_last_result(self) -> dict[str, Any]:
        if not self._path.is_file():
            return {}
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError as e:
            raise RuntimeError(f"ScannerStateStore.load_last_result: cannot read {self._path}: {e}") from e
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"ScannerStateStore.load_last_result: corrupted JSON in {self._path}: {e}") from e
        if not isinstance(data, dict):
            raise RuntimeError(f"ScannerStateStore.load_last_result: expected object at root in {self._path}")
        return data

    def save_runtime_state(self, payload: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        rt_path = self._path.with_name(self._path.stem + "_runtime.json")
        text = json.dumps(payload, ensure_ascii=False, indent=2, default=_ts_serialize)
        rt_path.write_text(text, encoding="utf-8")
        logger.debug("ScannerStateStore saved runtime → %s", rt_path)

    def load_runtime_state(self) -> dict[str, Any]:
        rt_path = self._path.with_name(self._path.stem + "_runtime.json")
        if not rt_path.is_file():
            return {}
        try:
            raw = rt_path.read_text(encoding="utf-8")
        except OSError as e:
            raise RuntimeError(f"ScannerStateStore.load_runtime_state: cannot read {rt_path}: {e}") from e
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"ScannerStateStore.load_runtime_state: corrupted JSON in {rt_path}: {e}") from e
        return data if isinstance(data, dict) else {}
