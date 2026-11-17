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
    # Defaults: Azure retail list prices, UK South, Global Standard gpt-5.4 and the lab's node pool VM sizes.
    p.add_argument("--model", default="gpt-5.4")
    p.add_argument("--price-in", type=float, default=2.50, help="USD per 1M input tokens")
    p.add_argument("--price-cached", type=float, default=0.25, help="USD per 1M cached input tokens")
    p.add_argument("--price-out", type=float, default=15.00, help="USD per 1M output tokens")
    p.add_argument("--node-rates", default="nodepool1=0.222,d4sv6=0.233,h100=8.725",
                   help="node pool name=USD per hour, comma separated")
    p.add_argument("--default-rate", type=float, default=0.233, help="USD per hour for an unrecognized node pool")
    p.add_argument("--studio-url", default="", help="Studio workspace URL to link traces to")
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
        u = re.search(r"usage input=(\d+) output=(\d+)(?: cached=(\d+))?", text)
        tin, tout, tcache = (int(u.group(1)), int(u.group(2)), int(u.group(3) or 0)) if u else (0, 0, 0)
        s = re.search(r"studio project=(\S+) investigation=(\S+)", text)
        # Attribute each tool's run time to the node pool named in the preceding tool call.
        pool_secs: dict[str, float] = {}
        pool = "unknown"
        for line in text.splitlines():
            m = re.search(r'nodePool = "?[^"]*/node[Pp]ools/([A-Za-z0-9-]+)', line)
            if m:
                pool = m.group(1)
            m = re.search(r"running for ([\d.]+) seconds", line)
            if m:
                pool_secs[pool] = pool_secs.get(pool, 0.0) + float(m.group(1))
        rows.append({"agent": r["agent"], "status": status, "exit": exit_code,
                     "input_tokens": tin, "cached_tokens": tcache, "output_tokens": tout,
                     "tool_compute_s": round(sum(pool_secs.values()), 1), "node_pools": sorted(pool_secs),
                     "pool_secs": pool_secs, "wall_s": wall,
                     "studio_project": s.group(1) if s else "", "studio_investigation": s.group(2) if s else ""})

    rates = dict(kv.split("=", 1) for kv in a.node_rates.split(",") if "=" in kv)
    rates = {k.strip(): float(v) for k, v in rates.items()}
    for x in rows:
        fresh = max(x["input_tokens"] - x["cached_tokens"], 0)
        x["model_cost_usd"] = round(fresh / 1e6 * a.price_in + x["cached_tokens"] / 1e6 * a.price_cached
                                    + x["output_tokens"] / 1e6 * a.price_out, 4)
        x["compute_cost_usd"] = round(sum(sec / 3600 * rates.get(p, a.default_rate)
                                          for p, sec in x.pop("pool_secs").items()), 4)
        x["total_cost_usd"] = round(x["model_cost_usd"] + x["compute_cost_usd"], 4)
    keys = ("input_tokens", "cached_tokens", "output_tokens", "tool_compute_s", "wall_s",
            "model_cost_usd", "compute_cost_usd", "total_cost_usd")
    tot = {k: round(sum(x[k] for x in rows), 4) for k in keys}

    studio = f"{a.studio_url.rstrip('/')}" if a.studio_url else ""
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "report-card.json").write_text(json.dumps(
        {"prices": {"model": a.model, "usd_per_1m_input": a.price_in, "usd_per_1m_cached_input": a.price_cached,
                    "usd_per_1m_output": a.price_out, "usd_per_node_hour": rates},
         "totals": tot, "agents": rows}, indent=1), encoding="utf-8")
    counts = {s: sum(x["status"] == s for x in rows) for s in ("completed_needs_review", "failed", "not_run")}
    usd = lambda v: f"${v:.4f}"
    trace = lambda x: (f"[{x['studio_investigation']}]({studio})" if studio and x["studio_investigation"]
                       else x["studio_investigation"] or "-")
    md = ["# Catalog live report card", "", f"Counts: {counts}", "",
          "| Agent | Status | Input tokens | Output tokens | Tool compute (s) | Node pool | Turn time (s) "
          "| Model $ | Compute $ | Total $ | Studio trace |",
          "|---|---|---:|---:|---:|---|---:|---:|---:|---:|---|"]
    md += [f"| {x['agent']} | {x['status']} | {x['input_tokens']:,} | {x['output_tokens']:,} | {x['tool_compute_s']} | "
           f"{', '.join(x['node_pools']) or '-'} | {x['wall_s']} | {usd(x['model_cost_usd'])} | "
           f"{usd(x['compute_cost_usd'])} | {usd(x['total_cost_usd'])} | {trace(x)} |" for x in rows]
    md += [f"| **Total** | | **{tot['input_tokens']:,}** | **{tot['output_tokens']:,}** | **{round(tot['tool_compute_s'], 1)}** "
           f"| | **{tot['wall_s']}** | **{usd(tot['model_cost_usd'])}** | **{usd(tot['compute_cost_usd'])}** "
           f"| **{usd(tot['total_cost_usd'])}** | |"]
    md += ["", f"Prices (Azure list, USD): {a.model} ${a.price_in}/1M input, ${a.price_cached}/1M cached input, "
           f"${a.price_out}/1M output; node pools {rates} per hour. Estimates from measured usage, not an Azure bill. "
           "Tool compute counts container run time only, not idle nodes.",
           "",
           "Completed means the Studio response finished, not that the answer is correct. "
           "Agent output is not published here; open the Studio trace (project shown in the investigation) to review it.", ""]
    (a.out / "report-card.md").write_text("\n".join(md), encoding="utf-8")
    print("\n".join(md))
    return 0 if counts["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
