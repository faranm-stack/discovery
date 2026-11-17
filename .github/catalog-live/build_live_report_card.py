#!/usr/bin/env python3
"""Build a report card (Markdown + JSON) from Studio-turn evidence written by Start-PilotRun.ps1.

A turn whose response completed is "completed", not "passed": a reviewer must read the final message
and tool output. The report shows both so nobody mistakes completion for scientific correctness.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--evidence", required=True, type=Path)
    p.add_argument("--pilot", required=True, type=Path)
    p.add_argument("--out", default="report-card", type=Path)
    p.add_argument("--only", default="", help="report only this agent")
    p.add_argument("--price-in", type=float, default=None, help="USD per 1M input tokens")
    p.add_argument("--price-out", type=float, default=None, help="USD per 1M output tokens")
    a = p.parse_args()

    rows = []
    ledger = a.evidence / "ledger.csv"
    ledger_lines = ledger.read_text(encoding="utf-8").splitlines() if ledger.exists() else []
    for r in csv.DictReader(a.pilot.open(encoding="utf-8")):
        if a.only and r["agent"] != a.only:
            continue
        d = a.evidence / r["label"]
        summary = next(d.rglob("summary.txt"), None) if d.exists() else None
        text = summary.read_text(encoding="utf-8", errors="replace") if summary else ""
        exit_code, wall = None, 0
        for line in ledger_lines:
            if f",{r['label']}," in line:
                exit_code = line.rsplit("exit=", 1)[-1]
                m = re.search(r"secs=(\d+)", line)
                wall = int(m.group(1)) if m else 0
        if not d.exists():
            status = "not_run"
        elif exit_code == "0" and text:
            status = "completed_needs_review"
        else:
            status = "failed"
        u = re.search(r"usage input=(\d+) output=(\d+)", text)
        tin, tout = (int(u.group(1)), int(u.group(2))) if u else (0, 0)
        compute = sum(float(s) for s in re.findall(r"running for ([\d.]+) seconds", text))
        rows.append({"agent": r["agent"], "label": r["label"], "status": status, "exit": exit_code,
                     "input_tokens": tin, "output_tokens": tout, "tool_compute_s": round(compute, 1),
                     "wall_s": wall, "excerpt": text[-600:].strip()})

    def cost(x):
        if a.price_in is None or a.price_out is None:
            return None
        return round(x["input_tokens"] / 1e6 * a.price_in + x["output_tokens"] / 1e6 * a.price_out, 4)

    for x in rows:
        x["model_cost_usd"] = cost(x)
    tot = {k: sum(x[k] for x in rows) for k in ("input_tokens", "output_tokens", "tool_compute_s", "wall_s")}
    tot["model_cost_usd"] = None if a.price_in is None else round(sum(x["model_cost_usd"] for x in rows), 4)

    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "report-card.json").write_text(json.dumps({"totals": tot, "agents": rows}, indent=1), encoding="utf-8")
    counts = {s: sum(x["status"] == s for x in rows) for s in ("completed_needs_review", "failed", "not_run")}
    money = lambda v: "-" if v is None else f"${v:.4f}"
    md = ["# Catalog live report card", "", f"Counts: {counts}", "",
          "| Agent | Status | Exit | Input tokens | Output tokens | Tool compute (s) | Turn time (s) | Model cost |",
          "|---|---|---|---:|---:|---:|---:|---:|"]
    md += [f"| {x['agent']} | {x['status']} | {x['exit']} | {x['input_tokens']:,} | {x['output_tokens']:,} | "
           f"{x['tool_compute_s']} | {x['wall_s']} | {money(x['model_cost_usd'])} |" for x in rows]
    md += [f"| **Total** | | | **{tot['input_tokens']:,}** | **{tot['output_tokens']:,}** | "
           f"**{round(tot['tool_compute_s'], 1)}** | **{tot['wall_s']}** | **{money(tot['model_cost_usd'])}** |"]
    md += ["", "Tokens come from the Studio response. Tool compute is container run time reported by each tool call. "
           "Model cost is shown only when MODEL_PRICE_IN/OUT (USD per 1M tokens) are set. "
           "Completed means the Studio response finished. Read each excerpt before calling it a pass.", ""]
    for x in rows:
        md += [f"## {x['agent']}", "```", x["excerpt"] or "(no summary)", "```", ""]
    (a.out / "report-card.md").write_text("\n".join(md), encoding="utf-8")
    print("\n".join(md[:6 + len(rows)]))
    return 0 if counts["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
