"""Synthetic ledgers only. Exercise the reporting entry point without loading the runner."""

import json
import os
from pathlib import Path

import pytest

from build_agent_scorecard import PRIVATE_HTML, PUBLIC_HTML, main


def ledger():
    return {"mode": "live", "total": 2, "counts": {"passed": 1, "blocked": 1}, "submitted_responses": 1,
            "results": [{"catalog_agent": "alpha", "status": "passed", "tool_roundtrip": "pass",
                         "response_submissions": 1}, {"catalog_agent": "beta", "status": "blocked"}]}


@pytest.fixture
def paths(tmp_path):
    return tmp_path / "ledger.json", tmp_path / "share", tmp_path / "restricted"


def run(paths, doc, *args):
    source, public, private = paths
    source.write_text(json.dumps(doc), encoding="utf-8")
    return cli(source, public, private, *args)


def cli(source, public, private, *args):
    return main(["--ledger", str(source), "--public-dir", str(public), "--private-dir", str(private), *args])


def test_cli_rejects_arbitrary_arguments_without_repeating_them(paths, capsys):
    with pytest.raises(SystemExit) as stopped:
        cli(*paths, "--prompt", "synthetic private text")
    assert stopped.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "Invalid report arguments. Use --help.\n"


def test_public_is_a_positive_list_and_private_is_escaped(paths, capsys):
    _, public, private = paths
    doc = ledger()
    assert run(paths, doc) == 0
    clean = (public / PUBLIC_HTML).read_bytes()
    row = doc["results"][0]
    row.update(reason='<script>alert("private-reason")</script>', project="private-project", investigation="private-case",
               response_id="private-response", conversation_id="private-thread",
               assessment={"operation_id": "private-operation", "call_id": "private-call", "note": "arbitrary-note"},
               prompt="prompt-canary", owner={"contact": "synthetic@example.invalid"}, resource_requirement="arbitrary label",
               cost_usd=12.625, usage={"total_tokens": 98765}, resource_label="unchecked label", grade="injected grade",
               studio_url="javascript:synthetic", output="arbitrary answer")
    assert run(paths, doc) == 0
    assert (public / PUBLIC_HTML).read_bytes() == clean  # None of these private fields can affect public output.
    assert list(public.iterdir()) == [public / PUBLIC_HTML]
    assert list(private.iterdir()) == [private / PRIVATE_HTML]
    html = (private / PRIVATE_HTML).read_text(encoding="utf-8")
    assert "&lt;script&gt;alert(&quot;private-reason&quot;)&lt;/script&gt;" in html
    for value in ("private-project", "private-case", "private-response", "private-thread", "private-operation", "private-call",
                  "<td>98765</td>", "<td>12.625</td>", "Total tokens: Unknown (reported for 1/2 rows)",
                  "all rows: Unknown (reported for 1/2 rows)"):
        assert value in html
    for value in ("<script", "javascript:", "prompt-canary", "@example.invalid", "arbitrary-note", "arbitrary answer", "href=", "src="):
        assert value not in html and value.encode() not in clean
    captured = capsys.readouterr()
    assert captured.out == "Reports ready.\n" * 2 and captured.err == ""


def test_pinned_inventory_is_never_run_evidence(paths):
    source, public, private = paths
    inventory = source.with_name("inventory.json")
    inventory.write_text(json.dumps({"commit": "e" * 40, "count": 2, "agents": ["alpha", "absent"],
                                     "note": "private-extra"}), encoding="utf-8")
    doc = ledger()
    doc["results"][0]["resource_requirement"] = "gpu_optional"
    assert run(paths, doc, "--inventory-json", str(inventory)) == 0
    html = (public / PUBLIC_HTML).read_text(encoding="utf-8")
    assert "Ledger rows: 2. Reported response submissions: 1." in html and "not_run: 0" in html
    assert "GPU optional" in html and html.count("<tbody>") == 1 and html.count("<tr>") == 3
    assert "Missing from ledger (not results or submissions): absent" in html and "outside baseline: beta" in html
    assert "<td>absent</td>" not in html and "private-extra" not in html
    assert "absent" not in (private / PRIVATE_HTML).read_text(encoding="utf-8")
    assert json.loads(source.read_text(encoding="utf-8")) == doc


