# Agentic Harness Engineering Method Reference

This runner treats a harness as an evolvable implementation artifact evaluated
through a protected benchmark adapter.

## Roles

- **Controller:** owns lifecycle, stage identity, immutable artifacts, recovery,
  held-out split secrecy, checkpoint selection, and final export.
- **Benchmark adapter:** owns native benchmark execution, verification,
  metrics, training-evidence conversion, candidate-specific checks, comparison,
  and selection keys.
- **Source Explore Agent:** inspects the actual framework, adapter, seed harness,
  and manifest once, then produces a reusable read-only NexAU skill.
- **Web Explore Agent:** researches transferable public methods once, then
  produces a reusable read-only NexAU skill without benchmark leakage.
- **Debugger:** analyzes individual training evaluation units and emits
  structured diagnoses.
- **Evolve Agent:** synthesizes aggregate evidence, all debugger outputs,
  historical transitions, and both Explore skills to modify the harness.

The protected core prompts define each role's operating procedure. A project
may append a hashed domain supplement for Source, Web, Debugger, or Evolve,
but a supplement cannot replace or weaken the core procedure.

## Evolution loop

1. Validate and freeze the seed harness as checkpoint C0.
2. Evaluate C0 on the configured training split.
3. Materialize a standard evidence bundle from native benchmark artifacts.
4. Run both Explore Agents and register their outputs as NexAU skills.
5. For each round:
   - run or resume per-task Debugger units;
   - create a Git workspace from the previous checkpoint;
   - run or resume one logical Evolve stage;
   - validate protected boundaries and executable loading;
   - freeze committed Git state as the next immutable checkpoint;
   - evaluate the new checkpoint on training;
   - materialize training evidence;
   - attribute the transition using the adapter.
6. Close evolution after the configured number of rounds.
7. Evaluate every checkpoint on validation.
8. Select one checkpoint using the adapter's deterministic native selection
   key.
9. Evaluate C0 and the selected checkpoint on final test.
10. Export the selected harness and full lineage.

Each logical edit is a testable hypothesis. Its change record names the
affected component paths, supporting evidence, proposed mechanism,
`predicted_fixes`, and `risk_tasks`. The next combined-candidate evaluation
compares those predictions with observed task changes. This is heuristic
prediction matching, not isolated causal proof for each edit.

## Evidence boundary

Only training evaluations may be converted into evidence visible to Debugger or
Evolve. Validation and final-test artifacts are controller-owned and remain
outside all meta-agent roots. Split task IDs and hidden state are adapter-owned.

Source Explore and Evolve receive only operator-declared public adapter roots.
The runner never infers the adapter implementation file as an authorized read
root. Adapter code and data dependencies are hashed for provenance without
becoming readable to meta-agents.

Evidence should preserve:

- native aggregate metrics;
- valid zero outcomes;
- infrastructure failures separately from candidate failures;
- per-task trajectories or sanitized equivalents;
- enough provenance to trace every diagnosis back to native artifacts.

## Evolvable components

The manifest may authorize:

1. system prompts;
2. tool descriptions;
3. tool implementations;
4. middleware;
5. skills;
6. sub-agents;
7. memory.

Runtime-owned files and protected configuration fields remain invariant. Task
IDs, exact task text, expected answers, and task-specific lookup tables are
forbidden memorization material.

## Recovery

Stage completion is based on a persisted stage record whose output hashes
verify, not on directory existence.

For evaluation:

- semantic run identity excludes fresh preflight timestamps and nonces;
- unit IDs derive from the persisted run identity;
- attempts are absolute and contiguous;
- a valid completed zero reward is not retried;
- finalization requires every expected unit to verify.

For Evolve:

- every infrastructure attempt has a separate Git workspace;
- retry N starts from the committed `HEAD` of attempt N-1;
- dirty state from interrupted attempts is archived as recovery evidence;
- only committed Git state can become a checkpoint;
- prior attempt logs and workspaces remain immutable evidence;
- the logical stage completes once, despite multiple infrastructure attempts.

Evolve may inspect immutable checkpoint history and selectively restore
authorized component paths from an earlier checkpoint. It chooses `KEEP`,
`IMPROVE`, or `ROLLBACK + PIVOT`; the controller does not automatically roll
back a candidate solely because one aggregate score decreased.

## Exact-model contract

Every model-backed stage performs a fresh observable-contract preflight.
Requests use one exact unprefixed model ID, chat completions, non-streaming
responses, configured reasoning effort, and no SDK retries. Every completion
must return the exact requested observable model label. A missing or different
label aborts the stage.

A positive model label does not prove unobservable upstream identity when a
proxy synthesizes that label. Reports must state this limitation.

## Explore skill delivery

Explore outputs are genuine `SKILL.md` directories registered in Evolve's
NexAU `skills` list. NexAU initially exposes only short descriptions. Evolve
calls `LoadSkill` to reveal the full content when needed. The skills are copied
read-only into the meta-agent runtime and are never writable by Evolve.

Web Explore also receives the operator-declared research seed URLs. Source and
Web outputs are immutable research seeds for the campaign; changing a seed URL,
safe source root, role supplement, resolved benchmark context, core prompt,
tool implementation, or this reference requires a new campaign identity.

## Validation and selection

Every candidate is checked for:

- required files and manifest rules;
- path escape and symlink violations;
- file and tree size limits;
- Python syntax;
- protected configuration drift;
- runtime-owned-file drift;
- training-task memorization;
- NexAU `AgentConfig` loading;
- adapter-specific candidate checks.

Validation selection is deterministic and adapter-native. Final test runs only
after selection and never influences which checkpoint is chosen.

## Failure semantics

Infrastructure failures include transport timeouts, connection failures,
server-side failures, and explicitly classified benchmark infrastructure
errors. They are resumable.

Candidate failures include invalid harnesses, failed rewards, malformed
deliverables, policy violations, and deterministic adapter rejection. They do
not become infrastructure retries.

No layer may turn an error into a success-shaped result or silently substitute a
different model, harness, checkpoint, task, or metric.
