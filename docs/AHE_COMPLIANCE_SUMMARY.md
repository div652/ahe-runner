# AHE Compliance Repair Summary

**Outcome:** All accepted findings in
[`AHE_COMPLIANCE_AUDIT.md`](AHE_COMPLIANCE_AUDIT.md) have been implemented and
validated. The detailed audit remains the historical report for commit
`a7704c4`; this document describes the repaired runner tested at
`646429ba9a8aed2e327db86cc215423fb599ef11`.

Two historical statements were intentionally narrowed before implementation:

1. the pinned public Debugger received dense evidence, but was not literally an
   interactive file-tool agent;
2. mutable Explore knowledge is supported by paper language, but is not clearly
   required by the pinned public implementation.

The runner therefore adds a navigable Debugger as product hardening and keeps
Explore outputs immutable. Automatic score-triggered rollback was also not
added; rollback remains an explicit Evolve decision.

## Repaired findings

| Finding group | Result |
|---|---|
| A01 prediction/attribution | Strict per-change manifests and heuristic prediction/risk observations from the combined candidate evaluation |
| A02 Debugger | Bounded NexAU investigator with large-file ranges, actual-read evidence, and paired trial analysis |
| A03 rollback/pivot | Immutable checkpoint log/diff plus selective path restore and explicit `KEEP` / `IMPROVE` / `ROLLBACK + PIVOT` |
| R01-R03 runtime/model policy | Immediate protected-action yield, repairable completion errors, final-HEAD validation, and exact request-boundary settings/retry ownership |
| C01-C02 benchmark/evidence boundary | Declared public adapter roots only; exact evaluation request, coverage, artifacts, metrics, and schema-V2 evidence lineage |
| P01-P02 customization/compatibility | Hashed append-only role supplements, Web seed URLs, direct Evolve context, manifest-selected root config, support modules, and benchmark-aware task-ID checks |
| O01-O02 reproducibility/export | Runner/prompt/tool/reference/dependency provenance in campaign identity and atomic hash-verified selected export |
| D01 onboarding | Separate offline fixture and genuine runtime-backed direct/protected-action example with complete production configuration |

## Preserved method choices

- one shared harness per checkpoint;
- training evidence only for evolution;
- held-out validation selection and final test remain controller-owned;
- one global Evolve update per round;
- immutable Git checkpoints and strict change hypotheses;
- benchmark-native metrics and trace semantics;
- agent-mediated rollback rather than automatic aggregate-score rollback;
- immutable Explore research seeds;
- infrastructure failures kept separate from candidate reward.

## Honest scope

The current ABI supports text-interactive NexAU sessions and runtime-injected
structured actions. Typed multimodal content, dynamic tool-set changes,
interruptions, and event streams remain future contracts.

Meta-agent file tools enforce declared roots, but candidate tools, middleware,
sub-agents, adapter code, and import-time Python execute in the host process.
The supported deployment model is trusted local operators/authors unless an
external process, container, or VM boundary is added.

The runner promises a verified optimization process and the best selected
checkpoint under the declared rule, which may correctly be C0. It does not
promise inevitable score improvement.

## Validation

- `43 passed`;
- repository-wide Ruff lint and format checks passed;
- wheel and source distribution built;
- clean-wheel offline and runtime configuration conformance passed;
- generated three-round campaign produced C0-C3 and 34 completed stages;
- resume was idempotent and added no attempts.

See [`VALIDATION_RESULTS.md`](VALIDATION_RESULTS.md) for exact versions,
commands, artifact hashes, and live-endpoint limitations.
