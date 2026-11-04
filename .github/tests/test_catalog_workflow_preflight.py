"""Offline workflow safety tests. No Azure credentials, upstream reads or submissions."""
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace

import pytest
import yaml

import catalog_workflow_preflight as subject

ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 40


def repo_fixture(tmp_path, names=("example",)):
    repo = tmp_path / "repo"
    for name in names:
        folder = repo / "agents" / name
        folder.mkdir(parents=True)
        (folder / "agent.yaml").write_text("name: example\n", encoding="utf-8")
    fixtures = repo / ".github/smoke/task-inputs.yaml"
    fixtures.parent.mkdir(parents=True)
    fixtures.write_text("inputs: {}\n", encoding="utf-8")
    return repo


def upstream(repo, names=None):
    names = names or subject.agent_names(repo)
    entries = []
    for path in (repo / "agents").rglob("*"):
        if path.is_file():
            raw = path.read_bytes()
            entries.append({"path": path.relative_to(repo).as_posix(), "type": "blob", "mode": "100644",
                            "sha": hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()})
    def fetch(url):
        if url.endswith("/commits/main"):
            return {"sha": SHA}
        if "/contents/agents?ref=" in url:
            assert url.endswith(SHA)
            return [{"name": n, "type": "dir"} for n in names]
        assert url.endswith(f"/git/trees/{SHA}?recursive=1")
        return {"truncated": False, "tree": entries}
    return fetch


def plan(out, names=("example",), files=()):
    out.mkdir(parents=True)
    (out / "catalog-plan.json").write_text(json.dumps({"cases": [
        {"catalog_agent": n, "input_files": list(files)} for n in names]}), encoding="utf-8")
    (out / "workflow-run.json").write_text('{"run_label":"abc123"}', encoding="utf-8")


def approvals(monkeypatch, count=1):
    for key, value in {"CATALOG_SMOKE_LIVE_ENABLED": "true", "CATALOG_SMOKE_ARTIFACTS_APPROVED": "true",
                       "PRIVATE_REPO": "true", "MAX_INVOCATIONS": str(count), "WORKERS": "2",
                       "CLIENT_TIMEOUT": "1800", "ALLOW_LANES": "cpu", "GITHUB_EVENT_NAME": "workflow_dispatch"}.items():
        monkeypatch.setenv(key, value)


def test_98_vs_121_inventory_fails_before_cli_or_auth(tmp_path, monkeypatch):
    repo = repo_fixture(tmp_path, tuple(f"agent-{i}" for i in range(98)))
    out = tmp_path / "out"
    monkeypatch.setattr(subject.subprocess, "run", lambda *a, **k: pytest.fail("must not execute CLI"))
    with pytest.raises(ValueError, match="differs"):
        subject.gate(repo, out, None, upstream(repo, [f"agent-{i}" for i in range(121)]))
    receipt = json.loads((out / "inventory-gate.json").read_text())
    assert receipt["upstream_count"] == 121 and receipt["planned_count"] == 98
    assert len(receipt["missing"]) == 23


def test_offline_upstream_failure_retains_receipt(tmp_path):
    repo = repo_fixture(tmp_path)
    def unavailable(url):
        raise OSError("offline")
    with pytest.raises(ValueError, match="unavailable"):
        subject.gate(repo, tmp_path / "out", None, unavailable)
    assert (tmp_path / "out/preflight-report.md").is_file()


