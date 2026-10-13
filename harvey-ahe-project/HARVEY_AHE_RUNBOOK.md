# Harvey LAB AHE Runbook (operator)

These are the commands the operator runs. The implementation work did not run
any of them: no `ahe conformance`, `ahe validate`, `ahe run`, live model call,
or campaign was executed while building this project. Commands later run with
the operator's per-command approval are recorded in section 3a.

All commands run from the AHE repository root (`~/AHE_Harvey`, branch
`harveybench`) unless stated otherwise.

## 1. Pinned inputs

| Input | Expected |
|---|---|
| LAB repository | `~/harvey-labs`, branch `divyanshu_harvey_evolution` |
| LAB commit | `86678eba6fcbc6c03958742c6e9e75737514f513` (equals `lab_commit` in `splits.json`) |
| Splits | `harvey-ahe-project/splits.json`: 6 train, 3 validation, 3 test tasks; each task pinned by the sha256 of its `task.json` and documents tree |
| NexAU | 0.3.9 (from the AHE environment) |
| Endpoint | loopback GH Copilot Server, `http://127.0.0.1:3141/v1`, chat completions |

Check the LAB pin and a clean worktree:

```bash
git -C ../harvey-labs rev-parse HEAD
git -C ../harvey-labs status --short
```

The adapter refuses to load if any pinned task file or document differs.

## 2. One-time setup

Install LAB and the Harvey adapter into the AHE environment (editable):

```bash
uv pip install --python .venv/bin/python -r harvey-ahe-project/requirements.txt
```

Make the sandbox image available as `lab-sandbox:latest`. The adapter never
pulls or builds it; it fails with a configuration error if the image is
missing. Use one option.

Option A, build from the pinned LAB Dockerfile (reproducible from the commit):

```bash
podman build -f ../harvey-labs/lab_core/sandbox/Dockerfile -t lab-sandbox:latest ../harvey-labs/lab_core/sandbox
```

Option B, pull the published image (contents may differ from the pinned
commit):

```bash
podman pull ghcr.io/harveyai/lab-sandbox:latest && podman tag ghcr.io/harveyai/lab-sandbox:latest lab-sandbox:latest
```

Every trial result records the image ID (`provenance.sandbox_image_id`).

## 3. Fill the operator-owned values in `harvey-ahe-project/ahe.yaml`

List the exact model IDs the endpoint advertises:

```bash
curl -s http://127.0.0.1:3141/v1/models | python3 -c 'import json,sys; print("\n".join(sorted(m["id"] for m in json.load(sys.stdin)["data"])))'
```

Replace every `replace-with-exact-...` value:

| Field | Role | Fixed parameters |
|---|---|---|
| `models.evaluated.model` | the agent being evolved | `max_tokens` 64000, temperature 0.0 |
| `models.meta.model` | Explore, Debugger, Evolve | `max_tokens` 32000, temperature 0.2 |
| `project.adapter_settings.judges[0].model` | judge 1 (LAB standard: claude-sonnet-4-6) | `max_tokens` 64000, temperature 0.0 (required) |
| `project.adapter_settings.judges[1].model` | judge 2 (LAB standard: gpt-5.5) | `max_tokens` 64000, temperature 0.0 (required) |
| `project.adapter_settings.helper.model` | deliverable filename helper (LAB: claude-sonnet-4-6) | `max_tokens` 1024, temperature 0.0 (required) |

Use exact advertised IDs without `copilot/` or `openai/` prefixes. The two
judges must differ. Results are labelled `lab-standard-dual-v1` only if the
judge IDs are literally `claude-sonnet-4-6` and `gpt-5.5`; otherwise
`custom-dual`.

Then decide the live budget: `campaign.rounds`, `evaluation.*_trials`,
`evaluation.max_concurrency`, `debugger.max_concurrency`. The checked-in
values (1 round, 1 trial per split, concurrency 2) are a small first
campaign. Each trial is one agent run of up to 200 turns plus two judge calls
per rubric criterion (20 to 40 criteria per task).

Optionally set `adapter_settings.scratch_root` to a disk with room for one
copy of each running trial's documents and outputs (default: system temp).

## 3a. Operator record: first live configuration (2026-10-13)

The operator approved every command and value below one at a time.

Pre-run checks, all from the AHE root:

| Check | Result |
|---|---|
| `git -C ../harvey-labs rev-parse HEAD` | `86678eba6fcbc6c03958742c6e9e75737514f513` (matches the pin) |
| `git -C ../harvey-labs status --short` | empty (clean) |
| `ahe conformance` (placeholder model IDs) | `valid: true`, ABI v2, 0 errors, 0 warnings; NexAU smoke loaded `harvey_lab_agent` with 7 tools (`openai` tool-call mode) |
| `ahe validate` (placeholder model IDs) | `valid: true`; splits train 6, validation 3, test 3; one standard AHE harness warning: host-imported middleware is statically inspected, not OS-sandboxed (AHE-005) |
| `ahe validate` (filled `ahe.yaml`, commit `c24b6e3`) | `valid: true`, same single warning, same `harness_sha256`; the resolved model IDs, effort, and budget match the table below |
| `podman image inspect lab-sandbox:latest` | ID `b4c49628fec0a36f23fd694ab5ebf0441fef8e41d53dce8008506dcfa86e4383`, created 2026-09-30 23:58:29 UTC; also tagged `ghcr.io/harveyai/lab-sandbox:latest` (pulled, Option B) |
| Last change to `lab_core/sandbox` at the pin | `2a95bad4`, 2026-09-30 23:55:57 UTC, about 2.5 minutes before the image was built. The operator kept the pulled image. |
| `pandoc --version` | 3.11 |
| `ss -ltnp '( sport = :3141 )'` | `127.0.0.1:3141`, owned by the operator's own VS Code server process |
| `GET /v1/models` | 27 IDs, including `gpt-5.4-mini`, `gpt-5.4`, `gpt-5.5`, and `claude-sonnet-5`. `claude-sonnet-4-6` is **not** advertised. |
| Extension | `gh-copilot-server.gh-copilot-server-0.1.0`. SHA-256: `package.json` `85545ac5…31d5`, `out/server.js` `9090e9eb…7a45`, `out/extension.js` `c7ef05f7…f5ab` (full hashes in `operator-preflight/2026-10-13/README.md`) |

