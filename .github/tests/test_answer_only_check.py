"""Offline reporting grades: a structural answer check is never evidence of a tool pass."""

import pytest

from catalog_public_report import project


@pytest.mark.parametrize(("fields", "grade"), [
    ({"tool_roundtrip": "not_applicable", "agent_retried": True}, "C"),
    ({"tool_roundtrip": "pass", "reason": "answer_only_check: arbitrary prose"}, "A"),
    ({"tool_roundtrip": "pass", "agent_retried": True}, "B"),
    ({"tool_roundtrip": "pass", "agent_retried": "true"}, "A"),
    ({"reason": "Expected tool completed successfully"}, "Unknown"),
])
def test_pass_basis_comes_only_from_structured_fields(fields, grade):
    row = {"catalog_agent": "sample", "status": "passed", **fields}
    report = project({"mode": "live", "total": 1, "counts": {"passed": 1}, "results": [row]})
    assert report["rows"]["sample"] == ("passed", grade, "Unknown")


def test_failed_answer_check_is_not_a_pass():
    row = {"catalog_agent": "sample", "status": "failed", "tool_roundtrip": "not_applicable"}
    report = project({"mode": "live", "total": 1, "counts": {"failed": 1}, "results": [row]})
    assert report["rows"]["sample"][:2] == ("failed", "F")
