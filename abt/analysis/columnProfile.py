"""
abt/columnProfile.py
First-class data accessor. All modules interact with ColumnProfile / ABTProfile only.
"""

import json
import os
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

DATADUMP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "datadump")


@dataclass
class ColumnProfile:
    name: str
    data_type: str
    cas_data_type: str
    statistical_scale: str
    ordinal_position: int
    completeness_percent: float
    missing_count: int
    cardinality_count: int
    uniqueness_percent: float

    mean: Optional[float] = None
    median: Optional[float] = None
    std: Optional[float] = None
    min_val: Optional[float] = None
    max_val: Optional[float] = None
    skewness: Optional[float] = None
    kurtosis: Optional[float] = None
    q25: Optional[float] = None
    q50: Optional[float] = None
    q75: Optional[float] = None
    has_outliers: bool = False
    n_outliers: int = 0

    most_common_value: Optional[Any] = None
    least_common_value: Optional[Any] = None
    mode: Optional[Any] = None
    blank_value_count: int = 0
    mismatched_count: int = 0
    chars_max_count: Optional[int] = None
    chars_min_count: Optional[int] = None

    information_privacy: Optional[str] = None
    semantic_type_id: Optional[str] = None
    semantic_type_score: Optional[float] = None
    has_unique_field: bool = False
    actual_data_type: Optional[str] = None
    best_chart_type: Optional[str] = None
    raw_length: Optional[int] = None

    @classmethod
    def from_dict(cls, item: Dict) -> "ColumnProfile":
        a = item.get("attributes", {})
        # Fallback: if no 'attributes' key, treat item itself as flat attrs
        if not a and any(k in item for k in ("completenessPercent", "dataType", "statisticalScale")):
            a = item
        if a is None:
            a = {}

        def _s(key, default=None):
            """Safe get — returns default if key missing or value is None."""
            v = a.get(key)
            return v if v is not None else default

        def _f(key, default=None):
            """Safe float — returns default on None, non-numeric, or non-finite."""
            import math
            v = a.get(key)
            if v is None:
                return default
            try:
                f = float(v)
                return f if math.isfinite(f) else default
            except (TypeError, ValueError):
                return default

        def _i(key, default=0):
            """Safe int."""
            v = a.get(key)
            if v is None:
                return default
            try:
                return int(v)
            except (TypeError, ValueError):
                return default

        def _b(key, default=False):
            """Safe bool."""
            v = a.get(key)
            if v is None:
                return default
            return bool(v)

        return cls(
            name=item.get("name", ""),
            data_type=_s("dataType", "num"),
            cas_data_type=_s("casDataType", "double"),
            statistical_scale=_s("statisticalScale", "interval"),
            ordinal_position=_i("ordinalPosition", 0),
            completeness_percent=_f("completenessPercent", 100.0),
            missing_count=_i("missingCount", 0),
            cardinality_count=_i("cardinalityCount", 0),
            uniqueness_percent=_f("uniquenessPercent", 0.0),
            mean=_f("mean"),
            median=_f("median"),
            std=_f("standardDeviation"),
            min_val=_f("min"),
            max_val=_f("max"),
            skewness=_f("skewness"),
            kurtosis=_f("kurtosis"),
            q25=_f("quantiles25"),
            q50=_f("quantiles50"),
            q75=_f("quantiles75"),
            has_outliers=_b("hasOutliers", False),
            n_outliers=_i("nOutliers", 0),
            most_common_value=_s("mostCommonValue"),
            least_common_value=_s("leastCommonValue"),
            mode=_s("mode"),
            blank_value_count=_i("blankValueCount", 0),
            mismatched_count=_i("mismatchedCount", 0),
            chars_max_count=_i("charsMaxCount") if a.get("charsMaxCount") is not None else None,
            chars_min_count=_i("charsMinCount") if a.get("charsMinCount") is not None else None,
            information_privacy=_s("informationPrivacy"),
            semantic_type_id=_s("semanticTypeId"),
            semantic_type_score=_f("semanticTypeScore"),
            has_unique_field=_b("hasUniqueField", False),
            actual_data_type=_s("actualDataType"),
            best_chart_type=_s("bestChartType"),
            raw_length=_i("rawLength") if a.get("rawLength") is not None else None,
        )

    def is_numeric(self) -> bool:
        return self.data_type == "num"

    def is_char(self) -> bool:
        return self.data_type == "char"


def _f(v, default=None):
    """Module-level safe float helper for use outside from_dict."""
    import math
    if v is None:
        return default
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


@dataclass
class ABTProfile:
    abt_name: str
    row_count: int
    snapshot_date: str
    version: int = 1
    columns: List[ColumnProfile] = field(default_factory=list)

    @property
    def column_names(self) -> List[str]:
        return [c.name for c in self.columns]

    def get_column(self, name: str) -> Optional[ColumnProfile]:
        for c in self.columns:
            if c.name == name:
                return c
        return None

    def get_numeric_columns(self) -> List[ColumnProfile]:
        return [c for c in self.columns if c.is_numeric()]

    def get_char_columns(self) -> List[ColumnProfile]:
        return [c for c in self.columns if c.is_char()]


def load_abt_from_path(path: str, version: int = 1) -> ABTProfile:
    with open(path) as f:
        raw = json.load(f)
    snap = raw.get("abt_snapshot", {})
    # Use version stored in snapshot if available (set by registry._write_version_file)
    actual_version = snap.get("version", version)
    return ABTProfile(
        abt_name=snap.get("name", os.path.basename(path)),
        row_count=int(snap.get("row_count", 0)),
        snapshot_date=snap.get("snapshot_date", "unknown"),
        version=actual_version,
        columns=[ColumnProfile.from_dict(item) for item in raw.get("items", [])],
    )


def load_abt(table_name: str, version: int) -> ABTProfile:
    """Load by table name + version using the registry."""
    from abt.analysis.registry import resolve_path
    path = resolve_path(table_name, version)
    if not path or not os.path.isfile(path):
        raise FileNotFoundError(f"No data found for table='{table_name}' version={version}")
    return load_abt_from_path(path, version)