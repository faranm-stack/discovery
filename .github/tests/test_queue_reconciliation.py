"""Offline queue/receipt contracts. All service clients are in-memory fakes."""

import asyncio
import copy
import json
from collections import Counter
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

import catalog_sweep as sweep
import run_catalog_smoke as smoke


ENDPOINT = "https://test.workspace.discovery.azure.com"


@pytest.fixture(autouse=True)
def forbid_live_clients(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A real cloud client or invocation must never run in these tests")
    monkeypatch.setattr(sweep, "CatalogHttp", forbidden)
    monkeypatch.setattr(sweep, "run_live", forbidden)


def case(name, lane="cpu"):
    return {"catalog_agent": name, "lane": lane}


def terminal(name, status="passed"):
    return {"catalog_agent": name, "status": status, "remote_may_be_running": False}


class Clock:
    def __init__(self):
        self.now = 0
        self.waits = []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds
        await asyncio.sleep(0)


@pytest.mark.parametrize("status", ["passed", "failed", "blocked"])
def test_verified_terminal_release_never_resubmits_held_or_duplicate_cases(status):
    async def scenario():
        seen, checked = [], []
        async def worker(item):
            seen.append(item["catalog_agent"])
            return terminal(item["catalog_agent"])
        async def reconcile(held):
            checked.extend(item["catalog_agent"] for item in held)
            held[0]["lane"] = "mutated-copy"
            return [terminal("old", status)]
        old = case("old")
        rows = await sweep.run_queue([old, case("new"), case("new")], worker,
            held_cases=[old, old], concurrency=1, continue_independent=True,
            reconcile=reconcile, max_reconcile_checks=1)
        assert seen == ["new"] and checked == ["old"]
        assert old["lane"] == "cpu"
        assert len(rows) == 2
        assert rows[0]["status"] == status and rows[0]["remote_may_be_running"] is False
    asyncio.run(scenario())


@pytest.mark.parametrize("patch", [
    {"status": "passed"},
    {"status": "passed", "remote_may_be_running": None},
    {"status": "passed", "remote_may_be_running": 0},
    {"status": "passed", "remote_may_be_running": "false"},
    {"status": "passed", "remote_may_be_running": True},
    {"status": "uncertain", "remote_may_be_running": False},
    {"status": "inconclusive", "remote_may_be_running": False},
    {"status": "ready", "remote_may_be_running": False},
    {"status": "not_run", "remote_may_be_running": False},
    {"status": "passed", "remote_may_be_running": False, "error": "bad evidence"},
    {"status": "failed", "remote_may_be_running": False, "reconciliation_error": "TimeoutError"},
])
def test_unverified_rows_retain_capacity_until_check_budget_is_exhausted(patch):
    async def scenario():
        clock, checks = Clock(), []
        async def worker(item):
            pytest.fail("Held capacity was released without explicit terminal proof")
        async def reconcile(held):
            checks.append(clock())
            return [{"catalog_agent": "old", **patch}]
        rows = await sweep.run_queue([case("new")], worker, held_cases=[case("old")],
            concurrency=1, continue_independent=True, reconcile=reconcile,
            max_reconcile_checks=3, reconcile_interval=5, clock=clock, sleep=clock.sleep)
        assert checks == [0, 5, 10]
        assert {r["catalog_agent"]: r["status"] for r in rows} == {"new": "not_run", "old": "uncertain"}
        assert rows[1]["remote_may_be_running"] is True
    asyncio.run(scenario())


@pytest.mark.parametrize("rows", [None, {}, [None], [],
    [terminal("other")], [terminal("old"), terminal("old")]])
def test_missing_malformed_or_ambiguous_callback_output_cannot_release(rows):
    async def scenario():
        async def worker(item):
            pytest.fail("Invalid evidence released a reservation")
        async def reconcile(held):
            return rows
        result = await sweep.run_queue([case("new")], worker, held_cases=[case("old")],
            concurrency=1, continue_independent=True, reconcile=reconcile, max_reconcile_checks=1)
        assert [r["status"] for r in result] == ["not_run", "uncertain"]
    asyncio.run(scenario())


@pytest.mark.parametrize("enabled,budget", [(False, 3), (True, 0)])
def test_reconciliation_requires_explicit_continuation_and_nonzero_budget(enabled, budget):
    async def scenario():
        async def forbidden(items):
            pytest.fail("Disabled supervision or worker was invoked")
        rows = await sweep.run_queue([case("new")], forbidden, held_cases=[case("old")],
            concurrency=1, continue_independent=enabled, reconcile=forbidden, max_reconcile_checks=budget)
        assert [r["status"] for r in rows] == ["not_run", "uncertain"]
    asyncio.run(scenario())


@pytest.mark.parametrize("option,value", [
    ("concurrency", 0), ("concurrency", 5), ("concurrency", 1.5),
    ("max_reconcile_checks", -1), ("max_reconcile_checks", 11), ("max_reconcile_checks", 1.5),
    ("reconcile_interval", 4), ("reconcile_interval", 301),
    ("reconcile_timeout", 0), ("reconcile_timeout", 901), ("reconcile_timeout", float("nan")),
])
def test_queue_bounds_are_validated_before_callbacks(option, value):
    async def forbidden(items):
        pytest.fail("Validation must precede all work")
    with pytest.raises(ValueError):
        asyncio.run(sweep.run_queue([], forbidden, **{option: value}))


def test_callback_timeout_is_cancelled_counted_and_retains_reservation():
    async def scenario():
        entered, cancelled = [], []
        clock = Clock()
        async def worker(item):
            pytest.fail("Timed-out callback released capacity")
        async def reconcile(held):
            entered.append(True)
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)
        rows = await sweep.run_queue([case("new")], worker, held_cases=[case("old")],
            concurrency=1, continue_independent=True, reconcile=reconcile,
            max_reconcile_checks=2, reconcile_timeout=0.01,
            reconcile_interval=5, clock=clock, sleep=clock.sleep)
        assert len(entered) == len(cancelled) == 2
        assert rows[1]["reconciliation_error"] == "TimeoutError"
        assert rows[1]["status"] == "uncertain" and rows[0]["status"] == "not_run"
    asyncio.run(scenario())