Standalone probes run before the campaign: one positive exact-model
completion plus one nonexistent-model negative control per model, using
`~/.copilot/skills/gh-copilot-server-model-access/verify_observable_contract.py`.
The reports are in `operator-preflight/2026-10-13/`.

| Model (role) | Reasoning sent | Positive probe | Negative control |
|---|---|---|---|
| `gpt-5.4-mini` (evaluated) | none (provider default) | 200, returned `gpt-5.4-mini` | rejected, error named the fake ID |
| `gpt-5.4` (meta) | `high` | 200, returned `gpt-5.4` | rejected, error named the fake ID |
| `claude-sonnet-5` (judge 1, helper) | none (provider default) | 200, returned `claude-sonnet-5` | rejected, error named the fake ID |
| `gpt-5.5` (judge 2) | none (provider default) | 200, returned `gpt-5.5` | rejected, error named the fake ID |

Values chosen:

| Field | Value |
|---|---|
| `models.evaluated` | `gpt-5.4-mini`, provider-default reasoning, `max_tokens` 64000 |
| `models.meta` | `gpt-5.4`, reasoning `high`, `max_tokens` 32000 |
| `judges[0]` | `claude-sonnet-5` (LAB standard `claude-sonnet-4-6` is not advertised), provider-default reasoning |
| `judges[1]` | `gpt-5.5` (LAB standard), provider-default reasoning |
| `helper` | `claude-sonnet-5`, provider-default reasoning |
| Judge profile label | `custom-dual`, because judge 1 is not `claude-sonnet-4-6` |
| `campaign.rounds` | 3 |
| `evaluation.{train,validation,test}_trials` | 1 / 1 / 1 |
| `evaluation.max_concurrency` | 20 (in practice capped at 6 train and 3 validation/test units per evaluation) |
| `debugger.max_concurrency` | 20 |
| `meta.max_concurrent_model_calls` | 6 (raised from 2). AHE runs `min(debugger.max_concurrency, this)` debuggers at once, so 6 is the useful maximum for 6 training tasks. Explore still uses at most 2 workers. |

Endpoint caveats that apply to every role: the extension ignores
`temperature`, reports zero token usage, and synthesizes the response
`model` label from the model object it selected. So the configured
`temperature: 0.0` is not actually applied, cost and usage are unavailable,
and a silent upstream model substitution cannot be ruled out independently.

## 4. Offline checks

```bash
.venv/bin/ahe conformance --config harvey-ahe-project/ahe.yaml
.venv/bin/ahe validate --config harvey-ahe-project/ahe.yaml
```

Both load the adapter (which verifies every task pin) and validate the seed
harness against `harvey-ahe-project/harness_manifest.yaml`, including a NexAU
load smoke test. They make no model calls. Expect `"valid": true`.

## 5. Campaign

```bash
.venv/bin/ahe run --config harvey-ahe-project/ahe.yaml
```

`run` evaluates the seed (C0) on the training split, explores, runs the
configured evolution rounds, then validation and the final test. Before each
role's first model call (evaluated agent, meta agents, each judge, helper)
the runner checks the model catalog, runs an exact-model preflight, and runs
a negative control; a mismatch stops the stage. Inspect or continue a stopped
campaign with:

```bash
.venv/bin/ahe status --config harvey-ahe-project/ahe.yaml
.venv/bin/ahe resume --config harvey-ahe-project/ahe.yaml
```

Artifacts go to `harvey-ahe-project/campaign-artifacts/<campaign.name>/`.
Per-trial results are under `evaluations/.../units/<unit>/attempt-<n>/`:
`result.json`, `trace.jsonl`, `run/output/` (graded copies), and `grading/`
(both judges' per-criterion scores).

## 6. Known deviations and limits to report with results

- Seed harness: LAB's tools and prompt on NexAU, without LAB's skill files;
  tool descriptions drop skill references; `finish` rejections are
  `{"status": "error"}` dicts (see `benchmark_public/ADAPTER.md` section 6).
- Trials that end in `agent_error` are still graded on their output (LAB's
  own runner would not score them).
- LAB-002: a judge prompt above 240,000 characters is scored `fail` without a
  model call. LAB-003: judge disagreement scores 0.5 but is a failing
  diagnostic. See `docs/HARVEY_LAB_BENCHMARK_ISSUES.md`.
- Harness Python runs on the host; the adapter's import and call bans are
  static checks, not an OS boundary (AHE-005).
- Branch-local AHE fixes AHE-001 to AHE-008 are listed in
  `docs/HARVEY_INTEGRATION_UPSTREAM_RECOMMENDATIONS.md`.
