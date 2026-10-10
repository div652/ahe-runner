# Campaign Controller and Executable Example

> Historical implementation note: the production NexAU backend described as
> future work below was added in the subsequent real-meta-backend change. The
> scripted backend remains the deterministic server-free integration fixture.

This change turns the committed core substrate into an executable tool.

## Operator interface

The package now provides:

```text
ahe init-project PATH
ahe validate --config PATH/ahe.yaml
ahe run --config PATH/ahe.yaml
ahe resume --config PATH/ahe.yaml
ahe status --config PATH/ahe.yaml
ahe run-round --config PATH/ahe.yaml --round N
ahe close-evolution --config PATH/ahe.yaml
ahe run-validation --config PATH/ahe.yaml
ahe run-final-test --config PATH/ahe.yaml
```

`run_ahe.py` exposes the same interface when the operator prefers an explicit
Python script.

## Full lifecycle

The controller freezes C0, evaluates and materializes training evidence,
creates Explore skills, runs one Debugger and one logical Evolve stage per
round, validates and freezes each candidate, evaluates every checkpoint,
attributes measured transitions, closes evolution, evaluates every checkpoint
on validation, selects through the adapter's native rule, evaluates C0 and the
selected checkpoint on final test, and exports the selected harness.

Every operation is an idempotent hash-verified stage. Re-running `run` or
`resume` verifies and skips completed stages rather than repeating them.

## Executable deterministic project

`ahe init-project` creates a complete local example:

- a standard NexAU harness;
- a harness manifest;
- a campaign config;
- a typed benchmark adapter;
- disjoint train, validation, and test splits;
- deterministic native metrics;
- stable evaluation units and evidence bundles;
- a scripted meta backend that makes committed prompt changes.

The example needs no model server. Its purpose is to prove the complete
controller, artifact layout, checkpoint history, validation selection, final
export, and resume behavior before an operator connects a real benchmark.

## Production backend

The model-backed implementation is documented in
`docs/REAL_META_BACKEND_IMPLEMENTATION.md`.
