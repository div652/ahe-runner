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
| LAB commit | `86678eba6fcbc6c03958742c6e9e75737514f513` (equals `lab_commit` in `splits.json` and `splits-v2.json`) |
| Splits | `harvey-ahe-project/splits.json` (campaigns 1-6): 6 train, 3 validation, 3 test tasks. `harvey-ahe-project/splits-v2.json` (campaign 7 onward): 60 train, 15 validation, 15 test tasks, built by `scripts/make_splits_v2.py` (section 5h). Each task is pinned by the sha256 of its `task.json` and documents tree |
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


## 5d. Campaign 4 proposal: `harvey-lab-campaign-4-judge-cap-output-backstop`

Approved by the operator on 2026-10-13, to start after campaign 3 has
finished. `ahe.yaml`, `harvey_ahe/`, and the runner code are part of the
campaign fingerprint, so these changes cannot be applied to a campaign that
is still running. The campaign was first proposed as
`harvey-lab-campaign-4-grading-parallel-50`, renamed
`harvey-lab-campaign-4-gp50-output-backstop` when AHE-014 was added, and
renamed again when the load probe below failed and the judge plan changed.

Changes from campaign 3:

- AHE-014: Evolve and both Explore agents get a long-output safety net. A
  tool result over 64,000 characters is shortened to its first 24,000 and
  last 8,000 characters. The full result is saved in the run's
  `runtime/tool_outputs/` folder, which the agent can read through the
  read-only root `tool_outputs`. `LoadSkill` and `complete_task` are never
  shortened, and the Debugger is not covered. Details in
  `docs/HARVEY_INTEGRATION_UPSTREAM_RECOMMENDATIONS.md`, AHE-014. Offline
  check: the full suite passes 191/191, and campaign 3's 274,020-character
  listing would have shown the agent 32,734 characters.

- The adapter's `grading_parallel` limit is raised from 16 to 100
  (`harvey_ahe/adapter.py`). There was no documented reason for 16; it was a
  safety guard against typos. LAB has no upper limit. The offline tests reject
  0 and 101.
- First plan (superseded by the probe result below): `grading_parallel` goes
  from 6 to 50. Tasks have 33-40 criteria, so each judge sends its whole
  rubric at once. The two judges still run one after the other for each
  trial.

Expected load under the first plan: at most about 240 concurrent judge calls,
if all 6 training trials grade at the same moment (6 trials x up to 40
criteria). The limit is about 120 for 3-trial validation and test
evaluations. Trials usually finish at different times, so the real peak is
lower.

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

Probe result, 2026-10-13 14:10 UTC: **failed the gate.** Record:
`operator-preflight/2026-10-13/judge-load-probe-120.json`. 90 of 120 calls
succeeded (`claude-sonnet-5` 51/60, `gpt-5.5` 39/60). The 30 failures were
HTTP 500 with an empty message. The Copilot Chat log shows the cause:
upstream HTTP 429 (`ChatRateLimited`). In each wave, about 30 new requests
were accepted across both models together and the rest were refused. The
extension retried after 1 and 2 seconds; the third wave again accepted
about 30, and 30 calls gave up. The 90 successful calls returned the
requested model labels and parseable verdicts, and 89 of the 90 verdicts
matched campaign 3's. The Copilot Chat log reports `gpt-5.5` as the dated
snapshot `gpt-5.5-2026-04-23`.

What this means: total judge calls at once equal the number of trials being
graded at the same moment (up to `evaluation.max_concurrency`, 20) times
`grading_parallel`. The evaluated agent's calls share the same Copilot
limit, so a burst of judge calls could also make agent calls fail.

Revised plan, approved by the operator on 2026-10-13:

- New adapter setting `judge_max_concurrent_calls: 24`. It is one shared pool
  of call slots in `harvey_ahe/grading.py` for the whole evaluation process.
  Every judge criterion call takes a slot and waits while none is free,
  whichever trial it belongs to. Model preflights and the filename helper
  are not counted. The default (unset) keeps the old behaviour.
- `grading_parallel: 10` per judge per trial, still limited by the shared
  cap.
- The adapter's upper bound for `grading_parallel` stays at 100.
- Gate before launch: repeat the probe at exactly the cap (12 calls per judge
  model, 24 at once). Pass means all 24 succeed.

Implemented on 2026-10-13: `judge_max_concurrent_calls` in
`HarveyAdapterSettings` (1 to 100, unset by default). The adapter creates one
`threading.BoundedSemaphore` and passes it to every `grade_with_retries`
call; `RecordedCopilotJudge.evaluate_from_file` holds a slot only around the
judge call itself. A rubric item rejected by the LAB-002 input limit makes no
call and takes no slot. `ahe.yaml` now sets `grading_parallel: 10` and
`judge_max_concurrent_calls: 24`. New offline tests: settings 0 and 101 are
rejected; the adapter builds one shared semaphore and passes it to grading;
two trials graded at once with `grading_parallel` 10 and a cap of 3 never
have more than 3 calls in flight; without a cap they exceed 3. The full suite
passes 196/196, and ruff and pyright are clean for the changed files.

Probe at the cap, 2026-10-13 14:23 UTC: **passed the gate.** Record:
`operator-preflight/2026-10-13/judge-load-probe-24.json`. All 24 calls
succeeded (12 per judge model) in 6.1 seconds, with the requested model
labels, parseable verdicts, and the same verdicts as campaign 3. Per-call
time was p50 2.3 s for `claude-sonnet-5` and 2.6 s for `gpt-5.5`. The Copilot
Chat log has no new rate-limit lines; all 180 are from the 14:10 probe. This
probe is one burst. During the campaign, bursts repeat and the evaluated
agent's calls share the same limit, so agent or judge errors in campaign 4
should be checked against the Copilot Chat log for HTTP 429.

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

### Campaign 4 metric, training trials, and model-call queue

Approved by the operator on 2026-10-13, after the probe at the cap passed.

Why: in campaign 3 the primary metric, the LAB dual all-pass rate, was 0 on
every evaluation, because no task passed all of its 33-40 criteria. Each task's
score was that same value, so every round showed no improved or regressed
task, every round was classified "ineffective", and selection fell back to
the tie-break, criterion pass rate, which picked the seed C0. Mean criterion
pass rate on train was C0 0.533, C1 0.498, C2 0.477, C3 0.511.

Noise: the seed harness was byte-identical in campaigns 1-3 and used the same
evaluated model. Its repeated train runs scored 0.476 (campaign 1) and 0.533
(campaign 3), and single tasks moved by up to 0.175. Every round's change in
campaign 3 was smaller than that. The judges are stable (89 of 90 verdicts
repeated in the load probe), so the noise comes from the agent.

Changes:

1. The primary metric becomes `mean_criterion_pass_rate`. For one trial it is
   the mean of the two judges' criterion pass fractions; the primary is its
   mean over trials. The trial score, the task score, the evidence, and the
   Evolve objective switch to it. The LAB dual all-pass rate is still
   reported as `mean_dual_all_pass_rate` and is the selection tie-break. The
   task state text and the passing/failing trial labels stay all-pass based,
   because rate-based states would make every task look unstable from noise
   alone. The Harvey adapter version goes from 1 to 2.
2. Improved and regressed tasks count any change, with no threshold, so some
   flags will be noise.
3. `evaluation.train_trials` goes from 1 to 2. Validation and test stay at 1.
4. AHE-015: a new `transport.max_concurrent_requests: 24` makes every model
   call in the process wait in one shared queue for a free slot, instead of
   being sent and refused by Copilot. It covers the evaluated agent, the
   judges, the filename helper, and the meta agents; model preflights are
   not counted. The judge cap goes from 24 to 18, so judges cannot hold every
   slot and agents always have at least 6. The Copilot Chat logs of campaigns
   1-3 contain no rate-limit lines; only the 120-call burst probe hit the
   limit. A call that Copilot still refuses fails as an infrastructure error,
   so its trial is re-run and never scored low.

Deferred idea (not implemented): a noise threshold for improved and
regressed. Measure how much a task's score moves when nothing changed, then
count a change only if it is about twice that. From the seed's repeat runs
(7 degrees of freedom, so rough), one run of one task varies by about 0.06.
With 2 trials per task, a task's change between two checkpoints varies by
about 0.06, giving a threshold of 0.12 at the 95% level (1.96 x) or 0.10 at
the 90% level (1.645 x). The threshold is part of the campaign fingerprint,
so it cannot change during a campaign.

Implemented on 2026-10-13:

- `harvey_ahe/adapter.py`: `PRIMARY_METRIC` is `mean_criterion_pass_rate`.
  Trial score, stored unit `score`, task score, and trial facts use the dual
  criterion pass rate; `mean_dual_all_pass_rate` is reported for the
  evaluation and each task. `selection_key` is (criterion rate, all-pass
  rate), and `compare` reports `mean_dual_all_pass_rate_delta`. The metric
  description, the Evolve objective, the evidence overview, and
  `benchmark_public/ADAPTER.md` section 2 describe the new metric (that file
  had also named the old primary wrongly, as `primary_all_pass_rate`).
  `ADAPTER_VERSION` is 2.
- AHE-015 in `src/ahe_runner/config.py` and
  `src/ahe_runner/meta/model_client.py` (details in the recommendations doc).
- `ahe.yaml`: `train_trials: 2`, `judge_max_concurrent_calls: 18`,
  `transport.max_concurrent_requests: 24`, and the header comment.
