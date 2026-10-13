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
| `project.adapter_settings.grading_parallel` | 1 for campaigns 1 and 2; 6 (LAB's own default) for campaign 3, approved 2026-10-13 after the timing review in section 5b; 50 from campaign 4 on (adapter limit raised from 16 to 100), approved 2026-10-13, see section 5d |

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

## 5a. Campaign log: `harvey-lab-first-campaign` (stopped at round 2)

Run phase by phase. The operator approved each command.

### Round 1: `ahe run-round --round 1` (2026-10-13 07:02-07:48 UTC, exit 0)

Detached; log in `campaign-artifacts/run-round-1.log`. Every stage completed
on its first attempt: baseline freeze, C0 train evaluation and evidence,
exploration, debugger, evolve, candidate validation, freeze, C1 train
evaluation and evidence, and attribution.

Criteria passed, out of the task's total (judge 1 = claude-sonnet-5,
judge 2 = gpt-5.5; 1 trial per task):

| Training task | C0 j1 | C0 j2 | C1 j1 | C1 j2 |
|---|---|---|---|---|
| banking-finance/identify-issues-in-officer-certificate | 12/35 | 12/35 | 16/35 | 16/35 |
| corporate-ma/compare-credit-agreement-to-commitment-letter | 23/40 | 26/40 | 28/40 | 30/40 |
| energy-natural-resources/identify-issues-in-tax-equity-flip-partnership-agreement | 17/36 | 18/36 | 15/36 | 17/36 |
| environmental-esg/identify-issues-in-expert-remediation-report | 5/33 | 9/33 | 8/33 | 10/33 |
| real-estate/draft-warranty-deed | 37/38 | 36/38 | 37/38 | 36/38 |
| tax/identify-tax-issues-in-irs-closing-agreement | 8/33 | 8/33 | 7/33 | 8/33 |

| Metric | C0 | C1 |
|---|---|---|
| Primary: mean dual all-pass | 0.0 | 0.0 |
| Mean criterion pass rate | 0.476 | 0.515 |
| Pooled criterion pass rate | 0.491 | 0.530 |
| Terminations | 6 `finish_tool` | 6 `finish_tool` |
| Total assistant turns, 6 trials | 25 | 87 |
| Total agent wall-clock, 6 trials | 464 s | 1108 s |

What C1 changed (`checkpoints/C1/harness/change_manifest.json`):
- A system-prompt protocol: inventory the documents, read them, build a
  checklist in `/workspace/review_checklist.md`, draft from it, then audit
  before finishing.
- A new `coverage-audit` skill.
- A `finish` gate that rejects source-driven deliverables until that
  checklist exists.

All six C1 trials wrote the checklist and loaded the skill. The three
rejected `finish` calls in C1 are this gate working as designed.

Attribution marked all three changes `INEFFECTIVE` (0 of 4 to 6 predicted
fixes). It counts a fix only when a task becomes fully passed, and no task
did; it also records `mean_criterion_pass_rate_delta` = +0.038.

Limits:
- With one trial per task, the +3.9-point criterion change is within
  plausible run-to-run noise.
- The primary metric is all-or-nothing per trial, so it gives
  evolution no signal until a task fully passes.

The `Finish Tool ... execution failed, will continue` lines in the log are
recoverable rejections: three are the Harvey `finish` gate, and one each
comes from the debugger's `submit_debugger_diagnosis` and evolve's
`complete_task`. In every case the agent retried and the stage completed.
The exact messages:
- `finish`: "Not finished: create /workspace/review_checklist.md ...".
- Debugger: "failing paired evidence does not cite verified failing trial
  artifacts". It had cited a task summary instead of trial traces.
- Evolve: "change_manifest.json is invalid ... predicted_fixes and
  risk_tasks overlap".

### Round 2: `ahe run-round --round 2` (2026-10-13 07:56-08:00 UTC, exit 2, stopped)

Log in `campaign-artifacts/run-round-2.log`. C1 train evaluation and
evidence were reused. `round.2.debugger` completed. `round.2.evolve`
attempt 1 failed on its third model call: six parallel `search_text` calls
over the debugger's raw model-call logs returned 617,681 tokens, against a
200,000-token context. The stage was then blocked with no retry path. See
AHE-010 and AHE-011 in
`docs/HARVEY_INTEGRATION_UPSTREAM_RECOMMENDATIONS.md`.

