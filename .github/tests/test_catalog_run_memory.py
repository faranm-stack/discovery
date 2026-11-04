import json
import pytest
from catalog_run_memory import require_current_inventory, write_run_memory


def fetch(url):
    if url.endswith("/commits/main"):
        return {"sha": "abc123"}
    assert "ref=abc123" in url
    return [{"name": n, "type": "dir"} for n in ["cpu-agent", "new-gpu-agent"]]


def test_missing_gpu_and_extra_legacy_agent_block_launch_with_report(tmp_path):
    with pytest.raises(ValueError, match="differs"):
        require_current_inventory(tmp_path, ["cpu-agent", "legacy"], fetch)
    gate = json.loads((tmp_path / "inventory-gate.json").read_text())
    assert gate["missing"] == ["new-gpu-agent"]
    assert gate["extra"] == ["legacy"]
    assert (tmp_path / "preflight-report.md").exists()


def test_verified_inventory_records_commit_not_content_parity(tmp_path):
    result = require_current_inventory(tmp_path, ["cpu-agent", "new-gpu-agent"], fetch)
    assert result["exact_inventory_match"]
    assert result["upstream_commit"] == "abc123"
    assert result["source_content_verified"] is False


def test_unavailable_upstream_fails_closed_with_report(tmp_path):
    def unavailable(url):
        raise OSError("offline")
    with pytest.raises(ValueError, match="unavailable"):
        require_current_inventory(tmp_path, [], unavailable)
    assert (tmp_path / "preflight-report.md").exists()


def test_run_memory_preserves_blocker_not_fake_failure_or_retry(tmp_path):
    ledger = {"counts": {"blocked": 1}, "total": 1, "submitted_responses": 0}
    card = {"totals": {"grades": {"I": 1}}, "agents": [{"agent": "new-gpu-agent", "status": "blocked",
        "grade": "I", "category": "input_not_staged", "reason": "input_not_staged", "fix_owner": "harness",
        "suggested_fix": "Stage input and verify attachment"}]}
    memory = write_run_memory(tmp_path, ledger, card)
    assert memory["actions"][0]["grade"] == "I"
    assert memory["actions"][0]["automatic_retry_authorized"] is False
    assert memory["inventory"]["full_current_catalog_claim_allowed"] is False
    assert (tmp_path / "next-run-handoff.md").exists()