- Tests: the adapter test now checks the criterion-rate primary, task
  scores, trial facts, stored score, all-pass rate, selection key, and that
  `compare` flags any task whose score rises. Three new model-client tests
  cover the shared queue. The full suite passes 199/199. ruff is clean, and
  pyright reports only 2 errors that were already in
  `tests/test_model_access.py`. The shipped `ahe.yaml` loads with the new
  values.

### Model endpoint for campaign 4: server-hosted VS Code

Campaign 3's final test lost the endpoint three times. Each time, the
operator's laptop dropped its VS Code Remote connection, and the orphaned
extension host kept port 3141 but no longer answered chat requests. For
campaign 4 the endpoint runs on the model host itself: VS Code desktop
1.141.0, the same build as the laptop's remote host at the time, with built-in
Copilot Chat 0.69.0. It runs under Xvfb in tmux session `copilot-host` and is
started by `~/copilot-host/start-copilot-host.sh`, with its own profile. It
uses the same GH Copilot Server 0.1.0 code (`out/server.js` `9090e9eb…`,
`out/extension.js` `c7ef05f7…`) on the same `127.0.0.1:3141`, so `ahe.yaml`
does not change. The laptop window's server must stay stopped.

Checks on 2026-10-13 (details and hashes in
`operator-preflight/2026-10-13-server-host/README.md`):

- All four campaign models passed the exact-model and negative-control
  probes. `/v1/models` advertised 31 IDs.
- In a disconnect test, the laptop disconnected and VS Code Remote started a
  new extension host. Every probe request kept returning 200 with
  `gpt-5.4-mini`, and port 3141 stayed with the server-side VS Code.

To restart the server without the viewer, run `DISPLAY=:99 xdotool key F1`,
type `GH Copilot Server: Start Server`, then press Return.

## 5e. Campaign log: `harvey-lab-campaign-4-judge-cap-output-backstop`

Configuration as in section 5d, at commit `b7029c3`. Before launch,
`ahe conformance` and `ahe validate` both returned `valid: true` with only the
AHE-005 warning. Each round runs in tmux session `ahe-c4` on the model host,
one window per round, so a laptop disconnect does not stop it.

### Round 1: `ahe run-round --round 1` (2026-10-13 14:44-15:12 UTC, exit 0)

Log in `campaign-artifacts/c4-run-round-1.log`. Every stage completed on its
first attempt. Total time was 28.4 minutes; campaign 3's round 1 took 41.6.

| Stage | Campaign 3 | Campaign 4 |
|---|---|---|
| `evaluation.C0.train` | 567 s (6 trials) | 286 s (12 trials) |
| `exploration` | 668 s | 439 s |
| `round.1.debugger` | 482 s | 373 s |
| `round.1.evolve` | 222 s | 269 s |
| `round.1.validate` and `freeze` | 13 s | 12 s |
| `evaluation.C1.train` | 519 s (6 trials) | 301 s (12 trials) |

The training evaluations were faster even with twice the trials. Judges
grade 10 criteria at once instead of 6, and the 12 trials run together.
Each evaluation now waits mainly on the slowest agent (corporate-ma, 241 s
in C0 and 254 s in C1).

Load and errors:
- About 1,900 recorded model calls in the two training evaluations and 170
  in the meta-agent stages. There was no `error.json` and no
  `infrastructure_error.json`.
- The AHE-015 queue was used: 133 evaluation calls waited for a slot, at
  most 0.96 s.
- The Copilot Chat log shows 6 `rateLimited` (HTTP 429) responses out of
  2,130 requests. 5 came at 14:45:35-38 during C0 grading (3
  claude-sonnet-5, 2 gpt-5.5) and 1 at 15:09:20 (`gpt-5.4-mini`). The
  extension's same-model retries absorbed all of them; none reached the
  runner.
- The AHE-014 backstop did not trigger: Explore and Evolve saved no
  shortened tool outputs.

Criteria passed (judge 1 = claude-sonnet-5, judge 2 = gpt-5.5), trial 0 then
trial 1. The judges never disagreed on all-pass.

| Training task | C0 j1 | C0 j2 | C1 j1 | C1 j2 |
|---|---|---|---|---|
| banking-finance/identify-issues-in-officer-certificate | 15, 15/35 | 14, 14/35 | 12, 13/35 | 12, 14/35 |
| corporate-ma/compare-credit-agreement-to-commitment-letter | 33, 35/40 | 31, 35/40 | 21, 29/40 | 22, 31/40 |
| energy-natural-resources/identify-issues-in-tax-equity-flip-partnership-agreement | 17, 14/36 | 20, 17/36 | 16, 18/36 | 16, 19/36 |
| environmental-esg/identify-issues-in-expert-remediation-report | 10, 8/33 | 14, 8/33 | 5, 7/33 | 7, 8/33 |
| real-estate/draft-warranty-deed | 37, 36/38 | 36, 34/38 | 34, 37/38 | 35, 37/38 |
| tax/identify-tax-issues-in-irs-closing-agreement | 5, 8/33 | 7, 7/33 | 8, 9/33 | 9, 9/33 |

| Metric | C0 | C1 |
|---|---|---|
| Primary: mean criterion pass rate | 0.529 | 0.483 |
| Mean dual all-pass (tie-break) | 0.0 | 0.0 |
| Pooled criterion pass rate | 0.547 | 0.498 |
| Terminations | 12 `finish_tool` | 12 `finish_tool` |
| Total assistant turns, 12 trials | 57 | 124 |
| Total agent wall-clock, 12 trials | 1,063 s | 1,595 s |

C0 is the same seed harness as in campaigns 1 and 3 (0.476 and 0.533 there).
Within one checkpoint, the two trials of a task differ by up to 0.21
(C1 corporate-ma: 0.538 and 0.750).

What C1 changed (`checkpoints/C1/harness/change_manifest.json`):
- `chg-1`, `systemprompt.md`: a required scratch checklist and a final
  verification note before `finish`.
- `chg-2`, `finish.tool.yaml`: `finish` now requires `deliverables` and a
  `checklist_path`.
- `chg-3`, `tools/finish.py`: `finish` is rejected (up to the existing
  rejection limit) unless the listed deliverables exist and the note exists,
  has four required headings, and names every deliverable.

Effects observed in C1: 7 of 12 trials had `finish` rejected at least once
(9 rejections in all; none in C0), for example a missing
`/workspace/final_check.md` or a note that did not name every deliverable.
All 12 trials still ended through `finish`. The agent took about twice as
many turns.

Attribution: `mixed`, primary delta -0.046. 3 tasks improved and 3
regressed, and none of them were the ones Evolve predicted. All three
changes were marked `INEFFECTIVE`, with hit rates 0/4, 0/2 and 0/4 and no
declared risks observed.

Other recoverable `Finish Tool ... execution failed` lines:
- Debugger `submit_debugger_diagnosis`, 3 times: citations of single-line
  trace records not covered by a full read (the AHE-010 follow-up).
- Evolve `complete_task`, once: "predicted_fixes and risk_tasks overlap".

### Round 2: `ahe run-round --round 2` (2026-10-13 16:10-16:15 UTC, stopped: stage blocked)

Log in `campaign-artifacts/c4-run-round-2.log`. `round.2.debugger` attempt 1
failed with `IntegrityError`. Because it was not an infrastructure error, the
stage is `blocked`, and the runner will not retry it. Campaign 4 ends here;
its artifacts are kept unchanged.

- Four of the six debuggers submitted diagnoses. The real-estate and
  corporate-ma debuggers stopped with "meta-agent context remains above the
  configured compaction target after preserving the latest iteration:
  176783 > 144000" and "196294 > 144000". The target is 0.72 × 200,000
  tokens.
- Cause: in its last turn, the real-estate debugger read the four `.docx`
  deliverables of both trials at once (`read_file`, default 64,000 bytes).
  `read_file` has no binary check. It decodes a `.docx` (a zip archive) with
  `errors="replace"`, so each file came back as about 145,000 characters of
  unreadable text, measured at about 36,000 tokens. The four results alone
  were about 146,000 tokens. Compaction may only shorten older turns, so the
  middleware stopped the agent. The corporate-ma debugger did the same with
  two 120,000-byte trace reads and four `.xlsx`/`.docx` reads.
- This was not new. Debuggers read binary deliverables in every earlier
  campaign: 7-9 such reads per round in campaigns 1 and 3, then 16 in
  campaign 4 round 1. In round 2 there were 30, because two trials per task
  double the files.
- The operator decided not to patch this one gap alone. Instead, the meta
  agents will be compared with the original AHE agent harnesses, and their
  tools, descriptions, skills and middleware brought in line (AHE-016).
  Campaign 5 follows.

## 5f. Campaign 5 proposal: original-AHE meta-agent parity (AHE-016)

Approved by the operator on 2026-10-13 after the campaign 4 block: compare
every meta agent with the original AHE (upstream
`china-qijizhifeng/agentic-harness-engineering` at `8b2a55d`, cloned to
`~/ahe-original-upstream`), adopt the same tools, tool descriptions, skills
and middleware, write a detailed runner guide, then run campaign 5.

### What the original uses (and NexAU 0.3.9 already ships)

The installed NexAU 0.3.9 contains the original's tool code unchanged
(`read_file`, `search_file_content`, `glob`, `list_directory`, `write_file`,
`replace`, `run_shell_command`) and its two middlewares
(`LongToolOutputMiddleware`, `ContextCompactionMiddleware`; only import lines
differ). The tool description YAMLs (MIT licence) are copied unchanged.

