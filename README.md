# AHE Runner

`ahe-runner` applies Agentic Harness Engineering (AHE) to file-backed NexAU
agents. You provide:

1. a **base harness** containing the behavior that may evolve;
2. a **benchmark adapter** providing tasks, tools, rewards, traces, and splits;
3. a **campaign configuration** selecting models, transport, and rounds.

The runner evaluates the base harness, preserves every checkpoint, uses
Explore/Debugger/Evolve agents to improve it from training evidence, selects on
validation data, and exports the selected harness after final testing.

## Start with three stages

Verify one boundary at a time:

| Stage | Configuration | What it proves | Model calls |
|---|---|---|---:|
| 1. Offline rehearsal | `ahe.yaml` | Installation, checkpoints, evidence, selection, export, and resume | No |
| 2. Live rehearsal | `ahe.runtime.yaml` | Model connectivity, real NexAU sessions, structured tools, and AHE agents | Yes |
| 3. Research campaign | Your configuration | Your harness and benchmark work end to end | Yes |

The generated project is a complete sample. Run Stages 1 and 2 before
replacing it with your benchmark.

## 0. Install the runner

Stage 1 requires Python 3.12+. Stages 2 and 3 also require an OpenAI-compatible
model endpoint.

```bash
git clone https://github.com/div652/ahe-runner.git
cd ahe-runner
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e '.[web,dev]'
```

This installs the CLI as `.venv/bin/ahe`.

If `venv` reports missing `ensurepip` on Debian or Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y python3.12-venv
python3.12 -m venv .venv
```

Without `sudo`, use an existing `uv` installation:

```bash
uv venv --python 3.12 .venv
. .venv/bin/activate
uv pip install --python .venv/bin/python -e '.[web,dev]'
```

See [Operator Guide - Installation](docs/OPERATOR_GUIDE.md#1-install-the-runner).

## 1. Create the sample project

An operator project is one evolution experiment. Generate it in an empty
directory:

```bash
ahe init-project ./my-ahe-project
```

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

| Path | Purpose |
|---|---|
| `harness/` | Sample NexAU harness and the files AHE may improve |
| `harness_manifest.yaml` | Declares evolvable files and protected fields |
| `benchmark_adapter.py` | Offline toy adapter used by `ahe.yaml` |
| `runtime_benchmark_adapter.py` | Live toy adapter used by `ahe.runtime.yaml` |
| `benchmark_public/` | Benchmark information meta-agents may read |
| `*_meta_supplements/` | Benchmark-specific guidance for AHE roles |

The generated harness and adapters are already conformant; do not implement
your benchmark yet. See
[Operator Guide - Create the project](docs/OPERATOR_GUIDE.md#2-create-the-operator-project).

## 2. Stage 1: offline rehearsal

Both checks are local and make no model calls:

- `conformance` gives a compact adapter, Harness ABI, and NexAU-load report;
- `validate` runs the same checks but returns the full configuration, splits,
  hashes, and harness report.

```bash
ahe conformance --config ./my-ahe-project/ahe.yaml
ahe validate --config ./my-ahe-project/ahe.yaml
```

If they pass, run the deterministic sample:

```bash
ahe run --config ./my-ahe-project/ahe.yaml
```

This uses a scripted meta backend and phrase-scoring adapter. It does not run
an evaluated agent; it exercises the controller, checkpoints, evidence,
selection, export, and resume.

See [Harness ABI V2](docs/HARNESS_ABI.md) and
[Operator Guide - Validation and Route A](docs/OPERATOR_GUIDE.md#7-validate-before-spending-model-calls).

## 3. Stage 2: live toy campaign

`ahe.runtime.yaml` runs real NexAU sessions and model-backed AHE agents on two
tiny tasks:

1. return one exact uppercase answer;
2. call the adapter-owned `lookup_code` tool and use its result.

This proves that structured benchmark tools remain adapter-executed and
protected from the evolvable harness. The sample runs one evolution round.

### Configure the model connection

Edit `my-ahe-project/ahe.runtime.yaml`:

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

- Use exact, unprefixed model IDs advertised by the endpoint.
- Point `base_url` to the running OpenAI-compatible server.
- If authentication is required, put only the environment-variable name in
  YAML and export the secret:

```yaml
transport:
  api_key_env: MY_MODEL_API_KEY
