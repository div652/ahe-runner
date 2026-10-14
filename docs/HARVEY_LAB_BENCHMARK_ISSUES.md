# Harvey LAB benchmark issues and discussion notes

This is a cumulative record for Harvey LAB maintainers. It lists LAB behaviors
found while integrating LAB with AHE Runner: confirmed gaps, the local repairs
the operator approved, and open semantic questions for discussion. It does not
claim Harvey has accepted any change.

AHE Runner issues are recorded separately in
`docs/HARVEY_INTEGRATION_UPSTREAM_RECOMMENDATIONS.md`. That file keeps the full
LAB-001 record for historical continuity; this file summarizes it.

Inspected LAB source: branch `divyanshu_harvey_evolution`, commit
`86678eba6fcbc6c03958742c6e9e75737514f513` (base `d7e56d455` plus the
LAB-001 hook only). Line numbers below refer to that commit.

## Working agreement

The same procedure as for AHE issues applies:

1. Stop, explain the issue plainly, and wait for the operator.
2. After the operator understands and decides, record it here.
3. Apply the smallest approved local repair, preferring the Harvey adapter
   over LAB source edits, and commit it with an explanation before testing.
4. Record the outcome of the offline checks without erasing failures.

Semantic questions without a defect are recorded as discussion items, together
with the choice the integration made in the meantime.

## Register

| ID | Kind | Summary | Operator decision | Local status |
|---|---|---|---|---|
| LAB-001 | Defect | The filename helper hard-codes the Anthropic SDK and silently swallows its API failure | Repair authorized on 2026-10-12 | Fixed: LAB hook `86678eba6`, AHE callback `f0e3b0b` |
| LAB-002 | Defect | Output too large for the judge has no defined score; LAB comparison silently drops such runs | Option A with a 240,000-character limit, chosen 2026-10-12 | Implemented in the Harvey judge wrapper (`harvey_ahe/grading.py`); LAB unchanged |
| LAB-003 | Discussion | Dual-judge disagreement scores 0.5 in the headline but "not all-pass" in the per-run flag, with no explicit label | Disagreement treated as failing in diagnostics; headline unchanged, chosen 2026-10-12 | Implemented in Harvey adapter metrics; LAB unchanged |
| LAB-004 | Discussion | The `bash` tool description says the working directory persists and suggests installing packages; each call is a fresh shell in `/workspace` with no network | Recorded 2026-10-14; campaign 9 copies the description unchanged for exactness | Not changed; the Harvey seed harness (`harness/`) already uses an accurate description |
| LAB-005 | Discussion | Tool outputs have no size limit; LAB relies on the provider to reject an over-long prompt, so a transport that silently cuts the prompt instead (as GH Copilot Server did) goes unnoticed | Recorded 2026-10-14; `harness_lab` keeps LAB's behavior; the transport was fixed instead (AHE-021) | Not changed |
| LAB-006 | Discussion | An empty output passes the "Qualifying set — precision" criterion that 241 of 250 firm-knowledge tasks have, so a run that produces nothing still earns a non-zero criterion pass rate | Recorded 2026-10-14; the integration matches LAB (no zero-score rule for missing output) | Not changed |

## LAB-001: filename helper transport (summary)

When an expected deliverable name is not matched exactly or fuzzily, LAB asks
a helper model to map expected names to produced files. At the base commit,
that helper always used the Anthropic SDK and treated any API failure as "no
match", so a routing failure looked like a missing deliverable. The local
repair added an optional `deliverable_matcher` callback to `score_rubric`,
leaving legacy behavior unchanged. The Harvey integration supplies a recorded,
exact-model Copilot callback whose errors propagate. Full evidence and test
results are in the AHE recommendations file, section LAB-001.

Recommendation for Harvey: keep an injection seam for grader-side helper
models; consider failing grading on helper errors in a versioned protocol.

## LAB-002: output too large for the judge has no score

### Plain-language summary

LAB pastes the agent's complete deliverable text into every judge prompt
without any size limit. If an agent writes a very large file, for example a
2 MB memo, the judge API rejects the request as too long. Each criterion is
then recorded as "error", LAB refuses to write a score, and it suggests
re-running, which cannot succeed for the same output. LAB's comparison report
then silently leaves that run out of its averages. An ungradeable output
therefore has no defined outcome (undefined scoring) and is missing from
aggregates (survivorship bias).

### Evidence

- `lab_core/evaluation/scoring.py:444-463`: `_load_all_output` concatenates
  every output file's text. It skips only build artifacts and lockfiles, with
  no size bound.
- `scoring.py:533-549`: each criterion's prompt includes the listed
  deliverables' full text, or the full output when it lists none.
- `scoring.py:550-567`: any judge exception becomes verdict `"error"` with
  reasoning `"grading error: <Type>: <message>"`.
