"""Render and validate existing offline baseline/fix evidence; no cloud calls."""

import argparse
import json
from pathlib import Path
import re
import subprocess


BASE = "1a16c98f1aadc6a8d20d9ac9956b8c35f68d8c28"
FIX = "831f7a0b75302fe4f2e4c4260eb2c24cad3a3e87"
FAILURES = {
    "all_capture_failures__errored",
    "all_capture_failures__failed",
    "partial_capture_failure__errored",
    "partial_capture_failure__failed",
}
LABELS = {
    "all_capture_failures": "Both agent calls return HTTP 503",
    "partial_capture_failure": "One HTTP 503; other response passes",
    "all_pass": "All calls and evaluations pass",
    "criterion_failure": "Calls succeed; one criterion fails",
    "evaluator_item_error": "Calls succeed; one evaluator errors",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--workflow", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()
    base = json.loads((args.results / "baseline/regression-results.json").read_text())
    fixed = json.loads((args.results / "fixed/regression-results.json").read_text())
    before = {case["case"]: case for case in base["cases"]}
    after = {case["case"]: case for case in fixed["cases"]}
    checks = {
        "baseline_revision": base["source_sha"] == BASE,
        "fixed_revision": fixed["source_sha"] == FIX,
        "same_15_cases": len(before) == 15 and before.keys() == after.keys(),
        "baseline_expected_failures": (
            base["tests_run"] == 15 and base["failures"] == 4
            and base["errors"] == 0 and base["skipped"] == 0
            and {key for key, case in before.items() if not case["exit_matches"]} == FAILURES
            and (args.results / "baseline/test.exit").read_text().strip() == "1"
        ),
        "fixed_all_pass": (
            fixed["tests_run"] == 15 and fixed["failures"] == 0
            and fixed["errors"] == 0 and fixed["skipped"] == 0
            and all(case["exit_matches"] for case in after.values())
            and (args.results / "fixed/test.exit").read_text().strip() == "0"
        ),
        "11_passing_controls_preserved": all(
            before[key]["actual_exit"] == case["actual_exit"]
            for key, case in after.items() if key not in FAILURES
        ),
        "expectations_not_changed": all(
            before[key]["expected_exit"] == case["expected_exit"]
            for key, case in after.items()
        ),
        "no_network_attempts_and_sessions_closed": all(
            case["boundary_trace"]["network_attempts"] == 0
            and case["boundary_trace"]["session_closed"]
            for case in list(before.values()) + list(after.values())
        ),
    }
    unit_log = (args.results / "permanent-tests.log").read_text()
    checks["19_permanent_tests_pass"] = (
        (args.results / "permanent-tests.exit").read_text().strip() == "0"
        and re.search(r"Ran 19 tests in ", unit_log) is not None
        and re.search(r"^OK$", unit_log, re.MULTILINE) is not None
        and "Syntax checked against Python 3.10 grammar" in unit_log
    )

    workflow = args.workflow.read_text()
    match = re.search(r"grep -E '([^']+)'", workflow)
    if match is None:
        raise RuntimeError("CI path guard was not found")
    guard_cases = {
        "utilities/agent-evaluation/evaluators/pipeline.py": True,
        "utilities/agent-evaluation/README.md": True,
        ".github/tests/test_agent_evaluation_exit_codes.py": True,
        ".github/scripts/validate_pr.py": True,
        ".github/workflows/unit-tests.yml": True,
        ".github/workflows/probe-aka-ms.yml": True,
        "docs/unrelated.md": False,
        "utilities/unrelated/README.md": False,
    }
    guard_results = {}
    for path, expected in guard_cases.items():
        result = subprocess.run(
            ["grep", "-E", "--", match.group(1)], input=path + "\n",
            text=True, capture_output=True, check=False,
        )
        guard_results[path] = (
            result.returncode in (0, 1) and (result.returncode == 0) == expected
        )
    checks["ci_path_guard"] = all(guard_results.values())
    checks["ci_push_path"] = '- "utilities/agent-evaluation/**"' in workflow

    lines = [
        "# PR #100: capture-failure fix verification",
        "",
        "**This is an offline code-regression test, not the sample's live Option B.**",
        "The original client, capture/conversion, reporting and actual CLI exits run.",
        "Only credentials, HTTP transport and Foundry SDK boundaries are fakes.",
        "",
        f"- Original PR: `{BASE}`",
        f"- Fix for the author to cherry-pick: `{FIX}`",
        "- Baseline: expected 11 passing cases and the 4 previously reproduced failures.",
        "- Fixed: require all 15 cases to pass, without changing expectations.",
        "- Permanent tests: require all 19 cases in the proposed commit to pass.",
        "- New permanent tests also cover query limits, queryless skips, separate",
        "  capture/evaluator counts, summaries, and simulated capture timeouts.",
        "",
        "## Actual utility process exits",
        "",
        "**0 = success; 2 = fail-on policy failure.** A test passes when the actual",
        "exit matches the expected exit, including an expected nonzero exit.",
        "",
        "| Scenario | fail-on | Expected | Original CLI | Fixed CLI | Fixed assertion |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for key, case in after.items():
        lines.append(
            f"| {LABELS[case['scenario']]} | `{case['fail_on']}` | "
            f"{case['expected_exit']} | {before[key]['actual_exit']} | "
            f"{case['actual_exit']} | {'PASS' if case['exit_matches'] else 'FAIL'} |"
        )
    lines.extend(["", "## Verification checks", "", "| Check | Result |", "| --- | --- |"])
    lines.extend(
        f"| {name} | {'PASS' if passed else 'FAIL'} |"
        for name, passed in checks.items()
    )
    lines.extend([
        "",
        "Source/harness identities, source diff, raw test logs, fixture traces and",
        "per-case original pipeline summaries are retained in the evidence artifact.",
        "This does not claim live SDK/service compatibility, safety-evaluator",
        "availability, scientific correctness, or maintainer approval.",
        "",
    ])
    summary = "\n".join(lines)
    args.summary.write_text(summary, encoding="utf-8")
    (args.results / "verification-summary.md").write_text(summary, encoding="utf-8")
    (args.results / "verification-checks.json").write_text(
        json.dumps({"checks": checks, "ci_guard_cases": guard_results}, indent=2) + "\n"
    )
    print(summary)
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
