# Harness ABI V2

This is the file contract for an evolvable NexAU harness.

## Manifest identity

```yaml
schema_version: 2
abi_version: ahe.harness/v2

framework:
  name: nexau
  version: "0.3.9"

agent_config: agent.yaml

attachments:
  long_term_memory: system_prompt_suffix
```

Every evolvable file must match one component glob and be attached by the
loaded agent tree. Every attached evolvable file must match a component glob.

## `agent.yaml`

`agent.yaml` and every sub-agent config use the pinned NexAU
`AgentConfig.from_yaml` schema.

V2 invariants:

- `tool_call_mode: openai`;
- `system_prompt_type` is `file` or `jinja`;
- every referenced path stays inside `harness/`;
- local Python tools are loaded eagerly;
- tool, skill, and sub-agent names are unique within one agent;
- model settings are placeholders that the runner recursively overrides.

The root config is declared by `agent_config`. A sub-agent config is active
only when a parent contains:

```yaml
sub_agents:
  - name: reviewer
    config_path: ./sub_agents/reviewer/agent.yaml
```

The `name` must equal the nested config's `name`.

## System prompts

Prompt files are Markdown or Jinja text and need no Python signature. They are
active only when an agent config references them:

```yaml
system_prompt: ./systemprompt.md
system_prompt_type: file
```

## Internal tool description

One strict tool YAML describes one harness-owned tool:

```yaml
type: tool
name: record_note
description: Preserve one confirmed observation for later reasoning.
input_schema:
  type: object
  additionalProperties: false
  properties:
    note:
      type: string
  required:
    - note
binding: tools.record_note:record_note
```

The agent config registers that YAML:

```yaml
tools:
  - name: record_note
    yaml_path: ./tool_descriptions/record_note.tool.yaml
```

`name` must match in both files. `input_schema` must be valid JSON Schema with
`type: object` and `additionalProperties: false`.

## Internal tool implementation

The binding resolves relative to the harness root while the checkpoint is
loaded. The implementation must be a function with explicit
keyword-compatible parameters:

```python
def record_note(note: str, agent_state: AgentState) -> dict[str, object]: ...
```

Rules:

- schema property names exactly equal ordinary callable parameter names;
- schema `required` names exactly equal parameters without defaults;
- `agent_state`, `global_storage`, and `sandbox` are framework-injected and do
  not appear in the schema;
- keys supplied through `extra_kwargs` do not appear in the schema;
- positional-only parameters, `*args`, and `**kwargs` are rejected;
- the source file must be inside `harness/`;
- V2 local tools cannot use NexAU lazy binding.

Registered implementations may import support modules under the manifest's
tool-implementation globs. Conformance treats those transitive local imports
as active dependencies, so helpers do not need dummy tool registrations.
Unimported Python files matched by the same globs remain invalid because they
are not attached to runtime behavior.

## Middleware

Middleware must be a visible class derived from NexAU `Middleware`:

```python
from nexau.archs.main_sub.execution.hooks import Middleware


class ContextGuard(Middleware):
    def __init__(self, maximum_items: int = 20) -> None:
        self.maximum_items = maximum_items
```

Register it with constructor parameters:

```yaml
middlewares:
  - import: middleware.context_guard:ContextGuard
    params:
      maximum_items: 20
```

The import must resolve inside `harness/`, and the configured parameters must
bind to the constructor signature. Candidate middleware is host-imported
Python; conformance is not an operating-system security sandbox.

## Skills

A skill is active only when its directory is registered:

```yaml
skills:
  - ./skills/evidence-first
```

The directory contains `SKILL.md`:

```markdown
---
name: evidence-first
description: Use when a task needs a repeatable evidence-verification procedure.
---

# Procedure

Substantive reusable instructions...
```

Both `name` and `description` are required, names must be unique within one
agent, and the body must contain substantive instructions.

## Sub-agents

Each sub-agent has its own `agent.yaml` and file-backed system prompt. The same
prompt, tool, middleware, skill, import, and recursive sub-agent rules apply.
The evaluated runtime recursively replaces root and descendant model settings
with the campaign's exact evaluated-model settings.

## Long-term memory

Long-term memory is ordinary text/Markdown. It has no Python signature.

It is active only when:

1. it matches `components.long_term_memory`; and
2. the manifest declares:

   ```yaml
   attachments:
     long_term_memory: system_prompt_suffix
   ```

The runner reads matched files and appends them to the root agent's in-memory
`system_prompt_suffix` before constructing the `Agent`. The files are not
copied into the task prompt.

Runtime conversation history is short-term memory. A file named
`ShortTermMEMORY.md` is not automatically loaded; the template marks it
runtime-owned only to demonstrate an immutable runtime-state surface.

## Protected benchmark actions

Protected benchmark action schemas do not appear in this ABI or in
`harness/`. A V2 adapter declares them through `BenchmarkInterface`, and the
runner overlays selected schemas on the in-memory NexAU config for a session.
Their execution, call IDs, and semantics remain adapter-owned and immutable.

## Check command

```bash
ahe conformance --config /path/to/project/ahe.yaml
```

The command reports active file registrations, tool contracts, recursive
agent configs, the installed NexAU version, and the NexAU load smoke.
