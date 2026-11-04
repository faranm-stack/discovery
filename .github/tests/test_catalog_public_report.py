"""Public export/privacy contract tests; synthetic inputs and offline execution only."""

from collections import Counter
from copy import deepcopy
import csv
import io
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

import catalog_public_report as subject


ROOT = Path(__file__).resolve().parents[2]
UPLOAD = "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
SECRETS = ["SECRET_reason_12", "SECRET_prompt_34", "private.person@example.invalid",
           "https://tenant-private.example.invalid/subscriptions/sub-private",
           "C:\\private\\sensitive-evidence", "session-SECRET-56", "response-SECRET-78",
           "Bearer SECRET_ACCESS_TOKEN", "SECRET_AUTHOR", "9999888877776666",
           '</td><script>alert("SECRET_XSS")</script>', '=HYPERLINK("SECRET_FORMULA")']


def ledger(statuses=("passed", "failed", "blocked", "uncertain", "not_run"), mode="live"):
    rows = [{"catalog_agent": f"agent-{i}", "status": status} for i, status in enumerate(statuses)]
    return {"mode": mode, "total": len(rows), "counts": dict(Counter(statuses)), "results": rows}


def kit_report():
    return {"mode": "offline", "runtime_enabled": False,
            "kits": [{"kit": "demo-kit", "static": {"status": "passed"},
                      "e2e": {"status": "blocked", "executed": False, "assertions_evaluated": 0}}],
            "summary": {"kits": 1, "static_passed": 1, "static_failed": 0, "e2e_passed": 0,
                        "e2e_failed": 0, "e2e_blocked": 1, "e2e_not_run": 0, "e2e_executed": 0}}


def write_source(tmp_path, document):
    source = tmp_path / "input.json"
    source.write_text(json.dumps(document), encoding="utf-8")
    return source


def read_outputs(output):
    assert {p.name for p in output.iterdir()} == set(subject.FILES)
    return [(output / name).read_text(encoding="utf-8") for name in subject.FILES]


def inventory(names=("agent-0", "bo-doe")):
    return {"repo": "example/catalog", "ref": "main", "commit": "B" * 40,
            "checked_at": "2026-11-04T00:00:00Z", "agents": list(names), "count": len(names)}


def write_inventory(tmp_path, document):
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


@pytest.mark.parametrize("mode,status", [("plan", "not_run"), ("live", "passed"), ("reconcile", "failed")])
def test_inventory_missing_entry_counted_without_mutating_frozen_ledger(tmp_path, mode, status):
    doc = ledger((status,) * 120, mode)
    baseline = inventory([r["catalog_agent"] for r in doc["results"]] + ["bo-doe"])
    original_doc, original_baseline = deepcopy(doc), deepcopy(baseline)
    public = subject.project(doc, "catalog", "a" * 40, baseline)
    assert doc == original_doc and baseline == original_baseline
    assert len(public["rows"]) == 121
    missing = next(r for r in public["rows"] if r["name"] == "bo-doe")
    assert missing == {**subject.public_row("agent", "bo-doe", "not_run"),
                       "inventory_coverage": "missing_from_ledger"}
    assert public["inventory"] == {"inventory_baseline_sha": "b" * 40, "inventory_expected_count": 121,
                                    "ledger_total": 120, "inventory_missing_count": 1,
                                    "inventory_unexpected_count": 0}
    source = write_source(tmp_path, doc)
    baseline_path = write_inventory(tmp_path, baseline)
    original_bytes = (source.read_bytes(), baseline_path.read_bytes())
    output = tmp_path / "public"
    args = ["--ledger", str(source), "--output-dir", str(output), "--source-revision", "a" * 40,
            "--inventory-json", str(baseline_path)]
    assert subject.main(args) == 0
    first = read_outputs(output)
    assert subject.main(args) == 0 and read_outputs(output) == first
    assert (source.read_bytes(), baseline_path.read_bytes()) == original_bytes
    html, csv_text = first
    assert "Total: 121" in html and "Missing expected inventory names from ledger: 1" in html
    assert "Inventory baseline SHA: <code>" + "b" * 40 in html
    assert "Source revision (checkout, not evidence attestation): <code>" + "a" * 40 in html
    assert subject.INVENTORY_NOTICE in html
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    assert len(rows) == 121
    assert all(r["source_revision"] == "a" * 40 and r["inventory_baseline_sha"] == "b" * 40 for r in rows)
    assert all(r["scope_total"] == "121" and r["inventory_missing_count"] == "1" for r in rows)
    missing_csv = next(r for r in rows if r["name"] == "bo-doe")
    assert missing_csv["status"] == "not_run" and missing_csv["grade"] == "I"
    assert missing_csv["status_count"] == ("121" if mode == "plan" else "1")


