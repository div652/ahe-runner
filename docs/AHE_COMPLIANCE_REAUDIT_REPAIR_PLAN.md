# AHE Compliance Follow-Up Repair Plan

**Follow-up audit:** [`AHE_COMPLIANCE_REAUDIT.md`](AHE_COMPLIANCE_REAUDIT.md)  
**Audited runner commit:** `5380f65058a970e8e8ecfca00bf1f0a7045becea`  
**Repair baseline:** `ca179a6`

**Status:** F01-F07 and the historical documentation correction are
implemented. Repository-wide release validation is recorded separately in
[`VALIDATION_RESULTS.md`](VALIDATION_RESULTS.md).

## Decision

All seven follow-up findings are valid against the audited implementation.
The historical correction about the bundled public Debugger is also valid.
No finding requires redesigning the AHE optimizer, changing rollback policy,
making Explore outputs mutable, or moving benchmark-owned actions into the
evolvable harness.

The repair preserves:

- one shared harness per checkpoint;
- completed training evidence as the only input to the next edit;
- immutable checkpoints and typed change manifests;
- agent-mediated `KEEP`, `IMPROVE`, and `ROLLBACK + PIVOT`;
- benchmark-native metrics and adapter-owned protected actions;
- physically separate held-out evaluation and meta-agent roots.

## Accepted findings and implementation contract

| ID | Decision | Repair contract |
|---|---|---|
| F01 | Accept | Reject protected-field collisions in `extra_body`, validate standard aliases before provider access, normalize the configured reasoning representation, and record only the effective fixed request. Conflicts must send zero requests. |
| F02 | Accept | Fold legacy `after_model_hooks` into the guarded candidate middleware chain before NexAU constructs its executor. Protected capture runs first in reverse `after_model` order, and a captured official action cannot inherit an earlier `force_continue`. |
| F03 | Accept | Persist only a non-secret API-key placeholder in generated meta-agent YAML. Inject the resolved key into the loaded in-memory config and recorded client only after the readable file has been parsed. |
| F04 | Accept | Upgrade the evaluation contract with adapter-verified canonical trial facts: unit ID, native outcome, optional native score, termination reason, and a diagnostic class (`passing`, `failing`, or `other`). Evidence must exactly match those facts. |
| F05 | Accept | Upgrade Debugger citations to structured artifact/range references. Record only nonempty line reads, byte reads, and search hits; bind every diagnosis and paired citation to that ledger; derive paired availability and pass/fail artifact classes from verified trial facts. |
| F06 | Accept | Discover local Python support dependencies through a static recursive import graph rooted at registered bindings, in addition to observed eager imports. Function-local imports conform without executing arbitrary tools. |
| F07 | Accept | Compact old tool results only when the replacement is smaller, then compact old tool-call arguments while preserving call IDs, tool names, result IDs, and error flags. Preserve the latest iteration. If the target remains impossible, block the model call explicitly rather than silently proceeding. |

## Deliberate boundaries

### Protected-action middleware seam

On a turn containing a protected benchmark action, candidate `after_model`
middleware and legacy `after_model_hooks` do not receive a post-capture seam.
This is intentional: no candidate hook may mutate, continue, confirm, or
replace an official action before the adapter executes it. Normal turns retain
NexAU's configured ordering.

### Canonical trial facts

The runner does not parse benchmark-native traces or invent reward semantics.
The trusted adapter independently recomputes `EvaluationTrialFact` records in
`verify_evaluation()`. The runner verifies exact unit coverage and requires
training evidence to reproduce those records. `diagnostic_class` exists only
to state whether paired Debugger comparison is applicable; benchmarks with
non-binary or incomparable outcomes may use `other`.

### Debugger grounding

The gate proves that cited artifact ranges were returned by bounded tools. It
does not claim that the model understood the evidence or established causal
truth. A paired comparison is mandatory only when verified canonical facts
contain at least one `passing` and one `failing` trial for the task.

### Import analysis

Static import discovery supports ordinary absolute and relative Python imports
inside the harness. Dynamic imports remain valid only when they are observed
during registered binding import or represented by an explicit registered
component. The validator will not execute tool functions to discover imports.

### Compaction recovery

The preferred retained-iteration count remains a maximum. The middleware
reduces the retained window deterministically to one iteration. It may replace
older large payloads with hash-and-size markers, but it preserves the latest
iteration and the tool-call/result structure. If even that representation
exceeds the configured target, its model-call wrapper raises an explicit
integrity error before provider access.

## Historical correction

The pinned public repository contains a bundled NexAU Debugger under:

```text
agents/evolve_agent/skills/agent-debugger-cli/_source/agent_debugger_core/
```

The public controller invokes `adb ask`; the bundled implementation normalizes
trace paths and runs a tool-using NexAU agent with file, search, shell, Web, and
completion tools. Its default maximum is 25 iterations. The runner substitutes
a narrower read-only training-evidence agent, uses structured diagnoses and
grounding checks, and currently defaults to 80 iterations. Documentation will
describe this as a protected adaptation, not as adding navigation absent from
the public implementation.

## Acceptance sequence

1. Add failing regression tests for each reproduced path after this plan is
   committed.
2. Repair F01-F03 and commit the runtime-boundary changes.
3. Repair F04-F05 and commit the evidence/Debugger contract changes.
4. Repair F06-F07 and commit compatibility/compaction changes.
5. Correct the compliance, fidelity, ABI, adapter, architecture, and validation
   documentation.
6. Run focused reproductions, the complete suite, lint, formatting, packaging,
   clean-wheel installation, and generated-project lifecycle checks.

No live provider campaign is part of this repair. A live pilot still requires
operator-approved endpoint, exact models, task scope, concurrency, and spend.
