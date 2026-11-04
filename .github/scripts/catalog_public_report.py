"""Offline, allowlist-only public reports. Never publish raw evidence or attribution."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from html import escape
import io
import json
from pathlib import Path
import re
import sys


GRADES = {"passed": "A", "failed": "F", "blocked": "I", "uncertain": "I", "not_run": "I"}
RESOURCES = {
    "gpu_required": "GPU required", "gpu_optional": "GPU optional",
    "no_gpu_declared": "No GPU declared", "external_platform": "External / platform",
    "unknown": "Needs resource review",
}
FILES = ("catalog-public-report.html", "catalog-public-report.csv")
FIELDS = ("scope", "name", "status", "grade", "resource_requirement", "resource_label")
INVENTORY_FIELDS = ("inventory_baseline_sha", "inventory_expected_count", "ledger_total",
                    "inventory_missing_count", "inventory_unexpected_count")
INVENTORY_NOTICE = ("Pinned inventory baseline only, not a latest-upstream claim or source parity attestation. "
                    "Missing expected names are absent from the ledger and added to this report only as not_run/I; "
                    "unexpected ledger names are retained. The frozen ledger is unchanged. "
                    "Name coverage does not establish checkout contents or source parity.")
MAX_ROWS = 10000
MAX_BYTES = 16 * 1024 * 1024
NOTICE = ("Reported statuses only, not independently verified evidence or author attribution. "
          "A = passed; B = passed after agent retries (explicit boolean agent_retried=true only); "
          "F = failed; I = unjudged (blocked, uncertain, or not run). "
          "Absent or non-boolean retry metadata defaults passed rows to A; reasons never infer retries. "
          "Offline plans establish no live or scientific pass. Kit static checks are not kit E2E passes. "
          "kit_static rows describe static checks only and have blank grades; kit_e2e rows describe E2E status. "
          "Resource labels describe declarations, not observed usage. No raw evidence is included.")


def slug(value: object) -> str:
    # Also excludes CSV formulas, controls, paths, URLs, markup and contacts.
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value) or len(value) > 100:
        raise ValueError("Invalid identifier")
    return value


def enum(value: object, choices) -> str:
    if not isinstance(value, str) or value not in choices:
        raise ValueError("Invalid enum")
    return value


def count(value: object) -> int:
    if type(value) is not int or not 0 <= value <= MAX_ROWS * 2:
        raise ValueError("Invalid count")
    return value


def object_value(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Expected object")
    return value


def records(value: object) -> list:
    if not isinstance(value, list) or len(value) > MAX_ROWS:
        raise ValueError("Invalid records")
    return value


def public_row(scope: str, name: str, status: str, resource: object = None,
               *, agent_retried: object = None) -> dict:
    category = resource if isinstance(resource, str) and resource in RESOURCES else "unknown"
    status = enum(status, GRADES)
    grade = GRADES[status]
    if scope == "kit_static":
        grade = ""
    elif scope == "agent" and status == "passed" and agent_retried is True:
        grade = "B"
    return dict(zip(FIELDS, (scope, slug(name), status, grade,
                             category, RESOURCES[category])))


def project(document: object, kind: str, source_revision: str | None = None,
            inventory: object = None) -> dict:
    """Construct fresh objects; no input dictionary is copied into public output."""
    doc = object_value(document)
    baseline = None
    if inventory is not None:
        if kind != "catalog":
            raise ValueError("Inventory is catalog-only")
        inventory = object_value(inventory)
        commit = inventory.get("commit")
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
            raise ValueError("Invalid inventory revision")
        expected_names = [slug(name) for name in records(inventory.get("agents"))]
        if len(set(expected_names)) != len(expected_names) or count(inventory.get("count")) != len(expected_names):
            raise ValueError("Invalid inventory counts")
        baseline = {"inventory_baseline_sha": commit.lower(), "inventory_expected_count": len(expected_names)}
    if source_revision is not None and not re.fullmatch(r"[0-9a-fA-F]{40}", source_revision):
        raise ValueError("Invalid source revision")
    rows = []
    names = set()
    if kind == "catalog":
        mode = enum(doc.get("mode"), ("plan", "live", "reconcile"))
        for raw in records(doc.get("results")):
            raw = object_value(raw)
            row = public_row("agent", raw.get("catalog_agent"), raw.get("status"), raw.get("resource_requirement"),
                             agent_retried=raw.get("agent_retried"))
            if row["name"] in names:
                raise ValueError("Duplicate identifier")
            names.add(row["name"])
            if mode == "plan" and row["status"] in {"passed", "failed"}:
                raise ValueError("Plan cannot claim runtime verdicts")
            rows.append(row)
        actual = Counter(r["status"] for r in rows)
        supplied = object_value(doc.get("counts"))
        for key, value in supplied.items():
            enum(key, GRADES)
            if count(value) != actual[key]:
                raise ValueError("Inconsistent counts")
        if any(supplied.get(key, 0) != value for key, value in actual.items()) or count(doc.get("total")) != len(rows):
            raise ValueError("Inconsistent totals")
        if baseline is not None:
            expected = set(expected_names)
            missing = expected - names
            unexpected = names - expected
            baseline.update(ledger_total=len(rows), inventory_missing_count=len(missing),
                            inventory_unexpected_count=len(unexpected))
            for row in rows:
                row["inventory_coverage"] = "unexpected_in_ledger" if row["name"] in unexpected else "expected_in_ledger"
            for name in missing:
                row = public_row("agent", name, "not_run")
                row["inventory_coverage"] = "missing_from_ledger"
                rows.append(row)
    elif kind == "kit":
        mode = enum(doc.get("mode"), ("offline",))
        if doc.get("runtime_enabled") is not False:
            raise ValueError("Kit runtime must be disabled")
        for raw in records(doc.get("kits")):
            raw = object_value(raw)
            name = slug(raw.get("kit"))
            if name in names:
                raise ValueError("Duplicate identifier")
            names.add(name)
            static = enum(object_value(raw.get("static")).get("status"), ("passed", "failed"))
            e2e = object_value(raw.get("e2e"))
            status = enum(e2e.get("status"), ("blocked", "not_run"))
            if e2e.get("executed") is not False or count(e2e.get("assertions_evaluated")) != 0:
                raise ValueError("Offline kit cannot claim execution")
            rows.extend((public_row("kit_static", name, static), public_row("kit_e2e", name, status)))
        summary = object_value(doc.get("summary"))
        expected = {"kits": len(names), "static_passed": sum(r["status"] == "passed" for r in rows),
                    "static_failed": sum(r["status"] == "failed" for r in rows),
                    "e2e_passed": 0, "e2e_failed": 0, "e2e_executed": 0,
                    "e2e_blocked": sum(r["status"] == "blocked" for r in rows),
                    "e2e_not_run": sum(r["status"] == "not_run" for r in rows)}
        if any(count(summary.get(key)) != value for key, value in expected.items()):
            raise ValueError("Inconsistent kit counts")
    else:
        raise ValueError("Invalid report kind")
    report = {"mode": mode, "source_revision": source_revision.lower() if source_revision else "",
              "available": True, "rows": sorted(rows, key=lambda r: (r["scope"], r["name"]))}
    if baseline is not None:
        report["inventory"] = baseline
    return report


def render_csv(report: dict) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    baseline = report.get("inventory")
    fields = (*FIELDS, "inventory_coverage") if baseline is not None else FIELDS
    metadata_fields = (*INVENTORY_FIELDS, "inventory_notice") if baseline is not None else ()
    metadata = (*(baseline[field] for field in INVENTORY_FIELDS), INVENTORY_NOTICE) if baseline is not None else ()
    writer.writerow(("report_state", "mode", "source_revision", *fields, "scope_total", "status_count", *metadata_fields))
    prefix = ("available" if report["available"] else "unavailable", report["mode"], report["source_revision"])
    totals = Counter(row["scope"] for row in report["rows"])
    counts = Counter((row["scope"], row["status"]) for row in report["rows"])
    for row in report["rows"]:
        writer.writerow((*prefix, *(row[field] for field in fields),
                         totals[row["scope"]], counts[row["scope"], row["status"]], *metadata))
    if not report["rows"]:
        writer.writerow((*prefix, *("" for _ in fields), "", "", *metadata))
    return output.getvalue()


def render_html(report: dict) -> str:
    def text(value):
        return escape(str(value), quote=True)

    heading = "Catalog public report" if report["available"] else "Catalog public report unavailable"
    body = []
    baseline = report.get("inventory")
    fields = (*FIELDS, "inventory_coverage") if baseline is not None else FIELDS
    if baseline is not None:
        body.append("<h2>Inventory coverage differences</h2><p>Inventory baseline SHA: <code>" +
                    text(baseline["inventory_baseline_sha"]) + "</code></p><p>Expected inventory: " +
                    text(baseline["inventory_expected_count"]) + "; Ledger rows: " + text(baseline["ledger_total"]) +
                    "; Missing expected inventory names from ledger: " + text(baseline["inventory_missing_count"]) +
                    "; Unexpected ledger names outside baseline: " + text(baseline["inventory_unexpected_count"]) +
                    "</p><p>" + text(INVENTORY_NOTICE) + "</p>")
    for scope in ("agent", "kit_static", "kit_e2e"):
        selected = [r for r in report["rows"] if r["scope"] == scope]
        if selected:
            counts = Counter(r["status"] for r in selected)
            body.append("<h2>" + text(scope) + "</h2><p>Total: " + text(len(selected)) + "; " +
                        text("; ".join(f"{key}: {counts[key]}" for key in GRADES)) + "</p>")
    body.append("<table><caption>Allowlisted status rows</caption><thead><tr>" +
                "".join("<th scope=\"col\">" + text(key) + "</th>" for key in fields) + "</tr></thead><tbody>")
    for row in report["rows"]:
            body.append("<tr>" + "".join("<td>" + text(row[field]) + "</td>" for field in fields) + "</tr>")
    body.append("</tbody></table>")
    if not report["rows"]:
        body.append("<p>No validated rows available. This is not a successful validation result.</p>")
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">"
            "<title>" + text(heading) + "</title><style>"
            "body{font:16px system-ui,sans-serif;line-height:1.5;margin:2rem;color:#17233b;background:#f6f8fc}"
            "main{max-width:1200px;margin:auto}h1,h2{color:#164d82}table{border-collapse:collapse;width:100%;background:white}"
            "th,td{padding:.65rem;text-align:left;border:1px solid #cbd5e1;overflow-wrap:anywhere}"
            "th{background:#e5edf7}caption{text-align:left;font-weight:bold;padding:.5rem}"
            "code{overflow-wrap:anywhere}@media print{body{margin:0;background:white}}"
            "</style></head><body><main><h1>" + text(heading) + "</h1><p>" + text(NOTICE) +
            "</p><p>Mode: " + text(report["mode"]) + "</p><p>Source revision (checkout, not evidence attestation): <code>" +
            text(report["source_revision"] or "Not provided") + "</code></p>"
            "<p>Search with your browser's Find command. This standalone file makes no network requests.</p>" +
            "".join(body) + "</main></body></html>\n")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def read_document(source: Path) -> object:
    if source.is_symlink() or source.stat().st_size > MAX_BYTES:
        raise ValueError("Invalid input file")
    return json.loads(source.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)


def export(source: Path, output_dir: Path, kind: str, source_revision: str | None = None,
           inventory_json: Path | None = None) -> int:
    """Invalid/missing inputs replace stale reports with a fixed, non-sensitive failure."""
    code = 0
    try:
        if inventory_json is not None and kind != "catalog":
            raise ValueError("Inventory is catalog-only")
        document = read_document(source)
        inventory = object_value(read_document(inventory_json)) if inventory_json is not None else None
        report = project(document, kind, source_revision, inventory)
    except (OSError, ValueError, TypeError, RecursionError):
        report = {"mode": "unknown", "source_revision": "", "available": False, "rows": []}
        code = 1
    output_dir.mkdir(parents=True, exist_ok=True)
    if output_dir.is_symlink() or any((output_dir / name).is_symlink() for name in FILES):
        raise ValueError("Invalid output directory")
    for name, content in zip(FILES, (render_html(report), render_csv(report))):
        (output_dir / name).write_text(content, encoding="utf-8", newline="")
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--ledger", type=Path)
    sources.add_argument("--kit-report", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-revision")
    parser.add_argument("--inventory-json", type=Path, help="Catalog-only pinned inventory baseline (not latest or source parity proof)")
    args = parser.parse_args(argv)
    try:
        result = export(args.ledger or args.kit_report, args.output_dir,
                        "catalog" if args.ledger else "kit", args.source_revision, args.inventory_json)
    except (OSError, ValueError):
        result = 1
    # Never echo paths, arbitrary exceptions, JSON content or credentials.
    print("Public report ready." if result == 0 else "Public report unavailable; input or output validation failed.",
          file=sys.stderr if result else sys.stdout)
    return result


if __name__ == "__main__":
    raise SystemExit(main())