"""Minimal .kis.yaml import/export for preset strategies."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class KISYamlStrategy:
    strategy_id: str
    category: str | None
    name: str | None
    description: str | None
    params: dict[str, Any]
    risk: dict[str, Any]


def load_kis_yaml(path: Path) -> KISYamlStrategy:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("YAML root must be a mapping")

    meta = raw.get("metadata") or {}
    strat = raw.get("strategy") or {}
    risk = raw.get("risk") or {}

    if not isinstance(strat, dict) or not strat.get("id"):
        raise ValueError("strategy.id is required")

    sid = str(strat["id"]).strip()
    cat = strat.get("category")
    name = meta.get("name")
    desc = meta.get("description")

    params: dict[str, Any] = {}
    # lightweight: allow explicit params map
    if isinstance(strat.get("params"), dict):
        params = dict(strat["params"])
    else:
        # common golden_cross from README: infer fast/slow from indicators list if present
        inds = strat.get("indicators")
        if isinstance(inds, list):
            for ind in inds:
                if not isinstance(ind, dict):
                    continue
                alias = str(ind.get("alias") or "")
                pid = str(ind.get("id") or "")
                p = ind.get("params") or {}
                if pid == "sma" and isinstance(p, dict) and "period" in p:
                    if alias == "sma_fast":
                        params["fast"] = int(p["period"])
                    if alias == "sma_slow":
                        params["slow"] = int(p["period"])

    return KISYamlStrategy(
        strategy_id=sid,
        category=None if cat is None else str(cat),
        name=None if name is None else str(name),
        description=None if desc is None else str(desc),
        params=params,
        risk=dict(risk) if isinstance(risk, dict) else {},
    )


def dump_kis_yaml(strategy_id: str, *, name: str = "", description: str = "", params: dict[str, Any] | None = None) -> str:
    payload = {
        "version": "1.0",
        "metadata": {"name": name, "description": description, "author": "Sauvignon", "tags": []},
        "strategy": {"id": strategy_id, "params": params or {}},
        "risk": {},
    }
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)