def test_source_name_match_does_not_allow_modified_bytes(tmp_path):
    repo = repo_fixture(tmp_path)
    fetch = upstream(repo)
    (repo / "agents/example/agent.yaml").write_text("changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="bytes differ"):
        subject.verify_sources(repo, {"commit": SHA, "agents": ["example"]}, fetch)


@pytest.mark.parametrize("kind", ["truncated", "extra", "missing", "special"])
def test_source_gate_negative_cases(tmp_path, kind):
    repo = repo_fixture(tmp_path)
    fetch = upstream(repo)
    if kind == "extra":
        (repo / "agents/example/extra.txt").write_text("extra", encoding="utf-8")
    if kind == "missing":
        (repo / "agents/example/agent.yaml").unlink()
    def changed(url):
        data = fetch(url)
        if kind == "truncated":
            data["truncated"] = True
        if kind == "special":
            data["tree"][0]["mode"] = "120000"
        return data
    with pytest.raises(ValueError):
        subject.verify_sources(repo, {"commit": SHA, "agents": ["example"]}, changed)


def test_sources_pinned_to_resolved_latest_sha(tmp_path):
    repo = repo_fixture(tmp_path)
    result = subject.verify_sources(repo, {"commit": SHA, "agents": ["example"]}, upstream(repo))
    assert result["source_content_verified"] and result["upstream_commit"] == SHA


def test_required_fixtures_need_asset_mapping(tmp_path):
    repo = repo_fixture(tmp_path)
    manifest = repo / ".github/smoke/task-inputs.yaml"
    manifest.write_text("inputs:\n  example: [required.pdb]\n", encoding="utf-8")
    p = {"cases": [{"catalog_agent": "example", "input_files": ["additional.fasta"]}]}
    with pytest.raises(ValueError, match="missing required fixtures"):
        subject.required_assets(repo, p, None)
    asset = "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/rg/providers/Microsoft.Discovery/storageContainers/inputs/storageAssets/fixture"
    mapping = tmp_path / "assets.yaml"
    mapping.write_text(yaml.safe_dump({"assets": {"required.pdb": asset, "additional.fasta": asset}}), encoding="utf-8")
    assert set(subject.required_assets(repo, p, mapping)) == {"required.pdb", "additional.fasta"}


@pytest.mark.parametrize("key,value", [("PRIVATE_REPO", "false"), ("CATALOG_SMOKE_LIVE_ENABLED", "false"),
    ("CATALOG_SMOKE_ARTIFACTS_APPROVED", "false"), ("MAX_INVOCATIONS", "0"), ("MAX_INVOCATIONS", "2"),
    ("WORKERS", "5"), ("CLIENT_TIMEOUT", "299"), ("ALLOW_LANES", "cpu,unknown"), ("ALLOW_LANES", "")])
def test_live_gates_fail_closed(tmp_path, monkeypatch, key, value):
    repo = repo_fixture(tmp_path)
    out = tmp_path / "out"
    plan(out)
    approvals(monkeypatch)
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        subject.gate(repo, out, None, upstream(repo))


def test_missing_parent_run_label_is_a_blocker(tmp_path, monkeypatch):
    repo = repo_fixture(tmp_path)
    out = tmp_path / "out"
    plan(out)
    approvals(monkeypatch)
    monkeypatch.setattr(subject.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="--mode --timeout"))
    with pytest.raises(ValueError, match="parent CLI --run-label"):
        subject.gate(repo, out, None, upstream(repo))


def test_schedule_requires_separate_opt_in_and_current_count(tmp_path, monkeypatch):
    repo = repo_fixture(tmp_path)
    out = tmp_path / "out"
    plan(out)
    approvals(monkeypatch)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    with pytest.raises(ValueError, match="separately disabled"):
        subject.gate(repo, out, None, upstream(repo))
    monkeypatch.setenv("CATALOG_SMOKE_SCHEDULE_LIVE_ENABLED", "true")
    monkeypatch.setenv("EXPECTED_AGENT_COUNT", "98")
    with pytest.raises(ValueError, match="approved count"):
        subject.gate(repo, out, None, upstream(repo))
    monkeypatch.setenv("EXPECTED_AGENT_COUNT", "1")
    monkeypatch.setattr(subject.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="--run-label"))
    assert subject.gate(repo, out, None, upstream(repo))["status"] == "passed"


@pytest.mark.parametrize("date,slot", [("2026-11-02", "Run1Monday"), ("2026-11-05", "Run2Thursday")])
def test_iso_week_and_unique_short_attempt_label(date, slot):
    now = datetime.fromisoformat(date).replace(tzinfo=timezone.utc)
    first = subject.run_identity("schedule", "123456", "1", now)
    second = subject.run_identity("schedule", "123456", "2", now)
    assert first["iso_week"] == "2026-W45" and first["slot"] == slot
    assert re.fullmatch(r"[A-Za-z0-9-]{1,10}", first["run_label"])
    assert first["run_label"] != second["run_label"]
    assert first["session_label"].endswith("123456-a1")


