# Sub-agents

Create one directory containing an `agent.yaml` and its prompt files per
sub-agent, and list it under `sub_agents` in the root `agent.yaml`. NexAU
exposes each one to the parent as a `sub-agent-<name>` tool call.

- A sub-agent works in the same trial sandbox as its parent. It shares the
  session's global storage, so its tools can call
  `harvey_ahe.workspace.handle_for(agent_state)` like the root agent's tools.
- `max_iterations` must be an integer no greater than 200.
- A sub-agent's prompt must not contain task identifiers, task-specific content,
  or grading rubric details.