def test_transient_callback_failure_can_recover_without_retrying_worker():
    async def scenario():
        clock, checks, seen = Clock(), [], []
        async def worker(item):
            seen.append(item["catalog_agent"])
            return terminal(item["catalog_agent"])
        async def reconcile(held):
            checks.append(True)
            if len(checks) == 1:
                raise OSError("private service details must not be exported")
            return [terminal("old")]
        rows = await sweep.run_queue([case("new")], worker, held_cases=[case("old")],
            concurrency=1, continue_independent=True, reconcile=reconcile,
            max_reconcile_checks=2, reconcile_interval=5, clock=clock, sleep=clock.sleep)
        assert seen == ["new"] and len(checks) == 2
        assert all(r["status"] == "passed" for r in rows)
        assert "reconciliation_error" not in rows[1]
        assert "private service" not in json.dumps(rows)
    asyncio.run(scenario())


@pytest.mark.parametrize("code", ["http_401", "http_403"])
@pytest.mark.parametrize("source", ["exception", "reason", "reconciliation_error", "error"])
def test_access_denial_stops_all_new_dispatch_and_further_checks(code, source):
    async def scenario():
        checks = []
        async def worker(item):
            pytest.fail("Dispatch after denied access")
        async def reconcile(held):
            checks.append(True)
            if source == "exception":
                raise smoke.SmokeError(code, "denied")
            return [{**terminal("old"), source: {"code": code} if source == "error" else code}]
        rows = await sweep.run_queue([case("new")], worker, held_cases=[case("old")],
            concurrency=2, continue_independent=True, reconcile=reconcile, max_reconcile_checks=3)
        assert len(checks) == 1
        assert rows[0]["status"] == "not_run"
        assert rows[1]["stop_new_work"] is True and rows[1]["remote_may_be_running"] is True
    asyncio.run(scenario())


