"""User's minimal catalog smoke contract; shared by planning and execution."""

CONTRACT_ID = "first-documented-task-and-tool-roundtrip-v2"

ROUNDTRIP_PROMPT = (
    "Use the agent's own catalog tool for exactly one minimal domain task. "
    "If the action needs a nodePool, call GetNodePoolContext first and use the "
    "non-empty nodePool resource ID returned for a suitable existing pool. "
    "Then run the smallest valid task with the provided input. Do not list tools, "
    "check versions, create placeholder sample files in place of the task, or use platform helpers as a substitute. "
    "Return the actual result and any output path or artifact reference. "
    "If required input, credentials, or capacity are missing, report the blocker and stop."
)


def capability_prompt(agent, candidate=None, expected_output=None):
    """The documented task is the instruction, not optional background context."""
    name = str(agent.get("name") or "agent")
    if not candidate:
        return (f"Agent: {name}. No concrete documented task/input has been selected. "
                "Do not submit this entry; record the missing task or input instead of substituting a utility probe.")
    prompt = f"{candidate.strip()}\n\nAgent: {name}.\n{ROUNDTRIP_PROMPT}"
    if expected_output:
        prompt += f"\nDocumented output to return (not a precomputed answer): {expected_output}"
    return prompt