| Agent | Original setting |
|---|---|
| Debugger (`adb`) | 25 iterations, prompt "HARD budget of 20 tool-calling iterations", temperature 0.4, max_tokens 20000; failures fall back and never block |
| Evolve | 500 iterations, temperature 0.7, skill `nexau-evolution-guide` |
| Source / Web Explore | 200 iterations, temperature 0.2 |
| Debugger and Evolve middleware | long tool output: 10,000 characters, keep 50 + 30 lines or 5,000 + 5,000 characters, full text saved to a file; compaction: 220,000 × 0.75, keep 10 iterations, emergency summary-and-retry |
| Explore middleware | long tool output: 20,000 characters, 100 + 80 lines; compaction 200,000 × 0.75, keep 15 |

### Operator decisions

1. File access: the original tools sit behind a path guard. Each agent can
   reach only its allowed roots, and the roots are the same as today.
2. Shell: `run_shell_command` runs in a Docker container. Allowed roots are
   mounted at their real paths (read-only, except the agent's write root).
   There is no network, and the container's root filesystem is read-only.
3. Debugger: the original budget (25 iterations, prompt limit 20,
   temperature 0.4, max_tokens 20000) and the original workflow phases.
4. A failed debugger does not block the round. The task is recorded as
   "diagnosis unavailable", and Evolve reads that task's evidence directly.
   Model or endpoint failures still retry the stage.
5. Original temperatures per role: Evolve 0.7, both Explorers 0.2.
6. Readable evidence: the Harvey evidence stage also writes each trace as
   text with real line breaks, and each deliverable as the same text the
   judges read (LAB `read_file_as_text`: pandoc for `.docx`, pandas for
   `.xlsx`). The original `read_file` cuts lines at 2,000 characters, and
   our trace lines reach 30,000.
7. Evolve gets the original `nexau-evolution-guide` skill, unchanged.

### What stays from the runner

The submit/complete gates (debugger citation coverage, explorer skill gates,
Evolve clean-commit, manifest and validation gate), the Git, checkpoint and
`validate_candidate` tools, `delete_path`, the ddgs web tools (the original
`web_search` needs a Serper key), and the per-role root lists.

### Plan

1. Proposal commit (this section and AHE-016).
2. Implementation with offline tests, then a commit: guarded original tools,
   Docker shell, original middleware, debugger budget and non-blocking
   failures, role temperatures, readable evidence, evolution-guide skill,
   updated prompts and role supplements.
3. New file `docs/AHE_RUNNER_GUIDE.md`: a detailed guide to the runner.
4. Campaign 5 config, `ahe conformance`, `ahe validate`, then rounds one at
   a time with the operator's approval.

### Status

Step 2 is done (`6f9d941`, 227 offline tests pass, including one with the
real Docker shell). Details, deliberate differences and remaining risks are
in AHE-016's "Local result". Settings campaign 5 needs in `ahe.yaml`:

```yaml
meta:
  shell: {backend: docker, image: ahe-meta-shell:py313-v2}
  roles:
    debugger: {temperature: 0.4, max_tokens: 20000}
    evolve: {temperature: 0.7}
    explorers: {temperature: 0.2}
debugger:
  max_iterations: 25
```

Build the image once with
`docker build -t ahe-meta-shell:py313-v2 -f harvey-ahe-project/docker/meta-shell.Dockerfile harvey-ahe-project/docker`.

## 5g. Campaign logs: campaigns 5 and 6

### Campaign 5: `harvey-lab-campaign-5-original-meta-agents` (stopped after round 1)

Section 5f's configuration. Round 1 ran 18:38-19:09 UTC with exit 0, and
every stage completed on its first attempt: C0 eval 295 s, exploration
704 s, debugger 227 s (6/6 diagnosed), evolve 268 s, C1 eval 327 s.
C0 scored 0.546 and C1 0.527.

Every change was reported `INEFFECTIVE` (0/6, 0/3, 0/3). The cause was a
runner bug, not the changes. Evolve's `predicted_fixes` use the debugger's
per-round `task-<sha>` keys, and attribution compared them verbatim with
the evaluation `task_key`s, so nothing could ever match. Campaigns 3 and 4
had the same problem. AHE-017 (`db898d1`) fixes it. The campaign identity
fingerprints the runner package, so campaign 5 cannot resume under the
fixed code. The operator chose to start campaign 6 instead.

### Campaign 6: `harvey-lab-campaign-6-attribution-fix` (complete)

Campaign 5's configuration plus AHE-017, config at `2da1dee`. Each command
ran in tmux (`ahe-c6`, `ahe-c6-r2`, `ahe-c6-r3`, `ahe-c6-final`). Logs are
`campaign-artifacts/c6-run-*.log`. All stages completed on their first
attempt, and the runner logged no model-call errors.

| Stage (s) | Round 1 | Round 2 | Round 3 |
|---|---|---|---|
| exploration | 529 | - | - |
| debugger (6/6 diagnosed) | 188 | 196 | 195 |
| evolve | 234 | 514 | 344 |
| next train eval (12 trials) | 369 | 457 | 511 |
| round total | 27 min (with C0 eval 254 s) | 20 min | 18 min |

Training, mean criterion pass rate (2 trials per task):

| Task | C0 | C1 | C2 | C3 |
|---|---|---|---|---|
| energy-natural-resources/...tax-equity-flip... | 0.549 | 0.451 | 0.438 | 0.444 |
| environmental-esg/...expert-remediation-report | 0.311 | 0.303 | 0.288 | 0.288 |
| banking-finance/...officer-certificate | 0.400 | 0.400 | 0.350 | 0.386 |
| real-estate/draft-warranty-deed | 0.947 | 0.954 | 0.954 | 0.954 |
| corporate-ma/compare-credit-agreement... | 0.787 | 0.750 | 0.775 | 0.769 |
| tax/...irs-closing-agreement | 0.258 | 0.311 | 0.250 | 0.258 |
| **Mean** | **0.542** | **0.528** | **0.509** | **0.516** |

Changes and attribution (hit rate = predicted fixes that improved):

| Round | Change | Verdict |
|---|---|---|
| 1 | system prompt | PARTIALLY_EFFECTIVE 2/6 |
| 1 | new skill `legal-review-coverage` | PARTIALLY_EFFECTIVE 2/6 |
| 1 | `LongTermMEMORY.md` | INEFFECTIVE 0/3 |
| 2 | system prompt (atomic checklist) | INEFFECTIVE 0/5 |
| 2 | skill update | PARTIALLY_EFFECTIVE 1/4 |
| 2 | new sub-agent `coverage-reviewer` (used in 12/12 trials) | PARTIALLY_EFFECTIVE 1/6 |
| 2 | memory rollback, citing round 1 attribution | INEFFECTIVE 0/0 |
| 3 | `coverage-reviewer` prompt: checklist as a minimum, exact-detail checks | MIXED 3/5 |

All three transitions were classified `mixed`. There were 3, 4 and 0
unanticipated regressions, and no unresolved task references. Debugger
patterns: `issue-coverage-gap` on 6, 5 and 5 tasks, plus one
false-"conforming" finding in each of rounds 2 and 3. Evolve's finish tool
was rejected and retried once in round 2 and twice in round 3.

Final test (`ahe run-final-test`, 20:36-20:51 UTC, exit 0, 1 trial per
task):

| Validation task | C0 | C1 | C2 | C3 |
|---|---|---|---|---|
| emerging-companies-vc/...stockholder-agreement | 0.064 | 0.141 | 0.269 | 0.128 |
| bankruptcy-restructuring/...plan-of-reorganization | 0.231 | 0.141 | 0.128 | 0.308 |
| data-privacy-cybersecurity/...transfer-impact-assessment | 0.379 | 0.500 | 0.530 | 0.455 |
| **Mean** | **0.225** | **0.261** | **0.309** | **0.297** |

Selected: **C2**. Test results for C2 against C0:

| Test task | C0 | C2 |
|---|---|---|
| employment-labor/...counterparty-motion-brief | 0.174 | 0.217 |
| healthcare-life-sciences/...medicare-enrollment... | 0.397 | 0.462 |
| international-trade-sanctions/review-draft-voluntary-self | 0.367 | 0.400 |
| **Mean** | **0.313** | **0.360** |

C2 beat C0 on every test task, by +0.047 on the mean. It also used 62
tool calls against 36. The two results disagree: on training every evolved
checkpoint was below C0, while on validation and test they were above it.
Validation and test used one trial on three tasks, and the measured
per-trial noise is about 0.05, so this campaign does not show a reliable
gain. Campaign 7 uses more tasks and trials for that reason.

## 5h. Campaign 7 setup: `harvey-lab-campaign-7-60-15-15`

Planned with the operator on 2026-10-13. The harness, meta agents, models
and runner code are the same as campaign 6. The changes:

| Setting | Campaign 6 | Campaign 7 |
|---|---|---|
| `campaign.rounds` | 3 | 4 |
| `split_file` | `splits.json` (6/3/3) | `splits-v2.json` (60/15/15) |
| `validation_trials` / `test_trials` | 1 / 1 | 2 / 3 |
| `meta.max_concurrent_model_calls` | 6 | 20 |
| `attribution_min_task_delta` (new adapter setting) | 0.0 | 0.08 |

Split. Rebuild and check with
`.venv/bin/python harvey-ahe-project/scripts/make_splits_v2.py --lab-root ../harvey-labs`.
The output is the same on every run.
- 166 LAB tasks pass the pilot filter at the pinned commit, across 24
  practice areas.
- Train holds all 12 pilot tasks plus 48 more. The pilot validation and
  test tasks become training tasks here.
- The other 154 eligible tasks are dealt one per practice area per pass,
  with areas and tasks ordered by sha256. The first 15 dealt tasks are
  test, the next 15 validation, and the next 48 train.
- Validation and test each cover 15 distinct practice areas, and train
  covers 23.
- The full rule is in the file's `selection_policy`.

Attribution threshold. `compare()` counts a task as improved or regressed
only when its score moves by more than `attribution_min_task_delta`, one
value for every task. The value is recorded in each `transition.json`. The
operator chose 0.08 from three runs of the unchanged seed harness (C0 in
campaigns 4-6). Across those runs, 18 task-level changes had a median of
0.031, an 80th percentile of 0.083 and a 90th percentile of 0.091.
Attribution is the only consumer of `compare()`, so scores and checkpoint
selection do not change.

No answers carry over from earlier campaigns. Each campaign starts from the
same seed harness, and the meta agents read only their own campaign's
training evidence. The web explorer's leakage filter blocks LAB names and
training-task slugs. Validation and test slugs are deliberately left out of
the filter, because the filter terms appear in the meta prompts.

Expected load, from campaign 6 rates:
- About 120 rollouts per training evaluation, 30 per validation checkpoint
  and 45 per test checkpoint.
- About 840 rollouts and 70,000 model calls in total.
- About 25-35 minutes per training evaluation, about 50 minutes per round,
  and about 5 hours for the whole campaign.
- About 6-7 GB of artifacts.

Launch. One command runs the whole campaign: C0, exploration, four rounds,
validation of C0-C4, selection, and the final test. It runs in tmux session
`ahe-c7`. If a stage stops on an infrastructure error, the wrapper waits
2 minutes and runs `ahe resume`, at most 3 times. A resume reruns only the
interrupted stage. A blocked stage, or any other error, stops the loop.

```bash
tmux new-session -d -s ahe-c7 -c ~/AHE_Harvey 'L=harvey-ahe-project/campaign-artifacts/c7-run.log; C=harvey-ahe-project/ahe.yaml; .venv/bin/ahe run --config $C 2>&1 | tee -a $L; rc=${PIPESTATUS[0]}; n=0; while [ $rc -ne 0 ] && [ $n -lt 3 ] && ! grep -q "\"status\": \"blocked\"" harvey-ahe-project/campaign-artifacts/harvey-lab-campaign-7-60-15-15/stages/*/stage.json; do n=$((n+1)); echo "== resume $n after exit $rc" | tee -a $L; sleep 120; .venv/bin/ahe resume --config $C 2>&1 | tee -a $L; rc=${PIPESTATUS[0]}; done; echo "== final exit $rc" | tee -a $L; exec bash'
```

## 5i. Campaign log: `harvey-lab-campaign-7-60-15-15` (complete)

Launched at 21:03 UTC on 2026-10-13 from commit `43743bb`. The run finished
at 01:46 UTC on 2026-10-14 with exit 0, after 4 h 43 min. The log is
`campaign-artifacts/c7-run.log`, and the artifacts take 5.9 GB.

Interruptions. The endpoint timed out three agent model calls after
300 s (HTTP 500), and each one interrupted its stage:
- `evaluation.C4.train` at 00:17. 119 of 120 units were kept, and resume 1
  reran the one failed unit in 2.5 min.
- `evaluation.C3.validation` at 00:58 and again at 01:09, both on the same
  unit. Resumes 2 and 3 handled them.

The wrapper used all three resumes, so one more timeout would have stopped
the run for the operator. A judge-call timeout in C3 training was absorbed
by `grading_attempts`. No other model-call errors were recorded.

| Stage (min) | Round 1 | Round 2 | Round 3 | Round 4 |
|---|---|---|---|---|
| debugger (60/60 diagnosed) | 9.2 | 9.1 | 10.9 | 12.7 |
| evolve | 4.5 | 4.7 | 9.0 | 6.4 |
| next train eval (120 trials) | 21.7 | 24.1 | 23.5 | 26 + 2.5 resume |

C0's training evaluation took 21.1 min for about 9,000 model calls, with a
maximum slot wait of 3 s. Exploration took 7.3 min. Validation of each
checkpoint (30 trials) took 7-9 min, and test took 9.1 min for C0 and
12.4 min for C4 (45 trials each).

| Metric (mean criterion pass rate) | C0 | C1 | C2 | C3 | C4 |
|---|---|---|---|---|---|
| Train, 60 tasks × 2 | **0.506** | 0.477 | 0.469 | 0.469 | 0.473 |
| Validation, 15 tasks × 2 | 0.498 | 0.505 | 0.508 | 0.516 | **0.538** |
| Test, 15 tasks × 3 | **0.474** | - | - | - | 0.429 |

Selected: **C4**. The test does not confirm the selection.
- Validation, C4 against C0: +0.040, higher on 11 of 15 tasks, paired t 2.3.
- Test, C4 against C0: −0.046, lower on 13 of 15 tasks, paired t −2.9.
  Five test tasks dropped by more than 0.08 (healthcare 0.982 → 0.846, tax,
  data privacy, venture capital, employment), and one rose (corporate
  governance +0.107).
- Training agrees with test: C0 is best, and C4 has 5 tasks up and 16 down
  against C0 at the 0.08 threshold.
- The evolved harness took about three times as many turns: 648 against
  224 assistant turns on test.

What Evolve changed (attribution with the 0.08 threshold):

| Round | Changes | Verdicts |
|---|---|---|
| 1 | new skill `coverage-audit-protocol` with a required worklog and checklist | INEFFECTIVE 0/5; 16 unpredicted regressions |
| 2 | worklog gate in prompt, skill, `finish` description and `finish` code | prompt and `finish` code HARMFUL 0/3; others INEFFECTIVE |
| 3 | rolled back round 2's prompt, description and `finish` code to C1; refined the skill | skill PARTIALLY_EFFECTIVE 1/4 |
| 4 | new `coverage_reviewer` sub-agent and a pre-finish review step in the skill | both INEFFECTIVE 0/4 |

The debugger labelled 56-57 of 60 tasks `issue-coverage-gap` in every
round. Every Evolve round targeted that pattern, and none raised training
scores. C1 removed the 24 C0 trials that ended without `finish`, but that
did not raise scores either.

## 5j. Campaign 8 setup: `harvey-lab-campaign-8-evaluated-gpt-5.4`

The operator's choice on 2026-10-14: campaign 7's configuration with only
the evaluated model changed, from `gpt-5.4-mini` to `gpt-5.4`. Reasoning
stays at provider default, with `max_tokens` 64000. The meta model
(`gpt-5.4`, reasoning high), judges (`claude-sonnet-5`, `gpt-5.5`), helper,
split, trials, rounds and threshold are unchanged, so the scores stay
comparable with campaign 7.

Before launch, one `gpt-5.4` request with a tool schema and
`max_tokens` 64000 returned a correct structured tool call in 1.8 s.
`ahe conformance` and `ahe validate` both passed. Campaign 7 used all
three of its resumes, and the larger evaluated model may run longer calls,
so the launch wrapper now allows 5 resumes instead of 3. Otherwise it is
the section 5h command with the campaign 8 log (`c8-run.log`), stage path
and tmux session (`ahe-c8`).

## 5k. Campaign log: `harvey-lab-campaign-8-evaluated-gpt-5.4` (complete)

Launched at 04:55 UTC on 2026-10-14 from commit `c77deb7`. The run finished
at 13:15 UTC with exit 0, after 8 h 21 min, including 43 min with the host
down. The log is `campaign-artifacts/c8-run.log`, and the artifacts take
9.1 GB.

Interruptions:
- **Host reboot at 07:26** (kernel update), during `evaluation.C2.train`
  with 94 of 120 trials done. Six units had failed at 07:15 with `podman
  info` timeouts just before. The reboot ended the tmux sessions, the
  wrapper and the model endpoint. Recovery at 08:07-08:09, with the
  operator's approval:
  1. restarted the endpoint host and Start Server;
  2. checked the three models;
  3. removed 15 stale `lab-sandbox` containers;
  4. relaunched the wrapper with `ahe resume`.

  The stale attempt was marked abandoned, and attempt 2 ran the remaining
  26 units in 18.8 min.
- **300 s endpoint timeouts** on one agent model call each:
  - `evaluation.C3.train` at 09:35: 119 of 120 units were kept, and resume 1
    took 9.7 min;
  - `evaluation.C0.test` at 12:42: 44 of 45 kept, and resume 2 took 6.1 min.

  The wrapper used 2 of its 5 resumes.
- The many "Finish Tool 'finish' execution failed, will continue." log lines
  are the evolved `finish` tool rejecting a run, not errors.

| Stage (min) | Round 1 | Round 2 | Round 3 | Round 4 |
|---|---|---|---|---|
| debugger (60/60 diagnosed) | 10.5 | 12.4 | 11.9 | 13.7 |
| evolve | 5.4 | 5.5 | 5.6 | 13.7 |
| next train eval (120 trials) | 38.3 | 37 + 18.8 after reboot | 47.8 + 9.7 resume | 49.3 |

C0's training evaluation took 29.4 min, and exploration took 10.1 min.
Validation of each checkpoint (30 trials) took 12-19 min. Test took
14.1 + 6.1 min for C0 and 22.2 min for C4 (45 trials each).

| Metric (mean criterion pass rate) | C0 | C1 | C2 | C3 | C4 |
|---|---|---|---|---|---|
| Train, 60 tasks × 2 | 0.686 | 0.714 | 0.727 | 0.716 | **0.749** |
| Validation, 15 tasks × 2 | 0.690 | 0.740 | 0.756 | 0.745 | **0.765** |
| Test, 15 tasks × 3 | 0.684 | - | - | - | **0.754** |

Selected: **C4**. The test confirms the selection.
- Test, C4 against C0: +0.071, higher on 13 of 15 tasks, paired t 5.0. The
  two lower tasks dropped by 0.013 and 0.009. The largest gains were
  corporate governance (+0.154), employment (+0.153) and capital markets
  (+0.119).
- Validation, C4 against C0: +0.075, higher on 12 of 15 tasks, paired t 3.7.
- Train, C4 against C0: +0.063, higher on 49 and lower on 8 of 60 tasks,
  paired t 6.7.
- The tie-breaker metric fell. On test, 2 of C0's 45 trials passed every
  criterion under both judges, and none of C4's did (one passed under one
  judge).
