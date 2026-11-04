"""Upstream inventory gate and evidence-backed per-run continuation memory.

No deployments, invocations, credentials, or automatic application of fixes.
GitHub reads are pinned to one commit; unavailable inventory fails closed.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.request import Request, urlopen

from catalog_sweep import save_json

API = "https://api.github.com/repos/microsoft/discovery"


def get_json(url):
    request = Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "discovery-catalog-inventory"})
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def upstream_inventory(fetch=get_json):
    commit = fetch(f"{API}/commits/main")["sha"]
    entries = fetch(f"{API}/contents/agents?ref={commit}")
    names = sorted(entry["name"] for entry in entries if entry["type"] == "dir")
    if not names or len(names) != len(set(names)):
        raise ValueError("Invalid upstream agent inventory")
    return {"repository": "microsoft/discovery", "ref": "main", "commit": commit,
            "checked_at": datetime.now(timezone.utc).isoformat(), "agents": names, "count": len(names)}


def compare_inventory(inventory, planned):
    upstream, local = set(inventory["agents"]), set(planned)
    return {"upstream_commit": inventory["commit"], "upstream_count": len(upstream),
            "planned_count": len(local), "missing": sorted(upstream - local),
            "extra": sorted(local - upstream), "exact_inventory_match": upstream == local,
            "source_content_verified": False}


def require_current_inventory(out, planned, fetch=get_json):
    """Run before builds/uploads/deployment; retain a report even on failure."""
    try:
        inventory = upstream_inventory(fetch)
        comparison = compare_inventory(inventory, planned)
    except Exception as exc:
        save_json(out / "inventory-gate.json", {"status": "blocked", "reason": "upstream_inventory_unavailable",
                                                "error_type": type(exc).__name__})
        (out / "preflight-report.md").write_text(
            "# Run blocked before execution\n\nUpstream inventory could not be verified. No coverage claim or live submission is permitted.\n",
            encoding="utf-8")
        raise ValueError("Upstream inventory unavailable; no new run permitted") from exc
    save_json(out / "upstream-inventory.json", inventory)
    save_json(out / "inventory-gate.json", comparison)
    lines = ["# Catalog inventory preflight", "", f"Upstream commit: {inventory['commit']}",
             f"Upstream: {comparison['upstream_count']}; planned: {comparison['planned_count']}.",
             "", "Missing: " + (", ".join(comparison["missing"]) or "none"),
             "Extra: " + (", ".join(comparison["extra"]) or "none"), "",
             "An exact name match does not prove source-content parity. Preserve source hashes and disclose overlays."]
    (out / "preflight-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if not comparison["exact_inventory_match"]:
        raise ValueError("Catalog inventory differs from pinned upstream; synchronize an isolated snapshot before starting")
    return comparison


def write_run_memory(out, ledger, card):
    """Derived memory references this run only; it is not executable instruction."""
    evidence = out / "live"
    actions = []
    for agent in card["agents"]:
        name = agent["agent"]
        case_dir = evidence / name
        summary = {}
        if (case_dir / "summary.json").exists():
            summary = json.loads((case_dir / "summary.json").read_text(encoding="utf-8"))
        actions.append({"agent": name, "status": agent["status"], "grade": agent["grade"],
            "category": agent["category"], "reason": agent["reason"],
            "owner": agent["fix_owner"], "proposed_action": agent["suggested_fix"],
            "response_id": summary.get("response_id"), "conversation_id": summary.get("conversation_id"),
            "summary_path": str(case_dir / "summary.json") if summary else None,
            "verification": "One fresh approved attempt with correlated own-tool result; no historical pass carry-forward",
            "automatic_retry_authorized": False})
    inventory = json.loads((out / "inventory-gate.json").read_text()) if (out / "inventory-gate.json").exists() else {
        "status": "not_verified_at_launch", "full_current_catalog_claim_allowed": False}
    memory = {"run_id": out.name, "report_mode": ledger.get("mode", "unknown"),
              "generated_at": datetime.now(timezone.utc).isoformat(),
              "inventory": inventory, "counts": ledger["counts"], "agents_accounted": ledger["total"],
              "submitted_responses": ledger["submitted_responses"], "grades": card["totals"]["grades"],
              "evidence_root": str(out), "actions": actions,
              "rules": ["Read before next run; proposals require verification, not blind execution",
                        "Do not count blocked/not-run/uncertain or missing inputs as proven author breakage",
                        "Do not expand an active frozen ledger or retry its claimed cases",
                        "Preserve approvals, confirmations, credentials, budget and resource boundaries"]}
    save_json(out / "next-run-memory.json", memory)
    lines = [f"# Next-run handoff: {out.name}", "", f"Report mode: {ledger.get('mode', 'unknown')}. Counts are a point-in-time checkpoint, not a completion claim.",
             f"Agents accounted: {ledger['total']}; responses submitted: {ledger['submitted_responses']}.",
             "Prior evidence is diagnostic context, not a pass for the next run. Do not retry active cases.", "",
             "## Required next-run checks", "",
             "1. Resolve upstream main to an exact commit and compare every agent with the proposed snapshot.",
             "2. Acquire missing sources in an isolated snapshot; preserve hashes and explicitly record repair overlays.",
             "3. Read the prior report, operation evidence, unresolved remote work and owner actions below.",
             "4. Verify each proposed fix and its deployed image/tool/agent receipt; source edits alone are not runtime proof.",
             "5. Freeze a new ledger, preserve permission and resource limits, then submit only the approved new attempts.",
             "6. Publish the report even if blocked/interrupted; record missing coverage and unknown token usage.", "",
             "## Unresolved actions", "", "| Agent | Status/category | Owner | Proposed action | Evidence |",
             "|---|---|---|---|---|"]
    for a in actions:
        if a["grade"] in {"A", "B"}:
            continue
        cells = [a["agent"], f"{a['status']}/{a['category']}", a["owner"], a["proposed_action"], a["summary_path"] or "No response submitted; inspect case checkpoint"]
        lines.append("| " + " | ".join(str(c or "Unknown").replace("|", "/").replace("\n", " ") for c in cells) + " |")
    (out / "next-run-handoff.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return memory