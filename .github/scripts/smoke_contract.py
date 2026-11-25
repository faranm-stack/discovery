"""User's minimal catalog smoke contract; shared by planning and execution."""

import re


CONTRACT_ID = "first-documented-task-and-tool-roundtrip-v2"
MISSING_TASK_INPUT = "documented_task_input_missing"  # Existing execution-core stop condition.
UNRESOLVED_INPUTS = "documented_inputs_unresolved"

# A declaration is a list of literal, portable leaf names, not a location or a command.
_LEAF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*(?:\.[A-Za-z0-9][A-Za-z0-9_-]*)+")
_DEVICE = re.compile(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])\.", re.I)
_NO_FILES = {"none", "n/a", "not applicable", "not required", "none required",
             "no files", "no input files", "no files required", "no input files required"}


def is_input_filename(value: object) -> bool:
    return (isinstance(value, str) and len(value) <= 255 and _LEAF.fullmatch(value) is not None
            and not _DEVICE.match(value) and not value.lower().startswith("www."))


def documented_input_files(cell: str) -> list[str] | None:
    """Resolve the *entire* input cell. [] means explicitly file-free; None means blocked.

    Never take a basename from a path, fish a name out of prose, or open a file.
    A single unrecognized item makes a partial list unsafe to use.
    """
    text = cell.strip()
    if text.casefold() in _NO_FILES:
        return []
    names = []
    for token in re.split(r",|<br\s*/?>", text, flags=re.I):
        name = token.strip()
        if name.startswith("`") and name.endswith("`"):
            name = name[1:-1]
        if not is_input_filename(name):
            return None
        names.append(name)
    return list(dict.fromkeys(names))

ROUNDTRIP_PROMPT = (
    "Use the agent's own catalog tool for exactly one minimal domain task. "
    "If the action needs a nodePool, call GetNodePoolContext first and use the "
    "non-empty nodePool resource ID returned for a suitable existing pool. "
    "Then run the smallest valid task with the provided input. Do not list tools, "
    "check versions, create placeholder sample files in place of the task, or use platform helpers as a substitute. "
    "Return the actual result and any output path or artifact reference. "
    "If required input, credentials, or capacity are missing, report the blocker and stop."
)


def capability_prompt(agent, candidate=None, expected_output=None, *, inputs_unresolved=False):
    """The documented task is the instruction, not optional background context."""
    name = str(agent.get("name") or "agent")
    if inputs_unresolved:
        return (f"Agent: {name}. The selected task has an unresolved input file declaration. "
                "Do not submit this entry; do not infer, create or fetch substitute inputs.")
    if not candidate:
        return (f"Agent: {name}. No concrete documented task/input has been selected. "
                "Do not submit this entry; record the missing task or input instead of substituting a utility probe.")
    prompt = f"{candidate.strip()}\n\nAgent: {name}.\n{ROUNDTRIP_PROMPT}"
    if expected_output:
        prompt += f"\nDocumented output to return (not a precomputed answer): {expected_output}"
    return prompt