def test_inventory_unexpected_extra_retained_and_labeled():
    doc = ledger(("passed", "failed"))
    doc["results"][0]["agent_retried"] = True
    public = subject.project(doc, "catalog", inventory=inventory())
    rows = {r["name"]: r for r in public["rows"]}
    assert set(rows) == {"agent-0", "agent-1", "bo-doe"}
    assert rows["agent-0"]["inventory_coverage"] == "expected_in_ledger"
    assert rows["agent-0"]["grade"] == "B"
    assert rows["agent-1"]["inventory_coverage"] == "unexpected_in_ledger"
    assert rows["agent-1"]["status"] == "failed" and rows["agent-1"]["grade"] == "F"
    assert public["inventory"]["inventory_missing_count"] == 1
    assert public["inventory"]["inventory_unexpected_count"] == 1
    html = subject.render_html(public)
    assert "Total: 3" in html and "Unexpected ledger names outside baseline: 1" in html
    csv_rows = list(csv.DictReader(io.StringIO(subject.render_csv(public))))
    assert all(r["scope_total"] == "3" and r["inventory_unexpected_count"] == "1" for r in csv_rows)


def test_inventory_does_not_repair_invalid_ledger_counts():
    doc = ledger(("passed",))
    doc.update(total=2)
    with pytest.raises(ValueError):
        subject.project(doc, "catalog", inventory=inventory())


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("commit"), lambda d: d.update(commit="abc123"),
    lambda d: d.update(commit="g" * 40), lambda d: d.update(commit="a" * 41),
    lambda d: d.update(commit="a" * 40 + "\n"), lambda d: d.update(commit=None),
    lambda d: d.update(commit=[]), lambda d: d.pop("agents"),
    lambda d: d.update(agents="agent-0"), lambda d: d.update(agents={}),
    lambda d: d.update(agents=["agent-0", "agent-0"]),
    lambda d: d.update(agents=["agent-0", "../private"]),
    lambda d: d.update(agents=["agent-0", "=SECRET"]),
    lambda d: d.update(agents=["agent-0", None]),
    lambda d: d.pop("count"), lambda d: d.update(count=1),
    lambda d: d.update(count=True), lambda d: d.update(count=2.0),
    lambda d: d.update(count="2"), lambda d: d.update(count=-1),
])
def test_invalid_inventory_failclosed_replaces_stale_report(tmp_path, mutate, capsys):
    source = write_source(tmp_path, ledger(("passed",)))
    output = tmp_path / "public"
    assert subject.export(source, output, "catalog") == 0
    baseline = inventory()
    mutate(baseline)
    path = write_inventory(tmp_path, baseline)
    assert subject.main(["--ledger", str(source), "--output-dir", str(output),
                         "--inventory-json", str(path)]) == 1
    html, csv_text = read_outputs(output)
    logs = capsys.readouterr()
    assert "unavailable" in html and "unavailable" in csv_text
    assert "agent-0" not in html + csv_text
    assert "SECRET" not in html + csv_text + logs.out + logs.err
    assert str(path) not in logs.err


@pytest.mark.parametrize("bad_input", [None, "SECRET malformed", "null", "[]",
                                     '{"commit":"SECRET","commit":"duplicate"}'])
