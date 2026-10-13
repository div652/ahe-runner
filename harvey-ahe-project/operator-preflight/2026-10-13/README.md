# Operator model preflight, 2026-10-13

These reports come from standalone checks run before the first Harvey campaign.
Each report covers one model. It records the advertised catalog, one exact-model
"Reply with OK only." completion, and one request to a deliberately nonexistent
model, which must be rejected. Response text is not stored.

Command, run from the AHE root, once per model:

```bash
python3 ~/.copilot/skills/gh-copilot-server-model-access/verify_observable_contract.py \
  --base-url http://127.0.0.1:3141/v1 --model <ID> [--reasoning-effort high] \
  --output harvey-ahe-project/operator-preflight/2026-10-13/<role>-<ID>.json
```

| File | Model | Reasoning |
|---|---|---|
| `evaluated-gpt-5.4-mini.json` | `gpt-5.4-mini` | provider default |
| `meta-gpt-5.4-high.json` | `gpt-5.4` | `high` |
| `judge1-helper-claude-sonnet-5.json` | `claude-sonnet-5` | provider default |
| `judge2-gpt-5.5.json` | `gpt-5.5` | provider default |

Endpoint: loopback `http://127.0.0.1:3141/v1`, chat completions, non-streaming.
The listener belonged to the operator's own VS Code server process.

Extension `gh-copilot-server.gh-copilot-server-0.1.0` (SHA-256):

```text
85545ac5e6cb1d3b05e2a14cf7ca4e438c89c529157945042f7e056988ab31d5  package.json
9090e9ebf4aff5214c0e19617cdbd362f0ee528336b4c9846bbb93f29bae7a45  out/server.js
c7ef05f74d3645f7daac63cc3fdc230fd3eaf9a7bc689c65c92ab5ec06e3f5ab  out/extension.js
```

Scope: these checks test only what the proxy shows. The extension builds the
response `model` field from the model it selected, so they cannot rule out a
silent substitution upstream. `ahe run` repeats its own catalog check, exact-model
preflight, and negative control before each role's first call.