- The evolved harness took 3.7 times as many turns: 935 against 251
  assistant turns on test, and 1,225 against 520 tool calls.

Against campaign 7, which differs only in the evaluated model: gpt-5.4
starts 0.18 higher (C0 train 0.686 against 0.506). Evolution then helped
(+0.071 on test), whereas with gpt-5.4-mini it hurt (−0.046).

What Evolve changed (attribution with the 0.08 threshold):

| Round | Changes | Verdicts |
|---|---|---|
| 1 | an issue ledger and a final read-through, in the prompt, memory, `finish` description and `finish` code | memory EFFECTIVE 2/2; the other three PARTIALLY_EFFECTIVE; 2 unpredicted regressions |
| 2 | new skill `ledger-to-deliverable-review`, plus prompt and memory edits | all INEFFECTIVE; 2 unpredicted regressions |
| 3 | stricter audit: a saved review checklist required by the prompt, memory and skill, with `finish` rejecting runs without one | all INEFFECTIVE; 3 unpredicted regressions; train −0.010 |
| 4 | rolled back round 3's four files to C2; the `finish` description says a rejection names the specific missing items | prompt rollback INEFFECTIVE; the other four PARTIALLY_EFFECTIVE; no unpredicted regressions; train +0.033 |

The debugger labelled 53-57 of 60 tasks `issue-coverage-gap` in every
round, as in campaign 7. Throughout, the Debugger and Evolve role
supplements still described the old all-pass target (AHE-018, section 6).

