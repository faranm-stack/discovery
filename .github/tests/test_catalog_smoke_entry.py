"""Exercise the public plan and library boundary without a signed-in user or a service."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import catalog_sweep as sweep
import run_catalog_smoke as session


@pytest.mark.parametrize("mode", ["preflight", "live", "reconcile"])
def test_remote_mode_rejected_before_creating_output(mode, tmp_path):
    with pytest.raises(SystemExit) as exc:
        sweep.main(["--mode", mode, "--output-dir", str(tmp_path / "out")])
    assert exc.value.code == 2 and not list(tmp_path.iterdir())


def test_production_adapter_cannot_fall_back_to_a_local_login(tmp_path):
    with pytest.raises(ValueError, match="credential"):
        sweep.CatalogHttp("https://test.workspace.discovery.azure.com")
    with pytest.raises(ValueError, match="credential"):
        asyncio.run(session.run_live(SimpleNamespace(), {}, tmp_path))


def test_lookup_client_rejects_mutations_before_request_or_auth():
    client = sweep.CatalogHttp("https://test.workspace.discovery.azure.com", credential=object())
    with pytest.raises(ValueError, match="read-only"):
        asyncio.run(client.request("POST", client.endpoint, {"name": "fake"}))


def test_real_plan_accounts_for_every_folder_without_online_calls(monkeypatch, tmp_path):
    def no_remote(*args, **kwargs):
        pytest.fail("offline CLI tried the network or a live adapter")

    import socket
    monkeypatch.setattr(socket.socket, "connect", no_remote)
    monkeypatch.setattr(socket, "getaddrinfo", no_remote)
    monkeypatch.setattr(sweep, "execute_sweep", no_remote)
    repo = Path(__file__).resolve().parents[2]
    output = tmp_path / "plan"
    code = sweep.main(["--repo-root", str(repo), "--output-dir", str(output)])
    ledger = json.loads((output / "catalog-results.json").read_text(encoding="utf-8"))
    plan = json.loads((output / "catalog-plan.json").read_text(encoding="utf-8"))
    names = {p.name for p in (repo / "agents").iterdir() if p.is_dir()}
    assert {r["catalog_agent"] for r in ledger["results"]} == names
    assert ledger["total"] == len(names) == len(ledger["results"]) > 0
    assert ledger["counts"] == {"not_run": len(names)} and ledger["submitted_responses"] == 0
    assert not plan["execution_enabled"] and all(not c["live_ready"] for c in plan["cases"])
    assert code == int(bool(sweep.generate_manifest(repo)["schema_errors"]))


def test_canary_selection_cannot_substitute_an_agent(monkeypatch, tmp_path):
    monkeypatch.setattr(sweep, "generate_manifest", lambda repo: {"agents": [{"name": "autodock"}]})
    with pytest.raises(SystemExit) as exc:
        sweep.main(["--studio-probe", "--output-dir", str(tmp_path / "out")])
    assert exc.value.code == 2 and not list(tmp_path.iterdir())