This campaign ends here. Its artifacts are kept unchanged. The operator
chose to repair `search_text` and start a new campaign (section 5b).

## 5b. Campaign log: `harvey-lab-campaign-2-search-cap` (stopped in round 1)

Same pinned inputs, models, judges, budget, and seed as section 3a. One
difference: the AHE-010 `search_text` output cap (branch-local, in
`src/ahe_runner/meta/tools.py`). The protected-runner fingerprint changes,
so this is a separate campaign, run from round 1. The operator approved the
same phased sequence: rounds 1, 2, and 3, then `run-final-test`.

What happened:
- **08:34 UTC, endpoint outage.** Round 1 started (log
  `campaign-artifacts/c2-run-round-1.log`). All six C0 trials hung on their
  first model call. The operator's VS Code connection had dropped. Its old
  extension host (pid 3352855) still held port 3141 but answered nothing:
  `/v1/models` timed out. Steps taken:
  1. Stopped the run and removed the six orphaned `lab-sandbox` containers.
  2. With approval, killed pid 3352855.
  3. The operator started the server in the reconnected window (extension
     host pid 16603).
  4. Checked the restarted endpoint: catalog of 31 models, including all four
     campaign models; extension 0.1.0 file hashes unchanged.
  No trial had produced output or grades.
- **08:55 UTC, resume.** `ahe run-round --round 1` marked attempt 1 of
  `evaluation.C0.train` `interrupted` and started attempt 2 (log
  `c2-run-round-1-resume.log`). Model calls and grading resumed normally.
- **About 09:05 UTC, stopped on purpose.** A timing review of campaign 1,
  round 1 (below) showed grading was the bottleneck. The operator chose LAB's
  default grading parallelism. `ahe.yaml` is fingerprinted, so this campaign
  was stopped and campaign 3 started. Artifacts are kept as they are.

Where campaign 1's round 1 time went (46.5 min, measured from stage records
and artifact timestamps):

| Stage | Time | Main cost |
|---|---|---|
| `evaluation.C0.train` (6 trials) | 11.2 min | Agent 1.0-1.6 min per trial. Grading 6.5-9.0 min per trial: about 35 criteria per judge, one call at a time (about 6.5 s each), judge 1 then judge 2 |
| `exploration` | 8.9 min | Source and web explorers already run concurrently |
| `round.1.debugger` (6 tasks) | 4.9 min | Already concurrent; bounded by the slowest debugger |
| `round.1.evolve` | 7.0 min | One agent, 30 model calls |
| `round.1.validate` and `freeze` | 0.2 min | |
| `evaluation.C1.train` (6 trials) | 13.9 min | Agent 1.7-4.9 min, grading 6.3-8.7 min |

## 5c. Campaign log: `harvey-lab-campaign-3-grading-parallel`

Same as campaign 2 (section 5b), including the AHE-010 repair, except that
`grading_parallel` is 6 instead of 1. Each judge grades up to six rubric
criteria at once. LAB's own `run_eval.py` default is 6. Every criterion is
an independent, stateless judge call, and LAB returns verdicts in rubric
order, so the scores do not depend on this setting. Offline test:
`tests/test_harvey_grading.py::test_parallel_criteria_grading_matches_serial_in_rubric_order`.
Expected effect: about 6 fewer minutes per evaluation. The cost is up to 36
concurrent judge requests during a 6-trial evaluation.

Verification before launch, at commit `a565e2b`: the new test passes; the
six Harvey test files pass 101/101; the full suite passes 180/180; ruff
check and format are clean for the changed test.

### Round 1: `ahe run-round --round 1` (2026-10-13 09:07-09:48 UTC, exit 0)

Detached; log in `campaign-artifacts/c3-run-round-1.log`. Every stage
completed on its first attempt. Total time was 41.6 minutes; campaign 1's
round 1 took 46.5.

