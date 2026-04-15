"""종목코드 ↔ 섹터 매핑."""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class SectorMapper:
    """symbol → {sector_code, sector_name} (추후 마스터/CSV 연동)."""

    def __init__(self, mapping: dict[str, dict[str, str]] | None = None) -> None:
        self._map: dict[str, dict[str, str]] = dict(mapping or {})

    def load_from_dict(self, mapping: dict[str, dict[str, str]]) -> None:
        self._map.update(dict(mapping))

    def load_from_csv(self, path: str) -> None:
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"SectorMapper.load_from_csv: file not found: {p}")
        with p.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                raise ValueError(f"SectorMapper.load_from_csv: missing header row in {p}")
            need = {"symbol", "sector_code", "sector_name"}
            lower = {h.strip().lower(): h for h in reader.fieldnames}
            missing = need - set(lower.keys())
            if missing:
                raise ValueError(f"SectorMapper.load_from_csv: CSV must have columns {need}, missing {missing} in {p}")
            for row in reader:
                sym = str(row[lower["symbol"]]).strip()
                if not sym:
                    continue
                self._map[sym] = {
                    "sector_code": str(row[lower["sector_code"]]).strip(),
                    "sector_name": str(row[lower["sector_name"]]).strip(),
                }
        logger.info("SectorMapper loaded %d rows from %s", len(self._map), p)

    def get_sector(self, symbol: str) -> dict[str, str] | None:
        return self._map.get(str(symbol).strip())

    def attach_sector_info(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """rows 항목에 sector_code / sector_name 주입 (없으면 None)."""
        out: list[dict[str, Any]] = []
        for r in rows:
            item = dict(r)
            sym = str(item.get("symbol", "")).strip()
            info = self.get_sector(sym) if sym else None
            if info:
                item.setdefault("sector_code", info.get("sector_code"))
                item.setdefault("sector_name", info.get("sector_name"))
            else:
                item.setdefault("sector_code", item.get("sector_code"))
                item.setdefault("sector_name", item.get("sector_name"))
            out.append(item)
        return out
