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
