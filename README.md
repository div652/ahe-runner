# AHE Runner

`ahe-runner` is a reusable, checkpointed implementation of Agentic Harness
Engineering for NexAU harnesses.

The intended operator interface is:

```bash
ahe init-project /path/to/my-ahe-project
ahe validate --config /path/to/my-ahe-project/ahe.yaml
ahe run --config /path/to/my-ahe-project/ahe.yaml
ahe status --config /path/to/my-ahe-project/ahe.yaml
ahe resume --config /path/to/my-ahe-project/ahe.yaml
```

The complete architecture and input contract are specified in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Quick start

Install the runner with the web-research and development extras:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[web,dev]'
```

Create an operator project:

```bash
ahe init-project ./my-ahe-project
```

The generated project uses the deterministic `scripted` meta backend so the
complete lifecycle can be exercised without a model server:

```bash
ahe run --config ./my-ahe-project/ahe.yaml
```

For a real campaign, edit the explicit endpoint, exact model IDs, reasoning
efforts, concurrency, and round count in `ahe.yaml`, then set:

```yaml
meta:
  backend: nexau
```

Configure the API-key environment variable named by
`transport.api_key_env`. A loopback GH Copilot Server may use a non-secret
placeholder; never put a provider credential in `ahe.yaml`.

The selected harness is exported under:

```text
campaign-artifacts/<campaign>/final/selected-harness/
```

Every intermediate checkpoint, native evaluation, evidence bundle, Debugger
result, Evolve attempt, preflight, model call, validation decision, and
selection artifact remains in the same campaign directory.