## 5l. Campaign 9 setup: `harvey-lab-campaign-9-lab-exact-firm-knowledge`

The operator's choice on 2026-10-14 was a single evaluation, not evolution.
LAB's own harness is evaluated once, as C0, on all 250 firm-knowledge tasks.
No meta agents run.

- Harness: `harness_lab/` with `harness_manifest_lab.yaml`. It has LAB's
  system prompt including the docx, pptx and xlsx manuals, LAB's seven tool
  schemas and descriptions unchanged, and LAB's skill scripts. The scripts
  are copied into each sandbox's `/workspace/skills/` from `workspace_seed/`.
  The memory file is empty, so nothing is appended to the prompt (AHE-019).
  A test checks that the system message and tools actually sent through
  NexAU equal LAB's. The remaining differences from LAB are listed in
  section 6.
- Tasks (`splits-firm-knowledge.json`, built by
  `scripts/make_splits_firm_knowledge.py`): train is all 250
  `tasks/firm-knowledge/` tasks, unfiltered (`apply_task_filter: false`).
  They share the `firm-knowledge/dms` store (9,288 files), mounted read-only
  in place, as LAB does. A tamper check runs after each trial. The other
  1,760 LAB tasks are not included. AHE requires non-empty validation and
  test splits, so each holds one placeholder from `splits-v2.json`. They are
  never run.
- Model: `gpt-5.6-sol`, reasoning high, `max_tokens` 64000 (LAB's Copilot
  adapter default). 1 trial per task, 20 trials at once. Judges, helper,
  grading settings and the 24-call queue are unchanged from campaign 8.
- Context limit: `max_context_tokens` 921,793, the `max_input_tokens` the
  endpoint advertises for `gpt-5.6-sol`. LAB has no cap of its own. It ends
  a run with `context_overflow` only when the provider rejects the prompt,
  then grades the output. NexAU stops the same way once its own token count
  passes this value. Its count may differ slightly from the provider's.
- Checks on 2026-10-14: `ahe conformance` and `ahe validate` passed. The
  model preflight passed (`operator-preflight/2026-10-14/`), and one tool
  request returned a correct `read` call in 1.6 s. A podman smoke test ran
  LAB's `generate_from_md.py` and `validate.py` inside the sandbox and got a
  valid .docx. Writes to `documents/` failed (read-only), and the store was
  unchanged afterwards.

`ahe run` with `rounds: 0` would still start the Explore agents and the
validation and test evaluations. So `scripts/run_c0_train_only.py` runs
only the controller's baseline freeze and C0 training-evaluation stages.
Rerunning it resumes; only unfinished trials run again. The wrapper is the
section 5h loop with this script:

```bash
tmux new-session -d -s ahe-c9 -c ~/AHE_Harvey 'L=harvey-ahe-project/campaign-artifacts/c9-run.log; C=harvey-ahe-project/ahe.yaml; S=harvey-ahe-project/scripts/run_c0_train_only.py; n=0; .venv/bin/python $S --config $C 2>&1 | tee -a $L; rc=${PIPESTATUS[0]}; while [ $rc -ne 0 ] && [ $n -lt 5 ] && ! grep -q "\"status\": \"blocked\"" harvey-ahe-project/campaign-artifacts/harvey-lab-campaign-9-lab-exact-firm-knowledge/stages/*/stage.json; do n=$((n+1)); echo "== resume $n after exit $rc" | tee -a $L; sleep 120; .venv/bin/python $S --config $C 2>&1 | tee -a $L; rc=${PIPESTATUS[0]}; done; echo "== final exit $rc" | tee -a $L; exec bash'
```

Campaign 8's C0 training evaluation (120 trials) took 30 minutes. Campaign
9 has 250 trials on larger tasks with high reasoning, so expect roughly 1.5
to 3 hours.

### First launch, stopped after 6 minutes (2026-10-14 19:33 UTC)

`harness_lab/agent.yaml` still had the old harness's
`max_context_tokens: 200000`. Two trials stopped with no output at 223,791
and 297,887 prompt tokens. In one, the model had read eight .docx files in
a single turn. LAB's `read` has no output cap either, so LAB would have sent
those prompts to the model. The operator stopped the run, and the limit was
changed to 921,793 (above), with a test. The aborted artifacts are in
`campaign-artifacts/harvey-lab-campaign-9-aborted-ctx200k/` and are not
used.

Two things happened while stopping it. The runner ignored SIGINT while
trials were in flight, so it needed SIGTERM. SIGTERM left 17
`lab-sandbox-*` containers running, which had to be removed by hand
(`podman rm -f`). Stop the wrapper loop first, or it will resume the run.

## 5m. Campaign log: `harvey-lab-campaign-9-lab-exact-firm-knowledge` (stopped)

Second launch 2026-10-14 19:46 UTC; stopped by the operator at about 20:50
UTC with 101 graded trials. Results so far: mean criterion pass rate 0.508,
dual all-pass rate 0.173 (16 of 101 trials passed every criterion with both
judges). 84 attempts ended in an infrastructure error: 52 model-call
timeouts, 25 "No tool call found" and 7 "Message exceeds token limit".
These results are not used: the transport problems below affect them.

### Why it was stopped: the Copilot transport cut long prompts

Three kinds of infrastructure errors appeared that campaigns 7 and 8 never
had:

- "Model request timed out after 300000 ms": hung calls on small prompts.
  These are upstream stalls specific to gpt-5.6-sol (2.6% of its calls,
  against 0-0.03% for the other models in the same hour), mostly in calls
  2-9 of a trial. Successful calls never took more than 96 s. Each hang
  failed its whole trial; about 27% of unit attempts were lost. Repair:
  `transport.hung_call_retries` (AHE-022).
- "Message exceeds token limit": a single message over the ~270k budget.
- Copilot 400 "No tool call found for function call output with
  call_id ...", although each request contained the matching call.

The last two have one cause. VS Code fills in each model's default settings
for every language-model request, including GH Copilot Server's. For
gpt-5.4, gpt-5.5 and gpt-5.6-sol the default "Context Size" is 272000
(922000 is opt-in), even though `/v1/models` advertises 921,793. Copilot
Chat then deletes the oldest messages until the prompt fits about 270k
tokens. Evidence:

- Code: VS Code `sendChatRequest` merges the schema defaults into
  `configuration`. The ext host passes it to Copilot Chat as
  `modelConfiguration`. Copilot Chat uses `modelConfiguration.contextSize`
  as the budget, and prompt-tsx removes the earliest content first.
- In all 25 "No tool call found" requests the cut falls at the budget.
  Counting from the orphaned assistant message to the end gives ≥269.8k
  tokens; counting from its surviving tool result gives ≤270.5k. When the
  cut removes a tool-call message but keeps its result, the request is
  invalid. When the cut is clean, the call succeeds and the model has
  silently lost the system prompt and the task.