def test_schedule_wrong_day_blocks():
    with pytest.raises(ValueError, match="Monday or Thursday"):
        subject.run_identity("schedule", "1", "1", datetime(2026, 11, 3, tzinfo=timezone.utc))


def test_prepare_is_offline_and_map_is_outside_evidence(tmp_path, monkeypatch):
    repo = repo_fixture(tmp_path)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setenv("CATALOG_INPUT_ASSETS_YAML", "assets: {}\n")
    monkeypatch.setattr(subject.subprocess, "run", lambda *a, **k: pytest.fail("no commands in prepare"))
    out = tmp_path / "out"
    assert subject.prepare(repo, out)["attempt"] == "2"
    assert (out.parent / "catalog-input-assets.yaml").is_file()
    assert not (out / "catalog-input-assets.yaml").exists()


def test_failure_finalization_calls_real_grader_and_writes_handoff(tmp_path):
    repo = repo_fixture(tmp_path)
    out = tmp_path / "out"
    card = subject.finalize(repo, out, "live", "sweep-123-a1")
    assert card["agents"][0]["grade"] == "I"
    assert card["agents"][0]["owner"]["original_author"] is None
    assert (out / "report-card/agent-scorecard.html").is_file()
    assert (out / "next-run-handoff.md").is_file()
    memory = json.loads((out / "next-run-memory.json").read_text())
    assert memory["agents_accounted"] == 1 and memory["submitted_responses"] == 0
    assert memory["actions"][0]["automatic_retry_authorized"] is False


def test_plan_left_after_live_crash_does_not_hide_checkpoint(tmp_path):
    repo = repo_fixture(tmp_path)
    out = tmp_path / "out"
    checkpoint = out / "live/example/case.json"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text('{"response_submissions":1,"response_id":"resp_test"}', encoding="utf-8")
    (out / "catalog-results.json").write_text('{"mode":"plan","results":[]}', encoding="utf-8")
    card = subject.finalize(repo, out, "live", "sweep-123-a1")
    assert card["agents"][0]["status"] == "uncertain"
    assert json.loads((out / "next-run-memory.json").read_text())["submitted_responses"] == 1
    assert json.loads((out / "catalog-results.json").read_text())["mode"] == "plan"
    assert (out / "workflow-fallback-results.json").is_file()


@pytest.mark.parametrize("status,reason", [("failed", "own_tool_operations_failed"),
    ("blocked", "agent_load_failed"), ("not_run", "own_tool_not_called"), ("uncertain", "tool_arguments_invalid")])
def test_no_false_author_failure_or_eviction(tmp_path, status, reason):
    repo = repo_fixture(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    ledger = {"mode": "live", "total": 1, "submitted_responses": 0, "counts": {status: 1},
              "results": [{"catalog_agent": "example", "status": status, "reason": reason}]}
    (out / "catalog-results.json").write_text(json.dumps(ledger), encoding="utf-8")
    card = subject.finalize(repo, out, "live", "sweep-123-a1")
    row = card["agents"][0]
    assert row["grade"] == "I" and row["failing_streak"] == 0
    assert row["lifecycle"] == "review_required" and row["fix_owner"] == "triage"
    assert row["reason"] == reason and row["status"] == status
    assert not list((out / "report-card/author-notices").glob("*.md"))
    assert json.loads((out / "catalog-results.json").read_text()) == ledger


def test_grader_failure_still_writes_memory(tmp_path, monkeypatch):
    repo = repo_fixture(tmp_path)
    monkeypatch.setattr(subject.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1))
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="grader failed"):
        subject.finalize(repo, out, "live", "run")
    assert (out / "report-card/agent-scorecard.json").is_file()
    assert (out / "report-card/agent-scorecard.md").is_file()
    assert (out / "report-card/agent-scorecard.html").is_file()
    assert (out / "next-run-memory.json").is_file()


def test_submission_summary_survives_missing_case_counter(tmp_path):
    repo = repo_fixture(tmp_path)
    out = tmp_path / "out"
    saved = out / "live/example"
    saved.mkdir(parents=True)
    (saved / "summary.json").write_text('{"submission_attempted":true,"response_id":"resp_saved"}', encoding="utf-8")
    card = subject.finalize(repo, out, "live", "sweep-1-a1")
    assert card["agents"][0]["status"] == "uncertain"
    ledger = json.loads((out / "catalog-results.json").read_text())
    assert ledger["submitted_responses"] == 1
    assert ledger["results"][0]["response_id"] == "resp_saved"


