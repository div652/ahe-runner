"""RRSI's component vocabulary and its mapping onto NexAU harness components.

RRSI tags every edit with one component of a fixed vocabulary K (paper
Eq. 12); novelty counts the structural subset K_str (Eq. 15). The proposer
declares the tag, and the tag is kept only when the edit's files are of a
NexAU component class that can carry it; otherwise the edit is re-tagged to
its class's default, as RRSI re-tags a declaration the diff does not support.

| NexAU class (change manifest)   | RRSI tags it can carry                      |
|---------------------------------|---------------------------------------------|
| system_prompt                   | prompt                                      |
| tool_description (edited)       | prompt                                      |
| tool_implementation (edited)    | control_flow, output_plumbing, context_mgmt |
| tool_description or             | client_tool (a new tool)                    |
| tool_implementation (added)     |                                             |
| middleware                      | control_flow, context_mgmt, output_plumbing |
| skill                           | skill                                       |
| sub_agent                       | subagent                                    |
| memory                          | memory                                      |
| agent_config (agent.yaml)       | config                                      |
| other files                     | control_flow, output_plumbing, context_mgmt |
"""

from __future__ import annotations

from collections.abc import Iterable

K = (
    "prompt",
    "control_flow",
    "config",
    "output_plumbing",
    "context_mgmt",
    "client_tool",
    "skill",
    "memory",
    "subagent",
)
K_STR = ("client_tool", "skill", "memory", "subagent")

ALLOWED_TAGS: dict[str, tuple[str, ...]] = {
    "system_prompt": ("prompt",),
    "tool_description": ("prompt",),
    "tool_implementation": ("control_flow", "output_plumbing", "context_mgmt"),
    "middleware": ("control_flow", "context_mgmt", "output_plumbing"),
    "skill": ("skill",),
    "sub_agent": ("subagent",),
    "memory": ("memory",),
    "agent_config": ("config",),
    "other": ("control_flow", "output_plumbing", "context_mgmt"),
}
DEFAULT_TAG: dict[str, str] = {
    "system_prompt": "prompt",
    "tool_description": "prompt",
    "tool_implementation": "control_flow",
    "middleware": "control_flow",
    "skill": "skill",
    "sub_agent": "subagent",
    "memory": "memory",
    "agent_config": "config",
    "other": "control_flow",
}
# Precedence when an edit spans several classes: the most structural class
# decides the change-manifest component and the default tag.
_CLASS_PRECEDENCE = (
    "sub_agent",
    "skill",
    "memory",
    "tool_implementation",
    "tool_description",
    "middleware",
    "system_prompt",
    "other",
    "agent_config",
)


def primary_class(classes: Iterable[str]) -> str:
    present = set(classes)
    for name in _CLASS_PRECEDENCE:
        if name in present:
            return name
    return "other"


def normalize_tag(
    declared: str | None,
    changes: list[dict[str, str]],
) -> tuple[str, str | None]:
    """The tag an edit keeps, and why a declared tag was replaced (or None).

    ``changes`` are the edit's file changes: {"path", "change", "component"}.
    """

    tag = (declared or "").strip().lower()
    classes = [row["component"] for row in changes]
    new_tool = any(
        row["change"] == "added"
        and row["component"] in {"tool_description", "tool_implementation"}
        for row in changes
    )
    allowed: set[str] = set()
    substantive = [
        row for row in changes if row["component"] != "agent_config"
    ] or changes
    for row in substantive:
        if row["change"] == "added" and row["component"] in {
            "tool_description",
            "tool_implementation",
        }:
            allowed.add("client_tool")  # a new tool is client_tool only
        else:
            allowed.update(ALLOWED_TAGS.get(row["component"], ALLOWED_TAGS["other"]))
    if tag in K and tag in allowed:
        return tag, None
    fallback = "client_tool" if new_tool else DEFAULT_TAG[primary_class(classes)]
    reason = (
        f"declared {tag!r} is not in the vocabulary {list(K)}"
        if tag not in K
        else f"declared {tag!r} is not supported by the changed files "
        f"({sorted(set(classes))}); re-tagged {fallback!r}"
    )
    return fallback, reason


def novelty(components: Iterable[str], accepted_counts: dict[str, int]) -> int:
    """nu_t(H'): structural component types touched that never won before."""

    return sum(
        1
        for component in set(components)
        if component in K_STR and accepted_counts.get(component, 0) == 0
    )
