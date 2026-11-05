"""Fail-closed workflow adapter. Offline preparation/finalization never authenticate.

Only gate reads public upstream; only auth reads existing Azure credentials.
No deployment, upload, retry, confirmation approval, login or role setup here.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

import yaml

from catalog_run_memory import API, get_json, require_current_inventory, write_run_memory
from catalog_sweep import load_input_assets, save_json

LANES = {"cpu", "gpu_capable", "external_or_platform", "resource_review"}


def agent_names(repo: Path) -> list[str]:
    root = repo / "agents"
    return sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else []


def run_identity(event: str, run_id: str, attempt: str, now=None) -> dict:
    if not re.fullmatch(r"[1-9][0-9]*", run_id) or not re.fullmatch(r"[1-9][0-9]*", attempt):
        raise ValueError("numeric run ID and attempt required")
    now = now or datetime.now(timezone.utc)
    slot = {0: "Run1Monday", 3: "Run2Thursday"}.get(now.weekday(), "Manual")
    if event == "schedule" and slot == "Manual":
        raise ValueError("scheduled live slot must be Monday or Thursday UTC")
    week = now.strftime("%G-W%V")
    # Ten hex characters, within future CLI's arbitrary [A-Za-z0-9-]{1,12} contract.
    # Run ID + attempt are retained in full below; never treat the short hash as identity.
    label = hashlib.sha256(f"{week}/{slot}/{run_id}/{attempt}".encode()).hexdigest()[:10]
    return {"run_label": label, "session_label": f"{week}-{slot}-{run_id}-a{attempt}",
            "run_id": run_id, "attempt": attempt, "iso_week": week, "slot": slot}


def prepare(repo: Path, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    identity = run_identity(os.getenv("GITHUB_EVENT_NAME", "workflow_dispatch"),
                            os.getenv("GITHUB_RUN_ID", "1"), os.getenv("GITHUB_RUN_ATTEMPT", "1"))
    save_json(out / "workflow-run.json", {**identity, "repository": os.getenv("GITHUB_REPOSITORY"),
              "revision": os.getenv("GITHUB_SHA"), "owner_history_root": str(repo.resolve())})
    if os.getenv("GITHUB_OUTPUT"):
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
            stream.write(f"run_label={identity['run_label']}\n")
    # This map can contain private resource IDs; keep it outside uploaded evidence.
    raw = os.getenv("CATALOG_INPUT_ASSETS_YAML", "")
    if raw:
        path = out.parent / "catalog-input-assets.yaml"
        path.write_text(raw, encoding="utf-8")
        path.chmod(0o600)
    return identity


def verify_sources(repo: Path, inventory: dict, fetch=get_json) -> dict:
    commit = inventory["commit"]
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("full pinned upstream SHA required")
    tree = fetch(f"{API}/git/trees/{commit}?recursive=1")
    if tree.get("truncated") is not False:
        raise ValueError("complete upstream source tree required")
    expected = {}
    for entry in tree["tree"]:
        path = entry["path"]
        if not path.startswith("agents/") or entry["type"] == "tree":
            continue
        if entry["type"] != "blob" or entry.get("mode") not in {"100644", "100755"}:
            raise ValueError("upstream special agent file rejected")
        expected[path] = entry["sha"]
    actual = {}
    for path in (repo / "agents").rglob("*"):
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ValueError("linked agent source rejected")
        if path.is_file():
            data = path.read_bytes()
            actual[path.relative_to(repo).as_posix()] = hashlib.sha1(
                b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
    if not expected or actual != expected:
        raise ValueError("checked-out agent bytes differ from pinned latest upstream")
    if any(not (repo / "agents" / name / "agent.yaml").is_file() for name in inventory["agents"]):
        raise ValueError("agent definition missing")
    return {"source_content_verified": True, "agent_files": len(actual), "upstream_commit": commit}


def required_assets(repo: Path, plan: dict, assets_path: Path | None) -> dict:
    manifest = yaml.safe_load((repo / ".github/smoke/task-inputs.yaml").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("inputs"), dict):
        raise ValueError("fixture requirements mapping required")
    required = set()
    for row in plan["cases"]:
        files = list(row.get("input_files") or [])
        files += manifest["inputs"].get(row["catalog_agent"], [])
        if any(not isinstance(f, str) or not f for f in files):
            raise ValueError("invalid fixture requirement")
        required.update(files)
    assets = load_input_assets(assets_path)
    if required - assets.keys():
        raise ValueError("input asset map missing required fixtures")
    return {key: assets[key] for key in sorted(required)}


def gate(repo: Path, out: Path, assets_path: Path | None, fetch=get_json) -> dict:
    # Inventory comes first, even if a separate approval/CLI prerequisite is absent.
    out.mkdir(parents=True, exist_ok=True)
    require_current_inventory(out, agent_names(repo), fetch)
    inventory = json.loads((out / "upstream-inventory.json").read_text(encoding="utf-8"))
    source = verify_sources(repo, inventory, fetch)
    save_json(out / "source-content-gate.json", source)
    plan = json.loads((out / "catalog-plan.json").read_text(encoding="utf-8"))
    names = [c["catalog_agent"] for c in plan["cases"]]
    if sorted(names) != inventory["agents"]:
        raise ValueError("plan must account for exactly every current upstream agent")
    assets = required_assets(repo, plan, assets_path)
    if assets and os.getenv("CATALOG_SMOKE_INPUTS_APPROVED") != "true":
        raise ValueError("owner approval of staged fixture contents and licenses required")
    for key in ("CATALOG_SMOKE_LIVE_ENABLED", "CATALOG_SMOKE_ARTIFACTS_APPROVED"):
        if os.getenv(key) != "true":
            raise ValueError("explicit live and private evidence approvals required")
    if os.getenv("PRIVATE_REPO") != "true":
        raise ValueError("private repository required for live evidence")
    allowance = int(os.getenv("MAX_INVOCATIONS", "0"))
    workers = int(os.getenv("WORKERS", "0"))
    timeout = int(os.getenv("CLIENT_TIMEOUT", "0"))
    lanes = os.getenv("ALLOW_LANES", "").split(",")
    if not 1 <= allowance <= len(names) or not 1 <= workers <= 4 or not 300 <= timeout <= 3600:
        raise ValueError("explicit bounded invocation/concurrency/timeout required")
    if not lanes or not set(lanes) <= LANES or len(lanes) != len(set(lanes)):
        raise ValueError("explicit valid lanes required")
    if os.getenv("GITHUB_EVENT_NAME") == "schedule":
        if os.getenv("CATALOG_SMOKE_SCHEDULE_LIVE_ENABLED") != "true":
            raise ValueError("scheduled live separately disabled")
        if int(os.getenv("EXPECTED_AGENT_COUNT", "0")) != len(names):
            raise ValueError("scheduled approved count must equal latest inventory (currently 121)")
    cli = subprocess.run([sys.executable, str(repo / ".github/scripts/catalog_sweep.py"), "--help"],
                         capture_output=True, text=True, timeout=30, check=False)
    if cli.returncode or "--run-label" not in cli.stdout:
        raise ValueError("parent CLI --run-label support missing; do not run without session isolation")
    identity = json.loads((out / "workflow-run.json").read_text(encoding="utf-8"))
    if not re.fullmatch(r"[A-Za-z0-9-]{1,12}", identity["run_label"]):
        raise ValueError("run label outside future CLI contract")
    result = {**source, "status": "passed", "agent_count": len(names), "required_assets": len(assets),
              "max_invocations": allowance, "lanes": lanes, "run_label": identity["run_label"],
              "authentication": "not_yet_checked"}
    save_json(out / "workflow-preflight.json", result)
    return result


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("authenticated redirect refused")


def validate_actions_oidc() -> None:
    """Fail closed before the shared factory can select a fallback credential."""
    keys = ("AZURE_CLIENT_ID", "AZURE_TENANT_ID",
            "ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if any(not os.getenv(key, "").strip() for key in keys):
        raise ValueError("complete GitHub Actions OIDC configuration required; no CLI fallback")
    for key in keys[:2]:
        if not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", os.environ[key]):
            raise ValueError("GitHub Actions OIDC client and tenant must be UUIDs")
    raw = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"]
    # Match the entire authority, not a substring: no userinfo, lookalike suffix,
    # arbitrary port, IP literal, empty DNS label or URL-parser whitespace tricks.
    label = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    try:
        url = urlsplit(raw)
        valid = (not re.search(r"[\s\x00-\x1f\x7f]", raw)
                 and url.scheme == "https" and not url.fragment
                 and re.fullmatch(rf"(?:{label}\.)+actions\.githubusercontent\.com(?::443)?",
                                  url.netloc, re.IGNORECASE))
    except ValueError:
        valid = False
    if not valid:
        # Never include the request URL or token in errors or receipts.
        raise ValueError("GitHub Actions OIDC requires an HTTPS GitHub Actions token host")


def auth(repo: Path, out: Path, assets_path: Path | None) -> None:
    receipt = json.loads((out / "workflow-preflight.json").read_text(encoding="utf-8"))
    if receipt.get("status") != "passed":
        raise ValueError("source/input gate must pass before credential use")
    # A failed recheck must not retain an earlier authentication success receipt.
    receipt["authentication"] = "not_yet_checked"
    receipt.pop("assets_metadata_verified", None)
    receipt.pop("asset_file_contents_verified", None)
    save_json(out / "workflow-preflight.json", receipt)
    from run_catalog_smoke import API_VERSION, validate_endpoint
    import uuid

    in_actions = os.getenv("GITHUB_ACTIONS") == "true"
    if in_actions:
        validate_actions_oidc()
    endpoint = validate_endpoint(os.getenv("WORKSPACE_API_URL", ""))
    subscription = str(uuid.UUID(os.environ["AZURE_SUBSCRIPTION_ID"]))
    for key in ("AZURE_CLIENT_ID", "AZURE_TENANT_ID", "AZURE_SUBSCRIPTION_ID", "RESOURCE_GROUP", "MODEL"):
        if not os.getenv(key):
            raise ValueError("required Azure/workspace configuration missing")
    plan = json.loads((out / "catalog-plan.json").read_text(encoding="utf-8"))
    assets = required_assets(repo, plan, assets_path)
    if in_actions:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "utilities" / "agent-evaluation" / "evaluators"))
        from azure_credential import get_credential

        credential = get_credential()
        auth_method = "github_oidc_read_verified"
    else:
        from azure.identity import AzureCliCredential

        account = subprocess.run(["az", "account", "show", "--output", "json"],
                                 capture_output=True, text=True, timeout=30, check=False)
        if account.returncode:
            raise ValueError("existing Azure CLI authentication required; no sign-in attempted")
        identity = json.loads(account.stdout)
        if (identity.get("id", "").lower() != subscription.lower() or
                identity.get("tenantId", "").lower() != os.environ["AZURE_TENANT_ID"].lower()):
            raise ValueError("existing Azure account target differs; no account switch permitted")
        credential = AzureCliCredential(tenant_id=os.environ["AZURE_TENANT_ID"], subscription=subscription)
        auth_method = "existing_cli_read_verified"
    try:
        opener = build_opener(NoRedirect())
        # Read-only checks using the selected identity; never sign in or switch accounts.
        for resource, url, asset_id in [
            ("https://discovery.azure.com/.default", endpoint +
             f"/projects/weekly-agent-svc-test/agents?api-version={API_VERSION}", None),
            *[("https://management.azure.com/.default", f"https://management.azure.com{asset_id}?api-version={API_VERSION}", asset_id)
              for asset_id in sorted(set(assets.values()))],
        ]:
            if asset_id and not asset_id.lower().startswith(f"/subscriptions/{subscription}/".lower()):
                raise ValueError("asset subscription differs from approved target")
            token = credential.get_token(resource).token
            request = Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
            with opener.open(request, timeout=45) as response:
                payload = json.load(response)
            if asset_id and (payload.get("id", "").lower() != asset_id.lower() or
                             payload.get("properties", {}).get("provisioningState") != "Succeeded"):
                raise ValueError("asset readiness not verified")
    finally:
        credential.close()
    receipt["authentication"] = auth_method
    receipt["assets_metadata_verified"] = True
    receipt["asset_file_contents_verified"] = False
    save_json(out / "workflow-preflight.json", receipt)


def original_owner(repo: Path, name: str) -> dict:
    unknown = {"original_author": None, "original_author_email": None, "original_commit": None,
               "github_login": None, "attribution": "Unknown"}
    relative = f"agents/{name}/agent.yaml"
    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=30, check=False)
    try:
        if git("rev-parse", "--is-shallow-repository").stdout.strip() != "false":
            return unknown
        if git("ls-files", "--error-unmatch", "--", relative).returncode:
            return unknown
        history = git("log", "--follow", "--diff-filter=A", "--format=%H%x1f%an%x1f%ae", "--", relative)
        lines = history.stdout.strip().splitlines()
        if history.returncode or len(lines) != 1:
            return unknown
        commit, author, email = lines[0].split("\x1f")
        # A tracked snapshot/import commit is not evidence of the original author.
        parent = git("rev-parse", f"{commit}^")
        if parent.returncode or "[bot]" in (author + email).lower():
            return unknown
        changed = git("diff-tree", "--no-commit-id", "--name-only", "-r", commit).stdout.splitlines()
        definitions = [p for p in changed if re.fullmatch(r"agents/[^/]+/agent.yaml", p)]
        if definitions != [relative]:
            return unknown
        return {**unknown, "original_author": author, "original_author_email": email,
                "original_commit": commit, "attribution": "tracked_definition_origin; identity not independently verified"}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return unknown


def finalize(repo: Path, out: Path, mode: str, run_label: str) -> dict:
    """Always produce report + memory; missing/partial evidence never becomes author failure."""
    import build_agent_scorecard as grader

    out.mkdir(parents=True, exist_ok=True)
    ledger_path = out / "catalog-results.json"
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        if (not isinstance(ledger.get("results"), list) or not ledger["results"]
                or not isinstance(ledger.get("counts"), dict)
                or ledger.get("total") != len(ledger["results"])
                or not isinstance(ledger.get("submitted_responses"), int)):
            raise ValueError("incomplete ledger")
        if mode == "live" and ledger.get("mode") != "live":
            raise ValueError("offline plan is not a completed live ledger")
    except (OSError, ValueError):
        rows = []
        for name in agent_names(repo):
            checkpoint = out / "live" / name / "case.json"
            try:
                row = json.loads(checkpoint.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                row = {}
            try:
                summary = json.loads(checkpoint.with_name("summary.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                summary = {}
            submitted = max(row.get("response_submissions", 0),
                            int(bool(summary.get("submission_attempted") or summary.get("response_id"))))
            rows.append({**row, "catalog_agent": name, "status": "uncertain" if row or summary else "not_run",
                         "response_submissions": submitted,
                         "response_id": row.get("response_id") or summary.get("response_id"),
                         "reason": "workflow_incomplete; inspect protected checkpoint; no author verdict"})
        ledger = {"mode": mode, "total": len(rows), "counts": dict(Counter(r["status"] for r in rows)),
                  "submitted_responses": sum(r.get("response_submissions", 0) for r in rows), "results": rows}
        # Do not destroy corrupt/partial original evidence.
        if ledger_path.exists():
            ledger_path = out / "workflow-fallback-results.json"
        save_json(ledger_path, ledger)
    names = [row.get("catalog_agent") for row in ledger["results"]]
    if (any(not isinstance(n, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", n) for n in names)
            or len(names) != len(set(names))):
        raise ValueError("invalid or duplicate ledger agent names")
    # The existing grader's reason-based F classifications cannot establish author
    # responsibility. Preserve raw rows above; automation grades only proven passes.
    safe = {**ledger, "results": [{**r, "status": r.get("status") if r.get("status") == "passed" else "blocked",
             "reason": r.get("reason") if r.get("status") == "passed" else "automation_unknown_attribution"}
             for r in ledger["results"]]}
    safe_path = out / "automation-grading-ledger.json"
    save_json(safe_path, safe)
    card_dir = out / "report-card"
    command = [sys.executable, str(Path(__file__).with_name("build_agent_scorecard.py")), "--ledger", str(safe_path),
               "--repo-root", str(repo), "--evidence-root", str(out.parent), "--output-dir", str(card_dir),
               "--run-label", run_label]
    result = subprocess.run(command, capture_output=True, text=True, timeout=180, check=False)
    if result.returncode:
        card_dir.mkdir(parents=True, exist_ok=True)
        # Grader failure remains a nonzero failure, but still save an explicit receipt.
        agents = [{"agent": r["catalog_agent"], "status": r.get("status"), "lane": r.get("lane"),
                   "grade": "I", "category": "grader_failed", "reason": "offline grader failed; no author verdict",
                   "fix_owner": "harness", "suggested_fix": "Repair offline grading before drawing conclusions",
                   "failing_streak": 0, "lifecycle": "review_required", "owner": {"github_login": None,
                   "contact": None, "original_author": None}, "tokens": None} for r in ledger["results"]]
        card = {"run_label": run_label, "generated_at": datetime.now(timezone.utc).isoformat(),
                "evict_after_failing_runs": 4, "history_runs": 0,
                "totals": {"agents": len(agents), "grades": {"I": len(agents)}, "pass_rate_of_judged": None,
                           "categories": {"grader_failed": len(agents)}}, "agents": agents, "status": "grader_failed"}
        save_json(card_dir / "agent-scorecard.json", card)
        (card_dir / "agent-scorecard.md").write_text(grader.render_markdown(card), encoding="utf-8")
        (card_dir / "agent-scorecard.html").write_text(grader.render_html(card), encoding="utf-8")
        write_run_memory(out, ledger, card)
        raise ValueError("grader failed; diagnostic scorecard and handoff retained")
    card = json.loads((card_dir / "agent-scorecard.json").read_text(encoding="utf-8"))
    original_rows = {r["catalog_agent"]: r for r in ledger["results"]}
    for row in card["agents"]:
        raw = original_rows[row["agent"]]
        row.update(status=raw.get("status"), reason=raw.get("reason"), failing_streak=0,
                   lifecycle="keep" if row["grade"] in {"A", "B"} else "review_required")
        row["owner"].update(original_owner(repo, row["agent"]))
        if row["grade"] == "I":
            row.update(category="unknown_attribution", fix_owner="triage",
                       suggested_fix="Review correlated inputs, platform and operation evidence; no author defect or eviction established.")
    card["automation_policy"] = "No author failure streaks, eviction, relay or notifications; manual review required"
    save_json(card_dir / "agent-scorecard.json", card)
    (card_dir / "agent-scorecard.md").write_text(grader.render_markdown(card), encoding="utf-8")
    (card_dir / "agent-scorecard.html").write_text(grader.render_html(card), encoding="utf-8")
    grader.write_digest(card, card_dir, card["notify_to"], False)
    write_run_memory(out, ledger, card)
    return card


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "gate", "auth", "finalize"])
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--input-assets", type=Path)
    parser.add_argument("--mode", default="plan")
    parser.add_argument("--run-label", default="workflow-incomplete")
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            prepare(args.repo_root, args.output_dir)
        elif args.action == "gate":
            gate(args.repo_root, args.output_dir, args.input_assets)
        elif args.action == "auth":
            auth(args.repo_root, args.output_dir, args.input_assets)
        else:
            finalize(args.repo_root, args.output_dir, args.mode, args.run_label)
        return 0
    except Exception as error:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        # Never echo HTTP bodies, tokens, resource IDs, arbitrary exceptions to logs.
        save_json(args.output_dir / f"workflow-{args.action}-failure.json",
                  {"status": "blocked", "error_type": type(error).__name__, "action": args.action,
                   "next": "Review source/input/CLI/auth configuration in protected environment; no automatic retry"})
        print(f"Workflow {args.action} blocked ({type(error).__name__}); protected receipt saved.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())