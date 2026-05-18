"""
app.py — Flask entry point for RMEDAService
"""

from flask import Flask, render_template, request, redirect, url_for, jsonify
from abt.columnProfile import load_abt
from abt.analyze import run_analysis
from abt.compare import run_comparison
from abt.registry import ingest, list_tables, get_table_versions
import json

app = Flask(__name__)


def _safe_parse_json(raw: str) -> dict:
    """
    Try multiple strategies to parse JSON that may have been copied
    from Python reprs, logs, or double-encoded sources.
    """
    import re
    raw = raw.strip()

    # Strategy 1: direct parse (correct input)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Strategy 2: Python string repr with escaped chars
    if (raw.startswith('"') and raw.endswith('"')) or \
       (raw.startswith("'") and raw.endswith("'")):
        try:
            inner = raw[1:-1].replace("\\'", "'").replace('\\"', '"') \
                              .replace('\\n', '\n').replace('\\t', '\t') \
                              .replace('\\\\', '\\')
            return json.loads(inner)
        except Exception:
            pass

    # Strategy 3: double-encoded JSON string
    try:
        first = json.loads(raw)
        if isinstance(first, str):
            return json.loads(first)
    except Exception:
        pass

    # Strategy 4: extract JSON object/array from surrounding text
    match = re.search(r'(\{.*\}|\[.*\])', raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    raise json.JSONDecodeError("Could not parse JSON after multiple attempts", raw, 0)


# ── Home ──────────────────────────────────────────────────────────────────────
@app.route("/")
def home():
    tables = list_tables()
    return render_template("home.html", tables=tables)


# ── Ingest ────────────────────────────────────────────────────────────────────
@app.route("/ingest", methods=["GET"])
def ingest_page():
    return render_template("ingest.html")


@app.route("/ingest/submit", methods=["POST"])
def ingest_submit():
    table_name = request.form.get("table_name", "").strip()
    raw_json   = request.form.get("metadata_json", "").strip()

    if not table_name or not raw_json:
        return render_template("ingest.html", error="Table name and metadata JSON are required.")

    try:
        metadata = _safe_parse_json(raw_json)
        # Accept either full API response (with 'items') or bare list
        if isinstance(metadata, list):
            metadata = {"items": metadata}
        result = ingest(table_name, metadata)
        return render_template("ingest_result.html", result=result, table_name=table_name)
    except json.JSONDecodeError as e:
        return render_template("ingest.html",
            error=f"Invalid JSON at position {e.pos}: {e.msg}. "
                  f"Paste raw JSON directly — not a Python repr or string-encoded version.")
    except Exception as e:
        return render_template("ingest.html", error=f"Ingest failed: {e}")


# ── Analyze ───────────────────────────────────────────────────────────────────
@app.route("/analyze", methods=["GET"])
def analyze_select():
    tables = list_tables()
    return render_template("analyze_select.html", tables=tables)


@app.route("/analyze/run", methods=["POST"])
def analyze_run():
    table_name = request.form.get("table_name", "").strip()
    version    = int(request.form.get("version", 1))
    target_col = request.form.get("target_col", "").strip() or None
    use_llm    = request.form.get("use_llm") == "on"

    try:
        abt     = load_abt(table_name, version)
        results = run_analysis(abt, target_col, use_llm=use_llm)
        # Storytelling sequence for template rendering:
        # S0(headline) → S1(health) → S9(actions) → S2(blockers) →
        # S3(warnings) → S4(governance) → S6(target) → S5(readiness) →
        # S7(distributions) → S8(health scores)
        return render_template("analyze_results.html", abt=abt, r=results)
    except FileNotFoundError as e:
        return render_template("error.html", message=str(e))
    except Exception as e:
        return render_template("error.html", message=f"Analysis failed: {e}")


# ── Compare ───────────────────────────────────────────────────────────────────
@app.route("/compare", methods=["GET"])
def compare_select():
    tables = list_tables()
    return render_template("compare_select.html", tables=tables)


@app.route("/compare/versions", methods=["POST"])
def compare_versions():
    """AJAX-style form step: given table name, return its versions."""
    table_name = request.form.get("table_name", "").strip()
    versions   = get_table_versions(table_name) or []
    tables     = list_tables()
    return render_template("compare_select.html", tables=tables,
                           selected_table=table_name, versions=versions)


@app.route("/compare/run", methods=["POST"])
def compare_run():
    table_name = request.form.get("table_name", "").strip()
    ver_list   = request.form.getlist("versions")

    if len(ver_list) < 2:
        tables    = list_tables()
        versions  = get_table_versions(table_name) or []
        return render_template("compare_select.html", tables=tables,
                               selected_table=table_name, versions=versions,
                               error="Select at least 2 versions to compare.")
    try:
        abts    = [load_abt(table_name, int(v)) for v in ver_list]
        use_llm = request.form.get("use_llm") == "on"
        results = run_comparison(abts, use_llm=use_llm)
        return render_template("compare_results.html", abts=abts, r=results,
                               version_labels=[a.abt_name for a in abts])
    except FileNotFoundError as e:
        return render_template("error.html", message=str(e))
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        return render_template("error.html", message=f"Comparison failed: {e}<br><pre>{tb}</pre>")


# ── API endpoint (optional programmatic access) ───────────────────────────────
@app.route("/api/ingest", methods=["POST"])
def api_ingest():
    """
    JSON API: POST {"table_name": "...", "metadata": {...}}
    Returns: {"table_name", "version", "action", "hash"}
    """
    data = request.get_json(force=True, silent=True) or {}
    table_name = data.get("table_name", "").strip()
    metadata   = data.get("metadata", {})
    if not table_name or not metadata:
        return jsonify({"error": "table_name and metadata are required"}), 400
    try:
        result = ingest(table_name, metadata)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)