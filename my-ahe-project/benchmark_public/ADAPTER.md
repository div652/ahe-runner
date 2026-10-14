# Public Adapter Contract

This generated adapter is an offline lifecycle fixture. It evaluates general
reliability mechanisms represented in the harness files; it does not call the
evaluated model or expose held-out task definitions to meta-agents.

Public training semantics:

- each task checks for a reusable reliability mechanism;
- every task/trial produces a result artifact and a trace artifact;
- the primary metric is mean binary success;
- task details may be shown to Debugger only for the training split.

The adapter implementation and held-out fixtures are protected benchmark
inputs. Source Explore and Evolve can read this public contract, the seed
harness, the Harness ABI manifest, NexAU source, and the packaged AHE method
reference. They cannot read `benchmark_adapter.py` unless an operator
explicitly adds it to `project.meta_agent_source_roots`.
