# AHE Runner guide

This guide is for operators who run AHE Runner campaigns and for developers who
adapt the runner to a new benchmark or change its meta agents. It describes
what the code on branch `harveybench` does after AHE-016 (commit `6f9d941`),
uses the Harvey LAB project as the worked example, and links to the older docs
for deeper detail.

## Contents

1. [What the runner is](#1-what-the-runner-is)
2. [Installing and running](#2-installing-and-running)
3. [Configuration reference](#3-configuration-reference)
4. [Artifacts on disk](#4-artifacts-on-disk)
5. [The meta agents](#5-the-meta-agents)
6. [Evidence and verification](#6-evidence-and-verification)
7. [Candidates, attribution and selection](#7-candidates-attribution-and-selection)
8. [Errors and troubleshooting](#8-errors-and-troubleshooting)
9. [Writing an adapter for a new benchmark](#9-writing-an-adapter-for-a-new-benchmark)
10. [Known differences from the original AHE](#10-known-differences-from-the-original-ahe)
11. [Glossary](#11-glossary)

Related documents:

| Document | Use it for |
|---|---|
| `README.md` | Short overview and install commands |
| `docs/ARCHITECTURE.md` | Design goals and invariants (some details are older than the code; see the notes in this guide) |
| `docs/OPERATOR_GUIDE.md` | Model endpoint rules, preflight, evaluation layout |
| `docs/ADAPTER_GUIDE.md` | Full adapter contract with examples |
| `docs/HARNESS_ABI.md` | What a harness must contain and what Evolve may change |
| `docs/HARVEY_INTEGRATION_UPSTREAM_RECOMMENDATIONS.md` | AHE-001 to AHE-016 issue records |
| `harvey-ahe-project/HARVEY_AHE_RUNBOOK.md` | Harvey operating log, campaign by campaign |

---

## 1. What the runner is

AHE means "Agentic Harness Engineering". A *harness* is the set of files that
define an agent: system prompt, tool descriptions, tool code, middleware,
skills, sub-agents and memory files. AHE improves a harness by running it on a
benchmark, studying the results with other agents (the *meta agents*), and
letting one of them edit the harness. The model that runs the harness stays
fixed. Only the harness changes.

AHE Runner (`src/ahe_runner/`) is a reimplementation of the original AHE
(cloned at `/home/agarwaldi/ahe-original-upstream`, commit `8b2a55d`). It keeps
the same loop and, since AHE-016, the same meta-agent tools, middleware and
model settings. It adds:

- a typed *benchmark adapter* plug-in, so any benchmark can be used
  (`src/ahe_runner/adapter.py`);
- durable *stages*: each step writes a record with hashes and can be resumed
  (`src/ahe_runner/state.py`);
- immutable *checkpoints* (C0, C1, ...) with file hashes
  (`src/ahe_runner/workspace.py`);
- checks that every artifact a meta agent sees matches the evaluation it came
  from (`src/ahe_runner/evidence.py`, `src/ahe_runner/evaluation.py`);
- a held-out validation split for choosing the final checkpoint, and a final
  test split (`src/ahe_runner/controller.py`).

The evaluated harness and the meta agents both run on NexAU, an agent
framework. The runner talks to one OpenAI-compatible chat-completions endpoint.

Main parts:

| Part | Code | Job |
|---|---|---|
| CLI | `src/ahe_runner/cli.py` | Parses commands, prints JSON results |
| Controller | `src/ahe_runner/controller.py` (`CampaignController`) | Runs stages in order |
| Stage store | `src/ahe_runner/state.py` (`StageStore`) | Records stage attempts, resumes, blocks |
| Adapter | your plug-in, e.g. `harvey-ahe-project/harvey_ahe/adapter.py` | Runs the benchmark, writes evidence, scores |
| Evaluated runtime | `src/ahe_runner/runtime.py` (`EvaluatedAgentRuntime`) | Builds NexAU sessions for the harness under test |
| Meta backend | `src/ahe_runner/meta/nexau_backend.py` (`NexAUMetaBackend`) | Runs Explorers, Debuggers and Evolve |
| Model client | `src/ahe_runner/meta/model_client.py` | Exact-model HTTP calls, shared request queue |

### 1.1 A campaign

A *campaign* is one full run with one config. `ahe run` executes these stages
(stage names as the controller writes them):

```text
baseline.freeze                       copy harness/ to checkpoints/C0 (read-only)
evaluation.C0.train                   run C0 on the training split
evidence.C0.train                     adapter writes the evidence bundle
exploration                           Source and Web Explorers write two skills
round.1 ... round.N                   see 1.2; each round makes checkpoint Ci
evolution.close                       mark evolution finished
evaluation.C0.validation ... evaluation.CN.validation
selection                             pick the best checkpoint on validation
evaluation.C0.test                    final test of the baseline
evaluation.C<selected>.test           final test of the selected checkpoint
final.complete                        export final/selected-harness, write final_test.json
```

If C0 is selected, only `evaluation.C0.test` runs. The number of rounds is
`campaign.rounds`.

### 1.2 A round

`ahe run-round --round i` runs these steps in order (`run_round` in
`controller.py`). Steps 1 to 3 are already complete after round `i-1`, so they
only re-verify.

| # | Stage | Reads | Writes |
|---|---|---|---|
| 1 | `evaluation.C{i-1}.train` | `checkpoints/C{i-1}/harness`, training split | `evaluations/C{i-1}-train/` |
| 2 | `evidence.C{i-1}.train` | `evaluations/C{i-1}-train/` | `evidence/C{i-1}-train/` |
| 3 | `exploration` | C0 harness, NexAU source, method reference, public adapter docs, the web | `explore_skills/` |
| 4 | `round.{i}.debugger` | `evidence/C{i-1}-train/`, `evaluations/C{i-1}-train/` | `debugger/C{i}/` |
| - | history (not a stage) | all earlier train evaluations, checkpoints, attributions | `history/history.json`, `history/history.md` |
| 5 | `round.{i}.evolve` | evidence, raw evaluation, debugger output, history, attribution `C{i-1}`, checkpoints, explore skills | `workspaces/C{i}/attempt-NNNN/` (git repo), `evolve/C{i}/attempt-NNNN/` |
| 6 | `round.{i}.validate` | workspace HEAD, parent harness | `evolve/C{i}/validation.json` |
| 7 | `round.{i}.freeze` | clean workspace, change manifest | `checkpoints/C{i}/` (read-only) |
| 8 | `evaluation.C{i}.train` | `checkpoints/C{i}/harness` | `evaluations/C{i}-train/` |
| 9 | `evidence.C{i}.train` | `evaluations/C{i}-train/` | `evidence/C{i}-train/` |
| 10 | `round.{i}.attribute` | both train results, `change_manifest.json` | `attribution/C{i}/transition.json` |

Note the naming: the debugger and Evolve outputs of round `i` are stored under
`C{i}` (the checkpoint being built), but they study `C{i-1}`.

There is no accept or reject step inside a round. A candidate that passes
validation always becomes checkpoint `C{i}` and the parent of the next round.
See [section 7](#7-candidates-attribution-and-selection).

---

## 2. Installing and running

### 2.1 Install

From the repository root (`README.md`):

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e '.[web,dev]'
```

The `web` extra installs `ddgs`, `beautifulsoup4` and `httpx`. The NexAU meta
backend needs them, because it requires both Explorers and the `ddgs` search
backend. The package needs Python 3.12 or newer (`pyproject.toml`).

For Harvey, also install LAB and the Harvey project into the same environment
(`harvey-ahe-project/HARVEY_AHE_RUNBOOK.md`, section 2):

```bash
uv pip install --python .venv/bin/python -r harvey-ahe-project/requirements.txt
```

Harvey also needs `pandoc` on the host and the Podman image
`lab-sandbox:latest` (the runbook shows how to build or pull it).

If `meta.shell.backend` is `docker`, build the meta-agent shell image once.
From `harvey-ahe-project/`:

```bash
docker build -t ahe-meta-shell:py313-v2 -f docker/meta-shell.Dockerfile docker
```

The image is `python:3.13-slim-bookworm` with `git`, `jq`, `ripgrep`, `file`,
`unzip` and `pyyaml` (`harvey-ahe-project/docker/meta-shell.Dockerfile`).

Every `ahe` command prints a line "E2B SDK not installed" when NexAU loads.
It is harmless.

### 2.2 CLI commands

All commands except `init-project` take `--config PATH`. Each prints a JSON
result on stdout. On a runner error it prints
`{"status": "error", "type": ..., "message": ...}` on stderr and exits with
code 2 (`cli.py`, `main`). Other Python errors print a normal traceback.

| Command | Purpose |
|---|---|
| `ahe init-project PATH` | Copy the project template (`src/ahe_runner/templates/project`) into an empty folder |
| `ahe conformance --config C` | Load adapter and harness, run the harness ABI checks and a NexAU load smoke test. No model calls |
| `ahe validate --config C` | Same checks, plus the full report: config snapshot, splits, harness validation. No model calls |
| `ahe run --config C` | Run the whole campaign (section 1.1). Safe to repeat: finished stages are skipped |
| `ahe resume --config C` | Same as `run`, but fails if the campaign does not exist yet |
| `ahe run-round --config C --round N` | Run up to and including round N (section 1.2). Needs checkpoint `C{N-1}` |
| `ahe close-evolution --config C` | Needs `evaluation.C{rounds}.train`; writes `selection/evolution_closed.json` |
| `ahe run-validation --config C` | Close evolution, evaluate C0..CN on validation, run `selection` |
| `ahe run-final-test --config C` | Validation and selection if needed, then the final test and export |
| `ahe status --config C` | Write and print `campaign_state.json` |
| `ahe extend-rounds --config C --reason R [--apply] [--allow-runner-code-change]` | After raising `campaign.rounds` in the config, accept the new value for an existing campaign (section 2.6). A dry run unless `--apply` |
| `ahe retry-blocked-stage --config C --stage S --reason R [--apply] [--allow-runner-code-change]` | Give stage S, blocked only by infrastructure failures, `recovery.maximum_stage_attempts` more attempts (section 2.6). A dry run unless `--apply` |

Neither `conformance` nor `validate` checks the model endpoint or the Docker
shell image. The endpoint is checked by preflights at the start of each model
stage. The image is checked by `docker image inspect` at the start of the
first meta stage.

### 2.3 Typical operator sequence

```bash
cd /home/agarwaldi/AHE_Harvey
.venv/bin/ahe conformance --config harvey-ahe-project/ahe.yaml
.venv/bin/ahe validate    --config harvey-ahe-project/ahe.yaml
docker image inspect ahe-meta-shell:py313-v2 >/dev/null && echo image ok
.venv/bin/ahe run-round   --config harvey-ahe-project/ahe.yaml --round 1
.venv/bin/ahe status      --config harvey-ahe-project/ahe.yaml
.venv/bin/ahe run-round   --config harvey-ahe-project/ahe.yaml --round 2
.venv/bin/ahe run-round   --config harvey-ahe-project/ahe.yaml --round 3
.venv/bin/ahe run-final-test --config harvey-ahe-project/ahe.yaml
```

`ahe run` does all of this in one command. Running round by round lets you
inspect each round before spending more model calls. Rounds must run in order.

### 2.4 Running in tmux

A campaign takes hours. Run it in tmux on the model host so a dropped SSH
connection does not stop it. Example for round 1 of campaign 5:

```bash
tmux new-session -d -s ahe-c5 -c /home/agarwaldi/AHE_Harvey ".venv/bin/ahe run-round --config harvey-ahe-project/ahe.yaml --round 1 2>&1 | tee harvey-ahe-project/campaign-artifacts/c5-run-round-1.log; exec bash"
```

- `-d` starts the session in the background.
- `tee` keeps a log next to the campaign artifacts.
- `exec bash` keeps the window open after the run ends, so you can read the
  exit output.

Attach with `tmux attach -t ahe-c5`. Detach with `Ctrl-b d`. Earlier campaigns
used one session per campaign (for example `ahe-c4`) with one window per round.

### 2.5 Resuming after a crash

Every stage has a record at `stages/<stage name>/stage.json` with one entry
per attempt. The same command can be run again at any time.

- **Completed** stages are not rerun. Their output hashes are checked again on
  every call.
- If the process died (crash, kill, reboot), the last attempt is still marked
  `running`. On the next call it is marked `interrupted` with error type
  `AbandonedStageAttempt`, and a new attempt starts.
- If a stage failed with an `InfrastructureError`, the stage is retryable, and
  attempts remain, the stage becomes `interrupted`. The command stops with
  `StageInterruptedError` ("Run resume to continue"). Run the same command (or
  `ahe resume`) again.
- Any other failure makes the stage `blocked`. A blocked stage raises
  `StageBlockedError` on every later call. Only `ahe retry-blocked-stage`
  unblocks it, and only when every attempt failed on infrastructure
  (section 2.6; AHE-011, AHE-026).
- A stage with more than `recovery.maximum_stage_attempts` attempts (default
  3), or more than its `attempt_allowance` after `retry-blocked-stage`,
  becomes `blocked`. Killed attempts count too.

The runner never retries inside one process. You decide when to rerun.

**Pre-stage command (AHE-026).** If the environment variable
`AHE_PRE_STAGE_COMMAND` is set, the runner runs it before every new stage
attempt (not for stages that are already complete). It is split like a shell
command line (`shlex.split`) but run without a shell. It gets
`AHE_STAGE_NAME` and `AHE_CAMPAIGN_DIR` in its environment, and its output
goes to stderr. If it exits non-zero, or runs longer than
`AHE_PRE_STAGE_TIMEOUT_SECONDS` (default 1200), the command stops with an
`InfrastructureError` before the attempt is recorded, so no attempt is used.
The command is saved in each attempt record as `pre_stage_command`. Harvey
uses it to restart the model server's VS Code window before it runs out of
memory (runbook section 5v). The variable is not part of the campaign
identity.

These stages are never retried, even after an infrastructure error:
`baseline.freeze`, `evidence.*`, `round.*.validate`, `round.*.freeze`,
`round.*.attribute`, `evolution.close`, `selection`, `final.complete`.
Evaluation, `exploration`, `round.*.debugger` and `round.*.evolve` are
retryable.

What is kept between attempts:

- Evaluation: the adapter keeps finished units. Harvey records failed units
  and raises `InfrastructureError` after the other units finish, so a resume
  reruns only the failed units (`harvey_ahe/adapter.py`, `evaluate`).
- Exploration: a skill that already passes the deliverable check is reused.
- Debugger: a task whose `result.json` holds a valid diagnosis for the same
  request hash is reused. Tasks marked "unavailable" run again.
- Evolve: attempt `n` clones the workspace of attempt `n-1`. Uncommitted work
  from that attempt is saved under
  `stages/round.{i}.evolve/attempts/NNNN/recovery/` and shown to Evolve as the
  `recovery` root.

### 2.6 When you must start a new campaign

`campaign_manifest.json` stores hashes of the config file, the runner package,
the meta prompts, tool descriptions, tool code, method reference, supplements,
seed URLs, adapter identity and dependency files, harness manifest, base
harness and splits (`_ensure_campaign_manifest` in `controller.py`). If any of
these change, every command fails with `IntegrityError: campaign identity
changed in protected inputs [...]; use a new artifacts directory`.

So any edit to runner code, prompts, supplements, `ahe.yaml` or adapter files
needs a new `campaign.artifacts_dir` (and usually a new `campaign.name`). The
same applies after a stage is blocked, unless the second exception below
applies.

The check runs again at the start of every round inside a running `ahe run`
or `ahe resume`, because the hashes are taken from the files on disk. Editing
any of these files while a campaign runs therefore stops it at the next round.
Make such edits in a separate git worktree, or after stopping the campaign.

**One exception: more rounds.** To give a campaign more rounds than it was
started with:

1. Stop the campaign process, then raise `campaign.rounds` in the config.
2. Run `ahe extend-rounds --config C --reason "..."`. This dry run lists every
   changed identity path. Only `config.config_sha256` and
   `config.document.campaign.rounds` are accepted. With
   `--allow-runner-code-change`, changed runner package files are accepted
   too, and listed. Prompts, tool descriptions, tool code, the method
   reference, adapter files, the harness and every other config value must
   be unchanged.
3. Run it again with `--apply`, then `ahe resume`.

Rules (`extend_rounds` in `controller.py`):

- The round count can only go up.
- It is refused once `evolution.close`, `selection` or `final.complete`
  exists.
- `--apply` appends an entry to `campaign_amendments.json`: the reason, the
  old and new round counts, the changed paths and the new identity with its
  hash. `campaign_manifest.json` and all stage records are not changed, so
  every finished stage stays valid.
- From then on, commands accept only the identity of the last amendment.
  Amendments must chain from the manifest's hash and match their own hashes;
  an edited ledger stops every command.

**Second exception: retrying a stage blocked by infrastructure (AHE-026).**
If an outage outlasts a stage's attempts (for example the model server is
down while a resume loop uses them up), the stage is blocked although nothing
is wrong with the campaign. To continue it:

1. Fix the cause and stop the campaign process.
2. Run `ahe retry-blocked-stage --config C --stage S --reason "..."`. This
   dry run shows the attempts so far and the new allowance.
3. Run it again with `--apply`, then `ahe resume`.

Rules (`retry_blocked_stage` in `controller.py`, `blocked_retry_plan` and
`reopen_blocked` in `state.py`):

- The stage must be `blocked`, and retryable (evaluation, `exploration`,
  `round.*.debugger`, `round.*.evolve`). Records written before stages stored
  `retryable` count as retryable only if an earlier attempt failed on
  infrastructure without blocking the stage.
- Every attempt must have failed on infrastructure (`infrastructure_failed`,
  or `interrupted` with `AbandonedStageAttempt`). A stage with any other
  failure is refused, so a valid result or a real error is never retried.
- No protected input may have changed. With `--allow-runner-code-change`,
  changed runner package files are accepted and listed, as for
  `extend-rounds`.
- `--apply` appends an entry of kind `retry_blocked_stage` to
  `campaign_amendments.json` (reason, stage, attempt statuses, new allowance,
  changed paths, identity and hash) and adds an `operator_overrides` entry to
  the stage record. The stage becomes `interrupted` with
  `attempt_allowance` = attempts so far + `recovery.maximum_stage_attempts`.
  Earlier attempts and all other stage records are not changed.
- The resume keeps what the stage kept before (section 2.5): for an
  evaluation, only units without `complete.json` run again.

### 2.7 The model endpoint

All model calls go to `transport.base_url`. For Harvey this is
`http://127.0.0.1:3141/v1`, an OpenAI-compatible GH Copilot server. It runs
on the model host in the tmux session `copilot-host` (VS Code under Xvfb,
runbook section 5c). `transport.api_key_env` is `null`, so the runner sends a
fixed placeholder key.

Runbook notes about this endpoint that affect results:

- it ignores `temperature`;
- it reports zero token usage;
- it fills in the `model` field of its response itself, so the exact-model
  check proves less than with a real provider.

`docs/OPERATOR_GUIDE.md` describes the general endpoint rules and the
preflight in detail.

---

## 3. Configuration reference

The config is one YAML file (`src/ahe_runner/config.py`, `load_config`).
Rules:

- `schema_version: 1` is required.
- Unknown keys are rejected in every section.
- `${VAR}` values are expanded from the environment. Any `${...}` left
  unresolved is a `ConfigurationError`.
- Relative paths resolve against the folder of the config file.
- Model IDs must be exact. A `openai/` or `copilot/` prefix is rejected.

The worked example is `harvey-ahe-project/ahe.yaml` (campaign 5). The tables
show code defaults and the Harvey values.

### 3.1 `campaign`

| Field | Default | Meaning | Harvey |
|---|---|---|---|
| `name` | required | Campaign name | `harvey-lab-campaign-5-original-meta-agents` |
| `artifacts_dir` | required | Campaign folder | `./campaign-artifacts/<name>` |
| `rounds` | 10 | Number of Evolve rounds | 3 |
| `root_seed` | 42 | Seed for trial seeds | 42 |

### 3.2 `project`

| Field | Default | Meaning |
|---|---|---|
| `harness_dir` | required | Seed harness; becomes C0 |
| `harness_manifest` | required | Harness ABI manifest (`docs/HARNESS_ABI.md`) |
| `adapter` | required | `module:Class`, imported from the project folder |
| `adapter_settings` | `{}` | Passed to the adapter as `settings` |
| `adapter_dependency_paths` | `[]` | Files hashed into the campaign identity. Not shown to meta agents |
| `meta_agent_source_roots` | `[]` | Public folders the Source Explorer and Evolve may read, as roots `adapter_public_0`, `adapter_public_1`, ... |

Harvey uses `benchmark_adapter:HarveyAdapter`, hashes `benchmark_adapter.py`,
`harvey_ahe/`, `splits.json`, `pyproject.toml` and `requirements.txt`, and
exposes `./benchmark_public` to the meta agents. Its `adapter_settings` are
described in the runbook (judges, helper model, sandbox image, grading
limits).

### 3.3 `models`

Two blocks, `evaluated` (the harness under test) and `meta` (all meta
agents). Same fields:

| Field | Default | Meaning | Harvey evaluated | Harvey meta |
|---|---|---|---|---|
| `model` | required | Exact model ID | `gpt-5.4-mini` | `gpt-5.4` |
| `max_tokens` | required | Max output tokens per call | 64000 | 32000 |
| `reasoning_effort` | `null` | `null`, `""` and `provider-default` all mean "not sent" | provider default | `high` |
| `temperature` | 0.2 | Sampling temperature | 0.0 | 0.2 |

The meta values can be changed per role with `meta.roles` (section 3.8).

### 3.4 `transport`

| Field | Default | Meaning |
|---|---|---|
| `base_url` | required | OpenAI-compatible endpoint |
| `api_key_env` | `null` | Name of the env var with the key. `null` sends a placeholder |
| `api_mode` | `chat_completions` | Only value allowed |
| `stream` | `false` | Only value allowed |
| `request_timeout_seconds` | 300 | Per-call timeout (Harvey: 600) |
| `observable_model_policy` | `exact` | The response must name the requested model |
| `model_catalog` | `optional` | `required`, `optional` or `disabled`: check `/models` lists the model |
| `negative_control` | `best_effort` | `required`, `best_effort` or `disabled`: check a fake model ID is refused |
| `max_concurrent_requests` | `null` | Max model calls in flight in this process (AHE-015). `null` means no limit |
| `hung_call_retries` | `0` | Times to resend a model call whose request timed out (client or endpoint) before any response, at most 5 (AHE-022). Each failed attempt is saved as `hung-attempt-N.json` in the call folder and listed under `hung_attempts` in `response.json` or `error.json`. Other errors are never resent |

Harvey sets `model_catalog: required`, `negative_control: required` and
`max_concurrent_requests: 24`; from the campaign 9 rerun also
`hung_call_retries: 2`.

### 3.5 `evaluation`

| Field | Default | Meaning | Harvey |
|---|---|---|---|
| `train_trials` | 2 | Trials per training task | 2 |
| `validation_trials` | 2 | Trials per validation task | 1 |
| `test_trials` | 3 | Trials per test task | 1 |
| `max_concurrency` | 8 | Units the adapter may run at once | 20 |

A *unit* is one task times one trial.

### 3.6 `debugger`

| Field | Default | Meaning | Harvey |
|---|---|---|---|
| `max_concurrency` | 8 | Upper bound on parallel per-task debuggers | 20 |
| `max_iterations` | 80 | NexAU iteration limit per debugger | 25 (the original AHE value) |

### 3.7 `recovery`

| Field | Default | Meaning |
|---|---|---|
| `maximum_stage_attempts` | 3 | Attempts per stage before it is blocked |
| `archive_incomplete_attempts` | `true` | Accepted, but not read by any runner code |

### 3.8 `meta`

| Field | Default | Meaning | Harvey |
|---|---|---|---|
| `backend` | `nexau` | `nexau`, or `scripted` (fixed offline output for rehearsals and tests) | `nexau` |
| `explore.source_enabled` | `true` | Must be `true` for the NexAU backend | `true` |
| `explore.web_enabled` | `true` | Must be `true` for the NexAU backend | `true` |
| `explore.web_search_backend` | `ddgs` | `ddgs` or `disabled`; NexAU backend needs `ddgs` | `ddgs` |
| `explore.research_seed_urls` | `[]` | URLs given to the Explorers as starting points | NexAU GitHub, python-docx, openpyxl, python-pptx docs |
| `role_supplements.source/web/debugger/evolve` | `null` | Markdown appended to each role prompt (section 5.1) | `./meta_supplements/*.md` |
| `roles.debugger/evolve/explorers.temperature` | `null` | Per-role temperature; `null` uses `models.meta` | 0.4 / 0.7 / 0.2 |
| `roles.debugger/evolve/explorers.max_tokens` | `null` | Per-role max tokens; `null` uses `models.meta` | 20000 / - / - |
| `shell.backend` | `disabled` | `docker` gives meta agents `run_shell_command` | `docker` |
| `shell.image` | `null` | Required when backend is `docker` | `ahe-meta-shell:py313-v2` |
| `shell.docker_binary` | `docker` | Docker command | default |
| `shell.timeout_seconds` | 300 | Per-command timeout | default |
| `shell.memory` | `2g` | Container memory and memory+swap | default |
| `shell.cpus` | 2.0 | Container CPUs | default |
| `shell.pids_limit` | 256 | Max processes in the container | default |
| `shell.tmpfs_size` | `1g` | Size of the writable `/tmp` | default |
| `max_concurrent_model_calls` | 2 | Caps parallel debugger and Explorer agents | 6 |
| `max_context_tokens` | 200000 | NexAU agent context limit | 200000 |
| `max_running_subagents` | 5 | NexAU sub-agent limit | 5 |
| `evolve_max_iterations` | 500 | Evolve iteration limit | 500 |
| `explore_max_iterations` | 200 | Explorer iteration limit | 200 |

Role overrides and shell settings are also part of the stage inputs of the
meta stages (`_meta_stage_inputs` in `controller.py`).

### 3.9 How the concurrency settings fit together

| Setting | Limits | Harvey |
|---|---|---|
| `transport.max_concurrent_requests` | All model calls in flight in the process: evaluated agent, Harvey judges and filename helper, meta agents. Extra calls wait for a slot | 24 |
| `evaluation.max_concurrency` | Units run at once by the adapter | 20 |
| `adapter_settings.judge_max_concurrent_calls` | Harvey judge calls at once (inside the 24) | 18 |
| `debugger.max_concurrency` and `meta.max_concurrent_model_calls` | Parallel debuggers = the smaller of the two | min(20, 6) = 6 |
| `meta.max_concurrent_model_calls` | Parallel Explorers = min(2, this value) | 2 |

Details of the shared queue (`model_client.py`):

- There is one queue per `base_url` per process.
- A call holds its slot only during the HTTP request.
- Preflight probes do not use the queue.
- Each recorded `response.json` or `error.json` has `slot_wait_seconds`, the
  time the call waited for a slot.
- Two different limits for the same URL in one process give a
  `ConfigurationError`.

---

## 4. Artifacts on disk

### 4.1 Layout

All paths are under `campaign.artifacts_dir`. For Harvey that is
`harvey-ahe-project/campaign-artifacts/<campaign name>/`.

```text
campaign_manifest.json        campaign identity hashes (section 2.6)
campaign_amendments.json      round-count extensions, if any (section 2.6)
campaign_state.json           written by every command and by `ahe status`
stages/<stage name>/
  stage.json                  status, semantic inputs, attempts, output hashes
  attempts/NNNN/
    attempt.json
    error.json                type, message, traceback (failed attempts)
    model-preflight.json      meta stages: exploration, debugger
    source-explorer/, web-explorer/   Explorer runtime folders (exploration)
    recovery/                 evolve: saved uncommitted work of the previous attempt
checkpoints/C<n>/
  checkpoint.json             parent, workspace commit, file hashes, validation report
  harness/                    read-only harness copy; change_manifest.json for n >= 1
evaluations/C<n>-train/ | C<n>-validation/ | C<n>-final-test/
  run_manifest.json
  units/<unit_id>/unit.json, complete.json, attempt-<k>/...
  runtime/<unit_id>/attempt-<k>/runtime_manifest.json, model_calls/call-NNNN/
  runtime/model-preflights/attempt-*/model-preflight.json
  adapter_result.json, evaluation_result.json
evidence/C<n>-train/
  evidence_manifest.json, overview.md, metrics.json
  tasks/<task_key>.json
  readable/<task_key>/trial-<k>/trace.md, deliverables/<path>.md   (Harvey)
explore_skills/
  source-exploration/SKILL.md, web-research/SKILL.md, skills_manifest.json
debugger/C<i>/
  summary.json, overview.json (same content), overview.md
  tasks/<task key>/request.json, result.json, submitted_diagnosis.json, runtime/
history/history.json, history.md
workspaces/C<i>/attempt-NNNN/  git repository Evolve edits
evolve/C<i>/
  attempt-NNNN/model-preflight.json, candidate_validation.json,
               evolve_result.json, historical_restores.json, runtime/
  validation.json             written by round.<i>.validate
attribution/C<i>/transition.json
selection/evolution_closed.json, selection.json
final/final_test.json, selected-harness/
reports/                      created, not written by the runner today
```

Notes:

- The test split folder is named `C<n>-final-test`, while the stage is named
  `evaluation.C<n>.test` (`_evaluation_dir` in `controller.py`).
- Debugger task keys are `task-` plus 20 hex characters from a hash of the
  task ID and its evidence detail hash (`_debugger_task_key`).
- For Harvey, a unit attempt folder holds `result.json`, `trace.jsonl`,
  `run/output/` (the graded deliverables), `grading/` (LAB score files such as
  `scores_dual.json`), and `infrastructure_error.json` if it failed for
  infrastructure reasons.

### 4.2 A meta-agent runtime folder

Each meta agent run writes a `runtime/` folder (for example
`evolve/C1/attempt-0001/runtime/`, `debugger/C1/tasks/<key>/runtime/`,
`stages/exploration/attempts/0001/source-explorer/`):

| File or folder | Content |
|---|---|
| `system_prompt.md` | Exact prompt sent, with the supplement |
| `agent.yaml` | Generated NexAU agent config: tools, middleware, model settings |
| `model_calls/call-NNNN/` | `request.json`, then `response.json` or `error.json` |
| `tool_outputs/` | Full copies of tool outputs that were shortened, and `shell-<id>/` folders with `command.txt`, `stdout.txt`, `stderr.txt` |
| `sandbox/` | NexAU work folder; loaded skills go to `sandbox/.skills/` |
| `scratch/` | Debugger only: its writable notes folder |
| `skills/` | Evolve only: read-only copies of the two Explore skills |
| `final_response.txt` | The agent's last answer |
| `conversation_trace.json`, `nexau_spans.json` | Full conversation and NexAU trace |

### 4.3 What to open first

When a round finishes or stops:

1. `campaign_state.json` (or `ahe status`): checkpoints, `blocked_stages`,
   `interrupted_stages` with `last_error`.
2. For a failed stage: `stages/<name>/stage.json`, then
   `stages/<name>/attempts/NNNN/error.json` (has the traceback).
3. `evidence/C{i-1}-train/overview.md`: scores per task.
4. `debugger/C{i}/overview.md`: one line per task, including "diagnosis
   unavailable" lines.
5. `checkpoints/C{i}/harness/change_manifest.json`: what Evolve changed and
   what it predicted.
6. `evolve/C{i}/attempt-NNNN/runtime/final_response.txt` and
   `conversation_trace.json`: how Evolve worked.
7. `attribution/C{i}/transition.json`: what improved and what regressed.
8. `history/history.md`: the whole campaign so far.

For one bad trial, open `evidence/C<n>-train/tasks/<task_key>.json` and the
readable views it lists. Model-call errors are in `model_calls/*/error.json`.

---

## 5. The meta agents

There are four meta-agent roles. All are NexAU agents built by
`NexAUMetaBackend._run_agent` in `src/ahe_runner/meta/nexau_backend.py`.

### 5.1 Common machinery

**Prompt.** Each role has a core prompt in `src/ahe_runner/meta/prompts/`.
If a supplement is configured, the runner appends:

```text
---
## Protected project domain supplement
The operator supplied the following append-only domain guidance. ...
<supplement text>
```

| Role | Prompt file | Supplement key | Harvey supplement |
|---|---|---|---|
| Source Explorer | `source_explorer.md` | `source` | `harvey-ahe-project/meta_supplements/source.md` |
| Web Explorer | `web_explorer.md` | `web` | `harvey-ahe-project/meta_supplements/web.md` |
| Debugger | `debugger.md` | `debugger` | `harvey-ahe-project/meta_supplements/debugger.md` |
| Evolve | `evolve.md` | `evolve` | `harvey-ahe-project/meta_supplements/evolve.md` |

Do not put Jinja template markup (`{{ }}`, `{% %}`) in a supplement (AHE-009).

**Model settings.** Each role uses `models.meta`, with `temperature` and
`max_tokens` replaced by `meta.roles.<role>` when set (`_role_settings`). The
Explorers share the `explorers` entry. NexAU retries are off
(`max_retries: 0`, `retry_attempts: 1`); streaming is off.

**Preflight.** Each meta stage attempt first runs a model preflight with the
role's settings: catalog check, a "Reply with OK only." probe, and a probe
with a random fake model that must fail (`meta/preflight.py`). The result is
saved as `model-preflight.json`.

**Tools.** Tool schemas are in `src/ahe_runner/meta/tool_descriptions/`.
Tool groups (`nexau_backend.py`):

| Group | Tools |
|---|---|
| Read | `read_file`, `search_file_content`, `glob`, `list_directory` |
| Write | `write_file`, `replace`, `delete_path` |
| Shell | `run_shell_command` (only if `meta.shell.backend: docker`) |
| Web | `web_search`, `web_read` |
| Git | `git_status`, `git_diff`, `git_commit` |

The read, write and shell tools are NexAU's built-in tools (the same tools as
the original AHE) wrapped by a path guard (section 5.7).

**Roots.** A *root* is a named folder an agent may use. Paths can be written
as `root:relative/path`, as absolute paths, or relative to the role's default
root. Every agent also gets two reserved read-only roots:

- `tool_outputs`: the run's `runtime/tool_outputs/`, where shortened outputs
  are saved in full;
- `loaded_skills`: `runtime/sandbox/.skills/`, only for agents with skills.

The agent's first message lists every root with its absolute path.

**Middleware.** In this order (`_run_agent`):

1. `DebuggerAccessRecorderMiddleware` (Debugger only). Records which evidence
   lines the debugger saw, for the citation gate.
2. `AheLongToolOutputMiddleware`. NexAU's `LongToolOutputMiddleware`, with
   one change: it measures and saves the tool's text field, not a JSON dump,
   so the saved copy keeps real line breaks. A long output is shortened to a
   head and a tail. The full output is saved under `tool_outputs/`. The tools
   `LoadSkill` and the role's stop tool are never shortened.
3. `AheContextCompactionMiddleware`. NexAU's `ContextCompactionMiddleware`
   with `auto_compact: true`, `threshold: 0.75` and
   `compaction_strategy: tool_result_compaction`. One change: its emergency
   summary call keeps the campaign's temperature and max_tokens, because the
   runner's model client only accepts those.

| Role | Long output: max chars | head / tail lines | head / tail chars | Not shortened | Compaction: max tokens, keep iterations |
|---|---|---|---|---|---|
| Debugger | 10,000 | 50 / 30 | 5,000 / 5,000 | `run_shell_command`, `LoadSkill`, `submit_debugger_diagnosis` | 220,000, 10 |
| Evolve | 10,000 | 50 / 30 | 5,000 / 5,000 | `run_shell_command`, `LoadSkill`, `complete_task` | 220,000, 10 |
| Explorers | 20,000 | 100 / 80 | NexAU default | `LoadSkill`, `complete_task` | 200,000, 15 |

`run_shell_command` is not shortened by the middleware because the tool
shortens its own output: when stdout plus stderr pass 10,000 characters, each
stream keeps its first and last 5,000 characters. The full streams stay in
`tool_outputs/shell-<id>/`.

### 5.2 Source Explorer and Web Explorer

**Purpose.** Write two reusable skills before round 1: one about the
framework and adapter source, one about agent-design research from the web.
Evolve loads both skills in every round.

| Item | Source Explorer | Web Explorer |
|---|---|---|
| Agent name | `AHE-Source-Explore` | `AHE-Web-Explore` |
| Deliverable | `explore_skills/source-exploration/SKILL.md` | `explore_skills/web-research/SKILL.md` |
| Read roots | `framework` (installed NexAU source), `seed_harness` (C0 harness), `manifest`, `method` (`src/ahe_runner/resources/AHE_METHODS_REFERENCE.md`), `adapter_public_N` | `method` |
| Write root (default) | `knowledge` (`explore_skills/`) | `knowledge` |
| Tools | read, write, shell, `complete_task` | read, write, shell, `web_search`, `web_read`, `complete_task` |
| Skills | none | none |
| Iterations | `meta.explore_max_iterations` | same |
| Settings | `meta.roles.explorers` | same |

Both run in parallel. A skill that already passes the check below is reused;
a finished skill folder is made read-only.

**Gates** (`meta/tools.py`):

- *Scaffold first.* Read, search, shell, web and checkpoint tools are refused
  until the agent has written its `SKILL.md` once.
- *Size.* The first write of `SKILL.md` must have at least 1,500 characters.
  Later writes must grow by at least 750 characters until 6,000, and must not
  shrink after that.
- *Write often.* After 24 research calls since the last `SKILL.md` write,
  research tools are refused until the agent writes `SKILL.md` again.
- *Stop tool.* `complete_task` succeeds only if `SKILL.md` has at least 3,000
  characters, front matter with `name` and `description`, and the words
  `architecture`, `configuration`, `tools`, `failure` (Source) or `sources`,
  `patterns`, `evidence`, `recommendations` (Web).

**Web tools.** `web_search` uses DDGS and returns 8 results by default, at
most 12. `web_read` fetches only public addresses, follows at most 5
redirects and returns at most 24,000 characters. Queries and URLs that contain the
adapter's leakage terms (for Harvey: LAB names and training task slugs) are
refused.

**Failure.** If the skill is still incomplete when the agent stops, the stage
fails with `IntegrityError` and is blocked.

### 5.3 Debugger

**Purpose.** For each training task, find the earliest cause of failure and
submit a structured diagnosis with line citations. Evolve reads these.

| Item | Value |
|---|---|
| Agent name | `AHE-Debugger-<task key>`, one per training task |
| Parallel agents | min(`debugger.max_concurrency`, `meta.max_concurrent_model_calls`) |
| Read roots | `evidence` (default root; `evidence/C{i-1}-train`), `raw_evaluation` (`evaluations/C{i-1}-train`) |
| Write root | `scratch` (`debugger/C{i}/tasks/<key>/runtime/scratch`, emptied before each run) |
| Tools | read, write, shell, `submit_debugger_diagnosis` |
| Skills | none |
| Iterations | `debugger.max_iterations`; the prompt sets a hard budget of 20 tool-calling iterations in phases (skim, locate, read, compare, submit) |
| Settings | `meta.roles.debugger` |
| Stop tool | `submit_debugger_diagnosis` |

The first message gives the task key, task state, primary detail file, trial
classes, readable views, the adapter's prompt context and the benchmark
interface.

**Diagnosis contents** (`src/ahe_runner/debugger.py`, `DebuggerDiagnosis`):
`primary_failure_class`, `earliest_failure`, `evidence` (citation,
observation, implication), `evidence_coverage.artifacts_read`,
`paired_comparison`, `root_cause`, `contributing_factors`,
`recommended_changes`, `recurrent_pattern_key`, `confidence`, `falsifier`.

**Citation gate** (`submit_debugger_diagnosis` in `meta/tools.py`). The
submission is refused, with a message the agent can act on, unless:

- it passes the schema, and the task ID matches;
- `artifacts_read` includes `evidence:<task detail path>`;
- every file in `artifacts_read` had lines returned by `read_file` or
  `search_file_content`;
- every citation is a line range, and every cited line was returned in full
  by `read_file` or as a `search_file_content` hit. Byte ranges are always
  refused. Lines cut at 2,000 characters by `read_file` ("... [truncated]"),
  lines next to an omission marker, and shell output do not count;
- every cited file is listed in `artifacts_read`;
- if the task has both a passing and a failing trial, `paired_comparison`
  is `performed` and cites passing and failing trial files of the right
  class; otherwise it is `unavailable`.

Only files under the `evidence` and `raw_evaluation` roots can be cited. The
recorder sees tool output after the long-output middleware, so it counts
exactly what the model saw.

**Failure.** One task's failure does not block the round:

- If the agent ends without a valid diagnosis, or fails with any error that
  is not an infrastructure error, the task's `result.json` gets
  `status: "unavailable"` with the error. `overview.md` tells Evolve to read
  that task's evidence directly.
- `InfrastructureError`, `ConfigurationError` and `StageInterruptedError`
  are raised. An infrastructure error makes the stage `interrupted`; on the
  next attempt, finished diagnoses are reused and unavailable tasks run again.

### 5.4 Evolve

**Purpose.** Make one global change set to the harness, based on the
evidence, commit it, write `change_manifest.json`, and validate it.

| Item | Value |
|---|---|
| Agent name | `AHE-Evolve`, one per round |
| Read roots | `history`, `evidence`, `raw_evaluation`, `debugger`, `checkpoints` (all checkpoints so far), `framework`, `method`, `explore_skills`, `adapter_public_N`, `attribution` (round 2 on), `recovery` (attempt 2 on) |
| Write root (default) | `workspace` (`workspaces/C{i}/attempt-NNNN`) |
| Tools | read, write, shell, web, git, `checkpoint_log`, `checkpoint_diff`, `restore_checkpoint_paths`, `validate_candidate`, `complete_task` |
| Skills | `source-exploration`, `web-research`, `nexau-evolution-guide` (`src/ahe_runner/meta/skills/nexau-evolution-guide/`, from the original AHE, MIT license) |
| Iterations | `meta.evolve_max_iterations` |
| Settings | `meta.roles.evolve` |
| Stop tool | `complete_task` |

The prompt (`evolve.md`) sets a fixed evidence order (history, evidence,
debugger, attribution, checkpoints, skills, workspace, recovery), a hypothesis
for each change, and a 17-step workflow: edit, review the diff, commit,
validate, write the manifest, commit, validate again, check the status is
clean, and finish.

**Special tools.**

- `git_commit` stages all changes and commits them.
- `checkpoint_log` and `checkpoint_diff` read earlier checkpoints, up to the
  parent.
- `restore_checkpoint_paths` copies files back from an earlier checkpoint
  (this is how Evolve rolls back).
- `validate_candidate` runs the generic harness checks with a NexAU load test
  and the adapter's `validate_candidate`, and writes
  `candidate_validation.json` with the HEAD commit.

**Gates.**

- *Durable progress.* If the agent has made more than 36 research calls and
  at least one change since its last commit, research tools are refused
  until it commits. Shell commands that change `git status` count as
  changes.
- *Stop tool.* `complete_task` succeeds only if the workspace is clean,
  `change_manifest.json` is valid and declares exactly the files changed since
  the seed commit, and `validate_candidate` passed at the current HEAD.

**After the agent stops**, the runner checks again: HEAD moved, workspace
clean, manifest present, and a passing validation for the final HEAD. Any
problem raises `IntegrityError`, which blocks the stage.

**Change manifest** (`src/ahe_runner/change_manifest.py`): `schema_version:
2`, `checkpoint`, `parent_checkpoint`, `objective`, `validation`, and
`changes`. Each change has an `id` (`chg-...`), `type` (`new`, `improvement`
or `rollback`), `component`, `paths`, `evidence`, `failure_pattern`,
`mechanism`, `rationale`, `predicted_fixes` and `risk_tasks`.
`predicted_fixes` and `risk_tasks` must not overlap.

### 5.5 Comparison with the original AHE

Original values are from `agents/evolve_agent/evolve_agent.yaml`,
`agents/explore_agent/{source_agent,web_agent}/agent.yaml` and
`agents/evolve_agent/skills/agent-debugger-cli/_source/agent_debugger_core/runtime/agent_config.yaml`
in the original repository.

| Setting | Original AHE | Runner default | Harvey campaign 5 |
|---|---|---|---|
| Debugger iterations | 25 | `debugger.max_iterations` 80 | 25 |
| Debugger temperature / max_tokens | 0.4 / 20,000 | `models.meta` | 0.4 / 20,000 |
| Debugger long output | 10,000 chars, 50+30 lines, 5,000+5,000 chars, shell not shortened | same (fixed in code) | same |
| Debugger compaction | 220,000 tokens, 0.75, keep 10 | same (fixed in code) | same |
| Evolve iterations | 500 | 500 | 500 |
| Evolve temperature / max_tokens | 0.7 / 32,000 | `models.meta` | 0.7 / 32,000 |
| Evolve middleware | same as Debugger | same as Debugger | same |
| Explorer iterations | 200 | 200 | 200 |
| Explorer temperature / max_tokens | 0.2 / 32,000 | `models.meta` | 0.2 / 32,000 |
| Explorer long output | 20,000 chars, 100+80 lines, no bypass | same | same |
| Explorer compaction | 200,000 tokens, 0.75, keep 15 | same | same |
| Agent context limit | 200,000 | `meta.max_context_tokens` 200,000 | 200,000 |

Tools compared:

| Role | Original AHE | Runner |
|---|---|---|
| Debugger | read, write, `replace`, search, glob, list, shell, `web_search`, `web_read`, `complete_task` | read, search, glob, list, write, `replace`, `delete_path`, shell, `submit_debugger_diagnosis` |
| Evolve | same ten tools, skill `nexau-evolution-guide` | read and write tools, shell, web, git, checkpoint tools, `validate_candidate`, `complete_task`; three skills |
| Source Explorer | read, list, glob, search, shell, write, `complete_task` | the same, plus `replace` and `delete_path` |
| Web Explorer | `WebSearch`, `WebFetch`, write, `complete_task` | read and write tools, shell, `web_search`, `web_read`, `complete_task` |

Deliberate differences kept in AHE-016 (recommendations doc, AHE-016
section):

1. A path guard around the file and shell tools.
2. The shell runs in Docker without network.
3. The debugger writes only to its own `scratch` root.
4. Debugger citations are line ranges covered by full reads.
5. A failed debugger gives "unavailable", not a fallback diagnosis.
6. `LoadSkill` and stop-tool results are never shortened.
7. The emergency summary keeps role settings; saved outputs have real line
   breaks.
8. `ag-ui-protocol` is pinned below 1.0.
9. Unknown `name:` path prefixes are left as written.

The web tools use DDGS because the original web tools need a Serper API key.

### 5.6 Role summary

| | Source / Web Explorer | Debugger | Evolve |
|---|---|---|---|
| Runs | once per campaign | once per task per round | once per round |
| Default root | `knowledge` (write) | `evidence` (read) | `workspace` (write) |
| Stop tool | `complete_task` | `submit_debugger_diagnosis` | `complete_task` |
| Main gate | SKILL.md size and topics, research budget | citation coverage | clean commit, manifest, validation, research budget |
| Failure effect | stage blocked | task "unavailable"; round continues | stage blocked |

### 5.7 The path guard

`src/ahe_runner/meta/guarded_tools.py` wraps NexAU's tools. In plain words:

- An agent can only reach files inside its roots. The real path is resolved
  first, so `..` and symlinks cannot escape. If roots overlap, the longest
  matching root decides.
- Writes are allowed only in write roots. The root folder itself and any
  `.git` folder cannot be written or deleted.
- `delete_path` removes one file or one empty folder.
- `glob` patterns must be relative and must not contain `..`.
- Paths with control characters are refused.
- `read_file` refuses binary documents and archives (`.docx`, `.xlsx`,
  `.pptx`, `.pdf`, `.zip`, `.pyc` and others). The error points the agent to
  the readable views and, if a shell exists, to `unzip -p`.
- `read_file` returns at most 2,000 lines per call by default and cuts each
  line at 2,000 characters (NexAU's `read_file`).

A refused call returns an error to the agent. It does not stop the agent.

### 5.8 The Docker shell

With `meta.shell.backend: docker`, each `run_shell_command` call starts a new
container (`docker_shell_argv` in `guarded_tools.py`):

- no network (`--network none`);
- all Linux capabilities dropped, no privilege gain;
- read-only root file system, with a writable `/tmp` of `shell.tmpfs_size`;
- runs as your own user ID, `HOME=/tmp`;
- limits from `shell.memory`, `shell.cpus`, `shell.pids_limit`;
- every root is mounted at its real host path. Read roots are read-only.
  Write roots are writable, but their `.git` folder is mounted read-only;
- the working folder is the default root, or `dir_path` if given;
- after `shell.timeout_seconds` the container is killed;
- exit code 125 means the container could not start.

So shell commands see the same files at the same paths as the file tools,
but cannot reach the network, other host files, or git history.

---

## 6. Evidence and verification

### 6.1 What the adapter provides

After each training evaluation, the controller calls
`adapter.materialize_training_evidence`. The adapter writes an evidence
folder and returns an `EvidenceBundle`. The folder holds:

- `evidence_manifest.json` (schema 3): checkpoint, supervision policy,
  whether true task IDs are shown, the source evaluation (path, ID, hashes),
  metrics equal to the evaluation summary, and one entry per task (task key,
  task ID, state, detail path, detail hash, trial count);
- one task detail file per task, with one entry per trial: unit ID, trial
  number, seed, outcome, score, termination reason, diagnostic class
  (`passing`, `failing`, `other`), `artifacts`, and `readable_artifacts`.

`artifacts` point into the evaluation folder. Each trial must list its trace
and its result (or evaluator) file. `readable_artifacts` point into the
evidence folder.

### 6.2 How it is verified

`verify_evidence_bundle` (`src/ahe_runner/evidence.py`) checks:

- the manifest hash, schema and checkpoint;
- the source evaluation is the same one the controller recorded;
- metrics equal the evaluation summary;
- every task and every trial of the evaluation is covered, with no
  duplicates;
- each trial matches its evaluation unit (unit ID, seed, outcome, score,
  termination reason, diagnostic class);
- every artifact exists and matches its SHA-256 hash, inside its root;
- the trial lists its trace and result artifacts.

The evaluation itself is checked first by `verify_evaluation_output`
(`src/ahe_runner/evaluation.py`): run manifest, `unit.json` and
`complete.json` for every unit, full task-by-trial coverage, a consistent
primary metric, and hashes for every file except `adapter_result.json` and
`evaluation_result.json`. The controller also asks the adapter to verify its
own result again (`verify_evaluation`) and requires the same answer.

Any mismatch is an `IntegrityError`.

### 6.3 Readable views

Harvey adds readable views (`harvey-ahe-project/harvey_ahe/readable.py`):

- `readable/<task>/trial-<k>/trace.md`: the trial's `trace.jsonl`, one
  section per line ("## jsonl line N"), with each event decoded and wrapped at
  1,000 columns.
- `readable/<task>/trial-<k>/deliverables/<path>.md`: each graded deliverable
  as text, using LAB's `read_file_as_text` (pandoc for `.docx`, pandas for
  `.xlsx`).

Why they exist:

- One `trace.jsonl` line can be about 30,000 characters. `read_file` cuts
  lines at 2,000 characters, and the debugger cannot cite a cut line.
- `.docx`, `.xlsx` and `.pptx` files are zip archives. Read as text they are
  noise. In campaign 4 this filled debugger contexts (section 8.3).

Readable views are listed as `readable_artifacts`, so they are hashed and can
be cited by the debugger like any evidence file.

---

## 7. Candidates, attribution and selection

### 7.1 No accept or reject per round

The runner does not compare a candidate with its parent to decide whether to
keep it. Each round:

1. Evolve commits a candidate and validates it.
2. `round.{i}.validate` runs the generic harness checks and the adapter's
   `validate_candidate` again. If the candidate is invalid, the stage raises
   `CandidateValidationError` and is blocked.
3. `round.{i}.freeze` copies the candidate to `checkpoints/C{i}`.
4. C{i} is evaluated and becomes the parent of round `i+1`, even if it scored
   lower.

Rollback is Evolve's decision in the next round. The evolve prompt says there
is no automatic score-based rollback. Evolve reads the history and
attribution, decides per change whether to keep, improve or roll back, and
uses `restore_checkpoint_paths` to bring back older files. Such changes have
`type: rollback` in the manifest.

### 7.2 Attribution

`round.{i}.attribute` calls `adapter.compare(before, after)` on the training
summaries of C{i-1} and C{i}. It then matches the result with the change
manifest (`write_transition` in `src/ahe_runner/history.py`):

- per change: predicted fixes seen, predicted fixes missing, risks seen, hit
  rate, and a verdict: `HARMFUL` (a risk regressed and no fix was seen),
  `MIXED`, `EFFECTIVE` (all predicted fixes improved), `PARTIALLY_EFFECTIVE`
  or `INEFFECTIVE`;
- overall: `harmful`, `mixed`, `effective`, `partially_effective` or
  `ineffective`;
- improvements nobody predicted, and regressions nobody listed as a risk.

The file says this is a heuristic for the combined candidate, not a causal
measurement of each change.

For Harvey, `compare` marks a task improved or regressed when its score moved
at all. There is no noise threshold. It also reports the change in the dual
all-pass rate.

### 7.3 Selection

After the last round, every checkpoint C0..CN is evaluated on the validation
split. The `selection` stage picks the checkpoint with the highest
`adapter.selection_key(summary)`. Ties go to the earliest checkpoint
(`_selection_action` in `controller.py`). `selection/selection.json` records
the rule and all validation summaries. There is no minimum-improvement
threshold.

For Harvey (`harvey_ahe/adapter.py`):

- primary metric: `mean_criterion_pass_rate`. A trial's score is the mean of
  the two judges' fractions of rubric criteria passed. The task score and the
  primary metric are means over trials and tasks;
- selection key: (`mean_criterion_pass_rate`, `mean_dual_all_pass_rate`). The
  dual all-pass rate breaks ties.

### 7.4 Final test

`run-final-test` evaluates C0 and the selected checkpoint on the test split.
`final.complete` copies the selected harness to `final/selected-harness/`,
checks the copy's hashes against the checkpoint, and writes
`final/final_test.json` with both test summaries. Nothing evolves after the
test.

---

## 8. Errors and troubleshooting

### 8.1 Error classes

All are in `src/ahe_runner/errors.py` and subclass `AHERunnerError`.

| Class | Meaning | Effect on a stage | What to do |
|---|---|---|---|
| `ConfigurationError` | Config, plug-in or environment is wrong (bad YAML, missing env var, unknown key, missing Docker image, missing `ddgs`) | Before any stage: nothing recorded. Inside a stage: the stage is blocked | Fix the config or environment. If a stage was blocked, start a new campaign |
| `InfrastructureError` | Something outside the run failed: timeout, connection error, HTTP 5xx, rate limit, sandbox failure | Retryable stage becomes `interrupted` | Fix the cause (endpoint, Docker, Podman), then rerun the same command |
| `IntegrityError` | A stored artifact does not match its record, a model answered with the wrong label, the campaign identity changed, or a meta agent did not deliver its contract | Stage blocked | Investigate. Never edit artifacts to make it pass. A changed identity needs a new artifacts folder |
| `CandidateValidationError` | The evolved harness failed validation in `round.{i}.validate` | Stage blocked | Read `evolve/C{i}/validation.json`. Start a new campaign |
| `StageInterruptedError` | A retryable attempt failed for infrastructure reasons and was saved | Stage `interrupted` | Rerun the command or `ahe resume` |
| `StageBlockedError` | A stage cannot continue | Stage `blocked` | Read `error.json`. If every attempt failed on infrastructure, use `ahe retry-blocked-stage` (section 2.6). Otherwise a new campaign is needed (AHE-011) |

Exit code 2 with a JSON error on stderr means one of these. A Python
traceback means an unexpected error, for example `--round` out of range or a
missing earlier checkpoint.

### 8.2 Before you launch

- Run `conformance` and `validate`; both must report `valid: true`.
- Check the shell image: `docker image inspect <meta.shell.image>`. A missing
  image is found only when the first meta stage starts, and blocks it.
- Check the endpoint is up and lists the configured models (preflights run at
  each stage; see `docs/OPERATOR_GUIDE.md`).
- For Harvey: `podman image inspect lab-sandbox:latest` and `pandoc
  --version`.
- Use a new `artifacts_dir` for any change to code, prompts, supplements or
  config.

### 8.3 Past incidents

From the runbook and the recommendations doc:

| Symptom | Cause | Fix or workaround |
|---|---|---|
| Campaign 1, round 2: Evolve context reached 617,681 tokens and the stage stopped | Old `search_text` tool had no output budget; searches hit raw model-call logs (AHE-010) | A per-call cap at the time; since AHE-016, NexAU tools with the long-output middleware |
| A meta stage is `blocked` after a non-infrastructure error, with attempts left | By design only infrastructure errors retry (AHE-011) | Keep the old campaign as evidence, start a new one |
| Campaign 3: one `list_files` call returned 274,020 characters into Evolve | No output budget on that tool (AHE-014) | Backstop middleware in campaign 4; replaced by the original middleware in AHE-016 |
| Judge load probe: 120 calls at once, 30 failed with HTTP 500 | Upstream HTTP 429: the Copilot service refuses large bursts, and nothing queued calls (AHE-015) | `transport.max_concurrent_requests: 24`, Harvey judge cap 18 |
| Campaign 3 final test: models were listed but chat calls hung for 300 s | An orphaned VS Code extension host behind the endpoint | Relaunched; the endpoint now runs in tmux session `copilot-host` on the server |
| Campaign 9: 52 evaluated-agent calls hung until the 300 s endpoint timeout, each failing its whole trial | Upstream stalls specific to gpt-5.6-sol (2.6% of its calls; under 0.1% for other models), mostly in calls 2-9 of a trial | `transport.hung_call_retries` resends the call (AHE-022) |
| Campaign 12, C4 train: 119 trials failed with "Connection error", then the resume loop used up the stage's 3 attempts and it was blocked | The model server's VS Code window ran out of memory after about 46 hours and closed port 3141 | Restart the server, then `ahe retry-blocked-stage` and `ahe resume`; a pre-stage command restarts the window before it gets that large (AHE-026) |
| Campaign 4, round 1: debugger submissions refused for uncovered citations | Citing single-line trace records that were not fully read | Recoverable inside the run; readable views and line-only rules (AHE-016) make this clearer |
| Campaign 4, round 1: Evolve `complete_task` refused, "predicted_fixes and risk_tasks overlap" | A task listed as both a predicted fix and a risk | Recoverable inside the run; the agent fixed its manifest |
| Campaign 4, round 2: two debuggers stopped with "context remains above the configured compaction target", stage blocked | They read `.docx`/`.xlsx` files as text (about 36,000 tokens each); old compaction stopped the agent | AHE-016: binary refusal in `read_file`, readable views, original middleware, non-blocking debugger failures |

### 8.4 Where to look

| Question | File |
|---|---|
| Which stage failed and why | `campaign_state.json`, `stages/<name>/attempts/NNNN/error.json` |
| Did the endpoint refuse or time out | `.../model_calls/call-NNNN/error.json` |
| Were calls queued | `slot_wait_seconds` in `response.json` / `error.json` |
| Was a tool output shortened | `runtime/tool_outputs/` of that agent |
| What a shell command did | `runtime/tool_outputs/shell-<id>/command.txt`, `stdout.txt`, `stderr.txt` |
| Why a debugger task is unavailable | `debugger/C{i}/tasks/<key>/result.json` (`error`) |
| Why a Harvey unit failed | `evaluations/.../units/<unit>/attempt-<k>/infrastructure_error.json` |

---

## 9. Writing an adapter for a new benchmark

Read `docs/ADAPTER_GUIDE.md` for the full contract, and use
`ahe init-project` for a working template. Harvey's adapter
(`harvey-ahe-project/harvey_ahe/adapter.py`) is a complete real example.

An adapter subclasses `BenchmarkAdapterV2` (`src/ahe_runner/adapter.py`). It
receives `project_root` and `settings` (`project.adapter_settings`).

| Method | Called by | Must do |
|---|---|---|
| `identity()` | all stages | Return name, version and settings identity; part of every stage's inputs |
| `benchmark_interface()` | controller start | Describe how the harness receives tasks and which protected actions exist |
| `split(name)` | controller start | Return the task IDs of `train`, `validation` or `test`. Splits must not overlap |
| `evaluate(request, runtime)` | `evaluation.*` | Run every task times trial with sessions from `runtime`, write the evaluation folder, return an `EvaluationResult`. Raise `InfrastructureError` for infrastructure failures, never a low score |
| `verify_evaluation(request)` | `evaluation.*` | Rebuild the result from disk; must equal what `evaluate` returned |
| `materialize_training_evidence(request)` | `evidence.*` | Write the evidence folder (section 6) and return an `EvidenceBundle` |
| `validate_candidate(request)` | `validate_candidate` tool, `round.*.validate` | Benchmark-specific checks of an evolved harness; never run held-out tasks |
| `compare(before, after)` | `round.*.attribute` | Return a `TransitionSummary`: primary delta, improved, regressed and unchanged task keys |
| `selection_key(summary)` | `selection` | Return a sortable tuple; higher wins |
| `meta_prompt_context()` | meta agents | Describe the agent's role, objective, evidence, failure taxonomy, protected rules, research scope, leakage terms |

The older `BenchmarkAdapter` (version 1) has `evaluate(request)` without a
runtime and no `benchmark_interface`.

---

## 10. Known differences from the original AHE

Records are in `docs/HARVEY_INTEGRATION_UPSTREAM_RECOMMENDATIONS.md`. LAB
issues are in `docs/HARVEY_LAB_BENCHMARK_ISSUES.md`.

| ID | Issue | Status on this branch |
|---|---|---|
| AHE-001 | Evaluated sessions skipped the model preflight | Fixed (`4a4e57b`) |
| AHE-002 | Skill helper files are not recognized as skill components | No patch; Harvey seed has no skills |
| AHE-003 | Harness folders can shadow protected Python modules | Harvey `validate_candidate` rejects them |
| AHE-004 | `ahe-runner` cannot be a uv dependency (NexAU git pin) | Worked around in Harvey `pyproject.toml` |
| AHE-005 | No benchmark-owned execution environment for harness tools | Harvey sandbox handle (`harvey_ahe.workspace`) |
| AHE-006 | Passing session resources through context is unsafe | `AgentSessionRequest.session_resources` in `runtime.py` |
| AHE-007 | Import scan misreads `from pkg import name` | No patch; not triggered by Harvey |
| AHE-008 | Skills were copied into the operator's current folder | Per-run sandbox work folder |
| AHE-009 | Supplements are rendered as Jinja | No patch; supplements avoid markup |
| AHE-010 | `search_text` had no output budget | Capped; later replaced by AHE-016 tools |
| AHE-011 | Blocked stages cannot be retried | Only infrastructure-blocked stages: `ahe retry-blocked-stage` (AHE-026). Otherwise start a new campaign |
| AHE-012 | Independent evaluations run one after another | No patch (performance only) |
| AHE-013 | No tool-output safety net, no shell | Addressed by AHE-014 and AHE-016 |
| AHE-014 | `list_files` had no output budget | Backstop middleware; replaced in AHE-016 |
| AHE-015 | No endpoint-wide limit on calls in flight | `transport.max_concurrent_requests` |
| AHE-016 | Meta agents differed from the original agents | Original tools, middleware, settings, skill (`6f9d941`); campaign 5 is the live check |

---

## 11. Glossary

| Term | Meaning |
|---|---|
| Adapter | The benchmark plug-in. It runs tasks, scores them and writes evidence |
| Artifact | Any file the runner or adapter writes; most are hashed |
| Attribution | The comparison of a checkpoint with its parent on training tasks, matched to the change manifest |
| Campaign | One full run with one config and one artifacts folder |
| Candidate | The harness in an Evolve workspace before it is frozen |
| Change manifest | `change_manifest.json`: the list of changes Evolve made, with evidence and predictions |
| Checkpoint | A frozen, read-only, hashed harness: C0 is the seed, C{i} the result of round `i` |
| Citation | A debugger reference to lines of an evidence file, e.g. `evidence:tasks/x.json` lines 10-20 |
| Compaction | Shortening old turns when the context gets close to its limit |
| Default root | The root used for relative paths, and the shell's working folder |
| Diagnostic class | `passing`, `failing` or `other`, set per trial by the adapter |
| Evidence | The adapter's per-task view of a training evaluation, given to the meta agents |
| Gate | A check in a tool that refuses the call until a condition holds |
| Harness | The files that define the evaluated agent and that Evolve may change |
| Interrupted | Stage status: an attempt failed for infrastructure reasons or the process died; rerun to continue |
| Blocked | Stage status: the stage will not run again in this campaign |
| Meta agent | An agent that improves the harness: Source Explorer, Web Explorer, Debugger, Evolve |
| Preflight | Model probes before a stage: catalog, a real call, a fake-model call |
| Readable view | A text version of a trace or deliverable, made for reading and citing |
| Root | A named folder a meta agent may read or write |
| Round | One Debugger, Evolve, validate, freeze, evaluate and attribute cycle that makes one checkpoint |
| Selection | Choosing the best checkpoint on the validation split |
| Split | A fixed set of task IDs: `train`, `validation` or `test` |
| Stage | A durable, named step with recorded inputs, attempts and output hashes |
| Stop tool | The tool that ends a meta agent's run if its checks pass |
| Supplement | Project Markdown appended to a meta-agent prompt |
| Supervision policy | What the evidence reveals, e.g. Harvey's `dense` (full traces and grades, true task IDs) |
| Trial | One run of a task; trials differ by seed |
| Unit | One task times one trial; the smallest piece of an evaluation |
| Workspace | The git repository where Evolve edits the candidate |
