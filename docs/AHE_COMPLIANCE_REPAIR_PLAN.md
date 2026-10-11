# AHE Compliance Repair Plan

This document records the implementation decisions taken from
`AHE_COMPLIANCE_AUDIT.md` before changing the runner.

**Status:** Implemented and validated. All accepted finding groups below are
complete. See `AHE_COMPLIANCE_SUMMARY.md` and `VALIDATION_RESULTS.md`.

The later follow-up audit found supported-path gaps in this implementation.
Those are adjudicated and repaired separately in
`AHE_COMPLIANCE_REAUDIT_REPAIR_PLAN.md`; the historical decisions below remain
the original repair contract.

The repair target is the pinned public AHE implementation at
`8b2a55d97590363fe50c3cc6b5e833b020a4bb4c`, while retaining the approved
general-purpose NexAU product boundaries:

- benchmark-native metrics and task orchestration;
- runner-owned NexAU `Agent` / `Executor`;
- immutable held-out validation and test evidence;
- protected benchmark actions supplied as runtime structured tools;
- explicit Harness ABI component files;
- resumable, immutable checkpoints.

## Finding decisions

| Finding | Decision |
|---|---|
| A01 prediction and attribution | Accept. Use one typed per-change manifest contract and restore heuristic per-change prediction/risk matching from the combined candidate evaluation. Never describe it as isolated causal attribution. |
| A02 Debugger investigation | Accept the evidence-access and paired-analysis defect. The pinned controller invokes `adb ask`; its bundled implementation is a tool-using NexAU Debugger over normalized trace files. The runner substitutes narrower read-only roots, structured diagnoses, and explicit grounding checks while preserving dense task evidence. |
| A03 rollback and pivot | Accept for agent-mediated rollback. Restore `KEEP`, `IMPROVE`, and `ROLLBACK + PIVOT`, historical snapshot/diff access, and constrained selective restore. Do not add score-triggered automatic rollback. |
| R01 protected action continuation | Accept. An official action must yield immediately; candidate middleware cannot request another model turn before the adapter supplies exact results. |
| R02 failed completion | Accept. Failed completion returns NexAU's native error shape and remains repairable. Validation is bound to the final committed content and includes adapter candidate validation. |
| R03 model/resource policy | Accept. Campaign model ID, reasoning setting, temperature, output limit, streaming mode, and retry ownership are enforced at each actual request boundary. Candidate context-management policy remains evolvable only where the manifest permits it. |
| C01 held-out adapter source | Accept. Meta-agents receive explicit safe source roots and public adapter documentation, not an inferred whole implementation file. |
| C02 evaluation/evidence invariants | Accept. The runner verifies request identity, split/checkpoint identity, expected trials/tasks, artifact hashes, metric consistency, and evidence lineage without standardizing native trace events. |
| P01 role customization | Accept. Add hashed append-only domain supplements and explicit safe source/research roots. Full prompt replacement remains an acknowledged methodology fork and is not added. |
| P02 harness compatibility | Accept the reproduced filename, helper-module, and short-ID defects. Multimodal/event-stream support remains outside the current text-interactive ABI and will be documented rather than implied. |
| O01 campaign provenance | Accept. Freeze the protected runner prompts/tools/reference bundle, resolved domain guidance, and declared adapter dependency roots into campaign identity. |
| O02 selected export | Accept. Publish through a verified temporary tree and atomically replace or reject incomplete output. |
| D01 onboarding | Accept. Keep the offline phrase-scoring fixture explicitly labelled as lifecycle-only and add a separate runtime-backed adapter example and complete production-backend instructions. |

## Explicit methodology choices

These choices avoid silently changing the optimization method:

1. **Attribution remains heuristic.** Multiple edits may receive credit for the
   same observed task improvement because the candidate is evaluated as one
   combined harness.
2. **Rollback remains Evolve-selected.** The runner gives Evolve the evidence
   and tools to restore prior code, but does not automatically discard a
   checkpoint based only on score.
3. **Explore outputs remain immutable research seeds.** The pinned public code
   does not establish later mutation as a required mechanism. Making them
   mutable can be studied separately.
4. **Infrastructure failures remain distinct from candidate scores.** The
   runner resumes or blocks infrastructure failures instead of silently
   counting them as benchmark failures.
5. **Core role prompts remain protected.** Projects may append hashed domain
   supplements; replacing the complete procedure is a methodology fork.
6. **No universal benchmark-event schema is introduced.** The common contract
   covers identity, coverage, outcome, termination, metrics, and hashed native
   artifact references.

## Implementation stages

### 1. Restore distinctive AHE mechanisms

- define a strict change-manifest model shared by Evolve instructions,
  completion checks, freeze validation, and attribution;
- produce per-change prediction/risk observations plus unanticipated
  regressions and unattributed improvements;
- make the Debugger a bounded read-only NexAU agent over authorized task
  evidence;
- support ranged reads and transparent large-file search truncation;
- expose protected prior checkpoint snapshots/diffs and a selective restore
  tool;
- restore explicit keep/improve/rollback/pivot instructions.

### 2. Harden execution and benchmark boundaries

- add a trusted protected-action yield guard around candidate middleware;
- return dictionary error results from failed stop-tool completion;
- bind candidate validation to exact Git `HEAD` and rerun adapter validation
  inside Evolve;
- clear provider-default reasoning overrides;
- enforce declared temperature and output limits in the recorded client;
- set one native NexAU request attempt and leave infrastructure retries to the
  existing outer stage policy;
- verify evaluation request identity and evidence provenance.

### 3. Improve compatibility and reproducibility

- honor `manifest.agent_config` at runtime;
- distinguish directly registered component entry points from transitive
  support modules;
- reject task memorization using stable benchmark aliases rather than arbitrary
  short substring matches;
- hash protected runner resources, role supplements, prompt context, method
  reference, and declared adapter-safe roots;
- export the selected harness atomically after exact tree verification.

### 4. Correct the operator surface

- separate lifecycle-only and runtime-backed examples;
- document the full production meta-backend configuration;
- replace broad prompt-fidelity claims with an original-to-runner semantic
  matrix;
- state the supported text-interactive NexAU subset and host-execution trust
  boundary precisely.

## Validation contract

The repair is complete only when deterministic reproductions show:

- protected actions cause exactly one model call before adapter execution,
  even with candidate `force_continue`;
- failed completion receives another agent turn and can repair its output;
- provider-default reasoning clears candidate effort and altered
  temperature/output limits fail before provider contact;
- two edits receive separate heuristic prediction verdicts;
- paired long traces can be inspected through bounded reads and searches;
- Evolve can selectively restore one earlier edit;
- malformed evaluation identity, missing coverage, false lineage, and stale
  metrics are rejected;
- alternate root config names and transitive helper modules work;
- partial final exports cannot be recorded as complete;
- changing any frozen protected input prevents resume;
- the runtime-backed example uses the public adapter/session API without
  patching runner internals.

These acceptance items passed in the original 43-test repair suite. The
follow-up release record, including the additional request, hook, credential,
trial-fact, citation, lazy-import, and write-compaction regressions, is in
`VALIDATION_RESULTS.md`.
