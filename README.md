# AHE Runner

`ahe-runner` applies Agentic Harness Engineering (AHE) to file-backed NexAU
agents. It keeps the evaluated model and benchmark protocol fixed while
Explore, Debugger, and Evolve agents use completed training evidence to improve
the agent harness.

You provide three things:

1. a **base harness** containing the prompts, tools, middleware, skills,
   sub-agents, and memory that may evolve;
2. a **benchmark adapter** providing tasks, protected benchmark actions,
   rewards, traces, and train/validation/test splits;
3. a **campaign configuration** selecting models, transport, seeds, trials,
   and evolution rounds.

The runner preserves every harness checkpoint and evaluation, selects a
checkpoint on validation data, evaluates the selected checkpoint once on test
data, and exports the final harness with its complete history.

## The three-stage path

Do not begin with your research benchmark. Verify one boundary at a time:

| Stage | Configuration | What it proves | Model calls |
|---|---|---|---:|
| 1. Offline rehearsal | `ahe.yaml` | Installation, controller lifecycle, checkpoints, evidence, selection, export, and resume | No |
| 2. Live runtime rehearsal | `ahe.runtime.yaml` | Model connectivity, real NexAU sessions, structured tools, and model-backed AHE agents | Yes |
| 3. Research campaign | Your edited configuration | Your harness, benchmark, metrics, traces, and evolution setup work together | Yes |

The generated project is already a complete sample. Run Stages 1 and 2 before
replacing it with your own benchmark.

## 0. Install the runner

Stage 1 requires Python 3.12 or newer. Stages 2 and 3 additionally require an
OpenAI-compatible model endpoint.

Clone the engine, create an isolated Python environment, activate it, and
install the runner with its Web Explore and development dependencies:

```bash
git clone https://github.com/div652/ahe-runner.git
cd ahe-runner
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e '.[web,dev]'
```

The installation creates `.venv/bin/ahe`. That command invokes the CLI defined
by `ahe_runner.cli:main`.

### If `venv` reports missing `ensurepip`

On Debian or Ubuntu, install the optional Python `venv` package and retry:

```bash
sudo apt-get update
sudo apt-get install -y python3.12-venv
python3.12 -m venv .venv
```

If you do not have `sudo` but already have `uv`, use:

```bash
uv venv --python 3.12 .venv
. .venv/bin/activate
uv pip install --python .venv/bin/python -e '.[web,dev]'
```

