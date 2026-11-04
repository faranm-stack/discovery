import pytest
from catalog_sweep import SYNC_REJECTION


@pytest.mark.parametrize("message", [
    "Error: Invalid nodePool value: 'default'. Expected an ARM resource ID in format: /subscriptions/...",
    "Error: A file named 'extract_embeddings.py' already exists in this conversation. Use a different file name.",
])
def test_observed_preexecution_rejections(message):
    assert SYNC_REJECTION.match(message)


@pytest.mark.parametrize("message", ["Error: unexpected execution failure", "Error: A file failed after submission",
    "Tool response: Supercomputer gave Error", "Error: Invalid nodePool value; possibly started"])
def test_unknown_failure_is_not_safe_rejection(message):
    assert not SYNC_REJECTION.match(message)