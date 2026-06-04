"""
app.py — Flask entry point for RMEDAService
"""

from flask import Flask, render_template, request, redirect, url_for, jsonify, session
from abt.columnProfile import load_abt
from abt.analyze import run_analysis
from abt.compare import run_comparison
from abt.registry import ingest, list_tables, get_table_versions
from abt.threshold_config import (
    ThresholdConfig, THRESHOLD_META, from_form, to_hidden_fields,
    is_default, from_dict
)
#from abt.export import export_analysis_csv
from services.ic_client import fetch_table_metadata, ICFetchError, test_connection
import json
from dataclasses import asdict
from abt.export import export_analysis_xlsx

app = Flask(__name__)
app.secret_key = "rmeda-session-key-change-in-prod"   # needed for session storage of config


# ── Threshold config helpers ──────────────────────────────────────────────────

def _load_cfg() -> ThresholdConfig:
    """Load ThresholdConfig from session, or return defaults."""
    raw = session.get("threshold_cfg")
    if raw:
        try:
            return from_dict(raw)
        except Exception:
            pass
    return ThresholdConfig()


def _save_cfg(cfg: ThresholdConfig):
    """Persist ThresholdConfig into session."""
    session["threshold_cfg"] = asdict(cfg)
    session.modified = True


def _cfg_template_vars(cfg: ThresholdConfig) -> dict:
    """Return template variables needed by analyze_select / compare_select."""
    return {
        "cfg_is_default": is_default(cfg),
        "cfg_hidden":     to_hidden_fields(cfg),
    }


# ── JSON parse helper ─────────────────────────────────────────────────────────

def _safe_parse_json(raw: str) -> dict:
    import re
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    if (raw.startswith('"') and raw.endswith('"')) or \
       (raw.startswith("'") and raw.endswith("'")):
        try:
            inner = raw[1:-1].replace("\\'", "'").replace('\\"', '"') \
                              .replace('\\n', '\n').replace('\\t', '\t') \
                              .replace('\\\\', '\\')
            return json.loads(inner)
        except Exception:
            pass
    try:
        first = json.loads(raw)
        if isinstance(first, str):
            return json.loads(first)
    except Exception:
        pass
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


# ── Threshold Configuration ───────────────────────────────────────────────────

@app.route("/config")
def config_page():
    """Render the threshold configuration page."""
    return_to = request.args.get("return_to", "analyze")
    cfg = _load_cfg()
    return render_template(
        "config.html",
        meta=THRESHOLD_META,
        current_values=asdict(cfg),
        is_default=is_default(cfg),
        return_to=return_to,
    )


@app.route("/config/save", methods=["POST"])
def config_save():
    """Save user-submitted thresholds to session and redirect back."""
    return_to = request.form.get("return_to", "analyze")
    cfg = from_form(request.form)
    _save_cfg(cfg)
    if return_to == "compare":
        return redirect(url_for("compare_select"))
    return redirect(url_for("analyze_select"))


@app.route("/config/reset")
def config_reset():
    """Clear custom thresholds from session (revert to defaults)."""
    return_to = request.args.get("return_to", "analyze")
    session.pop("threshold_cfg", None)
    if return_to == "compare":
        return redirect(url_for("compare_select"))
    return redirect(url_for("analyze_select"))


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


# ── Fetch from IC ─────────────────────────────────────────────────────────────
@app.route("/fetch-ic", methods=["GET"])
def fetch_ic_page():
    return render_template("fetch_ic.html")


@app.route("/fetch-ic/submit", methods=["POST"])
def fetch_ic_submit():
    table_name = request.form.get("table_name", "").strip()
    caslib     = request.form.get("caslib", "").strip() or None

    if not table_name:
        return render_template("fetch_ic.html", error="Table name is required.")

    try:
        payload = fetch_table_metadata(table_name=table_name, caslib=caslib)

        if payload is None:
            return render_template("fetch_ic.html",
                error=f"Table '{table_name}' not found in Information Catalog."
                      + (f" (library: {caslib})" if caslib else ""))

        result = ingest(table_name, payload)
        return render_template("ingest_result.html",
            result=result,
            table_name=table_name,
            source="Information Catalog",
            column_count=len(payload.get("items", [])))

    except ICFetchError as e:
        return render_template("fetch_ic.html", error=f"IC fetch failed: {e}")
    except Exception as e:
        return render_template("fetch_ic.html", error=f"Unexpected error: {e}")


@app.route("/api/ic/test", methods=["GET"])
def api_ic_test():
    return jsonify(test_connection())


# ── Analyze ───────────────────────────────────────────────────────────────────
@app.route("/analyze", methods=["GET"])
def analyze_select():
    tables = list_tables()
    cfg    = _load_cfg()
    return render_template(
        "analyze_select.html",
        tables=tables,
        **_cfg_template_vars(cfg),
    )