def test_denial_finishing_during_reconciliation_is_consumed_before_dispatch():
    async def scenario():
        seen, checks = [], []
        release = asyncio.Event()
        clock = Clock()
        async def worker(item):
            seen.append(item["catalog_agent"])
            if item["catalog_agent"] == "held":
                return {"status": "uncertain", "remote_may_be_running": True}
            await release.wait()
            return {"status": "blocked", "reason": "http_403"}
        async def reconcile(held):
            checks.append(True)
            release.set()
            await asyncio.sleep(0)
            return [terminal("held")]
        rows = await sweep.run_queue([case("held"), case("denied"), case("never")], worker,
            concurrency=2, continue_independent=True, reconcile=reconcile, max_reconcile_checks=3,
            reconcile_interval=5, clock=clock, sleep=clock.sleep)
        assert seen == ["held", "denied"] and len(checks) == 1
        assert rows[2]["status"] == "not_run"
    asyncio.run(scenario())


def test_held_reservations_count_against_global_and_lane_caps():
    async def scenario():
        active, peak, seen = Counter(), Counter(), []
        async def worker(item):
            lane = item["lane"]
            active[lane] += 1
            peak[lane] = max(peak[lane], active[lane])
            assert sum(active.values()) + 2 <= 4
            assert active["cpu"] + 1 <= sweep.LANE_CONCURRENCY["cpu"]
            assert active["gpu_capable"] == 0
            seen.append(item["catalog_agent"])
            await asyncio.sleep(0)
            active[lane] -= 1
            return terminal(item["catalog_agent"])
        items = [case("gpu", "gpu_capable")] + [case(f"cpu-{i}") for i in range(4)]
        items += [case(f"ext-{i}", "external_or_platform") for i in range(4)]
        rows = await sweep.run_queue(items, worker, concurrency=4, continue_independent=True,
            held_cases=[case("old-cpu"), case("old-gpu", "gpu_capable")])
        assert len(seen) == 8 and "gpu" not in seen and peak["cpu"] == 1
        assert rows[0]["status"] == "not_run" and len(rows) == 11
    asyncio.run(scenario())


def response_with_operations(count=0, status="completed", confirmation=None):
    response = {"id": "response-test", "status": status, "output": [],
                "discoveryProperties": {"itemExtensions": {}}}
    for i in range(count):
        response["output"].extend([
            {"type": "function_call", "id": f"fc-{i}", "call_id": f"call-{i}",
             "name": "example-Execute" if i == 0 else "other-Execute", "status": "completed"},
            {"type": "function_call_output", "call_id": f"call-{i}", "output": "receipt", "status": "completed"},
        ])
        response["discoveryProperties"]["itemExtensions"][f"fc-{i}"] = {"supercomputerOperationId": f"{i:032x}"}
    if confirmation is not None:
        response["discoveryProperties"]["confirmations"] = [{"status": confirmation, "toolName": "example"}]
    return response


def setup_reconciliation(tmp_path, names=("old",)):
    args = SimpleNamespace(workspace_api_url=ENDPOINT, project="weekly-agent-svc-test", output_dir=tmp_path)
    store = sweep.SweepStore(tmp_path / "live", "hash")
    plan = {"cases": [case(name) for name in names]}
    for name in names:
        store.claim(name)
        store.save(name, {"status": "uncertain", "remote_may_be_running": True,
                          "response_submissions": 1, "human_approval_pending": True})
        sweep.save_json(store._folder(name) / "summary.json", {
            "workspace_api_url": ENDPOINT, "project": args.project, "conversation_id": f"conversation-{name}",
            "response_id": "response-test", "expected_tools": ["example"], "submission_attempted": True,
            "phase": "approval_checkpoint", "reason": "confirmation_requires_human_review",
            "human_approval_pending": True})
        sweep.save_json(store._folder(name) / "approval-needed.json", {"status": "awaiting_human_approval"})
    return args, plan, store


