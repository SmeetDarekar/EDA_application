"""
abt/registry.py
Handles metadata ingestion, versioning and the registry store.

Contract accepted:
{
  "table_name": "<any string>",
  "metadata": { "items": [...], "abt_snapshot": {...} }   <- items are column descriptors
}

Versioning rules:
- First time table seen        → version 1, store metadata
- Same table, same items hash  → no new version, update last_seen only
- Same table, different hash   → new version (v+1), store metadata
"""

import json
import os
import hashlib
from datetime import datetime, timezone
from typing import Dict, Tuple, Optional

DATADUMP_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "datadump")
REGISTRY_PATH = os.path.join(DATADUMP_DIR, "registry.json")


# ── helpers ──────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_items(items: list) -> str:
    """Deterministic hash of the items array (column descriptors only)."""
    canonical = json.dumps(
        [{k: v for k, v in sorted(item.items()) if k not in ("creationTimeStamp", "modifiedTimeStamp", "id", "links")}
         for item in sorted(items, key=lambda x: x.get("name", ""))],
        sort_keys=True
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _load_registry() -> Dict:
    if not os.path.isfile(REGISTRY_PATH):
        return {}
    with open(REGISTRY_PATH) as f:
        return json.load(f)


def _save_registry(reg: Dict):
    os.makedirs(DATADUMP_DIR, exist_ok=True)
    with open(REGISTRY_PATH, "w") as f:
        json.dump(reg, f, indent=2)


def _table_dir(table_name: str) -> str:
    safe = table_name.replace("/", "_").replace("\\", "_")
    return os.path.join(DATADUMP_DIR, safe)


def _version_path(table_name: str, version: int) -> str:
    return os.path.join(_table_dir(table_name), f"v{version}.json")


# ── public API ────────────────────────────────────────────────────────────────

def ingest(table_name: str, raw_metadata: Dict) -> Dict:
    """
    Ingest metadata for a table.
    Accepts both:
      - Full SAS catalog API response (has 'items', 'count', 'version', etc.)
      - Our internal format (has 'abt_snapshot' + 'items')
      - Bare list of column items
    Returns: {table_name, version, action, hash}
    action is one of: 'registered', 'version_added', 'unchanged'
    """
    # Handle bare list
    if isinstance(raw_metadata, list):
        raw_metadata = {"items": raw_metadata}

    items = raw_metadata.get("items", [])

    # Auto-derive snapshot from real SAS format if abt_snapshot not present
    snapshot = raw_metadata.get("abt_snapshot") or {
        "name": table_name,
        "row_count": raw_metadata.get("count", len(items)),
        "snapshot_date": _now()[:10],
    }
    incoming_hash = _hash_items(items)

    reg = _load_registry()
    now = _now()

    if table_name not in reg:
        # First time — register as version 1
        version = 1
        reg[table_name] = {
            "table_name": table_name,
            "created_at": now,
            "versions": [
                {
                    "version": 1,
                    "hash": incoming_hash,
                    "created_at": now,
                    "last_seen": now,
                    "snapshot": snapshot,
                    "path": _version_path(table_name, 1),
                }
            ],
        }
        _write_version_file(table_name, 1, items, snapshot, now)
        _save_registry(reg)
        return {"table_name": table_name, "version": 1, "action": "registered", "hash": incoming_hash}

    # Table exists — check hash against all existing versions
    existing_versions = reg[table_name]["versions"]
    for v in existing_versions:
        if v["hash"] == incoming_hash:
            # Same data — update last_seen only
            v["last_seen"] = now
            _save_registry(reg)
            return {"table_name": table_name, "version": v["version"], "action": "unchanged", "hash": incoming_hash}

    # Different hash — new version
    new_version = max(v["version"] for v in existing_versions) + 1
    existing_versions.append({
        "version": new_version,
        "hash": incoming_hash,
        "created_at": now,
        "last_seen": now,
        "snapshot": snapshot,
        "path": _version_path(table_name, new_version),
    })
    _write_version_file(table_name, new_version, items, snapshot, now)
    _save_registry(reg)
    return {"table_name": table_name, "version": new_version, "action": "version_added", "hash": incoming_hash}


def _write_version_file(table_name: str, version: int, items: list, snapshot: dict, ts: str):
    os.makedirs(_table_dir(table_name), exist_ok=True)
    payload = {
        "abt_snapshot": {
            "name": f"{table_name}_v{version}",
            "table_name": table_name,
            "version": version,
            "row_count": snapshot.get("row_count", 0),
            "snapshot_date": snapshot.get("snapshot_date", ts[:10]),
        },
        "items": items,
    }
    with open(_version_path(table_name, version), "w") as f:
        json.dump(payload, f, indent=2)


def list_tables() -> list:
    reg = _load_registry()
    result = []
    for table_name, info in reg.items():
        result.append({
            "table_name": table_name,
            "version_count": len(info["versions"]),
            "created_at": info["created_at"],
            "versions": [
                {"version": v["version"], "created_at": v["created_at"], "last_seen": v["last_seen"]}
                for v in info["versions"]
            ],
        })
    return result


def get_table_versions(table_name: str) -> Optional[list]:
    reg = _load_registry()
    if table_name not in reg:
        return None
    return reg[table_name]["versions"]


def resolve_path(table_name: str, version: int) -> Optional[str]:
    reg = _load_registry()
    if table_name not in reg:
        return None
    for v in reg[table_name]["versions"]:
        if v["version"] == version:
            return v["path"]
    return None