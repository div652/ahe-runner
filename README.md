# AHE Runner

`ahe-runner` is a reusable implementation of Agentic Harness Engineering
(AHE) for NexAU agents. You provide:

1. a file-backed NexAU harness containing the behavior that may evolve;
2. a typed benchmark adapter that supplies tasks, tools, rewards, traces, and
   held-out evaluation;
3. a campaign configuration that selects models, splits, seeds, and evolution
   rounds.

The runner evaluates the base harness, produces immutable checkpoints, uses
Explore/Debugger/Evolve agents to propose improvements, validates candidate
checkpoints, and exports the selected harness while preserving the complete
campaign history.

## Quick start

Requirements: Python 3.12+ and access to the model endpoint configured for your
campaign.

```bash
git clone https://github.com/div652/ahe-runner.git
cd ahe-runner
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e '.[web,dev]'

ahe init-project ./my-ahe-project
ahe conformance --config ./my-ahe-project/ahe.yaml
ahe validate --config ./my-ahe-project/ahe.yaml
ahe run --config ./my-ahe-project/ahe.yaml
```

The generated `ahe.yaml` is an offline lifecycle rehearsal: it verifies
checkpointing, evidence, evolution, selection, export, and recovery without
calling an evaluated agent. For a real NexAU evaluation, configure the endpoint
and exact model IDs in `ahe.runtime.yaml`, then run:

```bash
ahe conformance --config ./my-ahe-project/ahe.runtime.yaml
ahe validate --config ./my-ahe-project/ahe.runtime.yaml
ahe run --config ./my-ahe-project/ahe.runtime.yaml
```

Inspect or resume a campaign with:

```bash
ahe status --config ./my-ahe-project/ahe.runtime.yaml
ahe resume --config ./my-ahe-project/ahe.runtime.yaml
```

The selected harness is exported to
`campaign-artifacts/<campaign>/final/selected-harness/`. Intermediate
checkpoints, evaluations, traces, evidence, model calls, and selection records
remain under the same campaign directory.

## Implement your own benchmark

1. Implement the evolvable files under `harness/` according to the Harness
   ABI.
2. Implement `BenchmarkAdapterV2` for the benchmark's tasks, native execution,
   metrics, traces, and split policy.
3. Configure models, transports, rounds, seeds, and adapter paths in
   `ahe.yaml`.
4. Run `ahe conformance`, `ahe validate`, and `ahe run`.

Harness tools and the task's selected benchmark tools are both exposed through
the structured model API. Harness tools execute inside NexAU; protected
benchmark tools are intercepted and executed by the adapter.

## Documentation

Read these in order:

| Document | Purpose |
|---|---|
| [`OPERATOR_GUIDE.md`](docs/OPERATOR_GUIDE.md) | Complete setup, run, resume, and inspection procedure |
| [`HARNESS_ABI.md`](docs/HARNESS_ABI.md) | Required schemas and signatures for every harness component |
| [`ADAPTER_GUIDE.md`](docs/ADAPTER_GUIDE.md) | `BenchmarkAdapterV2`, protected tools, evidence, metrics, and splits |
| [`RUNTIME_BACKED_EXAMPLE.md`](docs/RUNTIME_BACKED_EXAMPLE.md) | Concrete evaluated-agent and protected-action example |
| [`ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Lifecycle, ownership, recovery, and protection guarantees |

Additional references:

- [`V2_DESIGN.md`](docs/V2_DESIGN.md) — design rationale and ownership
  boundaries.
- [`PROMPT_FIDELITY.md`](docs/PROMPT_FIDELITY.md) — AHE prompt and
  Explore-skill fidelity.
- [`VALIDATION_RESULTS.md`](docs/VALIDATION_RESULTS.md) — release validation
  evidence.
- [`FUTURE_FEATURES.md`](docs/FUTURE_FEATURES.md) — intentionally unsupported
  cases.
- [`TAU2_MIGRATION.md`](docs/TAU2_MIGRATION.md) — Tau2-specific migration
  guidance.