| Stage | Campaign 1 | Campaign 3 |
|---|---|---|
| `evaluation.C0.train` | 669 s | 567 s |
| `exploration` | 536 s | 668 s |
| `round.1.debugger` | 292 s | 482 s |
| `round.1.evolve` | 419 s | 222 s |
| `round.1.validate` and `freeze` | 13 s | 13 s |
| `evaluation.C1.train` | 835 s | 519 s |

Parallel grading worked. Grading took 1.0-1.6 minutes per C0 trial and
1.2-1.8 minutes per C1 trial, down from 6.3-9.0. There were no grading
errors, judge-input-limit failures, or rate-limit errors. Each evaluation
now waits mainly on the slowest agent: corporate-ma took 457 s in C0 and
398 s in C1. The meta-agent stages were slower or faster than in campaign 1
for reasons unrelated to this change (model latency, and the different
amounts of work each agent chose to do).

Criteria passed, out of the task's total (judge 1 = claude-sonnet-5,
judge 2 = gpt-5.5; 1 trial per task). The judges never disagreed on all-pass.

| Training task | C0 j1 | C0 j2 | C1 j1 | C1 j2 |
|---|---|---|---|---|
| banking-finance/identify-issues-in-officer-certificate | 15/35 | 15/35 | 17/35 | 17/35 |
| corporate-ma/compare-credit-agreement-to-commitment-letter | 32/40 | 31/40 | 30/40 | 31/40 |
| energy-natural-resources/identify-issues-in-tax-equity-flip-partnership-agreement | 21/36 | 21/36 | 14/36 | 15/36 |
| environmental-esg/identify-issues-in-expert-remediation-report | 8/33 | 8/33 | 4/33 | 4/33 |
| real-estate/draft-warranty-deed | 37/38 | 36/38 | 37/38 | 37/38 |
| tax/identify-tax-issues-in-irs-closing-agreement | 7/33 | 6/33 | 8/33 | 8/33 |

| Metric | C0 | C1 |
|---|---|---|
| Primary: mean dual all-pass | 0.0 | 0.0 |
| Mean criterion pass rate | 0.533 | 0.498 |
| Pooled criterion pass rate | 0.551 | 0.516 |
| Terminations | 4 `finish_tool`, 2 `no_tool_calls` | 6 `finish_tool` |
| Total assistant turns, 6 trials | 38 | 47 |
| Total agent wall-clock, 6 trials | 920 s | 942 s |

The C0 harness is identical to campaign 1's, yet C0 scored 0.533 here and
0.476 there. That 5.7-point gap is run-to-run noise. It is larger than the
C0-to-C1 changes in either campaign.

What C1 changed (`checkpoints/C1/harness/change_manifest.json`): one
change, `chg-c1-systemprompt-coverage-review`, to `systemprompt.md` only.
- It adds core rules, including "call `finish` explicitly; do not end with
  a plain message".
- It adds a required workflow: inventory, read the sources, keep a scratch
  coverage ledger with source anchors, draft, then re-check the draft
  against the ledger.
- It adds coverage-discipline guidance.
- The `task.json` line now says only "hidden grading data. Do not read,
  search, or reference it", without the auto-fail warning. No C0 or C1
  trial tool call touched `task.json`.

Effects observed in C1: all six trials called `finish`, and five of six
wrote a coverage ledger in `/workspace` (real-estate did not). Attribution
marked the change `INEFFECTIVE` (0 of 6 predicted fixes, no declared
risks, primary delta 0.0).

The `Finish Tool ... execution failed, will continue` lines were all
recoverable rejections; this time no Harvey `finish` gate was involved.
- Evolve `complete_task`, once: "predicted_fixes and risk_tasks overlap",
  the same as in campaign 1.
- Debugger `submit_debugger_diagnosis`, twice: "debugger citations were not
  covered by nonempty bounded reads or search hits: ...trace.jsonl#lines=10-10"
  (and `#lines=9-9`). These come from the AHE-010 search cap. Each cited
  line was a single 87-172 KB JSON record. The debugger had seen it only
  through 400-character search snippets or byte-capped `read_file` calls,
  and neither counts as reading the whole line. Each debugger then read the
  needed byte range, cited that, and was accepted on the next try. See the
  AHE-010 follow-up note in the recommendations document.