def test_unreadable_or_invalid_inventory_json_fails_closed(tmp_path, bad_input):
    source = write_source(tmp_path, ledger())
    output = tmp_path / "public"
    assert subject.export(source, output, "catalog") == 0
    path = tmp_path / "missing-inventory.json"
    if bad_input is not None:
        path.write_text(bad_input, encoding="utf-8")
    assert subject.export(source, output, "catalog", inventory_json=path) == 1
    html, csv_text = read_outputs(output)
    assert "unavailable" in html and "unavailable" in csv_text
    assert "SECRET" not in html + csv_text and "agent-0" not in html + csv_text


def test_inventory_bound_limits(tmp_path, monkeypatch):
    source = write_source(tmp_path, ledger((), "plan"))
    path = write_inventory(tmp_path, inventory())
    monkeypatch.setattr(subject, "MAX_BYTES", source.stat().st_size)
    assert subject.export(source, tmp_path / "public", "catalog", inventory_json=path) == 1
    monkeypatch.setattr(subject, "MAX_ROWS", 1)
    with pytest.raises(ValueError):
        subject.project(ledger((), "plan"), "catalog", inventory=inventory())


def test_inventory_metadata_and_free_text_never_leak(tmp_path):
    secret_text = " | ".join(SECRETS)
    doc = ledger(("not_run",), "plan")
    doc["results"][0].update(inventory_coverage=secret_text, reason=secret_text)
    baseline = inventory()
    baseline.update(dict.fromkeys(("repo", "ref", "checked_at", "reason", "source_revision",
                                   "inventory_notice", "inventory_coverage", "author"), secret_text))
    output = tmp_path / "public"
    assert subject.export(write_source(tmp_path, doc), output, "catalog",
                          inventory_json=write_inventory(tmp_path, baseline)) == 0
    text = "".join(read_outputs(output))
    assert "SECRET" not in text
    assert all(secret not in text for secret in SECRETS)


def test_kit_rejects_inventory_and_clears_stale_report(tmp_path):
    with pytest.raises(ValueError):
        subject.project(kit_report(), "kit", inventory=inventory())
    source = write_source(tmp_path, kit_report())
    path = write_inventory(tmp_path, inventory())
    output = tmp_path / "public"
    assert subject.export(source, output, "kit") == 0
    assert subject.main(["--kit-report", str(source), "--output-dir", str(output),
                         "--inventory-json", str(path)]) == 1
    html, csv_text = read_outputs(output)
    assert "unavailable" in html and "unavailable" in csv_text
    assert "demo-kit" not in html + csv_text


def test_empty_inventory_reports_counts_without_claiming_success():
    public = subject.project(ledger((), "plan"), "catalog", inventory=inventory(()))
    html = subject.render_html(public)
    assert "not a successful validation result" in html
    rows = list(csv.DictReader(io.StringIO(subject.render_csv(public))))
    assert len(rows) == 1 and rows[0]["inventory_expected_count"] == "0"
    assert rows[0]["inventory_baseline_sha"] == "b" * 40


def test_status_grades_counts_and_safe_resource_labels(tmp_path):
    doc = ledger()
    for row, category in zip(doc["results"], subject.RESOURCES):
        row.update(resource_requirement=category, resource_label="SECRET_LABEL", grade="SECRET_GRADE")
    public = subject.project(doc, "catalog", "A" * 40)
    assert [r["grade"] for r in public["rows"]] == ["A", "F", "I", "I", "I"]
    for row, category in zip(public["rows"], subject.RESOURCES):
        assert row["resource_label"] == subject.RESOURCES[category]
        assert set(row) == set(subject.FIELDS)
    output = tmp_path / "public"
    assert subject.export(write_source(tmp_path, doc), output, "catalog", "A" * 40) == 0
    html, csv_text = read_outputs(output)
    assert "Total: 5" in html and "passed: 1" in html
    assert "a" * 40 in html and "a" * 40 in csv_text
    assert "SECRET" not in html + csv_text
    csv_rows = list(csv.DictReader(io.StringIO(csv_text)))
    assert len(csv_rows) == 5
    assert [r["grade"] for r in csv_rows] == ["A", "F", "I", "I", "I"]
    assert all(r["scope_total"] == "5" and r["status_count"] == "1" for r in csv_rows)