def fake_reconciliation_client(monkeypatch, response, *, operation_state="Succeeded", error=None, wrong_id=False):
    requests = []
    class Client:
        def __init__(self, endpoint):
            assert endpoint == ENDPOINT
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def request(self, method, url, body=None):
            assert method == "GET" and body is None
            requests.append(url)
            if error and (error[0] == "response" or "/operations/" in url):
                raise smoke.SmokeError(error[1], "private service body")
            if "/operations/" not in url:
                return copy.deepcopy(response)
            operation_id = urlsplit(url).path.rsplit("/", 1)[-1]
            return {"id": "wrong" if wrong_id else operation_id, "status": operation_state}
    monkeypatch.setattr(sweep, "CatalogHttp", Client)
    return requests


@pytest.mark.parametrize("state,expected", [("Succeeded", "passed"), ("Failed", "failed"),
                                          ("Canceled", "failed"), ("Running", "uncertain")])
def test_read_only_reconciliation_clears_approval_but_requires_operation_terminal(tmp_path, monkeypatch, state, expected):
    args, plan, store = setup_reconciliation(tmp_path)
    requests = fake_reconciliation_client(monkeypatch, response_with_operations(1, confirmation="Confirmed"), operation_state=state)
    rows = asyncio.run(sweep.reconcile_sweep(args, plan, store))
    assert len(requests) == 2 and rows[0]["status"] == expected
    assert rows[0]["remote_may_be_running"] is (expected == "uncertain")
    assert rows[0]["human_approval_pending"] is False
    assert rows[0]["observed_tool_call_count"] == 1 and rows[0]["response_submissions"] == 1
    folder = store._folder("old")
    assert not (folder / "approval-needed.json").exists()
    summary = json.loads((folder / "summary.json").read_text())
    assert summary["human_approval_pending"] is False and summary["phase"] == "reconciliation"
    assert summary["remote_may_be_running"] is (expected == "uncertain")
    assert summary["server_may_be_running"] is (expected == "uncertain")
    receipt = json.loads((args.output_dir / rows[0]["reconciliation"] / "decision.json").read_text())
    assert receipt["new_submissions"] == 0
    assert store.read("old")["status"] == expected


@pytest.mark.parametrize("fault", ["pending", "nonterminal", "wrong_response", "wrong_operation", "operation_error"])
def test_reconciliation_fails_closed_and_clears_only_verified_stale_approval(tmp_path, monkeypatch, fault):
    args, plan, store = setup_reconciliation(tmp_path)
    response = response_with_operations(1, confirmation="Pending" if fault == "pending" else "Confirmed")
    if fault == "nonterminal":
        response["status"] = "in_progress"
    if fault == "wrong_response":
        response["id"] = "unrelated"
    fake_reconciliation_client(monkeypatch, response, wrong_id=fault == "wrong_operation",
                               error=("operation", "http_503") if fault == "operation_error" else None)
    rows = asyncio.run(sweep.reconcile_sweep(args, plan, store))
    assert rows[0]["status"] == "uncertain" and rows[0]["remote_may_be_running"] is True
    retained = fault in {"pending", "wrong_response"}
    assert (store._folder("old") / "approval-needed.json").exists() is retained
    assert store.read("old")["human_approval_pending"] is retained


