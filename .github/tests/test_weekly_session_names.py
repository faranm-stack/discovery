import re
import pytest
from run_catalog_smoke import investigation_name


def test_legacy_name_unchanged():
    assert investigation_name("chemberta") == "chemberta"


def test_weekly_run_and_agent_identity_are_distinct():
    names = {investigation_name(agent, run_id=label)
             for agent in ("chemberta", "a-very-long-agent-name-first", "a-very-long-agent-name-second")
             for label in ("45r1a", "45r2a")}
    assert len(names) == 6
    assert all(3 <= len(name) <= 24 and re.fullmatch(r"[A-Za-z0-9-]+", name) for name in names)


@pytest.mark.parametrize("label", ["", "../bad", "x"*13])
def test_unsafe_label_rejected(label):
    with pytest.raises(ValueError):
        investigation_name("chemberta", run_id=label)