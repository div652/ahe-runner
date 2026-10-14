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
    EvaluationTrialFact,
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

Use `src/ahe_runner/example/adapter.py` for the offline lifecycle fixture and
`src/ahe_runner/example/runtime_adapter.py` for a real evaluated-session and
protected-action reference. The latter is walked through in
`RUNTIME_BACKED_EXAMPLE.md`.

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

Also declare every local code/data dependency that can change adapter behavior:

```yaml
project:
  adapter_dependency_paths:
    - ./benchmark_adapter.py
    - ./adapter_support
```

These paths are hashed into campaign identity but are not automatically exposed
to meta-agents.

## Adapter packaging and dependencies

The adapter runs in the same Python environment as `ahe-runner`. Prefer a
plain project module plus `adapter_dependency_paths`; an adapter package should
not redeclare `ahe-runner` or an unconstrained `nexau` dependency, because a
resolver can otherwise replace the runner's tested framework version.

The current runner pins:

```text
nexau @ git+https://github.com/nex-agi/NexAU.git@v0.3.9
ag-ui-protocol>=0.1.10,<1.0
```

NexAU 0.3.9 is not published on PyPI. If a separately distributed adapter must
declare these dependencies, use constraints compatible with the runner rather
than a second PyPI-only NexAU declaration. Install and verify the runner first,
then install benchmark-only dependencies without changing the resolved runner
stack.

## Public source roots and role supplements

Source Explore and Evolve can read only explicitly declared public adapter
roots:

```yaml
project:
  meta_agent_source_roots:
    - ./benchmark_public
```

Put public schemas, allowed scoring semantics, trace-format documentation, and
integration guidance there. Never include held-out task content, hidden state,
private verifier code, or answer fixtures. The runner does not infer the
adapter class's implementation file as a readable root.

Projects may append domain guidance without replacing the protected AHE role
procedures:

```yaml
meta:
  role_supplements:
    source: ./meta_supplements/source.md
    web: ./meta_supplements/web.md
    debugger: ./meta_supplements/debugger.md
    evolve: ./meta_supplements/evolve.md
  explore:
    research_seed_urls:
      - https://example.org/primary-agent-method
```

The resolved supplements, research seeds, public-root hashes, adapter
dependencies, benchmark context, runner prompts/tools, and packaged method
reference are frozen into campaign identity. Changing any of them requires a
new campaign directory.

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
        session_resources={"public_repository": repository_index},
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

`working_directory` must be an existing per-trial directory. For the local
NexAU sandbox, the runner makes it the actual tool working directory instead
of inheriting the runner process's current directory. If it is omitted, the
runner creates `artifact_dir/workspace`. NexAU may materialize registered
skill assets under `.skills/` in that working directory, so adapters should
use an isolated trial workspace rather than the source repository or runner
checkout.

Session and activation `context` values are string-valued NexAU template
variables. `session_resources` is the separate object-valued seam: the runner
places those objects in the root agent's NexAU `GlobalStorage`, which its
sub-agents share, while every trial session receives a distinct storage
instance. Use it only for capabilities intentionally available to harness
tools or middleware. Never place hidden answers, verifier state, credentials,
or protected benchmark action implementations there. It is capability
routing, not an operating-system security boundary.

Repeated `session.run(...)` calls preserve that session's NexAU history.
Different sessions use different `Agent` instances and must not share mutable
trial state.

The runtime rejects missing, duplicate, unexpected, or mixed protected call
IDs. NexAU never directly executes the protected action implementation.
Candidate middleware cannot force another model call after an official action
has been captured. This includes NexAU's legacy `after_model_hooks`: the runner
normalizes them into the guarded candidate chain before executor construction.
On a captured official-action turn, candidate after-model processing is
intentionally skipped until the adapter returns the exact action result.

Use `EvaluationStore` to obtain:

- semantic run IDs;
- stable unit IDs;
- absolute contiguous attempt numbers;
- immutable prior attempts;
- hash-verified completion;
- completed-zero preservation;
- full-completion finalization.