### Round 2: `ahe run-round --round 2` (2026-10-13 09:50-10:12 UTC, exit 0)

Log in `campaign-artifacts/c3-run-round-2.log`. The C1 evaluation and
evidence were reused. Every new stage completed on its first attempt:
debugger 396 s, evolve 344 s, validate and freeze 12 s, C2 train
evaluation 505 s. Total time was about 22 minutes. Grading took 1.1-2.2
minutes per trial. There were no grading, judge-input-limit, or agent
errors, and the judges never disagreed on all-pass. The evolve agent loaded
both Explore skills and could read `attribution/C1`.

| Training task | C1 j1 | C1 j2 | C2 j1 | C2 j2 |
|---|---|---|---|---|
| banking-finance/identify-issues-in-officer-certificate | 17/35 | 17/35 | 15/35 | 16/35 |
| corporate-ma/compare-credit-agreement-to-commitment-letter | 30/40 | 31/40 | 25/40 | 27/40 |
| energy-natural-resources/identify-issues-in-tax-equity-flip-partnership-agreement | 14/36 | 15/36 | 14/36 | 18/36 |
| environmental-esg/identify-issues-in-expert-remediation-report | 4/33 | 4/33 | 4/33 | 5/33 |
| real-estate/draft-warranty-deed | 37/38 | 37/38 | 36/38 | 36/38 |
| tax/identify-tax-issues-in-irs-closing-agreement | 8/33 | 8/33 | 8/33 | 8/33 |

| Metric | C0 | C1 | C2 |
|---|---|---|---|
| Primary: mean dual all-pass | 0.0 | 0.0 | 0.0 |
| Mean criterion pass rate | 0.533 | 0.498 | 0.477 |
| Pooled criterion pass rate | 0.551 | 0.516 | 0.493 |
| Terminations | 4 `finish_tool`, 2 `no_tool_calls` | 6 `finish_tool` | 6 `finish_tool` |
| Total assistant turns, 6 trials | 38 | 47 | 60 |
| Total agent wall-clock, 6 trials | 920 s | 942 s | 903 s |

What C2 changed (`checkpoints/C2/harness/change_manifest.json`): two changes.
- `chg-c2-skill-legal-review-protocol`: a new registered skill,
  `skills/legal-review-protocol/SKILL.md` (added to `agent.yaml`). It
  defines ledger modes for issue-spotting, comparison, and drafting tasks;
  each ledger item must be marked include, rule out, or unresolved; and it
  adds an audit before `finish`. Predicted fixes: 6, risks: 0.
- `chg-c2-systemprompt-skill-routing`: `systemprompt.md` now sends
  non-trivial tasks through that skill, forbids drafting from a short
  "high-salience" list, and requires a complete ledger before drafting.
  Predicted fixes: 4, risks: 2.

Effects observed in C2: all six trials loaded the new skill, wrote a ledger
file, and called `finish`. Attribution marked both changes `INEFFECTIVE`
(0 of 6 and 0 of 4 predicted fixes, no declared risks observed, primary
delta 0.0).

The mean criterion pass rate has fallen by 2-3.5 points per round, and the
assistant turns have risen (38, 47, 60). Each step is still smaller than
the 5.7-point C0 difference between campaigns 1 and 3, so with one trial
per task this is not yet evidence of harm. Validation (3 held-out tasks)
will decide the selected checkpoint.

The one `Finish Tool ... execution failed, will continue` line was evolve's
`complete_task` rejecting `change_manifest.json` once: "predicted_fixes and
risk_tasks overlap", in both changes. This is the same recoverable
rejection seen in round 1 of both campaigns. Evolve fixed the manifest and
completed.

### Round 3: `ahe run-round --round 3` (2026-10-13 10:13-10:30 UTC, exit 0)

Log in `campaign-artifacts/c3-run-round-3.log`. The C2 evaluation and
evidence were reused. Every new stage completed on its first attempt:
debugger 251 s, evolve 382 s, validate and freeze 12 s, C3 train
evaluation 339 s. Total time was about 17 minutes. Grading took 1.0-1.6
minutes per trial, with no grading retries. There were no grading,
judge-input-limit, or agent errors, and the judges never disagreed on
all-pass. The evolve agent loaded both Explore skills and completed on its
first `complete_task`.