@pytest.mark.parametrize("code", ["http_401", "http_403"])
@pytest.mark.parametrize("stage", ["response", "operation"])
def test_reconciliation_denial_stops_reads_and_persists_stop_across_resume(tmp_path, monkeypatch, code, stage):
    args, plan, store = setup_reconciliation(tmp_path, ("old", "other"))
    requests = fake_reconciliation_client(monkeypatch, response_with_operations(1), error=(stage, code))
    rows = asyncio.run(sweep.reconcile_sweep(args, plan, store))
    assert len(requests) == (1 if stage == "response" else 2)
    assert all(r["stop_new_work"] and r["status"] == "uncertain" for r in rows)
    assert all(store.read(name)["stop_new_work"] for name in ("old", "other"))
    args.mode = "live"
    plan["cases"].append(case("new"))
    resumed = asyncio.run(sweep.execute_sweep(args, plan, store))
    assert resumed[-1]["status"] == "not_run"
    assert len(requests) == (1 if stage == "response" else 2)


@pytest.mark.parametrize("count", [16, 17])
def test_reconciliation_receipt_bound_includes_unexpected_tools(tmp_path, monkeypatch, count):
    args, plan, store = setup_reconciliation(tmp_path)
    requests = fake_reconciliation_client(monkeypatch, response_with_operations(count))
    rows = asyncio.run(sweep.reconcile_sweep(args, plan, store))
    assert len(requests) == (17 if count == 16 else 1)
    assert rows[0]["observed_tool_call_count"] == count
    assert rows[0]["status"] == ("passed" if count == 16 else "uncertain")
    if count == 17:
        assert rows[0]["reconciliation_error"] == "multiple_operations"


def test_reconciliation_subset_and_missing_identity_never_submit(tmp_path, monkeypatch):
    args, plan, store = setup_reconciliation(tmp_path, ("old", "other"))
    (store._folder("old") / "summary.json").unlink()
    requests = fake_reconciliation_client(monkeypatch, response_with_operations())
    rows = asyncio.run(sweep.reconcile_sweep(args, plan, store, cases=[plan["cases"][0]]))
    assert len(rows) == 1 and rows[0]["status"] == "uncertain" and requests == []


async def run_fake_live_session(tmp_path, response):
    """Exercise the real session implementation, including its HTTP adapter."""
    requests = []
    class Reply:
        status, headers = 200, {}
        def __init__(self, payload):
            self.payload, self.content = payload, self
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def iter_chunked(self, size):
            yield json.dumps(self.payload).encode()
    class Session:
        def request(self, method, url, **kwargs):
            path = urlsplit(url).path
            requests.append((method, path, kwargs.get("json")))
            assert "confirm" not in path.lower()
            if method == "POST":
                assert path.endswith("/responses")
                return Reply({"id": "response-test"})
            assert method == "GET"
            if "/agents/" in path:
                return Reply({"provisioningState": "Succeeded"})
            if "/operations/" in path:
                return Reply({"id": path.rsplit("/", 1)[-1], "status": "Succeeded"})
            assert path.endswith("/responses/response-test")
            return Reply(response)
    class Client:
        def __init__(self, base, *, token, api_version):
            self.base, self.token = base, token
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def _ensure_session(self):
            return Session()
        def _url(self, *parts, query=None):
            return self.base + "/" + "/".join(parts)
        async def create_investigation(self, project, name, **kwargs):
            assert name == "example"
        async def create_conversation(self, project, investigation):
            return "conversation-test"
    args = SimpleNamespace(workspace_api_url=ENDPOINT, project="weekly-agent-svc-test",
                           agent="example", preflight_only=False, contract="roundtrip")
    report = {"investigation": "example", "prompt": "original approved prompt", "expected_tools": ["example"],
              "server_terminal_observed": False}
    token = SimpleNamespace(token="fake-not-a-credential", expires_on=float("inf"))
    aiohttp = SimpleNamespace(ClientTimeout=lambda **kwargs: None)
    try:
        await smoke._run_live_session(args, report, tmp_path, None, token, aiohttp, Client)
    except smoke.SmokeError as exc:
        report["caught_error"] = exc.code
    return report, requests


