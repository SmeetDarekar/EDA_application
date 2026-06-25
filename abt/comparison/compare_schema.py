"""
abt/compare_schema.py
─────────────────────────────────────────────────────────────────────────────
Comparative schema changes: version summary, schema edits, cardinality shifts.
"""

from typing import Dict, List, Optional
from abt.analysis.columnProfile import ABTProfile
from abt.analysis.analyze import s5_readiness


def _ord(s: str) -> int:
    return {"ready": 0, "caution": 1, "drop": 2, "absent": -1}.get(s, 0)

def _worsened(a, b): return _ord(b) > _ord(a)
def _improved(a, b): return _ord(b) < _ord(a)


def c1_version_summary(abts: List[ABTProfile]) -> Dict:
    summaries = [
        {"name": a.abt_name, "version": a.version, "snapshot_date": a.snapshot_date,
         "row_count": a.row_count, "col_count": len(a.columns)}
        for a in abts
    ]
    pairwise = []
    for i in range(len(abts) - 1):
        a, b = abts[i], abts[i + 1]
        sa, sb = set(a.column_names), set(b.column_names)
        common = sa & sb
        rd_a = {r["column"]: r["status"] for r in s5_readiness(a)}
        rd_b = {r["column"]: r["status"] for r in s5_readiness(b)}
        worsened = [c for c in common if _worsened(rd_a.get(c), rd_b.get(c))]
        improved = [c for c in common if _improved(rd_a.get(c), rd_b.get(c))]
        pairwise.append({
            "from": a.abt_name, "to": b.abt_name,
            "row_delta":       b.row_count - a.row_count,
            "added_columns":   sorted(sb - sa),
            "dropped_columns": sorted(sa - sb),
            "worsened":        worsened,
            "improved":        improved,
            "stable":          [c for c in common if c not in worsened and c not in improved],
        })
    return {"versions": summaries, "pairwise": pairwise}


def c2_schema_changes(abts: List[ABTProfile]) -> List[Dict]:
    results = []
    for i in range(len(abts) - 1):
        a, b = abts[i], abts[i + 1]
        sa, sb = set(a.column_names), set(b.column_names)

        added = [{"column": n, "scale": b.get_column(n).statistical_scale,
                  "data_type": b.get_column(n).data_type,
                  "completeness": b.get_column(n).completeness_percent,
                  "note": "New column — evaluate for model inclusion in next version."}
                 for n in sorted(sb - sa)]

        dropped = [{"column": n, "scale": a.get_column(n).statistical_scale,
                    "data_type": a.get_column(n).data_type,
                    "last_completeness": a.get_column(n).completeness_percent,
                    "note": "Removed — verify intentional if this was a model feature."}
                   for n in sorted(sa - sb)]

        type_changed = []
        for n in sorted(sa & sb):
            ca, cb = a.get_column(n), b.get_column(n)
            if ca.cas_data_type != cb.cas_data_type or ca.statistical_scale != cb.statistical_scale:
                type_changed.append({
                    "column": n,
                    "from_type": f"{ca.cas_data_type} / {ca.statistical_scale}",
                    "to_type":   f"{cb.cas_data_type} / {cb.statistical_scale}",
                    "note": "Data type or scale changed — re-validate preprocessing pipeline.",
                })

        results.append({"from": a.abt_name, "to": b.abt_name,
                         "added": added, "dropped": dropped, "type_changed": type_changed})
    return results


def c10_cardinality_drift(abts: List[ABTProfile]) -> List[Dict]:
    all_cols = []
    seen = set()
    for a in abts:
        for col in a.columns:
            if col.name not in seen:
                seen.add(col.name)
                all_cols.append(col.name)

    results = []
    for col_name in all_cols:
        cardinalities = []
        for abt in abts:
            c = abt.get_column(col_name)
            if c:
                cardinalities.append({
                    "abt": abt.abt_name,
                    "cardinality": c.cardinality_count,
                    "scale": c.statistical_scale,
                })
            else:
                cardinalities.append({
                    "abt": abt.abt_name,
                    "cardinality": None,
                    "scale": None,
                })

        explosions = []
        for i in range(len(cardinalities) - 1):
            c1v = cardinalities[i]["cardinality"]
            c2v = cardinalities[i + 1]["cardinality"]
            if c1v and c2v and c1v > 0:
                ratio = c2v / c1v
                if ratio >= 1.5:    severity = "critical"   # doubled or more
                elif ratio >= 1.2:  severity = "notable"    # 20-50% increase
                else:               severity = None
                if severity:
                    explosions.append({
                        "from_ver":  cardinalities[i]["abt"],
                        "to_ver":    cardinalities[i + 1]["abt"],
                        "from_card": c1v, "to_card": c2v,
                        "ratio":     round(ratio, 2),
                        "severity":  severity,
                    })
            elif c1v and not c2v:
                explosions.append({
                    "from_ver": cardinalities[i]["abt"],
                    "to_ver":   cardinalities[i + 1]["abt"],
                    "from_card": c1v, "to_card": None,
                    "ratio": None, "severity": "dropped",
                })

        if explosions:
            results.append({
                "column":         col_name,
                "scale":          cardinalities[0].get("scale") or "nominal",
                "cardinalities":  cardinalities,
                "explosions":     explosions,
                "version_labels": [a.abt_name for a in abts],
            })

    return results
