"""
services/ic_client.py
Information Catalog (IC) API client.
Single responsibility: fetch raw column metadata for a given table from IC.
All versioning, hashing and storage logic stays in core/registry.py.
"""

import os
import requests
from typing import Optional, Dict, Any

# ── Configuration ─────────────────────────────────────────────────────────────
# Set these via environment variables in production.

IC_BASE_URL    = "https://d116403.ingress-nginx.rci1322a-m1.tsr-rci-ciaws.hpos.rnd.sas.com/"
IC_ACCESS_TOKEN = "eyJqa3UiOiJodHRwczovL2xvY2FsaG9zdC9TQVNMb2dvbi90b2tlbl9rZXlzIiwia2lkIjoibGVnYWN5LXRva2VuLWtleSIsInR5cCI6IkpXVCIsImFsZyI6IlJTMjU2In0.eyJzdWIiOiIzODhmYzk1ZS03Y2VkLTQ3NDEtYmI5NS0xZTdmNDdlYTNiZjMiLCJzZXNzaW9uX3NpZyI6IjVkYTQ4N2VlLWUzMzctNGIzMC1hZmNlLTViMjI5MjMwNTRlZSIsInVzZXJfbmFtZSI6InNhc2FkbSIsIm9yaWdpbiI6ImxkYXAiLCJpc3MiOiJodHRwOi8vbG9jYWxob3N0L1NBU0xvZ29uL29hdXRoL3Rva2VuIiwiYXV0aG9yaXRpZXMiOlsiUk1BZG1pbnMiLCJDaXJydXNBZG1pbnMiLCJyaXNrTW9kZWxpbmdBcHBBZG1pbiIsIlJNTW9kZWxlcnMiLCJyaXNrRGF0YUFkbWluIiwicmlza01vZGVsZXIiLCJyaXNrRGF0YUFuYWx5c3QiLCJyaXNrRGF0YUNvbmZpZ1VzZXJzIiwicmlza01vZGVsVmFsaWRhdG9yIiwiQ2lycnVzQnVpbGRlclNvbHV0aW9uVXNlcnMiLCJSTVZhbGlkYXRvcnMiLCJDaXJydXNVc2VycyIsIlJNRGF0YU1hbmFnZXJzIl0sImNsaWVudF9pZCI6Im15Y2xpZW50aWQiLCJhdWQiOlsibXljbGllbnRpZCIsInVhYSIsIm9wZW5pZCJdLCJleHRfaWQiOiJ1aWQ9c2FzYWRtLG91PXVzZXJzLGRjPWdlbCxkYz1jb20iLCJyZW1vdGVfaXAiOiIxNzIuMjcuMTQuMTc3IiwiemlkIjoidWFhIiwiZ3JhbnRfdHlwZSI6ImF1dGhvcml6YXRpb25fY29kZSIsInVzZXJfaWQiOiIzODhmYzk1ZS03Y2VkLTQ3NDEtYmI5NS0xZTdmNDdlYTNiZjMiLCJhenAiOiJteWNsaWVudGlkIiwic2NvcGUiOlsib3BlbmlkIiwidWFhLnVzZXIiXSwiYXV0aF90aW1lIjoxNzgwOTE4MDYwLCJleHAiOjE3ODA5MjE2NjAsImlhdCI6MTc4MDkxODA2MCwianRpIjoiOGU0ZjcyNzVjYThhNDA0ZjgwZTk4ZGJjMjIwOWU0ZGMiLCJlbWFpbCI6InNhc2FkbUBnZWwuY29tIiwicmV2X3NpZyI6ImRlZmNlZWRkIiwiY2lkIjoibXljbGllbnRpZCJ9.AzkKrCXCocQXGaeo1UnK8WXp4I7_T07ZxfcuRAogVs8uEv0nL-lf7ijCRr8fT1GpI3mnZAFzGbAKCwiNiLAyZ4FwjjBJ9fdDn-2y6ojsHxA3PqbfiYAtmlPSDHrNhAbnHDAhxQVVxJlWLDki7daKgOU5X8GmEMfAycnnPYGMjz65nHHA_osOmFozoL-ypXgYQg5tUdYYybmOtmjcwLGmPWdqzGtn4oBT9c3fdq1nHYXZWSxu417PDXNtxdHq7_xfyUaSGIjxIr4fxa2cnWC-rYpf6qqHWwn68HQ9haQaffeJ5EB8mfPD9lTKKokF7trniZbwGvt9ENGwVkq_yC9Rog"
IC_INSTANCES_ENDPOINT = f"{IC_BASE_URL}/catalog/instances"

