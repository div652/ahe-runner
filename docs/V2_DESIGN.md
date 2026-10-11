# AHE Runner V2 Design

## Status

Approved for implementation on 2026-10-11.

This document defines the V2 boundary between the protected AHE runner, the
evolvable NexAU harness, and a benchmark adapter. It is the implementation
contract for `ahe-runner 0.2.x`.

## Operator promise

An operator supplies:

```text
ahe.yaml
harness_manifest.yaml
benchmark_adapter.py
harness/
```

The runner then:

1. loads the file-backed harness;
2. constructs the evaluated agent with the same NexAU `Agent` and `Executor`
   used by the Source Explore, Web Explore, Debugger, and Evolve roles;
3. gives the benchmark adapter a protected evaluated-agent session;
4. runs the declared training/evolution/selection/test lifecycle;
5. exports the selected improved harness.

Benchmark integrations do not construct a separate agent loop.

## Three ownership domains

### AHE runner

The installed runner owns:

- campaign sequencing and recovery;
- immutable checkpoints and Git workspaces;
- evaluated-agent construction;
- exact model and transport injection;
- per-trial session isolation;
- protected benchmark-tool overlays;
- artifact locations and hash verification;
- held-out selection and final-test secrecy;
- meta-agent execution.

### Evolvable harness

The candidate `harness/` owns:

- system prompts;
- harness-owned tool descriptions and implementations;
- middleware and compaction policy;
- skills;
- sub-agents;
- explicitly attached long-term memory.

These files are the only Evolve write surface.

### Benchmark adapter

The protected adapter owns:

- task splits and native task identity;
- prompts, context, files, repositories, and environment setup;
- benchmark-owned external action schemas and execution;
- benchmark conversation or environment orchestration;
- rewards and aggregate metrics;
- result and trace contents;
- training-evidence materialization;
- benchmark-specific candidate validation.

## Evaluated-agent runtime

V2 introduces a protected runtime factory and session:

```python
class EvaluatedAgentRuntime:
    def open_session(
        self,
        request: AgentSessionRequest,
    ) -> EvaluatedAgentSession: ...


class EvaluatedAgentSession:
    def run(self, request: AgentInput) -> AgentOutput: ...
    def close(self) -> None: ...
```

The runtime loads `harness/agent.yaml` through
`nexau.AgentConfig.from_yaml()`, applies protected settings in memory, and
constructs `nexau.Agent(config=...)`.

Each benchmark unit/trial receives a fresh session. Repeated `run()` calls on
that session preserve NexAU conversation history for multi-turn benchmarks.

The adapter receives this session through the V2 evaluation method. It does
not receive responsibility for constructing NexAU.

## Standard activation boundary

`AgentInput` contains ordered user and protected tool-result messages plus
runtime context. The harness system prompt remains owned by the harness and
cannot be replaced by the adapter.

`AgentOutput` contains:

- final assistant text;
- the current normalized conversation messages;
- the uncompacted native NexAU trace;
- any protected benchmark action calls intercepted during the activation.

The output is a runtime interchange object. It does not impose a universal
on-disk benchmark trace schema.

## Harness tools versus benchmark tools

V2 distinguishes two kinds of tools.

### Harness-owned tools

Harness tools are agent capabilities intended to improve during evolution,
such as repository search, a planner, an internal retrieval helper, or a state
tracker.

They:

- are stored under `harness/`;
- are declared in `agent.yaml`;
- are visible and editable to Evolve;
- execute through the normal NexAU tool executor.

### Benchmark-owned tools

Some benchmarks define an official action space, such as domain APIs,
computer-control actions, or function-calling schemas. These actions define
the benchmark and must not evolve.

They:

- are declared by the protected adapter;
- are not stored under `harness/`;
- are not added to the text system prompt when structured calls are required;
- are converted to NexAU `Tool` objects in memory;
- are appended to the loaded `AgentConfig.tools` before `Agent` construction;
- are therefore sent through the model API's structured `tools` field in
  NexAU's OpenAI tool-call mode;
- use fail-closed bindings so the candidate harness cannot execute them
  through its own tool implementation;
- are intercepted at the protected after-model boundary and returned to the
  adapter with exact call IDs and arguments.

The effective runtime configuration is:

```text
candidate AgentConfig
  + protected model/transport settings
  + optional protected benchmark tool schemas
  + protected benchmark-action middleware
```

This is a runtime overlay. It does not modify the base harness or any
checkpoint files.

Tool-name collisions between harness tools and protected benchmark tools are
rejected.

## Making the overlay visible to meta-agents

The adapter supplies a typed `BenchmarkInterface` declaration describing:

- prompt, context, file, repository, and environment inputs;
- whether benchmark actions are absent, prompt-described, or structured;
- protected structured action names and schemas;
- the protected execution boundary.

The declaration is:

- frozen into campaign identity;
- included in Source Explore input;
- included in Debugger and Evolve benchmark context;
- described explicitly in the fixed Source Explore and Evolve prompts.

Source Explore must document how the runtime overlay is assembled. Evolve is
told that protected benchmark actions are available to the evaluated agent but
are not harness files, cannot be edited, and must not be reimplemented inside
the candidate harness.

## BenchmarkAdapterV2

V2 keeps benchmark semantics in the adapter while moving NexAU construction
into the runner:

```python
class BenchmarkAdapterV2(BenchmarkAdapterBase):
    def benchmark_interface(self) -> BenchmarkInterface: ...

    def evaluate(
        self,
        request: EvaluationRequest,
        runtime: EvaluatedAgentRuntime,
    ) -> EvaluationResult: ...
```

The adapter may call `runtime.open_session()` once per trial and invoke the
same session repeatedly for an interactive task.

The original `BenchmarkAdapter` remains available as a compatibility boundary
for existing V1 projects. New generated projects use `BenchmarkAdapterV2`.

## Pluggable benchmark inputs

The adapter may provide:

- user/task messages;
- structured context values;
- files and attachments;
- an isolated repository or workspace;
- an environment/controller;
- prompt-described benchmark capabilities;
- protected structured benchmark actions.

Repositories, datasets, hidden state, evaluators, and rewards remain outside
the candidate harness.

For benchmarks such as repository repair, the protected action list can be
empty: the adapter supplies the repository and task, while the harness uses
its own evolvable editing/search tools.

## Harness ABI V2

`harness_manifest.yaml` declares:

```yaml
schema_version: 2
abi_version: ahe.harness/v2

attachments:
  long_term_memory: system_prompt_suffix
```

The runner verifies:

- the installed NexAU version matches the manifest;
- `agent.yaml` loads with structured OpenAI tool calls;
- every declared system prompt is registered;
- tool YAML uses strict JSON Schema and resolves to a visible Python binding;
- schema properties match callable parameters;
- middleware imports and configured constructor parameters resolve;
- registered skills contain valid frontmatter and substantive instructions;
- registered sub-agents recursively load;
- every file matched as an evolvable component is runtime-attached;
- long-term memory declares an attachment mode and is actually injected;
- runtime-owned and protected fields remain unchanged.

V2 long-term memory initially supports explicit system-prompt-suffix
attachment. Short-term memory is NexAU session history, not a Markdown
component.

Checkpoint-local Python bindings are loaded with the harness root temporarily
isolated on `sys.path`, so the same relative import works in the operator seed,
an Evolve workspace, and an immutable checkpoint. V2 local tool bindings must
resolve when the session opens rather than using NexAU lazy imports.

## Artifact policy

V2 retains the existing adapter-native artifact policy:

- the runner verifies required result and trace paths;
- the runner verifies hashes and immutability;
- the adapter defines and verifies result and trace contents;
- no universal trace-event semantic schema is introduced.

Typed aggregate `EvaluationResult` and training `EvidenceBundle` contracts
remain required.

## Explore policy

The operator remains responsible for useful benchmark-specific Source and Web
Explore guidance through `MetaPromptContext`.

The fixed flow remains:

```text
benchmark-specific guidance
  -> Source/Web Explore
  -> read-only SKILL.md artifacts
  -> Evolve loads both skills
```

V2 does not attempt to judge the semantic quality of the supplied seed.

## Conformance

`ahe conformance` performs objective, non-live checks:

- project and split validation;
- harness ABI validation;
- installed framework-version verification;
- NexAU configuration load;
- protected evaluated-agent construction without a model call;
- model/transport override inspection;
- runtime benchmark-tool overlay inspection;
- benchmark-interface reporting.

The test suite additionally uses fake model clients to exercise:

- one ordinary call-free response;
- one harness-owned tool call;
- one protected benchmark action interception;
- exact call-ID tool-result resumption;
- session isolation;
- recursive exact-model propagation into sub-agents.

## Compatibility and migration

- Existing `BenchmarkAdapter` implementations remain loadable.
- V1 adapters retain responsibility for constructing their evaluated agent.
- V2 adapters receive the protected runtime and should not construct NexAU.
- Generated projects use manifest schema 2 and `BenchmarkAdapterV2`.
- Campaign identity records the adapter API generation and benchmark-interface
  declaration.

## Explicit V2 non-goals

V2 does not add:

- semantic validation of benchmark-native trace events;
- mid-episode persistence or exact partial-episode resume;
- automatic quality scoring of Explore seeds;
- support for agent frameworks other than NexAU.
