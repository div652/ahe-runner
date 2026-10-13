# Harvey LAB AHE Runbook (operator)

These are the commands the operator runs. The implementation work did not run
any of them: no `ahe conformance`, `ahe validate`, `ahe run`, live model call,
or campaign was executed while building this project.

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
