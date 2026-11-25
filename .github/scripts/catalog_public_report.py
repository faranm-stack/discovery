"""Positive-list projection of a catalog results ledger. No lookups or private fields."""

from __future__ import annotations

from collections import Counter
from html import escape
import re


STATUSES = ("passed", "failed", "blocked", "uncertain", "inconclusive", "not_run")
RESOURCE_LABELS = {
    "gpu_required": "GPU required", "gpu_optional": "GPU optional",
    "no_gpu_declared": "No GPU declared", "external_platform": "External / platform",
}
MAX_ROWS = 10_000


def _slug(value):
    if not isinstance(value, str) or len(value) > 100 or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value):
        raise ValueError("Invalid catalog identifier")
    return value


def _count(value):
    if type(value) is not int or value < 0:
        raise ValueError("Expected a nonnegative integer")
    return value


def _rows(value):
    if not isinstance(value, list) or len(value) > MAX_ROWS:
        raise ValueError("Invalid record list")
    return value


def _grade(status, roundtrip, retried):
    if status != "passed":
        if roundtrip in ("pass", "passed"):
            raise ValueError("Conflicting status and roundtrip")
        return "F" if status == "failed" else "I"
    if roundtrip == "not_applicable":
        return "C"
    if roundtrip in ("pass", "passed"):
        return "B" if retried is True else "A"
    if roundtrip not in (None, "unknown"):
        raise ValueError("Conflicting pass and roundtrip")
    return "Unknown"  # An unqualified pass is not evidence of a tool roundtrip.


def project(ledger, inventory=None):
    """Validate producer counts first. Never copy a ledger row or inspect its prose."""
    if not isinstance(ledger, dict) or ledger.get("mode") not in ("plan", "live", "reconcile"):
        raise ValueError("Invalid ledger mode")
    results = _rows(ledger.get("results"))
    if not results or _count(ledger.get("total")) != len(results):
        raise ValueError("Invalid ledger total")
    mode = ledger["mode"]
    rows = {}
    submissions = 0
    for row in results:
        if not isinstance(row, dict) or row.get("status") not in STATUSES:
            raise ValueError("Invalid ledger row")
        name = _slug(row.get("catalog_agent"))
        if name in rows:
            raise ValueError("Duplicate agent")
        status, roundtrip = row["status"], row.get("tool_roundtrip")
        attempts = _count(row.get("response_submissions", 0))  # Matches the ledger writer's sum.
        if mode == "plan" and (status not in ("not_run", "blocked") or attempts or roundtrip not in (None, "not_run")):
            raise ValueError("Plan cannot claim execution")
        submissions += attempts
        resource = row.get("resource_requirement")
        rows[name] = (status, _grade(status, roundtrip, row.get("agent_retried")),
                      RESOURCE_LABELS.get(resource, "Unknown") if isinstance(resource, str) else "Unknown")

    actual = Counter(values[0] for values in rows.values())
    supplied = ledger.get("counts")
    if (not isinstance(supplied, dict) or any(key not in STATUSES for key in supplied)
            or any(_count(supplied.get(key, 0)) != actual[key] for key in STATUSES)):
        raise ValueError("Inconsistent status counts")
    if "submitted_responses" in ledger and _count(ledger["submitted_responses"]) != submissions:
        raise ValueError("Inconsistent submission total")

    coverage = None
    if inventory is not None:
        commit = inventory.get("commit") if isinstance(inventory, dict) else None
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
            raise ValueError("Invalid pinned inventory")
        names = [_slug(name) for name in _rows(inventory.get("agents"))]
        expected = set(names)
        if len(expected) != len(names) or _count(inventory.get("count")) != len(names):
            raise ValueError("Inconsistent inventory")
        coverage = (commit.lower(), sorted(expected - rows.keys()), sorted(rows.keys() - expected))
    return {"mode": mode, "rows": dict(sorted(rows.items())), "counts": actual,
            "submitted": ledger.get("submitted_responses"), "coverage": coverage}


def table(headers, rows):
    def cells(tag, values):
        return "<tr>" + "".join(f"<{tag}>{escape(str(v))}</{tag}>" for v in values) + "</tr>"

    return ("<table><thead>" + cells("th", headers) + "</thead><tbody>"
            + "".join(cells("td", row) for row in rows) + "</tbody></table>")


def page(title, body):
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; "
            "base-uri 'none'; form-action 'none'\">"
            f"<title>{escape(title)}</title><style>body{{font:1rem/1.5 system-ui;margin:2rem;color:#17233b}}"
            "table{border-collapse:collapse}td,th{border:1px solid #bbc6d4;padding:.4rem;text-align:left;"
            "vertical-align:top;overflow-wrap:anywhere}th{background:#edf3f8}</style></head>"
            f"<body><main><h1>{escape(title)}</h1>{body}</main></body></html>\n")


def render_public(report):
    rows = report["rows"]
    submitted = report["submitted"] if report["submitted"] is not None else "Unknown"
    body = (f"<p>Mode: {escape(report['mode'])}. Ledger rows: {len(rows)}. Reported response submissions: {submitted}.</p>"
            "<p>Ledger statuses, not independent verification. A plan is not a live run. "
            "A/B = tool roundtrip (B: explicit retry); C = answer-only check, not a tool pass; "
            "F = reported failure; I = unjudged; Unknown = pass basis not recorded. "
            "Resource labels are declarations, not usage.</p>")
    body += "<p>" + "; ".join(f"{s}: {report['counts'][s]}" for s in STATUSES) + "</p>"
    body += table(("Catalog agent", "Ledger status", "Grade", "Resource declaration"),
                  ((name, *values) for name, values in rows.items()))
    if report["coverage"] is not None:
        commit, missing, unexpected = report["coverage"]
        body += (f"<h2>Pinned inventory comparison</h2><p>Baseline: {escape(commit)}. Name coverage only.</p>"
                 f"<p>Missing from ledger (not results or submissions): {escape(', '.join(missing)) or 'None'}.</p>"
                 f"<p>Ledger names outside baseline: {escape(', '.join(unexpected)) or 'None'}.</p>")
    return page("Catalog public report", body)
