from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path


def default_manifest() -> dict:
    return {
        "schema_version": 2,
        "latest_current": None,
        "latest_full": None,
        "latest_delta": {
            "tag": None,
            "release_type": "delta",
            "file_name": None,
            "sha256": None,
            "row_count": None,
            "created_at": None,
            "min_date": None,
            "max_date": None,
            "assets": [],
        },
        "release_type": None,
        "row_count": None,
        "created_at": None,
        "history": {
            "release_tags": [],
            "delta_tags": [],
        },
        "policy": {
            "rerun_window_trading_days": 10,
            "format": "parquet",
            "hash": "sha256",
        },
    }


def ensure_manifest_structure(manifest: dict | None) -> dict:
    base = default_manifest()
    if not manifest:
        return base

    out = deepcopy(base)
    out.update({k: v for k, v in manifest.items() if k not in {"history", "policy", "latest_delta"}})

    out["history"] = {
        "release_tags": list((manifest.get("history") or {}).get("release_tags") or []),
        "delta_tags": list((manifest.get("history") or {}).get("delta_tags") or []),
    }
    out["policy"] = {
        **base["policy"],
        **((manifest.get("policy") or {}) if isinstance(manifest.get("policy"), dict) else {}),
    }

    latest_delta = manifest.get("latest_delta")
    if isinstance(latest_delta, dict):
        out["latest_delta"] = {**base["latest_delta"], **latest_delta}
    return out


def iter_snapshot_bundle_files(tag: str, snapshot_dir: Path | None = None) -> list[Path]:
    """Files in snapshot_dir named ``{tag}.*`` (parquet, json, sha256, companion parquets)."""
    base = snapshot_dir or Path("data") / "snapshot"
    if not base.is_dir():
        return []
    prefix = f"{tag}."
    return sorted(p for p in base.iterdir() if p.is_file() and p.name.startswith(prefix))


def build_assets_from_bundle_dir(
    tag: str,
    *,
    release_type: str,
    created_at: str,
    snapshot_dir: Path | None = None,
) -> list[dict]:
    """Manifest ``assets`` entries matching on-disk release bundle (same idea as publish-snapshot _tmp scan)."""
    return [
        {
            "name": p.name,
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            "bytes": p.stat().st_size,
            "updated_at": created_at,
            "release_type": release_type,
        }
        for p in iter_snapshot_bundle_files(tag, snapshot_dir=snapshot_dir)
    ]


def build_release_entry(
    *,
    tag: str,
    release_type: str,
    file_name: str,
    sha256: str,
    bytes_size: int,
    created_at: str,
    row_count: int | None = None,
    min_date: str | None = None,
    max_date: str | None = None,
    assets: list[dict] | None = None,
) -> dict:
    return {
        "tag": tag,
        "release_type": release_type,
        "file_name": file_name,
        "sha256": sha256,
        "row_count": row_count,
        "created_at": created_at,
        "min_date": min_date,
        "max_date": max_date,
        "assets": assets
        or [
            {
                "name": file_name,
                "sha256": sha256,
                "bytes": int(bytes_size),
                "updated_at": created_at,
                "release_type": release_type,
            }
        ],
    }


def _dedupe_release_tags(entries: list[dict], limit: int = 120) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for item in entries:
        tag = str(item.get("tag") or "").strip()
        if not tag or tag in seen:
            continue
        out.append(item)
        seen.add(tag)
        if len(out) >= limit:
            break
    return out


def update_manifest_payload(manifest: dict | None, release_entry: dict) -> dict:
    out = ensure_manifest_structure(manifest)
    tag = str(release_entry["tag"])
    release_type = str(release_entry["release_type"])

    out["release_type"] = release_type
    out["row_count"] = release_entry.get("row_count")
    out["created_at"] = release_entry.get("created_at")

    history_item = {
        "tag": tag,
        "release_type": release_type,
        "created_at": release_entry.get("created_at"),
    }
    history = [history_item] + list(out["history"].get("release_tags") or [])
    out["history"]["release_tags"] = _dedupe_release_tags(history)

    if release_type == "delta":
        out["latest_delta"] = release_entry
        delta_tags = [tag] + list(out["history"].get("delta_tags") or [])
        dedup_delta: list[str] = []
        seen_delta: set[str] = set()
        for item in delta_tags:
            if item in seen_delta:
                continue
            dedup_delta.append(item)
            seen_delta.add(item)
            if len(dedup_delta) >= 120:
                break
        out["history"]["delta_tags"] = dedup_delta
    elif release_type in {"full", "snapshot"}:
        out["latest_current"] = release_entry
        if release_type == "full":
            out["latest_full"] = release_entry

    return out