Pass `evaluation_request_sha256(request)` into `EvaluationStore`. The runner
independently binds the returned `EvaluationResult.request_sha256` to the exact
checkpoint tree, split definition, trials, seed, model, transport, and
concurrency request.

Do not include fresh preflight timestamps, nonces, or process IDs in
`semantic_inputs`. They are attempt evidence, not run identity.

If the adapter makes its own calls to the same model endpoint—for example, a
benchmark-native judge—join the runner's endpoint-wide request budget:

```python
from ahe_runner.model_requests import model_request_slot

with model_request_slot(request.transport) as timing:
    response = judge_client.chat.completions.create(...)
```

Set `transport.max_concurrent_requests` to the endpoint's safe process-wide
limit. Recorded evaluated/meta calls and adapter calls wrapped by
`model_request_slot()` then share one queue. `timing` contains
`slot_wait_seconds` when a queue is configured. Unwrapped adapter clients
cannot be throttled by the runner.

Return native metrics in `EvaluationResult`. `artifact_hashes` should exclude
the result files that contain those hashes, avoiding recursive identity. It
must otherwise equal the complete output tree: every unit identity,
completion record, result, trace, metadata artifact, runtime trace, and log.

The runner verifies the common envelope before accepting adapter semantics:

- result checkpoint/split/request identity;
- exact task-by-trial unit coverage and trial seeds;
- result/trace/metadata paths and hashes inside each unit;
- task-state coverage and rollout counts;
- primary metric consistency;
- one canonical `EvaluationTrialFact` for every unit;
- complete output artifact coverage.

Each `EvaluationTrialFact` contains the evaluation unit ID, native outcome,
optional native score, termination reason, and a diagnostic class:
`passing`, `failing`, or `other`. `verify_evaluation()` must independently
recompute the same facts from protected native artifacts. The diagnostic class
states whether pass/fail paired Debugger comparison is applicable; it does not
replace benchmark-native metrics.

The adapter remains responsible for deciding whether the final text/action
sequence is a valid benchmark outcome. The runner verifies artifact paths,
hashes, and exact evidence agreement with the adapter-verified facts, but does
not parse private trace events or invent a universal reward function.

When metric semantics change, update all of these together:

1. adapter identity and primary-metric name;
2. evaluation aggregation and selection key;
3. protected verification and `EvaluationTrialFact` derivation;
4. `meta_prompt_context()` metric definitions;
5. public benchmark documentation and all role supplements;
6. tests, examples, and report labels.

Optionally list obsolete labels under `meta.deprecated_metric_terms`. Local
`validate` and `conformance` then warn when a role supplement still contains
one. This is a literal stale-term check, not a semantic proof that arbitrary
metric prose is correct. Metric changes require a fresh campaign directory.

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
- source evaluation ID, request identity, exact path, and hash;
- native aggregate metrics;
- task state;
- detail path, hash, and trial count.

Dense evidence may include complete allowed trajectories. Sanitized evidence
must preserve enough causal information for Debugger while removing protected
answers or hidden state.

Every schema-version-3 detail document contains one common trial envelope per
expected rollout:

- source evaluation unit ID, trial index, and seed;
- native outcome and optional score, including valid zero reward;
- termination reason;
- diagnostic class (`passing`, `failing`, or `other`);
- at least one trace plus a result/evaluator artifact;
- evaluation-relative artifact paths and hashes;
- optional benchmark-native fields underneath the common envelope.

The runner checks exact task/trial coverage, source evaluation/hash lineage,
metric equality with the accepted evaluation, every referenced artifact, and
exact equality of outcome, score, termination, and diagnostic class with the
adapter-verified canonical trial fact. Native event parsing and reward
recomputation remain adapter-owned.

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
success-shaped fallback. The same adapter check runs inside Evolve's
`validate_candidate` tool so a recoverable benchmark-specific defect can be
fixed before the stage completes. Completion is accepted only when validation
matches the final committed Git `HEAD`.

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