- 15 of the 101 graded trials made at least one such silent call (36
  calls). Their mean criterion pass rate is 0.429 against 0.522 for the
  rest, and their all-pass rate 0.067 against 0.192.
- Probe (gpt-5.4): a code placed in the first message is recalled from a
  5k-token prompt but not from a 317k-token prompt (`UNKNOWN`).

Copilot Chat also takes the reasoning effort from
`modelConfiguration.reasoningEffort`, whose default is "medium". It ignores
the `reasoning.effort` that GH Copilot Server forwards in `modelOptions`. In
the probe, effort "none" and "high" both took 8.5-13 s and both solved a
counting puzzle.

LAB has no protection against large tool outputs either. Its `bash`, `read`
and `parse-doc` outputs, `sandbox.exec` and the Copilot adapter all pass
full output (`lab_core/harness/tools.py` 456-540,
`lab_core/sandbox/sandbox.py` 436, `adapters/copilot.py` 172). It relies on
the provider's context window and stops cleanly only on "prompt is too
long" or "context_length_exceeded" (`agent_loop.py` 73-81). `harness_lab`
copies this faithfully.

### Repair (AHE-021)

GH Copilot Server's `out/server.js` in the host instance
(`~/copilot-host/extensions/gh-copilot-server.gh-copilot-server-0.1.0/`) now
sends `options.configuration` with `contextSize` set to the model's
`maxInputTokens` and, when the request sets one, `reasoningEffort`. Without
an effort, Copilot's default ("medium") still applies. The SHA-256 of
`server.js` changed from `9090e9eb…7a45` to `5dea2428…384d`. The window was
reloaded and the server restarted.

Probe after the patch (gpt-5.4): effort "none" took 0.9-1.2 s and gave
wrong answers; "high" took 12 s and was right. The 317k-token prompt
recalls the code. The judges (claude-sonnet-5, gpt-5.5) still answer, and
they send no effort and stay under 240k characters, so grading is unchanged.
The probe script is `scripts/copilot_context_effort_probe.py`; its results
are in `operator-preflight/2026-10-14/copilot-server-patch/`.

The stopped run's artifacts were renamed to
`campaign-artifacts/harvey-lab-campaign-9-stopped-copilot-272k/`, with its
run log inside as `c9-run.log`. Model preflights made before the patch (for
example `operator-preflight/2026-10-14/*.json`) predate the new
`server.js`, so rerun them before the next launch.

## 5n. Campaign 9 pilot on the patched server, then the full rerun

### Pilot: `harvey-lab-campaign-9-pilot-patched`

Ten tasks (`splits-firm-knowledge-pilot10.json`, drawn with
`random.Random(9).sample(range(250), 10)`: 002, 036, 048, 069, 087, 096,
119, 157, 174, 222), same harness and models as 5l, plus
`transport.hung_call_retries: 2`. Before launch the probe confirmed the
patch for gpt-5.6-sol (`after-gpt-5.6-sol.json`: effort "none" 0.9-1.5 s
and wrong, "high" 5-12 s and right; the 317k-token prompt is recalled).
Run 2026-10-14 21:31-21:48 UTC; log `c9-pilot-run.log` inside the
artifacts directory.

| Check | Result |
|---|---|
| Graded trials | 10 of 10; no infrastructure errors |
| Hung calls (300 s server timeout) | 7 of about 214 (3%), all on call 4 or 5 of a trial; every one succeeded on its first resend |
| Successful call time at real "high" | median 5 s, p99 31 s, max 40 s |
| Calls with prompts over 270k tokens | 60, all answered |
| Judges (claude-sonnet-5, gpt-5.5) | all 10 graded without errors |
| Mean criterion pass rate | 0.508 (pilot only, not a result) |
| Dual all-pass rate | 0.20 (tasks 087 and 096) |

Two trials (tasks 174 and 222) filled the whole 921,793-token context
through large file reads (LAB-005). NexAU's own pre-send estimate then
stopped the agent ("Prompt tokens ... exceed max_context_tokens"); LAB has
no such check and lets the provider decide (AHE-023). Neither wrote an
output file, yet they scored
0.25 and 0.33: their "Qualifying set — precision" criterion passes when
nothing is asserted (LAB-006). These are valid trials and are not retried.

### Full rerun: `harvey-lab-campaign-9-lab-exact-firm-knowledge`

`ahe.yaml` is back to the 5l configuration (all 250 tasks,
`splits-firm-knowledge.json`) with `hung_call_retries: 2` kept. The
directory name is reused; the stopped run lives under its new name (5m).
Launch with the C0-only command of 5l, wrapped in the resume loop used for
5m (log `campaign-artifacts/c9-run.log`).

Run 2026-10-14 21:58 UTC to 2026-10-15 00:21 UTC (2 h 23 min), commit
`aa9e013`.

| Measure | Value |
|---|---|
| Graded trials | 250 of 250 (246 on the first attempt, 4 on a second) |
| Mean criterion pass rate | 0.608 |
| Dual all-pass rate | 0.220 (52 trials passed every criterion with both judges) |
| Infrastructure errors | 4 attempts: 3 calls that hung three times, 1 "Message exceeds token limit" (task 208) |
| Hung calls | 106 hung attempts on 95 of 5,120 calls (2%); 92 recovered by resending |
| Stopped by NexAU's context estimate | 14 trials, no output, mean criterion pass 0.12 |
| Largest answered prompt | 797k real tokens; nothing was silently cut |

Caveat (AHE-023): NexAU's estimate of one token per four characters is
10-20% high for prose, so about 10 of the 14 stopped trials were probably
still under the real limit, where LAB would have continued. It is too low
for dense tables: task 208 sent a 962k-token prompt, Copilot rejected it,
and the runner counted that as an infrastructure error and reran the task
(0.056), where LAB would have ended the run. These results are kept with
this caveat; campaign 9b (5o) repeats all 250 tasks with the fix.

## 5o. Campaign 9b: `harvey-lab-campaign-9b-lab-exact-token-fix`

Campaign 9's full rerun again, on all 250 tasks, with the AHE-023 fix.
Everything else is unchanged: `harness_lab/`, `harness_manifest_lab.yaml`,
gpt-5.6-sol at reasoning high, the same judges, concurrency 20, 24 request
slots, `hung_call_retries: 2`, C0 training evaluation only.

The fix:

| Change | Where |
|---|---|
| NexAU counts the prompt with o200k_base and its own message and tool overheads, instead of characters / 4 | `harness_lab/agent.yaml` `token_counter: harvey_ahe.token_count:count_prompt_tokens`; `harvey_ahe/token_count.py` |
| NexAU stops the agent at 911,793 tokens, 10,000 below Copilot's 921,793, because Copilot silently cuts a longer prompt made of many messages (AHE-021) | `harness_lab/agent.yaml` `max_context_tokens` |
| A provider "too long" rejection ends the trial as a graded `context_overflow`, as in LAB, instead of an infrastructure rerun | `harvey_ahe/adapter.py` `_run_session` |

Preflight on 2026-10-15: the listener on 3141 is the server-host instance,
`server.js` is still `5dea2428…`, and gpt-5.6-sol at high passed the
positive and negative-control probes
(`operator-preflight/2026-10-15/c9b-gpt-5.6-sol-high.json`).

Launch the C0-only command of 5l in the same resume loop as 5n, with the
log at `campaign-artifacts/c9b-run.log`.

Run 2026-10-15 02:57 UTC to 05:46 UTC (2 h 49 min), commit `48242b0`.

| Measure | Campaign 9 rerun (5n) | Campaign 9b |
|---|---|---|
| Graded trials | 250 (4 on a second attempt) | 250 (11 on a second attempt) |
| Mean criterion pass rate | 0.608 | 0.635 |
| Dual all-pass rate | 0.220 (52 trials) | 0.224 (51 trials) |
| Stopped for context | 14 (by the characters / 4 estimate) | 9 (by the exact count) |
| Rejected by Copilot as too long | 1 (task 208, rerun) | 0 |
| Hung calls | 95 of 5,120 calls | 101 of 5,293 calls; 2 hung three times |
| Largest answered prompt | 797k tokens | 857k tokens; none over 921,793 |

- Per task, 9b minus 9 is +0.027 (standard error 0.021): 75 tasks better,
  64 worse, 111 equal. This is within noise.
- The 14 trials stopped in campaign 9: 12 finished in 9b, and their mean
  rose from 0.120 to 0.586. The 229 tasks stopped in neither run moved
  from 0.644 to 0.652.
- The 9 stops in 9b (tasks 052, 068, 069, 076, 093, 114, 134, 135, 171)
  wrote no output; mean 0.161. Their prompts would have been 919k to 12.2M
  tokens. Only task 052 (919,371) was within the 10,000-token margin.
  Seven of them had finished in campaign 9, so which tasks run out of
  context changes from run to run.
- The 11 second attempts were infrastructure failures: 8 `podman info`
  timeouts (10 s) between 05:05 and 05:08, just after a process inside one
  sandbox was killed for running out of memory at 05:04; 2 calls that hung
  three times; 1 sandbox unhealthy after its run. A single resume completed
  them.

### Detailed analysis of campaign 9b

All figures cover the 250 graded trials (one per task) and their 2,623
criteria. They were computed from each trial's `result.json` and the two
`scores_judge-N.json` files of its grading. Judge 1 is claude-sonnet-5 and
judge 2 is gpt-5.5. Definitions:

- **Criterion pass rate:** the share of a task's criteria a judge passes,
  averaged over tasks. The dual figure averages the two judges.
- **Pooled pass rate:** passed criteria over all criteria, so tasks with
  many criteria weigh more.
- **All-pass:** a judge passes every criterion of the task. LAB's "dual
  all-pass rate" is the mean of the two judges' all-pass rates. "Both
  judges" is the stricter count of tasks that both judges pass entirely.

**Judge reasoning mode**

Neither judge was sent a reasoning setting. AHE grading, like LAB's own
Copilot judge path, sends no `reasoning` field (temperature 0.0, max_tokens
64000), and every `scores_judge-N.json` records `reasoning_effort: null`. The
GH Copilot Server log shows `thinking=–, reasoning=–` on all 5,817 requests of
each judge. Copilot Chat 0.69.0's defaults therefore decided what each judge
actually ran with:

| Judge | API path | Effective reasoning | Call time median / p90 / max |
|---|---|---|---|
| claude-sonnet-5 | Anthropic Messages | No extended thinking | 1.5 / 1.9 / 6.2 s |
| gpt-5.5 (served as gpt-5.5-2026-04-23) | Copilot GPT path | Effort "medium" | 1.9 / 2.6 / 23.5 s |

- **claude-sonnet-5:** the model's configured default effort is "high", but
  Copilot Chat's Messages builder only turns on thinking, and only then sends
  an effort, when the caller sets `_enableThinking`. The server never sets it,
  so the request carries neither. The short call times fit this.
- **gpt-5.5:** Copilot Chat sends effort on GPT models without any thinking
  gate. The model's configured default and the code's fallback are both
  "medium", and no per-model override or `reasoningEffortOverride` setting
  exists in the host profile.

Confirmed: no effort was requested. Inferred: the effective values, which come
from reading the Copilot Chat code. The request sent to the provider is not
logged, and the server reports token usage as 0. This matches LAB's own
judges, which also send no thinking or effort setting. The `reasoning` text in
each verdict is a written field of the JSON answer, separate from hidden
thinking.

**Each judge**

| Judge | Criterion pass rate | Pooled pass rate | All-pass rate (tasks) |
|---|---|---|---|
| claude-sonnet-5 | 0.632 | 0.652 (1,709 of 2,623) | 0.228 (57) |
| gpt-5.5 | 0.638 | 0.667 (1,749 of 2,623) | 0.220 (55) |
| Dual | 0.635 | | 0.224; both judges 51, either judge 61 |

**Judge agreement**

