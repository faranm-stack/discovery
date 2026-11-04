"""Build an all-agent report card from a catalog sweep ledger.

Offline only: reads a finished ledger (catalog-results.json or the merged
catalog-all95-report.json), agent metadata and git history. It never contacts
Discovery and never sends messages; author notices are written as drafts.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

SCHEMA_VERSION = 1
DEFAULT_NOTIFY_TO = "faranm-stack"

# (category, who fixes it, matching reason fragments, suggested fix)
CATEGORIES = [
    ("credentials_missing", "platform",
     ("credentials_missing",),
     "Configure the required API access through an approved secret mechanism; never put credentials in the prompt."),
    ("license_missing", "platform",
     ("license_missing",),
     "The tool needs a valid license configured by its owner; do not bypass license checks."),
    ("input_not_staged", "harness",
     ("input_not_staged", "input_missing_reported"),
     "The test did not attach the task's input file, and the agent correctly stopped. Stage the file and map it in the input-assets file."),
    ("platform_blocked", "platform",
     ("resource_lane_not_approved", "existing_agent_differs", "not overwritten"),
     "Approve the connector, knowledge base or resource lane, or reconcile the deployed agent with the catalog."),
    ("load_failed", "author",
     ("agent_load_failed",),
        "Discovery reported a failed agent load. Inspect provisioning errors and bindings; a failed load alone does not establish a schema defect."),
    ("invalid_arguments", "author",
     ("tool_arguments_invalid",),
     "Agent passed arguments the service rejected (for example a non-Discovery nodePool). Tighten the instructions for resource selection."),
    ("tool_not_called", "author",
     ("own_tool_not_called",),
     "Agent answered without calling its own tool. Make the instructions require the tool for its documented first task."),
    ("tool_failed", "author",
     ("own_tool_operations_failed", "correlated tool operation failed"),
     "Tool job ran and failed. Check the container image, inputs and runtime dependencies in the tool logs."),
]
RETRY_MARKER = "after agent retries"
# An agent that stops because its input is absent is behaving correctly; that is a harness gap.
INPUT_BLOCKER = re.compile(
    r"(blocker|blocked|cannot (?:run|proceed)|could not (?:run|find)|can.t (?:run|find))[\s\S]{0,400}?"
    r"(not (?:available|present|found)|no (?:input|valid|workspace|project|discovery://|pdb|verilog|csv)|missing|upload)",
    re.IGNORECASE)


def reports_missing_input(text: str) -> bool:
    return bool(INPUT_BLOCKER.search(text or ""))


def final_message(response: dict) -> str:
    parts = []
    for item in response.get("output") or []:
        if item.get("type") == "message":
            parts += [c.get("text") or "" for c in item.get("content") or []]
    return "\n".join(parts)


def refine_with_evidence(row: dict, evidence_root: Path | None) -> dict:
    """Refine attribution using only a matching response and its linked failed operations.

    Execution status is preserved. Missing fixtures or credentials are not proof
    of an author defect and must not contribute to eviction streaks.
    """
    if not evidence_root or row.get("status") == "passed" or not row.get("response_id"):
        return row
    for path in evidence_root.glob(f"*/live/{row['catalog_agent']}/response.json"):
        try:
            response = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if response.get("id") != row["response_id"]:
            continue
        if "own_tool_not_called" in str(row.get("reason") or "") and reports_missing_input(final_message(response)):
            return {**row, "reason": "input_missing_reported_by_agent (own tool not called; " + str(row["reason"]) + ")"}
        links = (response.get("discoveryProperties") or {}).get("itemExtensions") or {}
        operation_ids = {v.get("supercomputerOperationId") for v in links.values() if isinstance(v, dict)} - {None}
        # Reconciliation snapshots can contain terminal logs absent from the
        # original operation receipt. Never use another response's operations.
        operation_paths = [path.with_name("operations.json"), *sorted(path.parent.glob("reconciliation/*/operations.json"))]
        for operation_path in operation_paths:
            try:
                operations = json.loads(operation_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for operation_id in operation_ids:
                operation = operations.get(operation_id) or {}
                if operation.get("id") != operation_id or operation.get("status") != "Failed":
                    continue
                logs = str(((operation.get("result") or {}).get("toolReport") or {}).get("logs") or "")
                category = None
                if re.search(r"API key required|HTTP 401 Unauthorized", logs, re.I):
                    category = "credentials_missing"
                elif "Invalid license key" in logs:
                    category = "license_missing"
                elif re.search(r"no (?:input|fasta|name|alignment|MS/MS) files found|no files found in |"
                               r"no FASTA file found|No \.pdb/\.cif/\.mmcif structure found|dftb_in\.hsd not found", logs, re.I):
                    category = "input_not_staged"
                if category:
                    return {**row, "original_reason": row.get("reason"),
                            "reason": f"{category}: verified in correlated failed operation {operation_id}",
                            "evidence_path": str(operation_path.relative_to(evidence_root))}
    return row


def classify(row: dict) -> dict:
    status = row.get("status")
    reason = str(row.get("reason") or "")
    lowered = reason.lower()
    if status == "passed":
        retried = RETRY_MARKER in lowered
        return {"grade": "B" if retried else "A", "category": "passed_after_retries" if retried else "passed",
                "fix_owner": None, "suggested_fix": None}
    for category, owner, needles, fix in CATEGORIES:
        if any(n in lowered for n in needles):
            grade = "F" if owner == "author" else "I"
            return {"grade": grade, "category": category, "fix_owner": owner, "suggested_fix": fix}
    if status in ("blocked", "not_run", "uncertain"):
        return {"grade": "I", "category": status, "fix_owner": "harness",
                "suggested_fix": "No verdict yet. Reconcile or rerun before judging the agent."}
    return {"grade": "F", "category": "unclassified_failure", "fix_owner": "triage",
            "suggested_fix": "Inspect the run evidence; the failure reason did not match a known category."}


def read_owner(repo: Path, agent: str) -> dict:
    owner = {"publisher": None, "contact": None, "party": None, "original_author": None,
             "original_author_email": None, "original_commit": None, "github_login": None,
             "last_author": None, "last_commit": None}
    agent_dir = repo / "agents" / agent
    meta = agent_dir / "metadata.yaml"
    if meta.is_file():
        data = yaml.safe_load(meta.read_text(encoding="utf-8")) or {}
        publisher = data.get("publisher") or {}
        owner.update(publisher=publisher.get("name"), contact=publisher.get("contact"), party=publisher.get("party"))
    if agent_dir.is_dir():
        try:
            out = subprocess.run(["git", "log", "--format=%an%x1f%ae%x1f%h", "--", str(agent_dir)],
                                 cwd=repo, capture_output=True, text=True, timeout=30, check=False).stdout
        except (OSError, subprocess.TimeoutExpired):
            out = ""
        humans = [c for c in ((line.split("\x1f") + ["", "", ""])[:3] for line in out.splitlines() if line)
                  if not _is_bot(c[0], c[1])]
        if humans:
            last, original = humans[0], humans[-1]  # git log is newest first
            owner.update(last_author=last[0], last_commit=last[2],
                         original_author=original[0], original_author_email=original[1], original_commit=original[2])
            match = re.match(r"^(?:\d+\+)?([^@]+)@users\.noreply\.github\.com$", original[1])
            if match:
                owner["github_login"] = match.group(1)
    return owner


def _is_bot(name: str, email: str) -> bool:
    text = f"{name} {email}".lower()
    return "[bot]" in text or "dependabot" in text or "github-actions" in text


def load_history(paths: list[Path], current_label: str | None = None) -> list[dict]:
    """Earlier report cards, oldest first. Unreadable files and a re-grade of the same run are skipped."""
    runs = {}
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            grades = {a["agent"]: a["grade"] for a in data["agents"]}
        except (OSError, ValueError, KeyError, TypeError):
            print(f"warning: skipped unreadable history file {path}", file=sys.stderr)
            continue
        label = data.get("run_label") or str(path)
        if label == current_label:
            continue  # Re-grading a run must not count that run twice.
        runs[label] = {"generated_at": data.get("generated_at", ""), "grades": grades}
    return sorted(runs.values(), key=lambda r: r["generated_at"])


def failing_streak(agent: str, grade: str, history: list[dict]) -> int:
    if grade != "F":
        return 0
    streak = 1
    for run in reversed(history):
        if run["grades"].get(agent) != "F":
            break
        streak += 1
    return streak


def lifecycle(streak: int, evict_after: int) -> str:
    if streak == 0:
        return "keep"
    if streak >= evict_after:
        return "evict_candidate"
    return "at_risk" if streak >= 2 else "watch"


def token_totals(agents: list[dict]) -> dict:
    """Keep the known sum separate from the nullable complete total; zero is reported usage."""
    reported = [a["tokens"] for a in agents if a.get("tokens") is not None]
    known_tokens = sum(reported)
    unknown = len(agents) - len(reported)
    return {"known_tokens": known_tokens, "usage_reported_agents": len(reported),
            "usage_unknown_agents": unknown, "tokens": known_tokens if not unknown else None}


def token_summary(card: dict) -> str:
    # Derive from rows so legacy cards and the workflow's minimal grader-failure
    # card do not need the new aggregate fields to render safely.
    usage = token_totals(card["agents"])
    coverage = "partial" if usage["usage_unknown_agents"] else "complete"
    total = "Unknown" if usage["tokens"] is None else str(usage["tokens"])
    return (f"Known token sum ({coverage}): {usage['known_tokens']}. Full token total: {total}. "
            f"Usage reported: {usage['usage_reported_agents']}/{len(card['agents'])} agents; "
            f"usage unknown: {usage['usage_unknown_agents']}.")


def build(ledger: dict, repo: Path, history: list[dict], evict_after: int, run_label: str,
          evidence_root: Path | None = None) -> dict:
    from catalog_resource_labels import resource_fields
    rows = ledger.get("results")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Ledger has no results to grade")
    names = [r.get("catalog_agent") for r in rows]
    if any(not isinstance(n, str) or not n for n in names):
        raise ValueError("Every ledger row needs a catalog_agent name")
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise ValueError(f"Duplicate agents in ledger: {', '.join(duplicates)}")
    agents = []
    for row in sorted(rows, key=lambda r: r["catalog_agent"]):
        name = row["catalog_agent"]
        row = refine_with_evidence(row, evidence_root)
        verdict = classify(row)
        streak = failing_streak(name, verdict["grade"], history)
        agents.append({
            "agent": name,
            "status": row.get("status"),
            "lane": row.get("lane"),
            **resource_fields(repo, name),
            **verdict,
            "reason": row.get("reason"),
            "original_reason": row.get("original_reason"),
            "evidence_path": row.get("evidence_path"),
            "session": row.get("investigation"),
            "tokens": (row.get("usage") or {}).get("total_tokens"),
            "elapsed_seconds": row.get("elapsed_seconds"),
            "failing_streak": streak,
            "lifecycle": lifecycle(streak, evict_after),
            "owner": read_owner(repo, name),
        })
    grades = Counter(a["grade"] for a in agents)
    judged = grades["A"] + grades["B"] + grades["F"]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_label": run_label,
        "evict_after_failing_runs": evict_after,
        "history_runs": len(history),
        "totals": {
            "agents": len(agents),
            "grades": dict(sorted(grades.items())),
            "categories": dict(sorted(Counter(a["category"] for a in agents).items())),
            "pass_rate_of_judged": round((grades["A"] + grades["B"]) / judged, 3) if judged else None,
            **token_totals(agents),
        },
        "agents": agents,
    }


GRADE_TEXT = {"A": "Roundtrip passed", "B": "Passed after agent retries", "F": "Roundtrip failed", "I": "Not judged"}


def render_markdown(card: dict) -> str:
    from catalog_resource_labels import RESOURCE_NOTE
    t = card["totals"]
    lines = [f"# Agent report card: {card['run_label']}", "",
             f"Generated {card['generated_at']}. Agents graded: **{t['agents']}**. "
             f"Pass rate of judged agents: **{(t['pass_rate_of_judged'] or 0):.0%}**.", "",
             token_summary(card), "",
             "| Grade | Meaning | Agents |", "|---|---|---:|"]
    for grade in ("A", "B", "F", "I"):
        lines.append(f"| {grade} | {GRADE_TEXT[grade]} | {t['grades'].get(grade, 0)} |")
    lines += ["", "| Failure category | Agents |", "|---|---:|"]
    lines += [f"| {k} | {v} |" for k, v in t["categories"].items() if not k.startswith("passed")]
    lines += ["", RESOURCE_NOTE, "",
              "Failure is not proof of author fault; pass is not proof of scientific correctness. Owner/lifecycle suggestions require review.", "",
              "| Agent | Grade | Declared resources | Category | Fix owner | Lifecycle | Contact | Reason | Tokens |",
              "|---|---|---|---|---|---|---|---|---:|"]
    for a in card["agents"]:
        o = a["owner"]
        contact = o["github_login"] and f"`{o['github_login']}`" or o["contact"] or ""
        reason = str(a["reason"] or "").replace("|", " ").replace("\n", " ")[:120]
        tokens = "Unknown" if a.get("tokens") is None else a["tokens"]
        lines.append(f"| {a['agent']} | {a['grade']} | {a.get('resource_label', 'Needs resource review')} | {a['category']} | {a['fix_owner'] or ''} | "
                 f"{a['lifecycle']} | {contact} | {reason} | {tokens} |")
    return "\n".join(lines) + "\n"


def render_html(card: dict) -> str:
    from catalog_resource_labels import RESOURCE_NOTE
    e = lambda v: html.escape(str(v if v is not None else ""))
    t = card["totals"]
    colors = {"A": "#0e6b46", "B": "#4f7d1f", "F": "#a93a3a", "I": "#8a5d00"}
    tiles = "".join(
        f'<div class="tile"><b style="color:{colors[g]}">{t["grades"].get(g, 0)}</b><span>{e(g)}: {e(GRADE_TEXT[g])}</span></div>'
        for g in ("A", "B", "F", "I"))
    rows = []
    for a in card["agents"]:
        o = a["owner"]
        contact = f"@{o['github_login']}" if o["github_login"] else (o["contact"] or "")
        tokens = "Unknown" if a.get("tokens") is None else a["tokens"]
        rows.append(
            f'<tr data-grade="{e(a["grade"])}"><td>{e(a["agent"])}</td>'
            f'<td><span class="g" style="background:{colors[a["grade"]]}">{e(a["grade"])}</span></td>'
            f'<td>{e(a["category"])}</td><td>{e(a["fix_owner"])}</td><td>{e(a["lifecycle"])}</td>'
            f'<td>{e(a.get("resource_label", "Needs resource review"))}</td><td>{e(contact)}</td><td>{e(a["suggested_fix"])}</td><td class="r">{e(a["reason"])}</td><td>{e(tokens)}</td></tr>')
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Agent report card</title>
<style>body{{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#1c2430;background:#f5f7fb}}
.tiles{{display:flex;gap:12px;margin:16px 0}}.tile{{background:#fff;border:1px solid #dfe7f1;border-radius:10px;padding:12px 18px}}
.tile b{{font-size:28px;display:block}}table{{border-collapse:collapse;width:100%;background:#fff;font-size:13px}}
th,td{{border:1px solid #dfe7f1;padding:6px 8px;text-align:left;vertical-align:top}}th{{background:#eef3fa;position:sticky;top:0}}
.g{{color:#fff;border-radius:6px;padding:2px 8px;font-weight:700}}.r{{color:#5d6b7a;max-width:340px}}
button{{margin-right:6px;padding:4px 10px}}</style></head><body>
<h1>Agent report card: {e(card['run_label'])}</h1>
<p>Generated {e(card['generated_at'])}. {e(t['agents'])} agents. Pass rate of judged agents:
<b>{e(f"{(t['pass_rate_of_judged'] or 0):.0%}")}</b>. Evict candidates are agents broken for
{e(card['evict_after_failing_runs'])} consecutive runs ({e(card['history_runs'])} earlier runs on record).</p>
<div class="tiles">{tiles}</div>
<p>{e(token_summary(card))}</p>
<p>{e(RESOURCE_NOTE)}</p>
<p>Failure is not proof of author fault; pass is not proof of scientific correctness. Fix-owner and lifecycle suggestions are unverified, not author findings or eviction decisions.</p>
<p>Filter: <button onclick="f('')">All</button><button onclick="f('A')">A</button><button onclick="f('B')">B</button>
<button onclick="f('F')">F</button><button onclick="f('I')">I</button></p>
<table><thead><tr><th>Agent</th><th>Grade</th><th>Category</th><th>Suggested fix owner</th><th>Provisional lifecycle</th><th>Declared resources</th>
<th>Contact</th><th>Suggested fix</th><th>Reason</th><th>Tokens</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<script>function f(g){{document.querySelectorAll('tbody tr').forEach(r=>r.style.display=(!g||r.dataset.grade===g)?'':'none')}}</script>
</body></html>"""


