# AHE Compliance Repair Summary

**Outcome:** The accepted findings in
[`AHE_COMPLIANCE_AUDIT.md`](AHE_COMPLIANCE_AUDIT.md) and follow-up findings
F01-F07 in [`AHE_COMPLIANCE_REAUDIT.md`](AHE_COMPLIANCE_REAUDIT.md) are
implemented. The two audit reports remain historical assessments of `a7704c4`
and `5380f65`; the follow-up implementation contract is
[`AHE_COMPLIANCE_REAUDIT_REPAIR_PLAN.md`](AHE_COMPLIANCE_REAUDIT_REPAIR_PLAN.md).

The pinned public controller invokes `adb ask`; its bundled source runs a
tool-using NexAU Debugger over normalized trace files with a default
25-iteration budget. The runner substitutes a narrower read-only Debugger with
structured, read-backed citations and defaults to 80 iterations. Mutable
Explore knowledge remains an intentional non-requirement, and rollback remains
an explicit Evolve decision rather than an automatic score trigger.

## Repaired findings

| Finding group | Result |
|---|---|
| A01 prediction/attribution | Strict per-change manifests and heuristic prediction/risk observations from the combined candidate evaluation |
| A02 / F05 Debugger | Bounded NexAU investigator with large-file ranges, structured citations bound to nonempty reads/search hits, verified pass/fail availability, and paired trial analysis |
| A03 rollback/pivot | Immutable checkpoint log/diff plus selective path restore and explicit `KEEP` / `IMPROVE` / `ROLLBACK + PIVOT` |
| R01-R03 / F01-F03 runtime/model policy | Immediate protected-action yield across modern middleware and legacy hooks, repairable completion errors, final-HEAD validation, effective serialized-request enforcement, retry ownership, and in-memory-only credentials |
| C01-C02 / F04 benchmark/evidence boundary | Declared public adapter roots only; exact evaluation request, coverage, artifacts, metrics, canonical per-trial facts, and schema-V3 evidence semantics/lineage |
| P01-P02 / F06-F07 customization/compatibility | Hashed append-only role supplements, Web seed URLs, direct Evolve context, manifest-selected root config, eager/function-local support imports, benchmark-aware task-ID checks, and bounded read/write-heavy compaction |
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

Release `0.3.0` was validated at source commit
`15a594064bfc58db69cffcbe3f64690f7a882bd6`: 57 tests passed, Ruff lint and
format checks passed across 98 files, source compilation passed, and both
distribution formats built successfully. A fresh wheel installation with Web
extras generated conforming offline and runtime projects, completed all 34
offline campaign stages through C3, selected C3, and resumed without changing
any attempt path or the campaign state.

See [`VALIDATION_RESULTS.md`](VALIDATION_RESULTS.md) for exact dependency
versions, artifact hashes, installed-resource checks, clean-wheel results, and
the explicit no-live-endpoint limitation.