@app.route("/analyze/run", methods=["POST"])
def analyze_run():
    table_name = request.form.get("table_name", "").strip()
    version    = int(request.form.get("version", 1))
    target_col = request.form.get("target_col", "").strip() or None
    use_llm    = request.form.get("use_llm") == "on"

    # Thresholds: prefer form-embedded values (from hidden fields), then session, then defaults
    cfg = from_form(request.form)

    try:
        abt     = load_abt(table_name, version)
        results = run_analysis(abt, target_col, use_llm=use_llm, cfg=cfg)
        return render_template(
            "analyze_results.html",
            abt=abt,
            r=results,
            cfg=cfg,
            cfg_is_default=is_default(cfg),
        )
    except FileNotFoundError as e:
        return render_template("error.html", message=str(e))
    except Exception as e:
        return render_template("error.html", message=f"Analysis failed: {e}")


 
# @app.route("/analyze/export-csv", methods=["POST"])
# def analyze_export_csv():
#     table_name = request.form.get("table_name", "").strip()
#     version    = int(request.form.get("version", 1))
#     target_col = request.form.get("target_col", "").strip() or None
 
#     # Respect active threshold config (same as analyze_run)
#     from abt.threshold_config import from_form
#     cfg = from_form(request.form)
 
#     try:
#         from flask import Response
#         abt     = load_abt(table_name, version)
#         results = run_analysis(abt, target_col, use_llm=False, cfg=cfg)
#         csv_str = export_analysis_csv(abt, results)
 
#         filename = f"{table_name}_v{version}_analysis.csv"
#         return Response(
#             csv_str,
#             mimetype="text/csv",
#             headers={"Content-Disposition": f"attachment; filename={filename}"}
#         )
#     except FileNotFoundError as e:
#         return render_template("error.html", message=str(e))
#     except Exception as e:
#         return render_template("error.html", message=f"CSV export failed: {e}")







@app.route("/analyze/export-xlsx", methods=["POST"])
def analyze_export_xlsx():
    table_name = request.form.get("table_name", "").strip()
    version    = int(request.form.get("version", 1))
    target_col = request.form.get("target_col", "").strip() or None
 
    from abt.threshold_config import from_form
    cfg = from_form(request.form)
 
    try:
        from flask import Response
        abt      = load_abt(table_name, version)
        results  = run_analysis(abt, target_col, use_llm=False, cfg=cfg)
        xlsx_bytes = export_analysis_xlsx(abt, results)
 
        filename = f"{table_name}_v{version}_analysis.xlsx"
        return Response(
            xlsx_bytes,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except FileNotFoundError as e:
        return render_template("error.html", message=str(e))
    except Exception as e:
        return render_template("error.html", message=f"Export failed: {e}")
 





















# ── Compare ───────────────────────────────────────────────────────────────────
@app.route("/compare", methods=["GET"])
def compare_select():
    tables = list_tables()
    cfg    = _load_cfg()
    return render_template(
        "compare_select.html",
        tables=tables,
        **_cfg_template_vars(cfg),
    )


@app.route("/compare/versions", methods=["POST"])
def compare_versions():
    """Form step: given table name, return its versions."""
    table_name = request.form.get("table_name", "").strip()
    versions   = get_table_versions(table_name) or []
    tables     = list_tables()
    cfg        = _load_cfg()
    return render_template(
        "compare_select.html",
        tables=tables,
        selected_table=table_name,
        versions=versions,
        **_cfg_template_vars(cfg),
    )


@app.route("/compare/run", methods=["POST"])
def compare_run():
    table_name = request.form.get("table_name", "").strip()
    ver_list   = request.form.getlist("versions")

    if len(ver_list) < 2:
        tables   = list_tables()
        versions = get_table_versions(table_name) or []
        cfg      = _load_cfg()
        return render_template(
            "compare_select.html",
            tables=tables,
            selected_table=table_name,
            versions=versions,
            error="Select at least 2 versions to compare.",
            **_cfg_template_vars(cfg),
        )

    # Thresholds: prefer form-embedded hidden fields
    cfg = from_form(request.form)

    try:
        abts    = [load_abt(table_name, int(v)) for v in ver_list]
        use_llm = request.form.get("use_llm") == "on"
        results = run_comparison(abts, use_llm=use_llm, cfg=cfg)
        return render_template(
            "compare_results.html",
            abts=abts,
            r=results,
            version_labels=[a.abt_name for a in abts],
            cfg=cfg,
            cfg_is_default=is_default(cfg),
        )
    except FileNotFoundError as e:
        return render_template("error.html", message=str(e))
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        return render_template("error.html", message=f"Comparison failed: {e}<br><pre>{tb}</pre>")


# ── API endpoint ──────────────────────────────────────────────────────────────
@app.route("/api/ingest", methods=["POST"])
def api_ingest():
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