"""Offline CLI regression probe for microsoft/discovery PR 100.

Run only in the isolated container described by the companion workflow:
    python -I -B /harness/pr100_capture_exit_probe.py \
        --source /source/evaluators --results /results

The six original Python modules are hash-checked and never edited. The real
Discovery client, capture/conversion code, Foundry result aggregation, reporting,
argument parser, and __main__ exit execute. Only HTTP transport, credentials,
and the Foundry SDK boundary are fakes. This does not validate live services or
installed SDK compatibility. There are no package installs or Azure calls.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
from types import ModuleType, SimpleNamespace
import unittest
from urllib.parse import urlsplit


PR_SHA = "1a16c98f1aadc6a8d20d9ac9956b8c35f68d8c28"
SOURCE_BLOBS = {
    "azure_credential.py": "b5ab703bfd10355c7568e4e16758617e1d5461a1",
    "discovery_client.py": "e315a1fad8fba58f9547d89b797c4e33539fce9d",
    "eval_datasets.py": "14637d876ccac3214cf8b4c32646a9979af30857",
    "pipeline.py": "3dcbb4c239b7247f69d63eb10b239d9fa3e9bd48",
    "responses_to_eval_dataset.py": "9f430e32c1b1e97888a820e892b1e0bf1ea14a58",
    "run_offline_eval.py": "37e2c25af92357ab8dcf656f0499172da101784e",
}
EXPECTED = {
    "all_capture_failures": {"errored": 2, "failed": 2, "none": 0},
    "partial_capture_failure": {"errored": 2, "failed": 2, "none": 0},
    "all_pass": {"errored": 0, "failed": 0, "none": 0},
    "criterion_failure": {"errored": 0, "failed": 2, "none": 0},
    "evaluator_item_error": {"errored": 2, "failed": 2, "none": 0},
}


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def register_module(name: str, **attributes: object) -> None:
    module = ModuleType(name)
    module.__path__ = []
    for key, value in attributes.items():
        setattr(module, key, value)
    sys.modules[name] = module
    if "." in name:
        parent, attribute = name.rsplit(".", 1)
        setattr(sys.modules[parent], attribute, module)


def run_child(source: Path, directory: Path, scenario: str, mode: str) -> None:
    if scenario not in EXPECTED or mode not in EXPECTED[scenario]:
        raise ValueError("Unknown test scenario or fail-on mode")
    trace = {
        "attempted_queries": [],
        "captured_queries": [],
        "investigations": 0,
        "conversations": 0,
        "evaluation_rows": 0,
        "evaluation_runs": 0,
        "session_closed": False,
        "network_attempts": 0,
    }
    responses = {}
    scored_rows = []

    def deny_network(event, arguments):
        if event in ("socket.connect", "socket.getaddrinfo", "socket.bind"):
            trace["network_attempts"] += 1
            raise RuntimeError(f"Unexpected network attempt: {event}")

    sys.addaudithook(deny_network)

    class FakeCredential:
        def get_token(self, scope):
            if scope != "https://discovery.azure.com/.default":
                raise AssertionError(f"Unexpected credential scope: {scope}")
            return SimpleNamespace(token="offline-fixture-not-a-credential")

    class FakeHttpResponse:
        def __init__(self, status, payload):
            self.status = status
            self.payload = payload

        async def __aenter__(self):
            return self

        async def __aexit__(self, *arguments):
            return None

        async def text(self):
            return json.dumps(self.payload)

    class FakeHttpSession:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True
            trace["session_closed"] = True

        def request(self, method, url, *, data=None, headers=None):
            parsed = urlsplit(url)
            if parsed.hostname != "discovery.invalid":
                raise AssertionError(f"Unexpected HTTP target: {url}")
            path = parsed.path
            if method == "PUT" and "/investigations/" in path:
                trace["investigations"] += 1
                return FakeHttpResponse(201, {})
            if method == "POST" and path == "/conversations":
                trace["conversations"] += 1
                return FakeHttpResponse(
                    201, {"name": f"offline-conversation-{trace['conversations']}"}
                )
            if method == "POST" and path.endswith("/openai/v1/responses"):
                body = json.loads(data.decode("utf-8"))
                query = body["input"][0]["content"][0]["text"]
                if body["agent"] != {"type": "agent_reference", "name": "offline-agent"}:
                    raise AssertionError("The real CLI did not forward the fixture agent")
                trace["attempted_queries"].append(query)
                if scenario == "all_capture_failures" or (
                    scenario == "partial_capture_failure" and query == "query-1"
                ):
                    return FakeHttpResponse(
                        503,
                        {"error": {"message": "Controlled offline capture failure"}},
                    )
                response_id = f"offline-response-{trace['conversations']}"
                responses[response_id] = (
                    query,
                    {
                        "id": response_id,
                        "status": "completed",
                        "tools": [],
                        "output": [{
                            "type": "message",
                            "role": "assistant",
                            "content": [{
                                "type": "output_text",
                                "text": "Controlled offline answer.",
                            }],
                        }],
                    },
                )
                return FakeHttpResponse(
                    201, {"id": response_id, "status": "in_progress"}
                )
            if method == "GET" and path.rsplit("/", 1)[-1] in responses:
                query, response = responses[path.rsplit("/", 1)[-1]]
                trace["captured_queries"].append(query)
                return FakeHttpResponse(200, response)
            raise AssertionError(f"Unexpected HTTP operation: {method} {url}")

    def create_eval(**kwargs):
        if kwargs["testing_criteria"][0]["evaluator_name"] != "builtin.coherence":
            raise AssertionError("Fixture evaluator was not forwarded")
        return SimpleNamespace(id="offline-eval")

    def create_run(**kwargs):
        if kwargs["eval_id"] != "offline-eval":
            raise AssertionError("Unexpected evaluation id")
        content = kwargs["data_source"]["source"]["content"]
        scored_rows.extend(entry["item"] for entry in content)
        for row in scored_rows:
            if row["response"][0]["role"] != "assistant":
                raise AssertionError("Real response conversion did not produce an answer")
        trace["evaluation_rows"] = len(scored_rows)
        trace["evaluation_runs"] += 1
        return SimpleNamespace(
            id="offline-run", eval_id="offline-eval",
            status="completed", result_counts={"total": len(scored_rows)},
        )

    def retrieve_run(**kwargs):
        if kwargs != {"run_id": "offline-run", "eval_id": "offline-eval"}:
            raise AssertionError("Unexpected evaluation polling arguments")
        return SimpleNamespace(
            id="offline-run", eval_id="offline-eval",
            status="completed", result_counts={"total": len(scored_rows)},
        )

    def list_output_items(**kwargs):
        if kwargs != {"run_id": "offline-run", "eval_id": "offline-eval"}:
            raise AssertionError("Unexpected evaluation result arguments")
        items = []
        for index, row in enumerate(scored_rows):
            if scenario == "evaluator_item_error" and index == 0:
                items.append({
                    "id": f"item-{index}", "status": "error",
                    "error": {"message": "Controlled offline evaluator error"},
                    "results": [],
                })
                continue
            passed = not (scenario == "criterion_failure" and index == 0)
            items.append({
                "id": f"item-{index}", "status": "pass" if passed else "fail",
                "results": [{
                    "name": "coherence", "passed": passed,
                    "score": 5 if passed else 1,
                }],
            })
        return items

    class FakeProject:
        def __init__(self, *, endpoint, credential):
            if endpoint != "https://foundry.invalid/api/projects/offline":
                raise AssertionError("Unexpected Foundry endpoint")

        def get_openai_client(self):
            return SimpleNamespace(evals=SimpleNamespace(
                create=create_eval,
                runs=SimpleNamespace(
                    create=create_run, retrieve=retrieve_run,
                    output_items=SimpleNamespace(list=list_output_items),
                ),
            ))

    register_module("azure")
    register_module("azure.ai")
    register_module("azure.identity", DefaultAzureCredential=FakeCredential)
    register_module("azure.ai.projects", AIProjectClient=FakeProject)
    register_module("openai")
    register_module("openai.types")
    register_module("openai.types.eval_create_params", DataSourceConfigCustom=dict)
    register_module("aiohttp", ClientSession=FakeHttpSession, ClientError=ConnectionError)

    dataset_dir = directory / "input"
    output_dir = directory / "output"
    dataset_dir.mkdir()
    write_json(dataset_dir / "probe-evaluators.json", {
        "evaluators": ["builtin.coherence"],
        "data": [{"query": "query-1"}, {"query": "query-2"}],
    })
    pipeline = source / "pipeline.py"
    sys.argv = [
        str(pipeline),
        "--workspace-api-url", "https://discovery.invalid",
        "--discovery-project", "offline-project",
        "--agent", "offline-agent",
        "--foundry-project-endpoint", "https://foundry.invalid/api/projects/offline",
        "--dataset-dir", str(dataset_dir),
        "--suites", "probe",
        "--output-dir", str(output_dir),
        "--fail-on", mode,
        "--timeout", "1",
        "--eval-timeout", "1",
        "--concurrency", "1",
    ]
    try:
        runpy.run_path(str(pipeline), run_name="__main__")
        raise AssertionError("The original CLI did not raise SystemExit")
    finally:
        write_json(directory / "boundary-trace.json", trace)


def run_tests(source: Path, results: Path) -> int:
    results.mkdir(parents=True, exist_ok=True)
    source_receipt = {}
    for name, expected_blob in SOURCE_BLOBS.items():
        payload = (source / name).read_bytes()
        actual = hashlib.sha1(
            f"blob {len(payload)}\0".encode("ascii") + payload
        ).hexdigest()
        if actual != expected_blob:
            raise RuntimeError(f"Original source mismatch: {name}: {actual}")
        source_receipt[name] = actual
    forbidden = [
        key for key in os.environ
        if key.startswith(("AZURE_", "GITHUB_", "ACTIONS_"))
        or key == "DISCOVERY_TOKEN"
    ]
    if forbidden:
        raise RuntimeError(f"Runner variables leaked into container: {forbidden}")
    write_json(results / "expectations.json", EXPECTED)
    write_json(results / "source-verification.json", {
        "source_sha": PR_SHA, "verified_blobs": source_receipt,
        "python": sys.version,
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    })
    receipts = []

    class ExitCodeCases(unittest.TestCase):
        def check_case(self, scenario, mode):
            case_id = f"{scenario}__{mode}"
            directory = results / "cases" / case_id
            directory.mkdir(parents=True)
            completed = subprocess.run(
                [
                    sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                    "--source", str(source), "--results", str(directory),
                    "--child", scenario, mode,
                ],
                env={"PATH": os.defpath, "HOME": "/tmp", "LANG": "C.UTF-8"},
                capture_output=True, text=True, timeout=10,
            )
            (directory / "stdout.txt").write_text(completed.stdout, encoding="utf-8")
            (directory / "stderr.txt").write_text(completed.stderr, encoding="utf-8")
            summary_path = directory / "output" / "summary.json"
            trace_path = directory / "boundary-trace.json"
            summary = json.loads(summary_path.read_text()) if summary_path.exists() else None
            trace = json.loads(trace_path.read_text()) if trace_path.exists() else None
            expected = EXPECTED[scenario][mode]
            receipts.append({
                "case": case_id, "scenario": scenario, "fail_on": mode,
                "expected_exit": expected, "actual_exit": completed.returncode,
                "exit_matches": completed.returncode == expected,
                "summary": summary, "boundary_trace": trace,
            })
            self.assertIsNotNone(
                summary, f"No CLI summary; inspect {case_id}/stderr.txt"
            )
            self.assertIsNotNone(trace, "Missing fixture boundary trace")
            self.assertEqual(trace["attempted_queries"], ["query-1", "query-2"])
            captured = 0 if scenario == "all_capture_failures" else (
                1 if scenario == "partial_capture_failure" else 2
            )
            self.assertEqual(len(trace["captured_queries"]), captured)
            self.assertEqual(trace["evaluation_rows"], captured)
            self.assertEqual(trace["evaluation_runs"], int(captured > 0))
            self.assertEqual(trace["investigations"], 1)
            self.assertEqual(trace["network_attempts"], 0)
            self.assertTrue(trace["session_closed"])
            self.assertEqual(summary["suites"]["probe"]["captured"], captured)
            self.assertEqual(summary["exit_code"], completed.returncode)
            self.assertEqual(
                completed.returncode, expected,
                f"{case_id}: expected process exit {expected}, "
                f"observed {completed.returncode}; "
                f"suite={summary['suites']['probe']}",
            )

    names = []
    for scenario, modes in EXPECTED.items():
        for mode in modes:
            name = f"test_{scenario}__{mode}"

            def test(self, scenario=scenario, mode=mode):
                self.check_case(scenario, mode)

            setattr(ExitCodeCases, name, test)
            names.append(name)
    suite = unittest.TestSuite(ExitCodeCases(name) for name in names)
    outcome = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {
        "source_sha": PR_SHA,
        "tests_run": outcome.testsRun,
        "failures": len(outcome.failures),
        "errors": len(outcome.errors),
        "skipped": len(outcome.skipped),
        "status": "passed" if outcome.wasSuccessful() else "failed",
        "cases": receipts,
        "limitations": [
            "Credentials, HTTP transport, and Foundry SDK boundaries are mocked.",
            "No live Azure invocation or installed SDK compatibility was tested.",
            "Only HTTP capture failures are injected; real timeout timing is not tested.",
        ],
    }
    write_json(results / "regression-results.json", report)
    print(
        f"\nSOURCE={PR_SHA} TESTS={outcome.testsRun} "
        f"FAILURES={len(outcome.failures)} ERRORS={len(outcome.errors)}",
        flush=True,
    )
    return 0 if outcome.wasSuccessful() else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--child", nargs=2, metavar=("SCENARIO", "FAIL_ON"))
    args = parser.parse_args()
    if args.child:
        run_child(args.source.resolve(), args.results.resolve(), *args.child)
        raise AssertionError("Child unexpectedly returned without the CLI exit")
    return run_tests(args.source.resolve(), args.results.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