def test_live_approval_checkpoint_returns_promptly_with_ids_prompt_and_reservation(tmp_path):
    async def scenario():
        seen = []
        async def worker(item):
            seen.append(item["catalog_agent"])
            report, requests = await run_fake_live_session(tmp_path, response_with_operations(1, "in_progress", "Pending"))
            assert report["phase"] == "approval_checkpoint" and report["human_approval_pending"] is True
            assert report["prompt"] == "original approved prompt"
            assert report["remote_may_be_running"] is True
            assert report["submission_attempted"] and report["response_id"] == "response-test"
            assert sum(method == "POST" for method, _, _ in requests) == 1
            assert sum(path.endswith("/responses/response-test") for _, path, _ in requests) == 1
            assert not any("/operations/" in path for _, path, _ in requests)
            assert json.loads((tmp_path / "operations.json").read_text()) == {}
            assert json.loads((tmp_path / "approval-needed.json").read_text())["status"] == "awaiting_human_approval"
            saved = json.loads((tmp_path / "summary.json").read_text())
            assert saved["conversation_id"] == "conversation-test" and saved["human_approval_pending"]
            response = json.loads((tmp_path / "response.json").read_text())
            return sweep.classify_execution(report, response, {})
        rows = await asyncio.wait_for(sweep.run_queue([case("old"), case("new")], worker,
            concurrency=1, continue_independent=True), timeout=1)
        assert seen == ["old"] and [r["status"] for r in rows] == ["uncertain", "not_run"]
    asyncio.run(scenario())


@pytest.mark.parametrize("count", [16, 17])
def test_live_receipt_fetches_are_bounded_at_sixteen_not_four(tmp_path, count):
    report, requests = asyncio.run(run_fake_live_session(tmp_path, response_with_operations(count)))
    operation_reads = [path for method, path, _ in requests if "/operations/" in path]
    assert len(operation_reads) == (16 if count == 16 else 0)
    assert report["observed_tool_call_count"] == count
    assert sum(method == "POST" for method, _, _ in requests) == 1
    if count == 17:
        assert report["caught_error"] == "multiple_operations"
    else:
        assert len(json.loads((tmp_path / "operations.json").read_text())) == 16
        assert report["verdict"] == "pass"


def setup_execute(tmp_path, monkeypatch, *, old_status="completed", invocation_cap=2):
    """Real sweep/store/queue wiring; fake only remote boundaries and source tree."""
    args, plan, store = setup_reconciliation(tmp_path)
    args.mode = "live"
    args.continue_independent = True
    args.subscription = "test-subscription"
    args.resource_group = "test-rg"
    args.model = "test-model"
    args.max_invocations = invocation_cap
    args.allow_lanes = {"cpu"}
    args.repo_root = tmp_path / "repo"
    args.agent_prefix = ""
    args.deploy_missing = False
    args.input_assets = {}
    args.timeout = 30
    args.concurrency = 2
    args.reconcile_checks = 1
    args.reconcile_interval = 5
    resources = [{"name": "example", "id": "/subscriptions/test/tools/example"}]
    deployed, invocations, requests = {}, [], []
    for name in ("new-a", "new-b"):
        item = {**case(name), "static_status": "passed", "source_fingerprints": {}, "candidate_prompt": "Approved task"}
        plan["cases"].append(item)
        source = {"kind": "prompt", "name": name, "instructions": "Use example.",
                  "model": {"options": {}}, "discoveryExtensions": {
                      "humanInTheLoop": "Enabled",
                      "tools": [{"toolId": "{{exampleToolId}}", "confirmation": "Enabled"}]}}
        folder = args.repo_root / "agents" / name
        (folder / "tools" / "example").mkdir(parents=True)
        (folder / "agent.yaml").write_text(json.dumps(source), encoding="utf-8")
        (folder / "tools" / "example" / "tool.yaml").write_text('{"name":"example"}', encoding="utf-8")
        payload, _ = sweep.build_agent_payload(source, args.model, {"{{exampleToolId}}": resources[0]["id"]})
        deployed[name] = {**payload, "provisioningState": "Succeeded"}
    class Client:
        def __init__(self, endpoint):
            assert endpoint == ENDPOINT
        async def __aenter__(self):
            return self
        async def __aexit__(self, *exc):
            return False
        async def collection(self, url):
            requests.append(("GET", url))
            return resources if "/tools?" in url else list(deployed.values())
        async def request(self, method, url, body=None):
            assert method == "GET" and body is None
            requests.append((method, url))
            if "/responses/" in url:
                return response_with_operations(status=old_status, confirmation="Confirmed")
            if "/agents/" in url:
                return deployed[urlsplit(url).path.rsplit("/", 1)[-1]]
            return {"properties": {"provisioningState": "Succeeded"}}
    async def invoke(local_args, report, output):
        invocations.append(local_args.agent)
        report.update(submission_attempted=True, response_id=f"response-{local_args.agent}",
                      conversation_id=f"conversation-{local_args.agent}", usage={"total_tokens": 9})
        response = {"id": report["response_id"], "status": "completed", "output": [
            {"type": "function_call", "id": "fc", "call_id": "call", "name": "example-Execute", "status": "completed"},
            {"type": "function_call_output", "call_id": "call", "status": "completed", "output": {"success": True}},
        ]}
        sweep.save_json(output / "response.json", response)
        sweep.save_json(output / "operations.json", {})
    monkeypatch.setattr(sweep, "CatalogHttp", Client)
    monkeypatch.setattr(sweep, "run_live", invoke)
    return args, plan, store, invocations, requests