| Measure | Value |
|---|---|
| Criterion verdicts that agree | 97.4% (Cohen's kappa 0.942) |
| Criteria only claude passes | 14 |
| Criteria only gpt passes | 54 |
| Tasks where both judges agree on all-pass | 96.0% |

The judges agree closely. When they differ, gpt-5.5 is the more lenient
about four times in five.

**All-pass disagreements.** The judges disagree on whether a task passes
entirely in 10 of 250 tasks (4.0%). In 6, only claude passes every
criterion; in 4, only gpt does. Each disagreement comes from a single
criterion, and in 8 of the 10 it is the precision criterion:

| Task | Criteria | Only this judge passes all | Criterion the judges split on |
|---|---|---|---|
| 027 | 7 | claude | Qualifying set — precision |
| 037 | 2 | gpt | Qualifying set — precision |
| 056 | 2 | gpt | Answer — Dunmore Energy 1016-00005 |
| 154 | 3 | claude | Qualifying set — precision |
| 161 | 2 | claude | Qualifying set — precision |
| 162 | 4 | claude | Qualifying set — precision |
| 164 | 4 | claude | Qualifying set — precision |
| 165 | 2 | claude | Qualifying set — precision |
| 187 | 6 | gpt | Qualifying set — precision |
| 231 | 3 | gpt | Qualifying matter — Calloway-Briggs Renewables 1015-00005 |

Over all criteria gpt-5.5 is the more lenient judge, but on the precision
criterion claude is (0.485 against 0.461), so claude more often passes a
whole task alone.

**Criterion types.** Grouped by the part of the title before "—"; the
precision criteria are counted from every title that contains "precision".

| Type | Criteria | Tasks | claude pass | gpt pass |
|---|---|---|---|---|
| Qualifying matter | 1,378 | 195 | 0.681 | 0.708 |
| Proof document | 456 | 81 | 0.664 | 0.675 |
| Precision (any title, LAB-006) | 241 | 241 | 0.485 | 0.461 |
| Classification | 78 | 4 | 0.513 | 0.526 |
| Answer | 14 | 14 | 0.500 | 0.571 |

The precision criterion is the hardest one, even though an empty output
passes it (LAB-006). All 9 trials without output passed it with gpt-5.5,
and 8 of them with claude. Without the precision criteria, the criterion
pass rate is 0.664 (claude) and 0.677 (gpt).

**Criteria per task.** Tasks have 1 to 85 criteria (median 6).

| Criteria in task | Tasks | Criterion pass rate | Both-judge all-pass |
|---|---|---|---|
| 1-3 | 71 | 0.528 | 0.310 |
| 4-6 | 59 | 0.659 | 0.220 |
| 7-10 | 44 | 0.734 | 0.227 |
| 11-85 | 76 | 0.659 | 0.079 |

All-pass falls steeply with the number of criteria; tasks with 1-3
criteria have the lowest criterion pass rate but the highest all-pass rate.

The same split into four wider buckets, per judge (claude = claude-sonnet-5,
gpt = gpt-5.5). All-pass cells give the rate and the number of tasks.

| Criteria in task | Tasks | Criteria | Criterion pass claude / gpt / dual | Pooled pass claude / gpt | All-pass claude | All-pass gpt | Dual all-pass | Both-judge all-pass |
|---|---|---|---|---|---|---|---|---|
| 1-5 | 114 | 364 | 0.569 / 0.568 / 0.568 | 0.585 / 0.582 | 0.333 (38) | 0.316 (36) | 0.325 | 0.289 (33) |
| 6-15 | 85 | 770 | 0.734 / 0.739 / 0.737 | 0.740 / 0.745 | 0.212 (18) | 0.212 (18) | 0.212 | 0.200 (17) |
| 16-25 | 28 | 572 | 0.615 / 0.618 / 0.617 | 0.621 / 0.624 | 0.036 (1) | 0.036 (1) | 0.036 | 0.036 (1) |
| 26+ | 23 | 917 | 0.591 / 0.633 / 0.612 | 0.623 / 0.661 | 0 (0) | 0 (0) | 0 | 0 (0) |
| All | 250 | 2,623 | 0.632 / 0.638 / 0.635 | 0.652 / 0.667 | 0.228 (57) | 0.220 (55) | 0.224 | 0.204 (51) |

Only one task with more than 15 criteria passes entirely; none with 26 or
more does. The judges differ most on the largest tasks, where gpt-5.5 is
more lenient (0.633 against 0.591).

**Score distribution (dual criterion pass rate)**

| Score | 0 | (0, 0.25) | [0.25, 0.5) | [0.5, 0.75) | [0.75, 1) | 1 |
|---|---|---|---|---|---|---|
| Tasks | 31 | 17 | 21 | 47 | 83 | 51 |

The median is 0.750.

**How trials ended.** 241 trials called `finish` (mean criterion pass
rate 0.653) and 9 stopped for context (0.161). Exactly those 9 have no
`response.md`. No judge input hit the 240,000-character limit.

**Agent behaviour**

| Measure | Median | p90 | Max | Total |
|---|---|---|---|---|
| Assistant turns | 17 | 37 | 104 | 5,225 |
| Tool calls | 36 | 63 | 114 | 9,925 |
| Agent run seconds (excluding grading) | 503 | 1,240 | 2,113 | 154,468 |

Tool calls: read 46%, bash 29%, glob 15%, grep 4%, write 2%, finish 2%,
edit 2%. Among finished trials, more turns go with slightly higher scores
(correlation 0.25).

**Model calls (evaluated agent)**

| Measure | Value |
|---|---|
| Calls | 5,293 |
| Calls that hung at least once | 101 (1.9%); 114 hung attempts |
| Calls that failed after resends | 2 (each hung three times) |
| Clean call seconds: median / p90 / p99 / max | 5.0 / 14.4 / 41.7 / 154 |

**Trajectory walkthroughs.** Call-by-call walkthroughs of six campaign 9b
runs, including one all-pass, are in
[`docs/HARVEY_CAMPAIGN_9B_TRAJECTORY_WALKTHROUGHS.md`](../docs/HARVEY_CAMPAIGN_9B_TRAJECTORY_WALKTHROUGHS.md).

## 5p. Campaign 9c (claude-opus-4.8 at reasoning high): prepared, not run

Campaign 9c was to repeat 9b with claude-opus-4.8 at reasoning high as the
evaluated model. On 2026-10-15 the server was patched and probed, then the
operator dropped 9c in favour of campaign 10. Nothing was launched.

- **Server patch, kept.** Copilot Chat turns on Claude thinking only when
  `modelOptions._enableThinking` is set, so GH Copilot Server ignored
  "high" for Claude (8-10 s calls with or without it). The patch sets it
  for Claude models when an effort is sent and logs the thinking parts
  received (they are not returned to the client). After it, "high" took
  16 s with 75-93 thinking parts, and a three-step tool loop worked. GPT
  requests and requests without effort, such as the judges', are
  unchanged. `server.js` sha256 `5dea2428…` became `ed67ab4c…`; the backup
  is `server.js.bak-2026-10-15-before-claude-thinking`. Record and probes:
  `operator-preflight/2026-10-15/copilot-server-claude-thinking-patch/`
  (script `scripts/copilot_claude_thinking_probe.py`).
- **Context limit (AHE-024).** Claude counts the same Harvey text as
  1.65-1.68 times as many tokens as the harness's o200k_base counter, and
  Anthropic rejects prompts over 1,000,000 of its tokens (about 597k
  o200k), well below the harness's 911,793. Any future non-GPT evaluation
  must deal with this first.

## 5q. Campaign 10 setup: `harvey-lab-campaign-10-lab-exact-60-15-15`

The first evolution campaign on the LAB-exact harness. The operator's
choices (2026-10-14, confirmed 2026-10-15):

| Setting | Value |
|---|---|
| Starting harness | `harness_lab/` with `harness_manifest_lab.yaml`, as evaluated in 9b (AHE-023 token counter, `max_context_tokens` 911793) |
| Split | `splits-firm-knowledge-60-15-15.json`: 60 train, 15 validation, 15 test, drawn at random from the 250 firm-knowledge tasks (`scripts/make_splits_firm_knowledge_c10.py`, salt `harvey-lab-campaign-10`) |
| Rounds | 5 |
| Trials per task | train 2, validation 2, test 3 |
| Evaluated model | `gpt-5.4`, provider-default reasoning (no effort sent; Copilot's default is medium), `max_tokens` 64000 |
| Meta model | `gpt-5.4`, reasoning high, `max_tokens` 32000; per-role settings as in campaign 8 |
| Noise floor for change attribution | none (`attribution_min_task_delta: 0.0`) |
| Judges, helper, transport | as in 9b, with `hung_call_retries: 2` |

Changes before launch:

- Web-research leakage terms now block training tasks as `tasks/017`
  instead of `017`. The bare number also blocked ordinary text such as
  "2017"; the harness check already used the wider form (`adapter.py`
  `meta_prompt_context`, test
  `test_web_leakage_terms_use_widened_numeric_slugs`).
- `meta_supplements/evolve.md` states the real context limit (911793 in
  o200k_base, kept 10,000 below Copilot's 921,793) instead of the old
  200000.
- Both the Evolve and Debugger supplements now describe the shared
  document store of firm-knowledge tasks: about 9,300 files in 266 matter
  folders, of which a task needs a few. Evolve no longer suggests "read
  everything", and the Debugger checks whether needed documents were
  found.
- gpt-5.4 has the same Copilot input limit as gpt-5.6-sol (921,793 in
  `/v1/models`), so the harness limit is unchanged.

Preflight on 2026-10-15: the listener on 3141 is the server-host instance;
`server.js` is `ed67ab4c…` (the Claude-thinking patch of 5p, which leaves
GPT requests unchanged). gpt-5.4 passed the positive and negative-control
probes at provider default and at high
(`operator-preflight/2026-10-15/c10-gpt-5.4-provider-default.json`,
`c10-gpt-5.4-high.json`). `ahe validate` and `ahe conformance` passed, and
261 tests pass.

Launch with the section 5h command (`ahe run`, then up to 5 `ahe resume`
attempts), with the log at `campaign-artifacts/c10-run.log`, the campaign 10
stage path, and tmux session `ahe-c10`.

## 5r. Campaign 10: first run stopped, restarted with one process per trial

The first run started at 09:22 UTC on 2026-10-15. By 10:35 UTC, 50 of the
120 C0 training trials had finished, and trials took about twice as long as
in 9b: the 20 trials ran as threads of one Python process, so their
host-side `grep` calls waited for each other (AHE-025). The operator chose
to stop, fix and restart from scratch.

- The stopped run is kept as
  `campaign-artifacts/harvey-lab-campaign-10-stopped-grep-contention/`, with
  its log `campaign-artifacts/c10-stopped-grep-contention-run.log`. Its
  results are not used.
- The fix is the adapter setting `trial_processes: true` in `ahe.yaml`: each
  trial's agent runs in its own Python process, as in LAB. Grading stays in
  the `ahe` process. Details and limits are in AHE-025.
- Check without model calls: 20 searches of the shared store at once took
  241 s each as threads and 10.5 s each as separate processes.
- Everything else in section 5q is unchanged. The restart uses the same
  campaign name and artifact folder, which the rename freed.

## 6. Known deviations and limits to report with results

- Task scope (campaigns 1 to 8): only 166 of LAB's 2,010 tasks (8%) are
  used. The pilot filter in `harvey_ahe/tasks.py` (`eligibility_errors`)
  keeps tasks with their own `documents/` folder of 1-10 files and at most
  5 MB, 20-40 rubric criteria, inline instructions and top-level
  deliverables. It was meant for the pilot but was kept for campaigns 7 and
  8 without being flagged to the operator. Excluded:
  - 1,519 tasks with more than 40 criteria (median 57 among tasks with their
    own documents); for 989 of them this is the only reason;
  - all 250 firm-knowledge tasks, which share one 9,288-file store through
    `docs_dir` (`load_task` refuses `docs_dir`, also a pilot-only limit);
  - 498 contracts tasks without top-level deliverables;
  - 323 tasks with more than 10 document files (11 of them also over 5 MB).

  Results describe this subset (short rubrics, small document sets), which
  is probably easier than LAB as a whole; they are not a claim about the
  full benchmark. Later campaigns should not narrow the task set without
  telling the operator what it excludes.
- Seed harness: LAB's tools and prompt on NexAU, without LAB's skill files;
  tool descriptions drop skill references; `finish` rejections are
  `{"status": "error"}` dicts (see `benchmark_public/ADAPTER.md` section 6).
- Trials that end in `agent_error` are still graded on their output (LAB's
  own runner would not score them).
- Campaigns 1 to 8: every evaluated trial's system prompt ended with 420
  characters that were not meant for it. The memory file's self-description
  (written for Evolve, mentioning grading rubrics) was appended with no
  separator: `...the work is complete.# Persistent harness memory ...`. Fixed
  from campaign 9: an empty memory file adds nothing, and the suffix starts
  after a blank line (AHE-019).
- Campaigns 1 to 9 ran before the GH Copilot Server patch of 2026-10-14
  (section 5m, AHE-021). Copilot ignored the configured reasoning effort,
  so "high" in their configs most likely ran as Copilot's default,
  "medium". Prompts over about 270k tokens were silently cut from the
  start. Campaigns 1 to 8 stopped at 200k tokens, so only campaign 9 was
  cut.
- Campaign 9 (`harness_lab/`): LAB's own system prompt with the docx, pptx
  and xlsx manuals, LAB's seven tool schemas and descriptions unchanged, and
  LAB's skill scripts copied into each sandbox's `/workspace/skills/`. The
  remaining differences from LAB: the model runs through NexAU, not LAB's
  loop; an unexpected tool argument returns an error, where LAB ignores it
  (AHE-020); LAB's `bash` description is kept even though it is inaccurate
  (LAB-004). Firm-knowledge tasks mount LAB's shared store read-only in
  place, as LAB does, and the run checks that the store is unchanged after
  each trial.
- Campaign 9 task scope: only the 250 firm-knowledge tasks (12% of LAB's
  2,010), one trial each, C0 only (section 5l). Its score is not comparable
  with campaigns 1 to 8, which used a different 166-task subset and harness.
- LAB-002: a judge prompt above 240,000 characters is scored `fail` without a
  model call. LAB-003: judge disagreement scores 0.5 but is a failing
  diagnostic. See `docs/HARVEY_LAB_BENCHMARK_ISSUES.md`.
- Harness Python runs on the host; the adapter's import and call bans are
  static checks, not an OS boundary (AHE-005).
- In campaign 4, Evolve and Explore tool results over 64,000 characters
  were shortened, with the full result saved under the run's own folder
  (AHE-014). From campaign 5 on, all meta agents use the original
  long-output limits (10,000 characters; 20,000 for the Explorers) and the
  original file tools behind a path guard (AHE-016).
- From campaign 5 on, meta agents have a shell in a network-less Docker
  container, and a failed per-task debugger is reported as "diagnosis
  unavailable" instead of blocking the round (AHE-016).
- From campaign 4 on, the primary metric is the mean criterion pass rate, not
  LAB's dual all-pass rate, which is still reported and breaks ties
  (section 5d).
- In campaigns 4 to 8, the Debugger and Evolve role supplements
  (`meta_supplements/`) still described the old target: Evolve's said the
  dual all-pass rate was the sole target and the criterion pass rate only a
  tie-breaker. The benchmark context and `ADAPTER.md` described the new
  metric, so both meta agents received conflicting objectives. The
  supplements are part of campaign identity, so they were fixed only after
  campaign 8 finished; the corrected wording applies from campaign 9
  (AHE-018).
- From campaign 4 on, all model calls in a process queue for one of 24 slots
  (AHE-015), and judge calls may use at most 18 of them.
- Campaigns 1 to 9b ran all trials as threads of one Python process, so
  host-side tools of concurrent trials waited for each other (AHE-025).
  Scores were not affected, but trials took longer than in LAB; 9b lost
  about 11% of trial time this way. From the campaign 10 restart on, each
  trial runs in its own process, and the 24 model-call slots are shared
  across these processes.
- AHE issues AHE-001 to AHE-020 and their branch-local fixes are listed in
  `docs/HARVEY_INTEGRATION_UPSTREAM_RECOMMENDATIONS.md`.
