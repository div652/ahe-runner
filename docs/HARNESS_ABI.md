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

## Context accounting

NexAU checks `max_context_tokens` before each model call. Its default counter
is only a character-based estimate, so a protected manifest can freeze the
counter and counting unit used by the evaluated harness:

```yaml
context_accounting:
  max_context_tokens: 911793
  token_counter: benchmark_support.token_count:count_prompt_tokens
  counting_unit: o200k_base tokens
  provider_max_input_tokens: 921793
```

When this block is present, every root and sub-agent config must declare the
same `max_context_tokens` and `token_counter`. A `null` counter means the field
must be absent and NexAU's fallback estimate is used. If a provider limit is
declared, the configured stop threshold cannot exceed it.

The manifest is outside the evolvable harness. Keep a custom counter in
protected project or adapter code so Evolve cannot change resource accounting;
conformance rejects a counter module inside `harness/`. Include the protected
source in `adapter_dependency_paths` so campaign provenance hashes it.
`provider_max_input_tokens` uses the declared counting unit. The runner does
not claim that one tokenizer is valid for every model: update the counter,
unit, limit, and safety margin whenever the evaluated model or transport
changes.

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

A protected manifest may opt into exact compatibility with an external tool
schema that omits `additionalProperties`:

```yaml
tool_schemas:
  allow_omitted_additional_properties: true
```

The default is `false`. Compatibility mode permits omission only; an explicit
value other than `false` remains invalid. Tool bindings still cannot accept
`**kwargs`, so an unexpected model argument reaches no benchmark operation:
NexAU returns a tool error instead. The manifest is outside the candidate
harness, so Evolve cannot relax this policy.

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
as active dependencies, including ordinary imports inside a registered tool
function and forms such as `from tools import helper`, so helpers do not need
dummy tool registrations. The validator walks the local static import graph
without executing tool functions and also retains observed eager imports.
Dynamic imports are recognized only when they execute while the registered
binding loads or are represented by an explicit registered component.
Unreferenced Python files matched by the same globs remain invalid because
they are not attached to runtime behavior.

Top-level candidate module/package names must not collide with Python
standard-library modules, already loaded host modules, or other importable
packages in the runner environment. The runner temporarily puts the harness
first on `sys.path`; rejecting names such as `json.py` prevents candidate code
from replacing runner, adapter, evaluator, or dependency imports. Prefer
harness-specific package names and keep reusable helpers below those packages.

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
agent, and the body must contain substantive instructions. The complete
registered skill directory is runtime-attached: `SKILL.md`, scripts,
references, templates, and other regular assets are hashed and checked by
conformance because NexAU uploads the complete directory. Declare the complete
directory in the component manifest, for example:

```yaml
components:
  skills:
    - skills/*/**
```

An undeclared file inside a registered skill fails conformance rather than
silently changing runtime behavior.

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
`system_prompt_suffix` before constructing the `Agent`. Empty or whitespace
files add nothing. Nonempty memory is separated from the rendered system
prompt by a blank line because NexAU otherwise concatenates the suffix
directly. The files are not copied into the task prompt.

The memory file itself is evaluated-agent prompt content. Keep it empty until
there is an evidence-supported reusable procedure or stable lesson to retain;
put instructions about how to use the component in Evolve guidance, not in
the memory shown to the evaluated agent.

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