@pytest.mark.parametrize("retry", [True, False, None, 1, 0, 1.0, "true", "false", "", [], [True], {}, {"value": True}])
@pytest.mark.parametrize("status,default_grade", [
    ("passed", "A"), ("failed", "F"), ("blocked", "I"), ("uncertain", "I"), ("not_run", "I"),
])
def test_retry_grade_requires_explicit_boolean_and_passed_status(tmp_path, retry, status, default_grade):
    doc = ledger((status,))
    doc["results"][0].update(agent_retried=retry, reason="SECRET arbitrary retry claim", grade="B")
    expected = "B" if status == "passed" and retry is True else default_grade
    output = tmp_path / "public"
    assert subject.export(write_source(tmp_path, doc), output, "catalog") == 0
    html, csv_text = read_outputs(output)
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    assert rows[0]["grade"] == expected
    assert f"<td>{status}</td><td>{expected}</td>" in html
    assert "SECRET" not in html + csv_text


@pytest.mark.parametrize("reason", ["passed after agent retries", "retry", "clean pass", None, {"agent_retried": True}])
def test_missing_retry_metadata_never_inferred_from_reason(reason):
    doc = ledger(("passed",))
    doc["results"][0].update(reason=reason, grade="B", retry_count=3)
    assert subject.project(doc, "catalog")["rows"][0]["grade"] == "A"


def test_notice_explains_card_grades_retry_evidence_and_static_scope():
    html = subject.render_html(subject.project(ledger(), "catalog"))
    for text in ("A = passed", "B = passed after agent retries", "boolean agent_retried=true only",
                 "F = failed", "I = unjudged (blocked, uncertain, or not run)",
                 "reasons never infer retries", "static checks only and have blank grades"):
        assert text in html
    assert "B = blocked" not in html and "N = not run" not in html


@pytest.mark.parametrize("kind", ["catalog", "kit"])
def test_secrets_and_raw_evidence_never_exported(tmp_path, kind, monkeypatch):
    import socket
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("No network allowed"))
    doc = ledger() if kind == "catalog" else kit_report()
    secret_text = " | ".join(SECRETS)
    extras = dict.fromkeys(("reason", "reasons", "prompt", "contact", "paths", "response",
                           "session_id", "response_id", "tokens", "tenant", "subscription",
                           "endpoint", "author", "source_revision", "grade", "resource_label"), secret_text)
    doc.update(extras)
    for row in doc.get("results", doc.get("kits", [])):
        row.update(extras)
        row["resource_requirement"] = {"secret": secret_text}
        if kind == "kit":
            row["static"].update(extras)
            row["e2e"].update(extras)
            row["members"] = [{"prompt": secret_text, "contact": secret_text}]
    source = write_source(tmp_path, doc)
    original = source.read_bytes()
    output = tmp_path / "public"
    assert subject.export(source, output, kind) == 0
    html, csv_text = read_outputs(output)
    for secret in SECRETS:
        assert secret not in html + csv_text
    assert "SECRET" not in html + csv_text
    assert source.read_bytes() == original


@pytest.mark.parametrize("name", ["../escape", "a/b", "a\\b", "=SUM(1)", "+calc", "-cmd", "@cmd",
                                  "a\nb", "a\tb", "<script>", "x@example.com", "https://x", "A", "a_1",
                                  "a--b", "a-", "a" * 101, "", None, [], {}, "é"])
def test_invalid_agent_identifiers_fail_closed(name):
    doc = ledger()
    doc["results"][0]["catalog_agent"] = name
    with pytest.raises(ValueError):
        subject.project(doc, "catalog")