HEADERS = {
    "Authorization": f"Bearer {IC_ACCESS_TOKEN}",
    "Accept": "application/json",
}

DEFAULT_LIMIT   = 3000
REQUEST_TIMEOUT = 30


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_table_metadata(
    *,
    table_name: str,
    caslib: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
) -> Optional[Dict[str, Any]]:
    """
    Fetch IC metadata for a CAS table using /catalog/instances.

    Args:
        table_name : Required. The CAS table name to look up.
        caslib     : Optional. If provided, narrows the search to a specific library.
        limit      : Max number of column instances to fetch (default 3000).

    Returns:
        Raw IC API payload dict (with 'items' array) if found, None if not found.

    Raises:
        ICFetchError : on network errors, auth failures, or unexpected HTTP errors.
    """
    if not table_name or not table_name.strip():
        raise ICFetchError("table_name cannot be empty.")

    filter_expr = _build_filter(table_name.strip(), caslib.strip() if caslib else None)

    params = {
        "filter": filter_expr,
        "level":  "detailedMetrics",
        "limit":  limit,
    }

    try:
        response = requests.get(
            IC_INSTANCES_ENDPOINT,
            headers=HEADERS,
            params=params,
            verify=False,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.exceptions.ConnectionError as e:
        raise ICFetchError(f"Cannot connect to IC at {IC_BASE_URL}: {e}")
    except requests.exceptions.Timeout:
        raise ICFetchError(f"IC request timed out after {REQUEST_TIMEOUT}s.")
    except requests.exceptions.RequestException as e:
        raise ICFetchError(f"Network error: {e}")

    if response.status_code == 404:
        return None

    if response.status_code == 401:
        raise ICFetchError("IC authentication failed. Check IC_ACCESS_TOKEN.")

    if response.status_code == 403:
        raise ICFetchError("IC access forbidden. Insufficient permissions for this table.")

    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        raise ICFetchError(f"IC returned HTTP {response.status_code}: {e}")

    try:
        payload = response.json()
    except ValueError:
        raise ICFetchError("IC returned non-JSON response.")

    if not payload.get("items"):
        return None  # Table not found or no columns

    return payload


def test_connection() -> Dict[str, Any]:
    """
    Ping IC to verify connectivity and auth.
    Returns {"ok": True/False, "message": str}
    """
    try:
        response = requests.get(
            IC_INSTANCES_ENDPOINT,
            headers=HEADERS,
            params={"limit": 1},
            verify=False,
            timeout=10,
        )
        if response.status_code in (200, 206):
            return {"ok": True, "message": "IC connection successful."}
        elif response.status_code == 401:
            return {"ok": False, "message": "Authentication failed — check IC_ACCESS_TOKEN."}
        else:
            return {"ok": False, "message": f"IC returned HTTP {response.status_code}."}
    except Exception as e:
        return {"ok": False, "message": f"Connection error: {e}"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_filter(table_name: str, caslib: Optional[str]) -> str:
    if caslib:
        return (
            "and("
            f"eq(caslib,'{caslib}'),"
            f"contains(resourceId,'{table_name}'),"
            "eq(definition,'casColumn')"
            ")"
        )
    return (
        "and("
        f"contains(resourceId,'{table_name}'),"
        "eq(definition,'casColumn')"
        ")"
    )


# ── Custom exception ──────────────────────────────────────────────────────────

class ICFetchError(Exception):
    """Raised when IC fetch fails for any reason."""
    pass