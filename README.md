# AHE Runner

`ahe-runner` is a reusable, checkpointed implementation of Agentic Harness
Engineering for NexAU harnesses.

The intended operator interface is:

```bash
ahe init-project /path/to/my-ahe-project
ahe conformance --config /path/to/my-ahe-project/ahe.yaml
ahe validate --config /path/to/my-ahe-project/ahe.yaml
ahe run --config /path/to/my-ahe-project/ahe.yaml
ahe status --config /path/to/my-ahe-project/ahe.yaml
ahe resume --config /path/to/my-ahe-project/ahe.yaml
```

The complete architecture and input contract are specified in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

Operational documentation:

- [`docs/V2_DESIGN.md`](docs/V2_DESIGN.md) - approved V2 evaluated-agent,
  adapter, harness ABI, and protected benchmark-tool overlay contract;
- [`docs/HARNESS_ABI.md`](docs/HARNESS_ABI.md) - exact file schemas,
  signatures, attachment rules, and conformance checks inside `harness/`;
- [`docs/FUTURE_FEATURES.md`](docs/FUTURE_FEATURES.md) - intentionally deferred
  partial-episode and Explore-seed features;
- [`docs/OPERATOR_GUIDE.md`](docs/OPERATOR_GUIDE.md) - prepare a harness,
  configure a campaign, run, resume, and inspect outputs;
- [`docs/ADAPTER_GUIDE.md`](docs/ADAPTER_GUIDE.md) - implement a new typed
  benchmark adapter;
- [`docs/RUNTIME_BACKED_EXAMPLE.md`](docs/RUNTIME_BACKED_EXAMPLE.md) - run the
  bundled evaluated-agent and protected-action example;
- [`docs/TAU2_MIGRATION.md`](docs/TAU2_MIGRATION.md) - map the existing
  Tau2/NexAU experiment into the reusable interface;
- [`docs/PROMPT_FIDELITY.md`](docs/PROMPT_FIDELITY.md) - preserved AHE prompt
  and Explore-skill mechanics;
- [`docs/VALIDATION_RESULTS.md`](docs/VALIDATION_RESULTS.md) - exact validation
  environment and results for this release.

## Quick start

Install the runner with the web-research and development extras:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e '.[web,dev]'
```

Create an operator project:

```bash
ahe init-project ./my-ahe-project
ahe conformance --config ./my-ahe-project/ahe.yaml
```

The generated project uses Harness ABI V2 and `BenchmarkAdapterV2`, with an
actively registered internal tool, skill, reviewer sub-agent, and explicit
long-term-memory attachment. `ahe.yaml` is an **offline lifecycle rehearsal**:
its deterministic phrase-scoring adapter and `scripted` meta backend prove the
controller, checkpoints, evidence, selection, export, and resume flow without a
model server:

```bash
ahe run --config ./my-ahe-project/ahe.yaml
```

It does not execute the evaluated agent. Merely changing
`meta.backend: nexau` does not turn it into a live benchmark.

For a genuine evaluated-agent integration, edit the endpoint and exact model
IDs in the generated `ahe.runtime.yaml`, install the `web` extra, and run:

```bash
ahe conformance --config ./my-ahe-project/ahe.runtime.yaml
ahe validate --config ./my-ahe-project/ahe.runtime.yaml
ahe run --config ./my-ahe-project/ahe.runtime.yaml
```

That example opens real runner-owned NexAU sessions for a direct task and an
adapter-owned `lookup_code` structured action. For research work, replace it
with your benchmark adapter and declare all public source roots, protected
dependencies, domain supplements, research seeds, native metrics, and held-out
policy. See the runtime example and operator guides.

Configure the API-key environment variable named by `transport.api_key_env`.
A loopback GH Copilot Server may use a non-secret placeholder; never put a
provider credential in a YAML file.

The selected harness is exported under:

```text
campaign-artifacts/<campaign>/final/selected-harness/
```

Every intermediate checkpoint, native evaluation, evidence bundle, Debugger
result, Evolve attempt, preflight, model call, validation decision, and
selection artifact remains in the same campaign directory.