def _author_key(owner: dict) -> str:
    return owner["github_login"] and f"github-{owner['github_login']}" or owner["contact"] or "unowned"


def _contact(owner: dict, mention: bool) -> str:
    """GitHub logins are only @-mentioned (which notifies them) when relaying is on."""
    if owner["github_login"]:
        return f"@{owner['github_login']}" if mention else f"`{owner['github_login']}`"
    return owner["contact"] or "no contact on record"


def write_digest(card: dict, out_dir: Path, notify_to: str, relay: bool) -> Path:
    """One message to the run owner covering every broken agent. Authors are only mentioned when relay is on."""
    broken = [a for a in card["agents"] if a["grade"] == "F"]
    harness = [a for a in card["agents"] if a["fix_owner"] == "harness"]
    evict = [a for a in broken if a["lifecycle"] == "evict_candidate"]
    t = card["totals"]
    lines = [f"To: @{notify_to}", f"Relay to authors: {'ON, authors are @-mentioned below' if relay else 'OFF, authors were not notified'}", "",
             f"## Agent report card: {card['run_label']}", "",
             f"{t['agents']} agents: {t['grades'].get('A', 0)} work, {t['grades'].get('B', 0)} work after retries, "
             f"{t['grades'].get('F', 0)} broken, {t['grades'].get('I', 0)} not judged. "
             f"Pass rate of judged agents: {(t['pass_rate_of_judged'] or 0):.0%}.", ""]
    if evict:
        lines += [f"### Removal candidates ({len(evict)}): broken {card['evict_after_failing_runs']} runs in a row", ""]
        lines += [f"- **{a['agent']}** ({a['category']}), author {_contact(a['owner'], relay)}" for a in evict] + [""]
    groups: dict[str, list[dict]] = defaultdict(list)
    for a in broken:
        groups[_author_key(a["owner"])].append(a)
    lines += [f"### Broken agents by author ({len(broken)})", ""]
    for key, items in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        lines += [f"**{_contact(items[0]['owner'], relay)}** ({len(items)})", ""]
        lines += [f"- {a['agent']}: {a['category']}, broken {a['failing_streak']} run(s) in a row ({a['lifecycle']}). "
                  f"Fix: {a['suggested_fix']}" for a in items] + [""]
    if harness:
        lines += [f"### Our harness to fix ({len(harness)}), not counted against authors", ""]
        lines += [f"- {a['agent']}: {a['category']}. {a['suggested_fix']}" for a in harness] + [""]
    path = out_dir / "notify-digest.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_notices(card: dict, out_dir: Path) -> int:
    """Per-author drafts for an optional relay. Nothing is sent from this script."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for a in card["agents"]:
        if a["grade"] == "F" and a["fix_owner"] == "author":
            groups[_author_key(a["owner"])].append(a)
    out_dir.mkdir(parents=True, exist_ok=True)
    for key, items in sorted(groups.items()):
        to = _contact(items[0]["owner"], mention=True)
        body = [f"DRAFT, NOT SENT. To: {to}", f"Subject: Discovery catalog report card: {len(items)} agent(s) need attention", "",
                f"The {card['run_label']} catalog validation could not confirm that these agents work end to end:", ""]
        for a in items:
            body += [f"- **{a['agent']}**: {a['category']} (broken {a['failing_streak']} run(s) in a row, status {a['lifecycle']})",
                     f"  - Observed: {a['reason']}", f"  - Suggested fix: {a['suggested_fix']}"]
        body += ["", "Agents that stay broken for "
                 f"{card['evict_after_failing_runs']} consecutive runs become candidates for removal from the catalog.",
                 "Reply or open a PR once fixed and the next run will regrade them."]
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", key)[:80]
        (out_dir / f"{safe}.md").write_text("\n".join(body) + "\n", encoding="utf-8")
    return len(groups)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ledger", type=Path, required=True)
    p.add_argument("--repo-root", type=Path, default=Path("."))
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--history", type=Path, nargs="*", default=[], help="Earlier agent-scorecard.json files")
    p.add_argument("--evidence-root", type=Path, help="Sweep output folder; refines 'tool not called' using saved responses")
    p.add_argument("--evict-after", type=int, default=4, help="Consecutive broken runs (2 runs a week, so 4 = 2 weeks)")
    p.add_argument("--notify-to", default=DEFAULT_NOTIFY_TO, help="GitHub login that receives the digest")
    p.add_argument("--relay-to-authors", action="store_true", help="@-mention authors in the digest")
    p.add_argument("--run-label", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    args = p.parse_args()
    if args.evict_after < 1:
        p.error("--evict-after must be at least 1")
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", args.notify_to):
        p.error("--notify-to must be a GitHub login")

    ledger = json.loads(args.ledger.read_text(encoding="utf-8"))
    history = load_history(args.history, args.run_label)
    card = build(ledger, args.repo_root.resolve(), history, args.evict_after, args.run_label, args.evidence_root)
    card.update(notify_to=args.notify_to, relayed_to_authors=args.relay_to_authors)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "agent-scorecard.json").write_text(json.dumps(card, indent=2), encoding="utf-8")
    (args.output_dir / "agent-scorecard.md").write_text(render_markdown(card), encoding="utf-8")
    (args.output_dir / "agent-scorecard.html").write_text(render_html(card), encoding="utf-8")
    write_digest(card, args.output_dir, args.notify_to, args.relay_to_authors)
    drafts = write_notices(card, args.output_dir / "author-notices")
    print(json.dumps({**card["totals"], "author_notice_drafts": drafts, "notify_to": args.notify_to,
                      "relayed_to_authors": args.relay_to_authors}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
