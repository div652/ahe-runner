# Benchmark Adapter Guide

The adapter is the protected typed boundary between generic AHE lifecycle logic
and a native benchmark.

## Minimal V2 class

```python
from ahe_runner.adapter import (
    AdapterIdentity,
    BenchmarkAdapterV2,
    BenchmarkInterface,
    BenchmarkToolSpec,
    CandidateValidationRequest,
    EvaluationRequest,
    EvaluationResult,
    EvidenceBundle,
    EvidenceRequest,
    MetaPromptContext,
    MetricSummary,
    SplitDefinition,
    SplitName,
    TransitionSummary,
    ValidationResult,
    VerificationRequest,
)
from ahe_runner.runtime import (
    AgentInput,
    AgentInputMessage,
    AgentSessionRequest,
    EvaluatedAgentRuntime,
)


class MyAdapter(BenchmarkAdapterV2):
    def identity(self) -> AdapterIdentity: ...

    def split(self, name: SplitName) -> SplitDefinition: ...

    def benchmark_interface(self) -> BenchmarkInterface: ...

    def evaluate(
        self,
        request: EvaluationRequest,
        runtime: EvaluatedAgentRuntime,
    ) -> EvaluationResult: ...

    def verify_evaluation(self, request: VerificationRequest) -> EvaluationResult: ...

    def materialize_training_evidence(
        self, request: EvidenceRequest
    ) -> EvidenceBundle: ...

    def validate_candidate(
        self, request: CandidateValidationRequest
    ) -> ValidationResult: ...

    def compare(
        self, before: MetricSummary, after: MetricSummary
    ) -> TransitionSummary: ...

    def selection_key(self, summary: MetricSummary) -> tuple[object, ...]: ...

    def meta_prompt_context(self) -> MetaPromptContext: ...
```

Use `src/ahe_runner/example/adapter.py` as an executable reference.

`BenchmarkAdapter` remains available only for existing V1 projects. New
projects should implement `BenchmarkAdapterV2`.

## Ownership boundary

The runner owns construction of the evaluated NexAU `Agent` and `Executor`.
The adapter owns benchmark tasks, environments, official actions, rewards,
native traces, and evidence conversion. The harness owns only evolvable agent
behavior.

Use this test:

- if changing a tool improves the agent, put it in `harness/`;
- if changing a tool changes the benchmark protocol, keep it in the adapter as
  a protected benchmark action.

Many repository/data benchmarks need no protected structured actions. Supply
their task prompt, files, repository, or workspace through the session input.
When a benchmark defines an official action API, declare it through
`BenchmarkInterface` and attach its schema per session.

## `identity()`

Return pinned adapter, benchmark, protocol, metric, and supervision identities.
These values become immutable campaign identity.

Change the adapter or benchmark version when native semantics change.

## `split()`

Return one stable `SplitDefinition` for train, validation, and test.

Requirements:

- identities are immutable and meaningful;
- task IDs are stable;
- the three task-ID sets are disjoint;
- hidden state remains inside the adapter or benchmark;
- validation/test IDs and content are never placed in training evidence.

The controller hashes split IDs in the campaign manifest but does not reveal
held-out tasks to meta-agents.

## `benchmark_interface()`

Declare all benchmark inputs and any protected structured actions:

```python
LOOK_UP = BenchmarkToolSpec(
    name="lookup_account",
    description="Read one account from the protected benchmark environment.",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "account_id": {"type": "string"},
        },
        "required": ["account_id"],
    },
)


def benchmark_interface(self) -> BenchmarkInterface:
    return BenchmarkInterface(
        description="Support tasks executed against a protected simulator.",
        input_kinds=("prompt", "environment"),
        tool_delivery="structured",
        protected_tools=(LOOK_UP,),
    )
```

This declaration is frozen into campaign identity and shown to Source Explore,
Debugger, and Evolve as immutable context.

Protected schemas are appended to the in-memory NexAU `config.tools` list.
With `tool_call_mode: openai`, they are sent through the model API's structured
`tools` field. They are not appended to the text prompt, written into
`harness/`, or exposed as Evolve-editable files.

## `evaluate(request, runtime)`

Run or resume the native benchmark using the exact request:

- immutable checkpoint harness;
- split definition;
- trials and root seed;
- evaluated model and transport;
- output directory;
- maximum concurrency.

Open one runtime session per independent trial:

```python
session = runtime.open_session(
    AgentSessionRequest(
        session_id=unit_id,
        artifact_dir=request.output_dir / "runtime" / unit_id,
        working_directory=prepared_repository,
        benchmark_tools=(LOOK_UP,),
        context={"task_id": task_id},
    )
)
try:
    output = session.run(
        AgentInput(
            messages=(
                AgentInputMessage(
                    role="user",
                    content=task_prompt,
                ),
            )
        )
    )
    while output.external_actions:
        result_messages = []
        for action in output.external_actions:
            native_result = environment.execute(action.name, action.arguments)
            result_messages.append(
                AgentInputMessage(
                    role="tool",
                    tool_call_id=action.tool_call_id,
                    content=native_result,
                )
            )
        output = session.run(AgentInput(messages=tuple(result_messages)))
finally:
    session.close()
```