def test_execute_sweep_reconciles_old_once_respects_invocation_cap_and_persists_results(tmp_path, monkeypatch):
    args, plan, store, invocations, requests = setup_execute(tmp_path, monkeypatch)
    rows = asyncio.run(sweep.execute_sweep(args, plan, store))
    assert [r["catalog_agent"] for r in rows] == ["old", "new-a", "new-b"]
    assert [r["status"] for r in rows] == ["failed", "passed", "not_run"]
    assert invocations == ["new-a"]
    assert sum(r.get("response_submissions", 0) for r in rows) == 2
    assert sum("/responses/" in url for _, url in requests) == 1
    assert rows[0]["human_approval_pending"] is False
    assert store.read("old")["status"] == "failed"
    assert store.read("new-a")["response_submissions"] == 1
    resumed = asyncio.run(sweep.execute_sweep(args, plan, store))
    assert [r["status"] for r in resumed] == ["failed", "passed", "not_run"]
    assert invocations == ["new-a"]  # Even not_run checkpoints are not reclaimed.


def test_execute_sweep_retains_running_reservation_after_budget_exhaustion(tmp_path, monkeypatch):
    args, plan, store, invocations, requests = setup_execute(tmp_path, monkeypatch, old_status="in_progress")
    args.concurrency = 1
    rows = asyncio.run(sweep.execute_sweep(args, plan, store))
    assert [r["status"] for r in rows] == ["uncertain", "not_run", "not_run"]
    assert invocations == [] and sum("/responses/" in url for _, url in requests) == 1
    assert store.read("old")["remote_may_be_running"] is True


@pytest.mark.parametrize("code", ["http_401", "http_403"])
def test_execute_sweep_persists_supervisor_failure_even_before_callback_checkpoint(tmp_path, monkeypatch, code):
    args, plan, store, invocations, requests = setup_execute(tmp_path, monkeypatch)
    checks = []
    async def deny(*args, **kwargs):
        checks.append(True)
        raise smoke.SmokeError(code, "private denied body")
    monkeypatch.setattr(sweep, "reconcile_sweep", deny)
    rows = asyncio.run(sweep.execute_sweep(args, plan, store))
    assert len(checks) == 1 and invocations == []
    assert rows[0]["stop_new_work"] is True
    assert store.read("old")["stop_new_work"] is True
    before = len(requests)
    asyncio.run(sweep.execute_sweep(args, plan, store))
    assert len(requests) == before and len(checks) == 1 and invocations == []