```

```bash
export MY_MODEL_API_KEY='your-secret-value'
```

Do not store credentials in YAML. Configuration details are in
[Operator Guide - Configure the campaign](docs/OPERATOR_GUIDE.md#6-configure-the-campaign);
the full sample is in
[Runtime-backed example](docs/RUNTIME_BACKED_EXAMPLE.md).

After configuring the endpoint and models:

```bash
ahe conformance --config ./my-ahe-project/ahe.runtime.yaml
ahe validate --config ./my-ahe-project/ahe.runtime.yaml
ahe run --config ./my-ahe-project/ahe.runtime.yaml
```

The first two commands remain local. `run` performs live model preflights,
opens NexAU sessions, executes the toy benchmark, and runs the AHE meta-agents.

## 4. Stage 3: integrate your benchmark

After both samples succeed:

1. Replace `harness/` with your base NexAU harness, following
   [Harness ABI V2](docs/HARNESS_ABI.md).
2. Update `harness_manifest.yaml` so every evolvable component is declared and
   runtime-attached.
3. Implement `BenchmarkAdapterV2` using the
   [Benchmark Adapter Guide](docs/ADAPTER_GUIDE.md).
4. Keep hidden tasks, verifier logic, expected answers, simulator state,
   credentials, and official action execution outside `harness/`.
5. Copy `ahe.runtime.yaml` to `ahe.research.yaml`, then configure the adapter,
   public source roots, role supplements, research seeds, models, trials,
   concurrency, and `campaign.rounds`.
6. Use a fresh campaign name and artifacts directory when protected inputs
   change.

When protected structured tools exist, both tool classes are model-visible but
have different execution owners:

```text
harness tools    -> executed by NexAU from harness implementations
benchmark tools  -> intercepted and executed by the protected adapter
```

Check the integration locally, then run it:

```bash
ahe conformance --config /path/to/your-project/ahe.research.yaml
ahe validate --config /path/to/your-project/ahe.research.yaml
ahe run --config /path/to/your-project/ahe.research.yaml
```

Follow the [complete Operator Guide](docs/OPERATOR_GUIDE.md) for this stage.

## 5. Inspect and resume

```bash
# Read state without continuing execution
ahe status --config /path/to/your-project/ahe.research.yaml

# Continue an interrupted campaign
ahe resume --config /path/to/your-project/ahe.research.yaml
```

The campaign directory preserves checkpoints, evaluations, traces, evidence,
model calls, selection, and recovery records. The selected harness is at:

```text
campaign-artifacts/<campaign>/final/selected-harness/
```

See [Operator Guide - Run or resume](docs/OPERATOR_GUIDE.md#8-run-or-resume)
and [Read the outputs](docs/OPERATOR_GUIDE.md#10-read-the-outputs).

## Documentation

| Document | Use it for |
|---|---|
| [`OPERATOR_GUIDE.md`](docs/OPERATOR_GUIDE.md) | Complete install-to-results procedure |
| [`HARNESS_ABI.md`](docs/HARNESS_ABI.md) | Exact schemas and signatures for harness components |
| [`ADAPTER_GUIDE.md`](docs/ADAPTER_GUIDE.md) | Benchmark execution, tools, metrics, traces, and evidence |
| [`RUNTIME_BACKED_EXAMPLE.md`](docs/RUNTIME_BACKED_EXAMPLE.md) | Live toy integration walkthrough |
| [`ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Lifecycle, ownership, recovery, and protection guarantees |

Additional references:
[`V2_DESIGN.md`](docs/V2_DESIGN.md),
[`PROMPT_FIDELITY.md`](docs/PROMPT_FIDELITY.md),
[`VALIDATION_RESULTS.md`](docs/VALIDATION_RESULTS.md),
[`FUTURE_FEATURES.md`](docs/FUTURE_FEATURES.md), and
[`TAU2_MIGRATION.md`](docs/TAU2_MIGRATION.md).