Repeated `session.run(...)` calls preserve that session's NexAU history.
Different sessions use different `Agent` instances and must not share mutable
trial state.

The runtime rejects missing, duplicate, unexpected, or mixed protected call
IDs. NexAU never directly executes the protected action implementation.

Use `EvaluationStore` to obtain:

- semantic run IDs;
- stable unit IDs;
- absolute contiguous attempt numbers;
- immutable prior attempts;
- hash-verified completion;
- completed-zero preservation;
- full-completion finalization.

Do not include fresh preflight timestamps, nonces, or process IDs in
`semantic_inputs`. They are attempt evidence, not run identity.

Return native metrics in `EvaluationResult`. `artifact_hashes` should exclude
the result files that contain those hashes, avoiding recursive identity.

The adapter remains responsible for deciding whether the final text/action
sequence is a valid benchmark outcome. The runner verifies artifact paths and
hashes but does not invent universal benchmark-event semantics.

## `verify_evaluation()`

Independently verify persisted native artifacts before the controller accepts
the stage.

At minimum:

- verify every expected unit exists;
- verify completion hashes;
- reject malformed or gapped attempts;
- recompute aggregate metrics from valid completed units;
- return the same `EvaluationResult` as `evaluate()`.

Do not merely trust `adapter_result.json` for a production benchmark.

## `materialize_training_evidence()`

Convert only training results into:

```text
evidence_manifest.json
tasks/<stable-key>.json
```

The manifest supplies:

- checkpoint;
- supervision policy;
- whether true task IDs are exposed;
- source evaluation identity and hash;
- native aggregate metrics;
- task state;
- detail path and hash.

Dense evidence may include complete allowed trajectories. Sanitized evidence
must preserve enough causal information for Debugger while removing protected
answers or hidden state.

Every detail document should distinguish:

- valid outcome, including zero reward;
- infrastructure failure;
- termination reason;
- canonical events or trace references;
- native metric contribution;
- source hashes.

## `validate_candidate()`

Add benchmark-specific static checks without running held-out tasks.

Examples:

- required domain tool registrations;
- prohibited evaluator imports;
- tool schema/implementation consistency;
- simulator boundary checks;
- task memorization patterns that generic validation cannot detect;
- model-routing invariants.

Return explicit errors and warnings. Never convert an invalid harness into a
success-shaped fallback.

## `compare()`

Compare two training summaries with benchmark-native semantics.

Return:

- primary metric delta;
- improved, regressed, and unchanged task keys;
- secondary metric or causal metadata.

This attribution becomes evidence for the next Evolve round.

## `selection_key()`

Return a deterministic tuple used to rank validation summaries.

Example:

```python
def selection_key(self, summary: MetricSummary) -> tuple[object, ...]:
    return (
        summary.primary.value,
        summary.metrics["pass_at_2"],
        -summary.resource_totals.get("model_calls", 0),
    )
```

The controller adds an earliest-checkpoint tie-break. Final-test metrics are
not available during selection.

## `meta_prompt_context()`

Translate benchmark vocabulary without replacing the full AHE prompts:

```python
return MetaPromptContext(
    evaluated_agent_role="enterprise support dialogue agent",
    benchmark_objective="Complete the user's goal under policy constraints.",
    evidence_description="Dense train trajectories with tool and state events.",
    failure_taxonomy=[
        "policy interpretation",
        "state tracking",
        "tool selection",
        "premature termination",
    ],
    protected_protocol=[
        "Environment, user simulator, reward, and held-out tasks are protected."
    ],
    web_research_scope="General reliable tool-using dialogue-agent methods.",
    source_exploration_scope="NexAU runtime and adapter integration.",
    task_id_leakage_terms=["benchmark-specific forbidden query fragment"],
)
```

The leakage terms are applied to public web queries and URLs.

## Infrastructure classification

Raise `InfrastructureError` only when no valid benchmark outcome was produced,
for example:

- endpoint timeout;
- connection failure;
- simulator service unavailable;
- corrupted external runtime;
- server-side transient error.

Do not raise it for:

- valid zero reward;
- agent policy failure;
- invalid tool use;
- deterministic candidate crash;
- malformed evolved harness.

The latter are candidate evidence or blocked failures, not automatic retries.

## Reproducibility checklist

- Pin benchmark and adapter versions.
- Persist native inputs and hashes.
- Record exact model request/returned labels for every model-calling role.
- Preserve attempt histories.
- Derive seeds deterministically.
- Recompute metrics during verification.
- Keep validation/test artifacts outside all meta-agent roots.
- Never silently change task sets, retry counts, models, or metrics on resume.