@pytest.mark.parametrize("status", ["SECRET", "PASS", "started", "<script>", "=cmd", None, [], {}])
def test_invalid_statuses_fail_closed(status):
    doc = ledger()
    doc["results"][0]["status"] = status
    with pytest.raises(ValueError):
        subject.project(doc, "catalog")


@pytest.mark.parametrize("value", [-1, True, 1.0, "1", None, {}, 10**30])
def test_invalid_counts_fail_closed(value):
    doc = ledger()
    doc["counts"]["passed"] = value
    with pytest.raises(ValueError):
        subject.project(doc, "catalog")


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(total=4), lambda d: d.update(total=True),
    lambda d: d.update(results={}), lambda d: d.update(counts=[]),
    lambda d: d["counts"].update(passed=2), lambda d: d["counts"].pop("passed"),
    lambda d: d["counts"].update(SECRET=0),
    lambda d: d["results"][0].update(catalog_agent="agent-1"),
    lambda d: d.update(mode="SECRET"), lambda d: d.update(mode="plan"),
    lambda d: d["results"].append(None),
])
def test_inconsistent_or_invalid_ledger_rejected(mutate):
    doc = ledger()
    mutate(doc)
    with pytest.raises(ValueError):
        subject.project(doc, "catalog")


@pytest.mark.parametrize("revision", ["abc123", "g" * 40, "a" * 41, "a" * 40 + "\n", "<script>", "=FORMULA", ""])
def test_revision_requires_full_hex_sha(revision):
    with pytest.raises(ValueError):
        subject.project(ledger(), "catalog", revision)


def test_offline_plan_and_kit_static_success_are_not_runtime_passes():
    plan = subject.project(ledger(("not_run", "blocked"), "plan"), "catalog")
    assert {r["grade"] for r in plan["rows"]} == {"I"}
    kit = subject.project(kit_report(), "kit")
    assert {(r["scope"], r["status"], r["grade"]) for r in kit["rows"]} == {
        ("kit_static", "passed", ""), ("kit_e2e", "blocked", "I")}
    assert "Kit static checks are not kit E2E passes" in subject.render_html(kit)


@pytest.mark.parametrize("static", ["passed", "failed"])
@pytest.mark.parametrize("e2e", ["blocked", "not_run"])
def test_kit_exports_keep_static_checks_ungraded_and_e2e_unjudged(tmp_path, static, e2e):
    doc = kit_report()
    doc["kits"][0]["static"].update(status=static, agent_retried=True, grade="A")
    doc["kits"][0]["e2e"].update(status=e2e, agent_retried=True, grade="B")
    doc["summary"].update(static_passed=int(static == "passed"), static_failed=int(static == "failed"),
                          e2e_blocked=int(e2e == "blocked"), e2e_not_run=int(e2e == "not_run"))
    output = tmp_path / "public"
    assert subject.export(write_source(tmp_path, doc), output, "kit") == 0
    html, csv_text = read_outputs(output)
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    assert {(r["scope"], r["status"], r["grade"]) for r in rows} == {
        ("kit_static", static, ""), ("kit_e2e", e2e, "I")}
    assert f"<td>kit_static</td><td>demo-kit</td><td>{static}</td><td></td>" in html
    assert f"<td>kit_e2e</td><td>demo-kit</td><td>{e2e}</td><td>I</td>" in html


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(runtime_enabled=True), lambda d: d.update(mode="live"),
    lambda d: d["kits"][0].update(kit="../private"),
    lambda d: d["kits"].append(deepcopy(d["kits"][0])),
    lambda d: d["kits"][0]["e2e"].update(status="passed"),
    lambda d: d["kits"][0]["e2e"].update(executed=True),
    lambda d: d["kits"][0]["e2e"].update(assertions_evaluated=True),
    lambda d: d["kits"][0]["static"].update(status="SECRET"),
    lambda d: d["summary"].update(kits=99),
    lambda d: d["summary"].update(e2e_executed=1),
])
def test_kit_input_is_untrusted(mutate):
    doc = kit_report()
    mutate(doc)
    with pytest.raises(ValueError):
        subject.project(doc, "kit")


