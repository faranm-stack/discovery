"""Generate separate private and public HTML reports offline from a finished catalog ledger.

Use --ledger, --private-dir and --public-dir; only the public directory is shareable.
Optional --inventory-json is a pinned {commit, count, agents} name comparison, not run evidence.
The only inputs read are these JSON files. No evidence discovery, attribution, or pricing.
Metrics are per-row usage.{input,output,total}_tokens and cost_usd (or cost.total_cost_usd).
Missing is not zero. Use a dedicated public directory and a caller-restricted private directory.
"""

from __future__ import annotations

import argparse
from decimal import Decimal
import json
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile

from catalog_public_report import page, project, render_public, table


PUBLIC_HTML = "catalog-public-report.html"
PRIVATE_HTML = "catalog-private-report.html"
MAX_BYTES = 16 * 1024 * 1024
UNKNOWN = "Unknown"


def _read(path):
    def unique(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError("Duplicate JSON key")
            obj[key] = value
        return obj

    def reject_constant(_):
        raise ValueError("Non-finite number")

    if path.is_symlink() or not path.is_file():
        raise ValueError("Expected a regular input file")
    with path.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("Input too large")
    return json.loads(data.decode("utf-8"), object_pairs_hook=unique, parse_float=Decimal, parse_constant=reject_constant)


def _unknown(value):
    return value is None or isinstance(value, str) and value.casefold() == "unknown"


def _object(value):
    if _unknown(value):
        return {}
    if not isinstance(value, dict):
        raise ValueError("Expected a ledger object")
    return value


def _text(value):
    if _unknown(value) or value == "":
        return UNKNOWN
    if not isinstance(value, str):
        raise ValueError("Expected a text field")
    return value  # table() escapes every cell, including reasons and identifiers.


def _metric(value, *, money=False):
    if _unknown(value):
        return None
    if (type(value) is not int and not (money and isinstance(value, Decimal) and value.is_finite())) or value < 0:
        raise ValueError("Invalid reported metric")
    return value


def _total(values):
    known = [v for v in values if v is not None]
    return str(sum(known)) if len(known) == len(values) else f"Unknown (reported for {len(known)}/{len(values)} rows)"


def _operations(row, assessment):
    extra = row.get("operation_ids")
    if not _unknown(extra) and not isinstance(extra, list):
        raise ValueError("Invalid operation IDs")
    ids = [row.get("operation_id"), assessment.get("operation_id"), *(extra if isinstance(extra, list) else [])]
    return ", ".join(dict.fromkeys(_text(v) for v in ids if not _unknown(v))) or UNKNOWN


def _private(ledger, report):
    details = []
    tokens, costs = [], []
    for row in sorted(ledger["results"], key=lambda r: r["catalog_agent"]):
        name = row["catalog_agent"]
        status, grade, _ = report["rows"][name]
        usage = _object(row.get("usage"))
        input_tokens, output_tokens, token = (_metric(usage.get(k)) for k in ("input_tokens", "output_tokens", "total_tokens"))
        cost = _metric(row["cost_usd"] if "cost_usd" in row else _object(row.get("cost")).get("total_cost_usd"), money=True)
        assessment = _object(row.get("assessment"))
        tokens.append(token)
        costs.append(cost)
        details.append((name, status, grade, _text(row.get("reason")), _text(row.get("project", ledger.get("project"))),
                        _text(row.get("investigation")), _text(row.get("conversation_id")), _text(row.get("response_id")),
                        _operations(row, assessment), _text(row.get("call_id", assessment.get("call_id"))),
                        *(UNKNOWN if v is None else v for v in (input_tokens, output_tokens, token, cost))))

    body = (f"<p>Private diagnostics. Mode: {report['mode']}. Ledger rows: {len(details)}. "
            "A plan is not a live run. A/B: tool roundtrip; C: answer-only, not a tool pass.</p>"
            "<p>Studio locator: open the recorded project, then find the investigation by its resource name. "
            "Missing IDs are not reconstructed.</p>"
            f"<p>Total tokens: {_total(tokens)}. Reported cost (USD), all rows: {_total(costs)}. "
            "Only supplied values are shown; this is not a billing estimate.</p>")
    return page("Catalog private report", body + table(
        ("Catalog agent", "Status", "Grade", "Ledger reason", "Studio project", "Investigation name", "Conversation ID",
         "Response ID", "Operation IDs", "Call ID", "Input tokens", "Output tokens", "Reported total tokens",
         "Reported cost (USD)"), details))


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = NamedTemporaryFile(dir=path.parent, delete=False)
    temporary = Path(stream.name)
    try:
        with stream:
            stream.write(content.encode("utf-8"))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def export_reports(ledger_path: Path, *, public_dir: Path, private_dir: Path, inventory_json: Path | None = None) -> None:
    """Replace only the two named reports. Bad input leaves neither report, never a previous success."""
    public_dir, private_dir = Path(public_dir).resolve(), Path(private_dir).resolve()
    ledger_path = Path(ledger_path)
    inputs = [ledger_path] if inventory_json is None else [ledger_path, Path(inventory_json)]
    public, private = public_dir / PUBLIC_HTML, private_dir / PRIVATE_HTML
    if (public_dir.is_relative_to(private_dir) or private_dir.is_relative_to(public_dir)
            or any(p.resolve() in (public, private) for p in inputs)
            or any(p.is_symlink() or (p.exists() and not p.is_file()) for p in (public, private))):
        raise ValueError("Unsafe report destinations")

    # Invalidate old files before parsing, including on missing input. Never write through an existing link.
    public.unlink(missing_ok=True)
    private.unlink(missing_ok=True)
    if any(p.resolve().is_relative_to(public_dir) for p in inputs) or (public_dir.exists() and any(public_dir.iterdir())):
        raise ValueError("Use a dedicated public directory")
    try:
        ledger = _read(ledger_path)
        inventory = _read(inputs[1]) if inventory_json is not None else None
        if inventory_json is not None and inventory is None:
            raise ValueError("Expected an inventory object")
        report = project(ledger, inventory)
        private_html, public_html = _private(ledger, report), render_public(report)
        _write(private, private_html)
        _write(public, public_html)  # Public last, after validation of both views.
    except (OSError, ValueError, ArithmeticError, RecursionError):
        public.unlink(missing_ok=True)
        private.unlink(missing_ok=True)
        raise ValueError("Report generation failed") from None


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default diagnostic repeats arbitrary arguments (possibly private text).
        self.exit(2, "Invalid report arguments. Use --help.\n")


def main(argv=None):
    parser = _Parser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--public-dir", required=True, type=Path)
    parser.add_argument("--private-dir", required=True, type=Path)
    parser.add_argument("--inventory-json", type=Path)
    args = parser.parse_args(argv)
    try:
        export_reports(args.ledger, public_dir=args.public_dir, private_dir=args.private_dir, inventory_json=args.inventory_json)
    except (OSError, ValueError, RuntimeError):
        print("Reports unavailable; check the input and output destinations.", file=sys.stderr)
        return 1
    print("Reports ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
