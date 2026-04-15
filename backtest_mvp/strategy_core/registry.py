"""
Backtest 전략 레지스트리.

Strategy Builder의 `@register` 패턴을 참고하되, 본 프로젝트는 백테스트 신호(DataFrame) 생성에 초점을 둔다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from strategy_core.base_strategy import BaseBacktestStrategy

_REGISTRY: dict[str, type[BaseBacktestStrategy]] = {}
_META: dict[str, dict[str, Any]] = {}


@dataclass(frozen=True)
class StrategyInfo:
    id: str
    category: str
    name: str
    description: str


def register(strategy_id: str, category: str) -> Callable[[type[BaseBacktestStrategy]], type[BaseBacktestStrategy]]:
    """전략 클래스를 레지스트리에 등록한다."""

    def deco(cls: type[BaseBacktestStrategy]) -> type[BaseBacktestStrategy]:
        sid = str(strategy_id).strip()
        if not sid:
            raise ValueError("strategy_id must be non-empty")
        _REGISTRY[sid] = cls
        _META[sid] = {
            "id": sid,
            "category": str(category),
            "name": getattr(cls, "name", sid),
            "description": getattr(cls, "description", ""),
        }
        return cls

    return deco


class StrategyRegistry:
    """전략 조회/생성."""

    @staticmethod
    def has(strategy_id: str) -> bool:
        return str(strategy_id) in _REGISTRY

    @staticmethod
    def ids() -> list[str]:
        return sorted(_REGISTRY.keys())

    @staticmethod
    def info(strategy_id: str) -> StrategyInfo:
        d = _META.get(str(strategy_id))
        if not d:
            raise KeyError(f"Strategy not registered: {strategy_id}")
        return StrategyInfo(**d)  # type: ignore[arg-type]

    @staticmethod
    def list_infos() -> list[StrategyInfo]:
        return [StrategyRegistry.info(sid) for sid in StrategyRegistry.ids()]

    @staticmethod
    def create(strategy_id: str, **params: Any) -> BaseBacktestStrategy:
        cls = _REGISTRY.get(str(strategy_id))
        if cls is None:
            raise KeyError(f"Strategy not registered: {strategy_id}")
        return cls(**params)