def test_html_defense_in_depth_and_no_external_assets():
    public = subject.project(ledger(), "catalog")
    # Even if the validated projection is accidentally bypassed, HTML escapes cells.
    public["rows"][0]["name"] = '<img src=x onerror="alert(1)">&'
    html = subject.render_html(public)
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;&amp;" in html
    for forbidden in ("<script", "<img", "<link", "<iframe", "fetch(", "http://", "https://"):
        assert forbidden not in html
    assert "default-src 'none'" in html and "<style>" in html and "<table>" in html


@pytest.mark.parametrize("bad_input", [None, "SECRET malformed", "[]", '{"mode":"live","mode":"SECRET"}',
                                     '{"results":null}', '{"mode":"live","counts":{"SECRET":1}}'])
def test_missing_or_invalid_input_replaces_stale_reports_without_leaking(tmp_path, bad_input, capsys):
    source = write_source(tmp_path, ledger())
    output = tmp_path / "public"
    assert subject.export(source, output, "catalog") == 0
    if bad_input is None:
        source.unlink()
    else:
        source.write_text(bad_input, encoding="utf-8")
    assert subject.main(["--ledger", str(source), "--output-dir", str(output)]) == 1
    html, csv_text = read_outputs(output)
    logs = capsys.readouterr()
    assert "unavailable" in html and "unavailable" in csv_text
    assert "agent-0" not in html + csv_text
    assert "SECRET" not in html + csv_text + logs.out + logs.err
    assert str(source) not in logs.err


def test_bounded_input_and_empty_report_are_not_success_claims(tmp_path, monkeypatch):
    source = write_source(tmp_path, ledger())
    monkeypatch.setattr(subject, "MAX_BYTES", 1)
    assert subject.export(source, tmp_path / "public", "catalog") == 1
    empty = subject.project(ledger((), "plan"), "catalog")
    assert "not a successful validation result" in subject.render_html(empty)


def test_cli_creates_only_two_files_and_is_repeatable(tmp_path):
    source = write_source(tmp_path, ledger(("not_run",), "plan"))
    output = tmp_path / "public"
    command = [sys.executable, "-B", str(ROOT / ".github/scripts/catalog_public_report.py"),
               "--ledger", str(source), "--output-dir", str(output), "--source-revision", "a" * 40]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    first = read_outputs(output)
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0 and read_outputs(output) == first
    rows = list(csv.DictReader(io.StringIO(first[1])))
    assert rows[0]["name"] == "agent-0" and rows[0]["grade"] == "I"


def workflows():
    return [yaml.load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
            for name in ("catalog-smoke-sweep.yml", "catalog-kit-validation.yml")]


def test_real_local_offline_planners_produce_exportable_reports(tmp_path):
    # Actual checked-out inventory, not a live workflow or cloud test. Static
    # findings can make a planner nonzero; its ledger must still be exportable.
    plan = tmp_path / "plan"
    command = [sys.executable, "-B", str(ROOT / ".github/scripts/catalog_sweep.py"),
               "--mode", "plan", "--repo-root", str(ROOT), "--output-dir", str(plan)]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert result.returncode in (0, 1)
    assert (plan / "catalog-results.json").is_file(), "Offline planner did not produce its ledger"
    output = tmp_path / "public"
    assert subject.export(plan / "catalog-results.json", output, "catalog") == 0
    rows = list(csv.DictReader(io.StringIO(read_outputs(output)[1])))
    expected = {p.name for p in (ROOT / "agents").iterdir() if p.is_dir()}
    assert {r["name"] for r in rows} == expected
    assert all(r["status"] == "not_run" and r["grade"] == "I" for r in rows)

    from catalog_kit_validation import build_report
    doc = build_report(ROOT)
    assert subject.export(write_source(tmp_path, doc), output, "kit") == 0
    rows = list(csv.DictReader(io.StringIO(read_outputs(output)[1])))
    assert len(rows) == 2 * len(doc["kits"]) > 0
    assert all(r["grade"] == "I" for r in rows if r["scope"] == "kit_e2e")
    assert all(r["grade"] == "" for r in rows if r["scope"] == "kit_static")