- `lab_core/evaluation/run_eval.py:227-233`: if any criterion has an error,
  `scores_dual.json` is not written: "Re-run the evaluation to grade them."
- `lab_core/evaluation/compare.py:174-192`: the comparison scans for existing
  score files only. A run without a score file is not counted or reported.
- The Copilot judge path (`judge.py:251-283`) sends `max_tokens=64000`, which
  many providers reserve inside the same context window as the input. A 128k
  context leaves about 64k input tokens.

On the AHE side, a context-length rejection is HTTP 400. AHE does not treat
that as a retryable infrastructure error (`ahe_runner/meta/model_client.py:259-293`),
so without a local rule one oversized candidate output would halt an
evolution campaign.

### Approved local repair (Harvey adapter only)

The operator chose option A on 2026-10-12:

- A fixed limit of **240,000 characters** on each fully formatted judge prompt
  (LAB's `rubric_criterion` template with all variables filled in). That is
  about 60k tokens at 4 characters per token.
- In the Harvey judge wrapper, a criterion whose prompt exceeds the limit is
  scored **fail** without any model call. Its reasoning starts with a fixed
  marker naming the limit and the measured size.
- The rule is deterministic, needs no model call, and applies identically to
  every checkpoint, including the C0 baseline.
- Over-limit criteria are counted separately in each judge's score file and in
  the adapter metrics, so they are never confused with genuine judge failures.
- Other non-transient judge errors (authentication, unknown model, malformed
  request) still halt the stage, because they indicate a setup mistake rather
  than candidate behavior. Transient errors and unparseable judge replies are
  retried within the attempt, then reported as infrastructure failures.
- This is a documented scoring deviation from native LAB, which has no score
  for these runs. LAB source is unchanged.

Residual limitation: the filename-helper prompt lists produced file names and
is not covered by this limit. An output with an extreme number of files could
still overflow the helper. That case halts the stage (fail-closed) rather than
producing a fabricated score.

### Offline verification (2026-10-12)

`tests/test_harvey_grading.py` runs LAB's real `score_rubric` against a
scripted model client, with no network or model calls. It covers:

- an over-limit prompt scored fail with no model call, counted per judge;
- the exact boundary of LAB's formatted prompt length;
- dual aggregation, including a 0.5 disagreement that is not all-pass;
- error classification: transient and unparseable errors are retryable;
  integrity errors stay integrity errors; other errors are configuration
  errors;
- in-attempt retries that keep each failed try's directory;
- rejection of non-LAB judge settings and identical judges.

First run: 47 passed, 1 failed. The failing test's precondition was wrong:
two test criteria had equal-length titles. That was fixed in a separate
commit. Second run, together with the AHE-003 and LAB-001 suites: 48 passed.
Ruff and Pyright (0 errors) are clean on the changed files.

### Recommendations for Harvey

1. Define an explicit outcome for outputs that cannot be graded because of
   their size: either "fail" or a separate "ungradeable" status that still
   counts in denominators.
2. Bound judge input deterministically. For example, cap each deliverable's
   text with a visible truncation notice, and document the cap as part of the
   protocol version.
3. Make `compare` report expected-but-unscored runs instead of skipping them,
   so averages cannot silently exclude failures.

## LAB-003: dual-judge disagreement semantics (discussion)

### Plain-language summary

LAB grades each run with two judge models. When one judge says every
criterion passed and the other does not, LAB reports it in two different ways:

- The headline score averages the two judges' pass/fail results, so the run
  scores **0.5** (half credit).
- The per-run `all_pass` flag requires both judges, so the same run is
  **not** all-pass.

Nothing labels the run as a disagreement (judge disagreement). Whether a
disagreement counts as half a success or as a failure depends on which field a
consumer reads.

### Evidence

- `lab_core/evaluation/run_eval.py:235-263`: `dual_all_pass_rate` is the mean
  of per-judge all-pass values; `all_pass` is `dual_ap == 1.0`.
- `docs/eval-strategies.md:121-131`: one task's dual all-pass rate is 0.0,
  0.5, or 1.0; the aggregate `all_pass` field requires both judges.
- `docs/eval-strategies.md:149-159`: the canonical headline is the averaged
  all-pass rate; strict both-agree all-pass is reported separately.
- `lab_core/evaluation/compare.py:128-170`: the comparison reads both
  `dual_all_pass_rate` (as the score) and `all_pass` (as a boolean).

### Choice made by the integration (operator, 2026-10-12)

- Primary metric: LAB's headline, the mean of `dual_all_pass_rate` over all
  task-trials. Disagreements contribute 0.5, exactly as in LAB.
- Per-trial diagnostic label: `passing` only when both judges all-pass, which
  is LAB's `all_pass`. Any disagreement, and any run where neither judge
  all-passes, is labelled `failing`. No third label is used.
- Reported alongside: the both-agree all-pass rate, the disagreement count,
  pooled and macro criterion pass rates, and per-judge statistics.

### Questions for Harvey

1. Which field is canonical for model selection: the averaged headline or the
   strict both-agree flag?
2. Should disagreements be first-class, for example a per-run
   `judges_disagree` flag and a published disagreement rate?
3. Is per-task inter-judge agreement tracked, to identify rubric criteria
   whose wording judges read differently?

## LAB-004: the `bash` tool description is inaccurate (discussion)

### Plain-language summary

LAB tells the model: "Use for running scripts, installing packages, file
manipulation, and any shell operation. The working directory persists between
calls." Neither part holds in LAB's sandbox. Each call starts a new shell in
`/workspace`, so a `cd` is lost, and the container has no network, so package
installs fail. A model that trusts the description may waste turns.

### Evidence

- `lab_core/harness/tools.py` lines 40-45: the description.
- `lab_core/sandbox/sandbox.py`: the container starts with
  `--network=none` (line 348); `exec` runs
  `podman exec -w /workspace ... bash -c "timeout ... bash -lc <command>"`
  for every call (lines 418-432).

### Choice made by the integration

The Harvey seed harness (`harness/`, campaigns 1 to 8) uses an accurate
description. The LAB-exact harness (`harness_lab/`, campaign 9) copies LAB's
description unchanged, because the operator asked for an exact copy.

### Suggestion for Harvey

Describe the real behavior: each call is a fresh shell starting in
`/workspace`, and there is no network.

## LAB-005: no limit on tool output size

### Plain-language summary

LAB passes every tool result to the model in full. A single `bash`, `read`
or `parse-doc` call on a large firm-knowledge file can return hundreds of
thousands of tokens. LAB relies on the provider to refuse a prompt that is
too long: it stops the run cleanly and grades it only when the error says
"prompt is too long" or "context_length_exceeded"; any other error crashes
the run. This works only if the provider refuses. In campaign 9 the GH
Copilot Server transport instead deleted the start of the prompt, including
the system prompt and the task, and the run continued without them
(AHE-021, runbook section 5m). This happened in 15 of 101 graded trials,
and 32 more attempts failed with transport errors caused by the same cut.

### Evidence

- `lab_core/harness/tools.py`: `_bash` (line 456), `_read` (472) and
  `_parse_in_sandbox` (526) return the full output.
- `lab_core/sandbox/sandbox.py` line 436: `exec` captures the full output.
- `lab_core/harness/adapters/copilot.py` line 172: tool results are sent
  unchanged. This adapter is a local addition on this branch (`d7e56d455`),
  not part of Harvey's upstream LAB; with the unpatched GH Copilot Server it
  would be cut in the same way.
- `lab_core/harness/agent_loop.py` lines 73-81: the only overflow handling.

### Choice made by the integration

`harness_lab/` copies LAB exactly, with no size limit. The transport was
fixed so that over-long prompts reach the model in full (up to 921,793
tokens for gpt-5.4). No size limit was added to the tools.

### Suggestion for Harvey

Cap or page large tool outputs (for example, return the first part plus a
note on how to read the rest), and document that a run depends on the
provider rejecting over-long prompts.

## LAB-006: an empty output passes the precision criterion

### Plain-language summary

241 of the 250 firm-knowledge tasks have exactly one criterion titled
"Qualifying set — precision", worded as "Does not assert any qualifying
matter outside: ...". A run that writes no output asserts nothing, so the
judges pass it. LAB has no rule that a missing deliverable scores zero, so
such a run still gets a non-zero criterion pass rate. The precision
criterion is on average 19% of a task's criteria (up to 50%; at least 25% in
90 tasks). The all-pass rate is unaffected because the other criteria fail.

In the campaign 9 pilot (runbook 5n), tasks 174 and 222 filled the model's
context, stopped without writing `response.md`, and scored 0.25 and 0.33,
with both judges agreeing.

### Evidence

- `tasks/firm-knowledge/tasks/*/task.json`: 241 tasks have one criterion
  whose title contains "precision"; for task 174 it reads "Does not assert
  any qualifying matter outside: 1041-00003 ...".
- `lab_core/evaluation/scoring.py` line 541: a missing deliverable is sent
  to the judge as "(File not found: ...)" and scored like any other output.
- `lab_core/harness/tools.py` lines 252-259: after the finish gate gives up,
  "the judge scores the missing file as "(File not found)"".
- Pilot judge reasoning for the passed criterion: "no matters outside the
  allowed set were asserted".

### Choice made by the integration

The Harvey judge wrapper matches LAB: missing output is graded as LAB grades
it, with no zero-score rule. Report criterion pass rates with this floor in
mind, and prefer the all-pass rate when comparing failed or truncated runs.

### Suggestion for Harvey

Score a criterion as failed when its deliverable is missing, or make the
precision criterion conditional on at least one asserted item.