def test_fixture_content_approval_is_required(tmp_path, monkeypatch):
    repo = repo_fixture(tmp_path)
    out = tmp_path / "out"
    plan(out, files=("example.fasta",))
    approvals(monkeypatch)
    asset = "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/rg/providers/Microsoft.Discovery/storageContainers/inputs/storageAssets/fixture"
    path = tmp_path / "assets.yaml"
    path.write_text(yaml.safe_dump({"assets": {"example.fasta": asset}}), encoding="utf-8")
    monkeypatch.delenv("CATALOG_SMOKE_INPUTS_APPROVED", raising=False)
    with pytest.raises(ValueError, match="fixture contents"):
        subject.gate(repo, out, path, upstream(repo))


def test_original_owner_requires_single_tracked_nonimport_definition(tmp_path):
    repo = repo_fixture(tmp_path)
    def git(*args):
        return subprocess.run(["git", "-c", "user.name=Fixture Author", "-c",
                               "user.email=fixture@example.invalid", *args], cwd=repo,
                              capture_output=True, text=True, check=True)
    git("init")
    git("add", ".")
    git("commit", "-m", "Root import, not original authorship")
    assert subject.original_owner(repo, "example")["attribution"] == "Unknown"
    folder = repo / "agents/new-agent"
    folder.mkdir()
    (folder / "agent.yaml").write_text("name: new-agent\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "Single definition origin")
    origin = subject.original_owner(repo, "new-agent")
    assert origin["original_author"] == "Fixture Author"
    assert origin["original_commit"] == git("rev-parse", "HEAD").stdout.strip()
    assert origin["github_login"] is None
    for name in ("bulk-a", "bulk-b"):
        p = repo / "agents" / name
        p.mkdir()
        (p / "agent.yaml").write_text("name: example\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "Bulk import")
    assert subject.original_owner(repo, "bulk-a")["attribution"] == "Unknown"


def test_auth_requires_gate_before_using_existing_credentials(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "workflow-preflight.json").write_text('{"status":"blocked"}', encoding="utf-8")
    with pytest.raises(ValueError, match="source/input gate"):
        subject.auth(tmp_path, out, None)


@pytest.mark.parametrize("wrong_target", [False, True])
def test_auth_uses_only_existing_cli_and_get_metadata(tmp_path, monkeypatch, wrong_target):
    import azure.identity

    repo = repo_fixture(tmp_path)
    out = tmp_path / "out"
    plan(out, files=("example.fasta",))
    (out / "workflow-preflight.json").write_text('{"status":"passed"}', encoding="utf-8")
    subscription = "11111111-1111-1111-1111-111111111111"
    asset = f"/subscriptions/{subscription}/resourceGroups/rg/providers/Microsoft.Discovery/storageContainers/inputs/storageAssets/fixture"
    path = tmp_path / "assets.yaml"
    path.write_text(yaml.safe_dump({"assets": {"example.fasta": asset}}), encoding="utf-8")
    for key, value in {"WORKSPACE_API_URL": "https://example.workspace.discovery.azure.com",
                       "AZURE_SUBSCRIPTION_ID": subscription, "AZURE_TENANT_ID": "tenant",
                       "AZURE_CLIENT_ID": "client", "RESOURCE_GROUP": "rg", "MODEL": "gpt-5-mini"}.items():
        monkeypatch.setenv(key, value)
    def cli(args, **kwargs):
        assert args == ["az", "account", "show", "--output", "json"]
        return SimpleNamespace(returncode=0, stdout=json.dumps({"id": subscription,
                               "tenantId": "wrong" if wrong_target else "tenant"}))
    monkeypatch.setattr(subject.subprocess, "run", cli)
    class Credential:
        def __init__(self, **kwargs):
            assert kwargs == {"tenant_id": "tenant", "subscription": subscription}
        def get_token(self, scope):
            return SimpleNamespace(token="NEVER-LOG-THIS-TOKEN")
        def close(self):
            pass
    requests = []
    class Opener:
        def open(self, request, timeout):
            assert request.get_method() == "GET" and timeout == 45
            requests.append(request.full_url)
            payload = {"id": asset, "properties": {"provisioningState": "Succeeded"}} if "management.azure.com" in request.full_url else {"value": []}
            return io.BytesIO(json.dumps(payload).encode())
    monkeypatch.setattr(azure.identity, "AzureCliCredential", Credential)
    monkeypatch.setattr(subject, "build_opener", lambda *a: Opener())
    if wrong_target:
        with pytest.raises(ValueError, match="no account switch"):
            subject.auth(repo, out, path)
        assert requests == []
    else:
        subject.auth(repo, out, path)
        assert len(requests) == 2
        receipt = json.loads((out / "workflow-preflight.json").read_text())
        assert receipt["assets_metadata_verified"] is True
        assert receipt["asset_file_contents_verified"] is False
        assert "NEVER-LOG-THIS-TOKEN" not in (out / "workflow-preflight.json").read_text()


def test_untracked_or_shallow_history_is_unknown(tmp_path):
    repo = repo_fixture(tmp_path)
    assert subject.original_owner(repo, "example")["attribution"] == "Unknown"


def test_workflow_structure_and_privacy():
    # BaseLoader avoids YAML 1.1 treating 'on' as a boolean.
    sweep = yaml.load((ROOT / ".github/workflows/catalog-smoke-sweep.yml").read_text(), Loader=yaml.BaseLoader)
    report = yaml.load((ROOT / ".github/workflows/agent-report-card.yml").read_text(), Loader=yaml.BaseLoader)
    assert sweep["on"]["schedule"] == [{"cron": "0 8 * * 1,4"}]
    assert sweep["on"]["workflow_dispatch"]["inputs"]["mode"]["default"] == "plan"
    live = sweep["jobs"]["sweep"]
    assert live["environment"] == "catalog-smoke" and int(live["timeout-minutes"]) >= 300
    assert "CATALOG_SMOKE_SCHEDULE_LIVE_ENABLED" in live["if"]
    assert live["env"]["CLIENT_TIMEOUT"] == "1800"
    steps = live["steps"]
    commands = "\n".join(s.get("run", "") for s in steps)
    assert commands.index("preflight.py gate") < commands.index("preflight.py auth") < commands.index("--mode live")
    for flag in ("--run-label", "--input-assets", "--max-invocations", "--allow-lanes", "--timeout"):
        assert flag in commands
    assert "--replace" not in commands and "--continue-independent" not in commands
    for workflow in (sweep, report):
        for job in workflow["jobs"].values():
            for step in job["steps"]:
                if "upload-artifact@" in step.get("uses", ""):
                    if "catalog-public-report" in step.get("with", {}).get("path", ""):
                        assert step["if"] == "always()"
                        paths = step["with"]["path"].splitlines()
                        assert len(paths) == 2
                        assert all(p.endswith(("/catalog-public-report.html", "/catalog-public-report.csv")) for p in paths)
                        assert not any("*" in p for p in paths)
                        continue
                    assert "always()" in step["if"] and "repository.private" in step["if"]
                    assert "ARTIFACTS_APPROVED" in step["if"]
                if "preflight.py finalize" in step.get("run", "") and workflow is sweep:
                    assert step["if"] == "always()"
    assert report["on"]["workflow_dispatch"]["inputs"]["notify"]["default"] == "false"
    report_job = report["jobs"]["report-card"]
    assert "CATALOG_SCORECARD_AUTOMATION_ENABLED" in report_job["if"]
    assert "issues" not in report.get("permissions", {})
    assert "issues" not in report_job.get("permissions", {})
    notification = report["jobs"]["notify"]
    assert "workflow_dispatch" in notification["if"] and "inputs.notify == true" in notification["if"]
    assert "NOTIFICATIONS_APPROVED" in notification["if"] and "repository.private" in notification["if"]
    assert notification["environment"] == "catalog-smoke"
    report_text = (ROOT / ".github/workflows/agent-report-card.yml").read_text()
    assert "--relay-to-authors" not in report_text and "cat " not in report_text
    assert "--history" not in report_text  # no unverified failing streak carry-forward
    assert "fetch-depth: 0" in report_text and "github.event.repository.default_branch" in report_text