def test_workflow_public_uploads_exact_files_after_always_export_and_private_raw_gates():
    for workflow in workflows():
        for job in workflow["jobs"].values():
            steps = job["steps"]
            exports = [(i, s) for i, s in enumerate(steps) if "python .github/scripts/catalog_public_report.py" in s.get("run", "")]
            assert len(exports) == 1
            export_index, export = exports[0]
            assert export["if"] == "always()"
            assert '--output-dir "$RUNNER_TEMP/catalog-public-report"' in export["run"]
            assert '--source-revision "$GITHUB_SHA"' in export["run"]
            if "--ledger" in export["run"]:
                assert "--inventory-json .github/smoke/catalog-inventory-baseline.json" in export["run"]
                final = next(i for i, s in enumerate(steps) if "preflight.py finalize" in s.get("run", ""))
                assert final < export_index and steps[final]["if"] == "always()"
                assert "catalog-results.json" in export["run"]
            else:
                assert "--inventory-json" not in export["run"]
                final = next(i for i, s in enumerate(steps) if "--format json >" in s.get("run", ""))
                assert final < export_index
            public_count = 0
            for i, step in enumerate(steps):
                if "upload-artifact@" not in step.get("uses", ""):
                    continue
                assert step["uses"] == UPLOAD
                paths = step["with"]["path"].splitlines()
                if "catalog-public-report" in step["with"]["path"]:
                    public_count += 1
                    assert step["if"] == "always()" and i > export_index
                    assert paths == ["${{ runner.temp }}/catalog-public-report/" + name for name in subject.FILES]
                    assert step["with"]["if-no-files-found"] == "error"
                else:
                    assert "always()" in step["if"] and "github.event.repository.private" in step["if"]
                    assert "CATALOG_SMOKE_ARTIFACTS_APPROVED == 'true'" in step["if"]
            assert public_count == 1
            summaries = [s["run"] for s in steps if "GITHUB_STEP_SUMMARY" in s.get("run", "")]
            assert all(s.startswith("echo '") and "cat " not in s for s in summaries)


def test_push_is_branch_limited_offline_and_live_gates_unchanged():
    sweep, kits = workflows()
    for workflow in (sweep, kits):
        assert workflow["on"]["push"] == {"branches": ["automation/catalog-validation-*"]}
        assert set(workflow["on"]) == {"push", "schedule", "workflow_dispatch"}
        assert workflow["permissions"] == {"contents": "read"}
    assert sweep["on"]["schedule"] == [{"cron": "0 8 * * 1,4"}]
    assert kits["on"]["schedule"] == [{"cron": "0 6 * * 1,4"}]
    live = sweep["jobs"]["sweep"]
    assert live["if"].split() == ("github.event.repository.private && vars.CATALOG_SMOKE_ARTIFACTS_APPROVED == 'true' && "
        "vars.CATALOG_SMOKE_LIVE_ENABLED == 'true' && "
        "((github.event_name == 'workflow_dispatch' && inputs.mode == 'live') || "
        "(github.event_name == 'schedule' && vars.CATALOG_SMOKE_SCHEDULE_LIVE_ENABLED == 'true'))").split()
    assert live["environment"] == "catalog-smoke" and live["needs"] == "plan"
    assert sweep["on"]["workflow_dispatch"]["inputs"]["mode"]["default"] == "plan"
    assert set(kits["jobs"]) == {"offline-kit-plan"}
    for job in (sweep["jobs"]["plan"], kits["jobs"]["offline-kit-plan"]):
        assert "permissions" not in job and "environment" not in job
        encoded = json.dumps(job)
        for forbidden in ("azure/login", "id-token", "--mode live", "secrets.", "preflight.py auth", "preflight.py gate"):
            assert forbidden not in encoded
        assert "test_catalog_public_report.py" in encoded