Detailed reference: [Operator Guide - Install the runner](docs/OPERATOR_GUIDE.md#1-install-the-runner).

## 1. Create the sample operator project

An operator project is one evolution experiment. It is separate from the
reusable runner engine and contains the candidate harness, benchmark adapter,
configuration, and eventual campaign artifacts.

Choose an empty destination and generate the sample:

```bash
ahe init-project ./my-ahe-project
```

This creates:

```text
my-ahe-project/
├── ahe.yaml
├── ahe.runtime.yaml
├── benchmark_adapter.py
├── runtime_benchmark_adapter.py
├── harness_manifest.yaml
├── harness/
├── benchmark_public/
├── meta_supplements/
└── runtime_meta_supplements/
```

| Generated path | Purpose |
|---|---|
| `harness/` | Complete sample NexAU harness and the files AHE may improve |
| `harness_manifest.yaml` | Declares the allowed component files and protected fields |
| `benchmark_adapter.py` | Deterministic offline toy adapter used by `ahe.yaml` |
| `runtime_benchmark_adapter.py` | Imports the live toy adapter used by `ahe.runtime.yaml` |
| `ahe.yaml` | Stage 1 offline-rehearsal configuration |
| `ahe.runtime.yaml` | Stage 2 model-backed configuration |
| `benchmark_public/` | Benchmark information that meta-agents may safely read |
| `*_meta_supplements/` | Benchmark-specific additions to protected AHE role prompts |

You do **not** implement your own benchmark yet. The generated harness and both
toy adapters are intended to pass their checks unchanged.

Detailed reference: [Operator Guide - Create the operator project](docs/OPERATOR_GUIDE.md#2-create-the-operator-project).

## 2. Stage 1: run the offline rehearsal

First inspect the generated sample locally. Neither command contacts the model
endpoint:

- `conformance` returns a compact report about the adapter API, protected
  benchmark interface, Harness ABI registrations, and whether NexAU can load
  `agent.yaml`;
- `validate` runs the same local checks but returns the complete resolved
  configuration, adapter identity, split summary, file hashes, and harness
  report.

Run both before starting a campaign:

```bash
ahe conformance --config ./my-ahe-project/ahe.yaml
ahe validate --config ./my-ahe-project/ahe.yaml
```

If both succeed, run the deterministic offline campaign:

```bash
ahe run --config ./my-ahe-project/ahe.yaml
```

This configuration uses `meta.backend: scripted` and a phrase-scoring toy
adapter. It does not run the evaluated NexAU agent and does not contact a model
server. Its purpose is to exercise checkpoint creation, evidence handling,
scripted edits, selection, final export, and idempotent resume.

Detailed references:

- [Harness ABI - exact conformance rules](docs/HARNESS_ABI.md)
- [Operator Guide - validation and Route A](docs/OPERATOR_GUIDE.md#7-validate-before-spending-model-calls)

## 3. Stage 2: configure and run the live toy campaign

`ahe.runtime.yaml` tests the real runtime path before you introduce your own
benchmark. It runs a real NexAU evaluated agent and model-backed Source
Explore, Web Explore, Debugger, and Evolve agents against two tiny task types:

1. return one exact uppercase answer;
2. call the adapter-owned `lookup_code` tool and ground the final answer in its
   result.

The second task proves that benchmark tools can be exposed through the
structured model API while their execution remains protected from the
evolvable harness. The generated campaign performs one evolution round.

### Configure the model connection first

Open `my-ahe-project/ahe.runtime.yaml` and replace these placeholders:

```yaml
models:
  evaluated:
    model: exact-model-id-for-the-agent-being-improved
  meta:
    model: exact-model-id-for-explore-debugger-and-evolve

transport:
  base_url: http://127.0.0.1:3141/v1
  api_key_env: null
```

- `models.evaluated.model` is the fixed model running each candidate harness.
- `models.meta.model` runs the AHE meta-agents.
- Model IDs must exactly match the endpoint's advertised, unprefixed IDs.
- `transport.base_url` must point to the running OpenAI-compatible endpoint.
- If authentication is required, set only the environment-variable name in
  YAML and export the secret in your shell:

```yaml
transport:
  api_key_env: MY_MODEL_API_KEY
```

```bash
export MY_MODEL_API_KEY='your-secret-value'
```

Do not place credentials directly in YAML. Confirm the endpoint is running and
that both exact model IDs are available before continuing.

Detailed references:

- [Operator Guide - Configure the campaign](docs/OPERATOR_GUIDE.md#6-configure-the-campaign)
- [Operator Guide - GH Copilot Server](docs/OPERATOR_GUIDE.md#gh-copilot-server)
- [Runtime-backed example](docs/RUNTIME_BACKED_EXAMPLE.md)

Now run the local checks against the runtime configuration, followed by the
live campaign:

```bash
ahe conformance --config ./my-ahe-project/ahe.runtime.yaml
ahe validate --config ./my-ahe-project/ahe.runtime.yaml
ahe run --config ./my-ahe-project/ahe.runtime.yaml
```

The first two commands still perform local checks only. The final `run` command
performs live endpoint/model preflights, opens real NexAU sessions, executes
the toy benchmark, and runs the AHE meta-agents.

## 4. Stage 3: replace the sample with your benchmark

Only begin this stage after the offline and runtime samples succeed.

1. **Implement the base harness.** Replace the generated files under
   `harness/`, preserving a valid NexAU `agent.yaml` and explicit file-backed
   components. Follow [Harness ABI V2](docs/HARNESS_ABI.md).
2. **Declare the evolvable surface.** Update `harness_manifest.yaml` so every
   evolvable component is declared and runtime-attached. See
   [Operator Guide - Declare what may evolve](docs/OPERATOR_GUIDE.md#4-declare-what-may-evolve).
3. **Implement `BenchmarkAdapterV2`.** Supply immutable splits, native
   evaluation, metrics, traces, evidence, candidate checks, protected actions,
   and selection semantics. Follow the
   [Benchmark Adapter Guide](docs/ADAPTER_GUIDE.md).
4. **Protect benchmark internals.** Keep hidden tasks, verifier logic, expected
   answers, simulator state, credentials, and official action execution outside
   `harness/`. See the
   [adapter ownership boundary](docs/ADAPTER_GUIDE.md#ownership-boundary).
5. **Configure the campaign.** Copy `ahe.runtime.yaml` to a distinct file such
   as `ahe.research.yaml`, then set the adapter path, public source roots, role
   supplements, research seed URLs, exact models, transport, trials, seeds,
   concurrency, and `campaign.rounds`. Follow
   [Operator Guide - Configure the campaign](docs/OPERATOR_GUIDE.md#6-configure-the-campaign).
6. **Use a fresh campaign identity.** Choose a new campaign name and artifacts
   directory whenever a protected input changes.

When the adapter declares protected structured tools, harness and benchmark
tools are both visible to the model through the structured API, but they have
different execution owners:

```text
model-visible tools
├── harness tools    -> executed by NexAU from harness implementations
└── benchmark tools  -> intercepted and executed by the protected adapter
```

After implementing the integration, check it before spending model calls:

```bash
ahe conformance --config /path/to/your-project/ahe.research.yaml
ahe validate --config /path/to/your-project/ahe.research.yaml
```

Then start the real evolution campaign:

```bash
ahe run --config /path/to/your-project/ahe.research.yaml
```

The complete procedure is in the [Operator Guide](docs/OPERATOR_GUIDE.md).

## 5. Inspect, resume, and find the result

Inspect durable campaign state without continuing execution:

```bash
ahe status --config /path/to/your-project/ahe.research.yaml
```

Continue an interrupted campaign from its verified stage/checkpoint state:

```bash
ahe resume --config /path/to/your-project/ahe.research.yaml
```

The campaign directory preserves intermediate checkpoints, evaluations,
traces, evidence, Debugger reports, Evolve attempts, model calls, attribution,
selection, and recovery records. The selected harness is exported to:

```text
campaign-artifacts/<campaign>/final/selected-harness/
```

Detailed references:

- [Operator Guide - Run or resume](docs/OPERATOR_GUIDE.md#8-run-or-resume)
- [Operator Guide - Read the outputs](docs/OPERATOR_GUIDE.md#10-read-the-outputs)
- [Architecture - Outputs](docs/ARCHITECTURE.md#outputs)

## Documentation map

| Document | Read it when you need to... |
|---|---|
| [`OPERATOR_GUIDE.md`](docs/OPERATOR_GUIDE.md) | Follow the complete install-to-results operating procedure |
| [`HARNESS_ABI.md`](docs/HARNESS_ABI.md) | Implement exact schemas and signatures for harness components |
| [`ADAPTER_GUIDE.md`](docs/ADAPTER_GUIDE.md) | Integrate benchmark execution, tools, metrics, traces, and evidence |
| [`RUNTIME_BACKED_EXAMPLE.md`](docs/RUNTIME_BACKED_EXAMPLE.md) | Understand and run the live toy integration |
| [`ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Understand lifecycle, ownership, recovery, and protection guarantees |
| [`V2_DESIGN.md`](docs/V2_DESIGN.md) | Understand why runner, harness, and adapter boundaries are separated |
| [`PROMPT_FIDELITY.md`](docs/PROMPT_FIDELITY.md) | Review preserved AHE meta-agent behavior and Explore skills |
| [`VALIDATION_RESULTS.md`](docs/VALIDATION_RESULTS.md) | Review release validation evidence |
| [`FUTURE_FEATURES.md`](docs/FUTURE_FEATURES.md) | Check deliberately unsupported scenarios |
| [`TAU2_MIGRATION.md`](docs/TAU2_MIGRATION.md) | Map an existing Tau2/NexAU setup into this runner |