def test_missing_unknown_and_explicit_zero_are_distinct(paths):
    _, public, private = paths
    doc = ledger()
    del doc["submitted_responses"]
    doc["results"][0].update(usage="unknown", cost_usd=None)
    assert run(paths, doc) == 0
    assert "Reported response submissions: Unknown" in (public / PUBLIC_HTML).read_text(encoding="utf-8")
    html = (private / PRIVATE_HTML).read_text(encoding="utf-8")
    assert "Total tokens: Unknown (reported for 0/2 rows)" in html and "all rows: Unknown (reported for 0/2 rows)" in html
    assert "<td>0</td>" not in html
    doc["results"][0].update(usage={"total_tokens": 0}, cost_usd=0)
    doc["results"][1].update(usage={"total_tokens": 5}, cost={"total_cost_usd": 0.0025})
    assert run(paths, doc) == 0
    html = (private / PRIVATE_HTML).read_text(encoding="utf-8")
    assert "Total tokens: 5." in html and "all rows: 0.0025." in html and "<td>0</td><td>0</td>" in html


def test_plan_not_live_even_if_schema_passed(paths):
    _, public, private = paths
    doc = {"mode": "plan", "total": 1, "counts": {"not_run": 1}, "submitted_responses": 0,
           "results": [{"catalog_agent": "planned", "status": "not_run", "schema": "passed"}]}
    assert run(paths, doc) == 0
    for path in (public / PUBLIC_HTML, private / PRIVATE_HTML):
        html = path.read_text(encoding="utf-8")
        assert "Mode: plan" in html and "A plan is not a live run" in html
        assert "<td>planned</td><td>not_run</td><td>I</td>" in html


def test_reconciled_inconclusive_row_and_partial_metrics(paths):
    _, public, private = paths
    doc = ledger()
    doc.update(mode="reconcile", counts={"inconclusive": 1, "blocked": 1})
    doc["results"][0].update(status="inconclusive", tool_roundtrip="inconclusive", operation_ids=["op-one", "op-two"],
                             usage={"input_tokens": 17, "output_tokens": 2})
    assert run(paths, doc) == 0
    assert "<td>inconclusive</td><td>I</td>" in (public / PUBLIC_HTML).read_text(encoding="utf-8")
    html = (private / PRIVATE_HTML).read_text(encoding="utf-8")
    assert "op-one, op-two" in html and "<td>17</td><td>2</td><td>Unknown</td>" in html
    assert "Total tokens: Unknown" in html


@pytest.mark.parametrize("change", [
    lambda d: d.update(total=3), lambda d: d.update(total=True), lambda d: d.pop("counts"),
    lambda d: d.update(counts={"blocked": 2}), lambda d: d.update(counts={"passed": True, "blocked": 1}),
    lambda d: d.update(submitted_responses=0), lambda d: d.update(mode="plan"), lambda d: d.update(mode="other"),
    lambda d: d["results"][1].update(catalog_agent="alpha"), lambda d: d["results"][0].update(catalog_agent="a@example.invalid"),
    lambda d: d["results"][0].update(catalog_agent="<b>private</b>"), lambda d: d["results"][0].update(status="free text"),
    lambda d: d["results"][0].update(tool_roundtrip="failed"), lambda d: d["results"][0].update(usage={"total_tokens": -1}),
    lambda d: d["results"][0].update(cost_usd=False), lambda d: d["results"][0].update(reason={"bad": "type"}),
    lambda d: d.update(total=0, counts={}, results=[], submitted_responses=0),
])
def test_invalid_input_clears_both_previous_reports(paths, change, capsys):
    _, public, private = paths
    doc = ledger()
    assert run(paths, doc) == 0
    change(doc)
    assert run(paths, doc) == 1
    assert not list(public.iterdir()) and not list(private.iterdir())
    captured = capsys.readouterr()
    assert captured.out == "Reports ready.\n" and captured.err == "Reports unavailable; check the input and output destinations.\n"


