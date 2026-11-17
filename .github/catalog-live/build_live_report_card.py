#!/usr/bin/env python3
"""Build a report card (Markdown + JSON) from Studio-turn evidence written by Start-PilotRun.ps1.

A turn whose response completed is "completed", not "passed": a reviewer must read the final message
and tool output. The report shows both so nobody mistakes completion for scientific correctness.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--evidence", required=True, type=Path)
    p.add_argument("--pilot", required=True, type=Path)
    p.add_argument("--out", default="report-card", type=Path)
    p.add_argument("--only", default="", help="report only this agent")
    a = p.parse_args()

    rows = []
    for r in csv.DictReader(a.pilot.open(encoding="utf-8")):
        if a.only and r["agent"] != a.only:
            continue
        d = a.evidence / r["label"]
        summary = next(d.rglob("summary.txt"), None) if d.exists() else None
        text = summary.read_text(encoding="utf-8", errors="replace") if summary else ""
        exit_code = None
        ledger = a.evidence / "ledger.csv"
        if ledger.exists():
            for line in ledger.read_text(encoding="utf-8").splitlines():
                if f",{r['label']}," in line or (line.count(",") == 2 and f",{r['agent']}," in line):
                    exit_code = line.rsplit("exit=", 1)[-1]
        if not d.exists():
            status = "not_run"
        elif exit_code == "0" and text:
            status = "completed_needs_review"
        else:
            status = "failed"
        rows.append({"agent": r["agent"], "label": r["label"], "status": status, "exit": exit_code,
                     "excerpt": text[-600:].strip()})

    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "report-card.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    counts = {s: sum(x["status"] == s for x in rows) for s in ("completed_needs_review", "failed", "not_run")}
    md = ["# Catalog live report card", "", f"Counts: {counts}", "",
          "| Agent | Status | Exit |", "|---|---|---|"]
    md += [f"| {x['agent']} | {x['status']} | {x['exit']} |" for x in rows]
    md += ["", "Completed means the Studio response finished. Read each excerpt before calling it a pass.", ""]
    for x in rows:
        md += [f"## {x['agent']}", "```", x["excerpt"] or "(no summary)", "```", ""]
    (a.out / "report-card.md").write_text("\n".join(md), encoding="utf-8")
    print("\n".join(md[:6 + len(rows)]))
    return 0 if counts["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
