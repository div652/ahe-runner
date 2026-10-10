# Benchmark Adapter Guide

The adapter is the protected typed boundary between generic AHE lifecycle logic
and a native benchmark.

## Minimal class

```python
from ahe_runner.adapter import (
    AdapterIdentity,
    BenchmarkAdapter,
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


class MyAdapter(BenchmarkAdapter):
    def identity(self) -> AdapterIdentity: ...

    def split(self, name: SplitName) -> SplitDefinition: ...

    def evaluate(self, request: EvaluationRequest) -> EvaluationResult: ...

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

## `evaluate()`

Run or resume the native benchmark using the exact request:

- immutable checkpoint harness;
- split definition;
- trials and root seed;
- evaluated model and transport;
- output directory;
- maximum concurrency.

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