@pytest.mark.parametrize("raw", [None, "{", '{"mode":"plan",' + json.dumps(ledger())[1:],
                                '{"extra":NaN,' + json.dumps(ledger())[1:], 'null'])
def test_bad_json_or_missing_file_also_invalidates(paths, raw):
    source, public, private = paths
    assert run(paths, ledger()) == 0
    if raw is None:
        source.unlink()
    else:
        source.write_text(raw, encoding="utf-8")
    assert cli(*paths) == 1
    assert not list(public.iterdir()) and not list(private.iterdir())


@pytest.mark.parametrize("inventory", [None, {"commit": 123, "count": 0, "agents": []},
                                      {"commit": "a" * 40, "count": 1, "agents": ["a", "b"]},
                                      {"commit": "c" * 40, "count": 1, "agents": ["private text"]}])
def test_invalid_optional_inventory_fails_closed(paths, inventory):
    source, public, private = paths
    assert run(paths, ledger()) == 0
    baseline = source.with_name("baseline.json")
    baseline.write_text(json.dumps(inventory), encoding="utf-8")
    assert cli(*paths, "--inventory-json", str(baseline)) == 1
    assert not list(public.iterdir()) and not list(private.iterdir())


@pytest.mark.parametrize(("share", "restricted"), [("out", "out"), ("out", "out/child"), ("out/child", "out"),
                                                  ("out/../safe", "safe"), ("safe/child", "safe/other/..")])
def test_output_directories_cannot_overlap(tmp_path, share, restricted):
    source = tmp_path / "ledger.json"
    assert run((source, tmp_path / share, tmp_path / restricted), ledger()) == 1
    assert list(tmp_path.iterdir()) == [source]


def test_public_directory_cannot_hold_the_ledger_or_unrelated_files(paths):
    _, public, private = paths
    public.mkdir()
    source = public / "input.json"
    assert run((source, public, private), ledger()) == 1
    before = source.read_bytes()
    assert run(paths, ledger()) == 1  # Existing unrelated file, not the input to this call.
    assert list(public.iterdir()) == [source] and source.read_bytes() == before and not private.exists()


def test_directory_alias_cannot_defeat_separation(paths):
    source, public, private = paths
    private.mkdir()
    try:
        public.symlink_to(private, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("Directory symlinks not supported")
        import _winapi
        _winapi.CreateJunction(str(private), str(public))  # No elevation needed.
    assert run((source, public, private), ledger()) == 1 and not list(private.iterdir())


def test_output_file_link_not_followed(paths):
    source, public, private = paths
    public.mkdir()
    try:
        (public / PUBLIC_HTML).symlink_to(source)
    except OSError:
        pytest.skip("File symlink creation is not permitted on this host")
    assert run(paths, ledger()) == 1
    assert json.loads(source.read_bytes()) == ledger() and not private.exists()


def test_existing_hard_link_is_replaced_without_writing_through(paths):
    _, public, private = paths
    assert run(paths, ledger()) == 0
    target = private / PRIVATE_HTML
    target.unlink()
    target.hardlink_to(public / PUBLIC_HTML)
    assert run(paths, ledger()) == 0
    assert not target.samefile(public / PUBLIC_HTML) and "private report" in target.read_text(encoding="utf-8")


def test_failed_public_replace_leaves_no_reports_or_temporaries(paths, monkeypatch):
    _, public, private = paths
    assert run(paths, ledger()) == 0
    replace = Path.replace

    def fail_public_write(source, target):
        if target == public / PUBLIC_HTML:
            raise OSError("synthetic write failure")
        return replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_public_write)
    assert run(paths, ledger()) == 1 and not list(public.iterdir()) and not list(private.iterdir())