| Training task | C2 j1 | C2 j2 | C3 j1 | C3 j2 |
|---|---|---|---|---|
| banking-finance/identify-issues-in-officer-certificate | 15/35 | 16/35 | 13/35 | 13/35 |
| corporate-ma/compare-credit-agreement-to-commitment-letter | 25/40 | 27/40 | 29/40 | 31/40 |
| energy-natural-resources/identify-issues-in-tax-equity-flip-partnership-agreement | 14/36 | 18/36 | 20/36 | 22/36 |
| environmental-esg/identify-issues-in-expert-remediation-report | 4/33 | 5/33 | 7/33 | 7/33 |
| real-estate/draft-warranty-deed | 36/38 | 36/38 | 36/38 | 35/38 |
| tax/identify-tax-issues-in-irs-closing-agreement | 8/33 | 8/33 | 6/33 | 8/33 |

| Metric | C0 | C1 | C2 | C3 |
|---|---|---|---|---|
| Primary: mean dual all-pass | 0.0 | 0.0 | 0.0 | 0.0 |
| Mean criterion pass rate | 0.533 | 0.498 | 0.477 | 0.511 |
| Pooled criterion pass rate | 0.551 | 0.516 | 0.493 | 0.528 |
| Terminations | 4 `finish_tool`, 2 `no_tool_calls` | 6 `finish_tool` | 6 `finish_tool` | 6 `finish_tool` |
| Total assistant turns, 6 trials | 38 | 47 | 60 | 60 |
| Total agent wall-clock, 6 trials | 920 s | 942 s | 903 s | 771 s |

What C3 changed (`checkpoints/C3/harness/change_manifest.json`): three
changes, all aimed at stopping the agent from finishing before a final
audit.
- `chg-c3-systemprompt-audit-routing`: `systemprompt.md` now requires a
  final audit note with sections for documents read, deliverables owed,
  open questions, and checklist dispositions. Predicted fixes: 3, risks: 1.
- `chg-c3-finish-tool-contract`: the `finish` schema
  (`tool_descriptions/finish.tool.yaml`) now requires the deliverable list
  and a verification-note path. Predicted fixes: 2, risks: 1.
- `chg-c3-finish-tool-enforcement`: `tools/finish.py` now rejects `finish`
  unless every listed deliverable exists and is not empty, and the
  verification note exists, has the required headings, records checklist
  dispositions, and names every deliverable. Predicted fixes: 3, risks: 2.

Effects observed in C3: all six trials wrote a final audit note and called
`finish`. The new check rejected the first `finish` in three trials, and
each agent fixed the problem and finished:
- corporate-ma: the audit note did not exist yet;
- real-estate: the deliverables were listed with an `output/` prefix, and the
  note was missing;
- environmental-esg: the note recorded no checklist dispositions.

In corporate-ma and real-estate the agent saved `final-audit.md` in the output
folder, so the judges graded it along with the deliverables.

Attribution marked all three changes `INEFFECTIVE`. No task's primary score
changed, because the all-pass score stayed 0.0 everywhere. No declared risks
were observed. The attribution metadata records a mean criterion pass rate
change of +0.033.

The mean criterion pass rate rose 3.3 points from C2. C3 is still 2.2 points
below C0, and every step remains smaller than the 5.7-point C0 difference
between campaigns. With one trial per task, this is noise-level evidence.

The five `Finish Tool ... execution failed, will continue` lines in the log
are:
- two debugger `submit_debugger_diagnosis` rejections, for the
  environmental-esg trace;
- the three evaluated-agent `finish` rejections above.

The debugger cited trace lines 11 and 14, which are 93 KB and 135 KB long.
Neither was fully covered by a read, which is the same AHE-010 follow-up
behaviour seen in round 1. The debugger was accepted on its third submission.

### Final test: `ahe run-final-test` (2026-10-13 10:33-11:45 UTC, exit 0 on the fourth launch)

`run-final-test` closes evolution, evaluates C0-C3 on the 3 validation tasks,
selects a checkpoint, and then evaluates C0 and the selected checkpoint on the
3 test tasks.

