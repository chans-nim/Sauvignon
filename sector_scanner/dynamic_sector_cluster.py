"""수익률 상관 기반 동적 섹터(클러스터) 생성."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class DynamicSectorCluster:
    """
    ``pct_change`` 기반 상관 → 거리(1-|ρ|) → 계층적 클러스터링.

    ``build_sector_groups()`` 결과 키는 ``DYN_0`` 형태.
    """

    def __init__(
        self,
        n_clusters: int = 28,
        min_cluster_size: int = 5,
        max_cluster_size: int = 200,
    ) -> None:
        self.n_clusters = max(2, int(n_clusters))
        self.min_cluster_size = max(1, int(min_cluster_size))
        self.max_cluster_size = max(self.min_cluster_size, int(max_cluster_size))
        self._groups: dict[str, list[str]] = {}
        self._fitted = False

    def fit(self, price_history: pd.DataFrame) -> None:
        """
        Parameters
        ----------
        price_history
            인덱스=일자, 열=심볼 종가(또는 수준). 내부에서 ``pct_change`` 적용.
        """
        if price_history.empty or price_history.shape[1] < 2:
            self._groups = {}
            self._fitted = False
            return

        px = price_history.sort_index().astype(float)
        rets = px.pct_change().iloc[1:].dropna(how="all", axis=0)
        rets = rets.dropna(axis=1, thresh=max(5, int(len(rets) * 0.5)))
        cols = list(rets.columns)
        if len(cols) < 2 or len(rets) < 10:
            self._groups = {f"DYN_0": cols}
            self._fitted = True
            return

        X = rets.values
        # correlation between columns (symbols)
        with np.errstate(invalid="ignore"):
            C = np.corrcoef(X.T)
        C = np.nan_to_num(C, nan=0.0)
        np.fill_diagonal(C, 1.0)
        dist = 1.0 - np.abs(C)
        np.fill_diagonal(dist, 0.0)

        try:
            from sklearn.cluster import AgglomerativeClustering

            n_cl = min(self.n_clusters, len(cols))
            model = AgglomerativeClustering(
                n_clusters=n_cl,
                metric="precomputed",
                linkage="average",
            )
            labels = model.fit_predict(dist)
        except Exception as e:
            logger.warning("DynamicSectorCluster.fit sklearn failed: %s — fallback single cluster", e)
            self._groups = {f"DYN_0": cols}
            self._fitted = True
            return

        groups: dict[str, list[str]] = {}
        for lab, sym in zip(labels, cols):
            k = f"DYN_{int(lab)}"
            groups.setdefault(k, []).append(str(sym))
        self._groups = groups
        self._fitted = True

    def build_sector_groups(self) -> dict[str, list[str]]:
        if not self._fitted:
            return {}
        return {k: list(v) for k, v in self._groups.items()}

    def filter_cluster_sizes(self, groups: dict[str, list[str]] | None = None) -> dict[str, list[str]]:
        """너무 작거나 큰 클러스터 제거 후 재라벨."""
        src = groups if groups is not None else self.build_sector_groups()
        out: dict[str, list[str]] = {}
        idx = 0
        for _k, syms in src.items():
            n = len(syms)
            if n < self.min_cluster_size or n > self.max_cluster_size:
                continue
            out[f"DYN_{idx}"] = list(syms)
            idx += 1
        if not out and src:
            # 모두 잘렸으면 가장 큰 클러스터 하나만 유지
            best = max(src.values(), key=len)
            out["DYN_0"] = list(best)
        self._groups = out
        return out