**Endpoint outages.** The run was interrupted three times. Each time the
operator's VS Code Remote connection dropped and VS Code started a new
extension host on reconnect. The old host kept port 3141 open with no window
attached, so `/v1/models` still answered from its cache but chat requests hung
until the extension's 300-second timeout (`Model request timed out after
300000 ms`). All failures were infrastructure failures, and AHE kept every
completed unit.

| Launch | Log | Outcome |
|---|---|---|
| 1 | `c3-run-final-test-attempt1.log` | C0 and C1 validation completed. All 3 C2 validation trials timed out (10:42-10:47). Orphaned host 16603. |
| 2 | `c3-run-final-test-attempt2.log` | C2 validation completed (unit attempt 2). C3 validation lost 1 trial to an agent timeout and 2 to grading timeouts. Orphaned host 157456. |
| 3 | `c3-run-final-test-attempt3.log` | C3 validation failed again (2 grading, 1 agent). Orphaned hosts 196033 and 206420 after reconnects at 11:27 and 11:33. |
| 4 | `c3-run-final-test.log` | C3 validation (unit attempt 3), selection, and the test completed with no errors. |

Recovery each time:
1. With approval, kill the orphaned extension host(s).
2. The operator ran `GH Copilot Server: Start Server` in the current window.
3. Confirm the port owner with `ss` and send one `gpt-5.4-mini` "Reply with
   OK only." request.
4. Rerun `run-final-test`.

The extension files were unchanged (`out/server.js` SHA-256 still begins
`9090e9eb`). No valid score was retried. Planned follow-up: run the endpoint
from a VS Code instance on this host, so it no longer depends on the laptop
connection.

Successful stage times:
- validation: C0 237 s, C1 235 s, C2 258 s, C3 242 s;
- C0 test: 170 s.

**Validation** (3 held-out tasks, 1 trial each; criteria passed, judge 1 /
judge 2):

| Validation task | C0 | C1 | C2 | C3 |
|---|---|---|---|---|
| bankruptcy-restructuring/identify-issues-in-plan-of-reorganization | 6/7 of 39 | 6/9 of 39 | 8/12 of 39 | 8/14 of 39 |
| data-privacy-cybersecurity/identify-issues-in-transfer-impact-assessment | 19/19 of 33 | 12/12 of 33 | 13/15 of 33 | 14/14 of 33 |
| emerging-companies-venture-capital/identify-governance-issues-in-stockholder-agreement | 6/8 of 39 | 10/10 of 39 | 7/8 of 39 | 6/6 of 39 |

| Validation metric | C0 | C1 | C2 | C3 |
|---|---|---|---|---|
| Primary: mean dual all-pass | 0.0 | 0.0 | 0.0 | 0.0 |
| Mean criterion pass rate | 0.307 | 0.271 | 0.291 | 0.287 |
| Pooled criterion pass rate | 0.293 | 0.266 | 0.284 | 0.279 |
| Terminations | 2 `finish_tool`, 1 `no_tool_calls` | 2 `finish_tool`, 1 `no_tool_calls` | 3 `finish_tool` | 3 `finish_tool` |
| Assistant turns, 3 trials | 15 | 26 | 33 | 29 |

There were no grading, judge-input-limit, or agent errors in the completed
units, and the judges never disagreed on all-pass.

**Selection: C0.** The rule is the adapter's selection key (primary, then
mean criterion pass rate), then the earliest checkpoint. Every checkpoint
scored 0.0 on the primary metric, so the mean criterion pass rate decided it,
and C0 was highest at 0.307.

**Test.** The selected checkpoint is the baseline, so AHE evaluated only C0
on the test split (`final/final_test.json`, selected harness SHA-256
`c1d33323…c082`).

| Test task | C0 j1 | C0 j2 | Termination |
|---|---|---|---|
| employment-labor/identify-issues-in-counterparty-motion-brief | 4/23 | 4/23 | `no_tool_calls` |
| healthcare-life-sciences/identify-compliance-issues-in-medicare-enrollment-application-package | 16/39 | 16/39 | `finish_tool` |
| international-trade-sanctions/review-draft-voluntary-self | 9/30 | 10/30 | `finish_tool` |

Test summary for C0:
- primary (mean dual all-pass): 0.0;
- mean criterion pass rate: 0.300; pooled: 0.321;
- 14 assistant turns and 35 tool calls in total.

**Result.** Campaign 3 found no held-out improvement. Training scores dipped
and recovered (C0 0.533, C1 0.498, C2 0.477, C3 0.511). On validation,
C1-C3 were 1.6-3.6 points below C0, which is within noise for 3 tasks with 1
trial each. The evolved checkpoints did make the agent finish cleanly more
often: no `no_tool_calls` terminations in C2 and C3 validation. But they used
about twice as many turns, and that did not raise criterion pass rates.

The primary metric was 0.0 for every trial in every split, so it gave
evolution and selection no signal. Selection fell back to the diagnostic
tiebreaker. Under the current settings (gpt-5.4-mini, 1 trial per task,
all-pass scoring), this result cannot show whether evolution helps.


## 5d. Campaign 4 proposal: `harvey-lab-campaign-4-grading-parallel-50`

Approved by the operator on 2026-10-13, to start after campaign 3 has
finished. `ahe.yaml` and `harvey_ahe/` are part of the campaign fingerprint,
so this change cannot be applied to a campaign that is still running.

Changes from campaign 3:

- The adapter's `grading_parallel` limit is raised from 16 to 100
  (`harvey_ahe/adapter.py`). There was no documented reason for 16; it was a
  safety guard against typos. LAB has no upper limit. The offline tests reject
  0 and 101.
- `grading_parallel` goes from 6 to 50. Tasks have 33-40 criteria, so each
  judge sends its whole rubric at once. The two judges still run one after
  the other for each trial.

Expected load: at most about 240 concurrent judge calls, if all 6 training
trials grade at the same moment (6 trials x up to 40 criteria). The limit is
about 120 for 3-trial validation and test evaluations. Trials usually finish
at different times, so the real peak is lower.

Expected effect: grading drops from 1-2 minutes per trial to roughly the
slowest single call per judge. Each evaluation still waits for its slowest
agent (corporate-ma, 6-7.5 minutes), so it saves less than a minute.

Risk: the Copilot rate limits behind the loopback endpoint are unknown. Retries
multiply load when calls fail: the extension makes up to 3 attempts per
request, LAB's judge up to 6 API attempts, and the adapter repeats grading up
to `grading_attempts` (2) times.

Gate before launch: a one-burst load probe that replays 60 saved judge
requests per judge model (120 at once) from campaign 3's `C2-train` grading
logs. It records only metadata, never prompt text. Pass means all 120 calls
succeed, return the requested model label, and return a parseable verdict. If
the probe fails, stop and decide before launching. Command, run from the
repository root after campaign 3 has finished:

```bash
.venv/bin/python harvey-ahe-project/operator-preflight/judge_load_probe.py \
  --evaluation-dir harvey-ahe-project/campaign-artifacts/harvey-lab-campaign-3-grading-parallel/evaluations/C2-train \
  --per-model 60 \
  --output harvey-ahe-project/operator-preflight/$(date -u +%F)/judge-load-probe-120.json
```

Offline verification on branch `harveybench-gp50`, at commit `f96094b` plus
a probe fix: the eight Harvey test files pass 103/103, and the full suite
passes 182/182. Both runs used the branch's code (`PYTHONPATH` set to the
worktree). ruff check and format are clean for the changed files. pyright is
clean for `adapter.py` and the probe after the fix: the probe no longer passes
its own `httpx` client, because this `openai` build uses `httpx2`; openai's
default limit of 1000 connections covers 120 calls. No test imports the probe,
so the full-suite result also holds after the fix. The probe's request
collection was checked offline against `C2-train`, with no model calls:
60 calls per judge model, all with a parseable baseline verdict. Baseline
latency at `grading_parallel` 6 was p50 6.3 s and p90 9.4 s for
`claude-sonnet-5`, and p50 5.1 s and p90 7.0 s for `gpt-5.5`. Prompts were up
to 11,250 characters.


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
