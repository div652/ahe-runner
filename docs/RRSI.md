# RRSI: Regularized Recursive Self-Improvement

`algorithm.name: rrsi` runs RRSI (Xia et al., "RRSI: Regularized Recursive
Self-Improvement of Agent Harnesses", arXiv:2609.24972) on a NexAU harness.
The method is ported from the reference implementation,
[google-research/rrsi](https://github.com/google-research/rrsi)
(Apache-2.0). Its prompts and pure functions keep the reference's wording
and arithmetic; the differences are listed under
[Differences from the reference implementation](#differences-from-the-reference-implementation).

## The method in one page

RRSI keeps the harness edit space open and regularizes how the search moves
through it, so that what survives is a reusable mechanism rather than a fit
to the evolve set. The evolve set is the training split.

Before round 0 the runner evaluates C0 on train, prices its cost, and
calibrates the noise band delta. Then, for round t = 0..T-1
(T = `campaign.rounds`):

1. **Analyze (F_t).** The analyst reads the task table of the incumbent's
   training evaluation and dispatches read-only digesters, each on one
   task's trials through a failure, capability-gap, or success lens. It
   reports failure modes, capability gaps, and success habits.
2. **Directives.** From the edit history:
   - edit budget b_t = ceil(b_min + (b_max - b_min)/2 (1 + cos(pi t / T)));
   - stall flag sigma_t = 1 when the incumbent's score rose by at most delta
     over the last w rounds;
   - untried components, with reserved exploration slots when stalled;
   - prune set B_t: components exercised without a strictly positive gain
     in the last n_prune rounds, with the accepted machinery to remove.
3. **Draft m variants**, each from the incumbent, in its own workspace:
   - the proposer edits the harness and submits tagged edits (at most b_t);
   - the critic screens the diff for leakage, grader gaming, degenerate or
     undeclared changes, and unbounded work, before any evaluation; up to
     `repair_rounds` repair rounds;
   - the runner validates the candidate and freezes it as a checkpoint.
4. **Evaluate** every frozen candidate on the full training split.
5. **Select** (Algorithm 2). With dS = S' - S_t and dC = (C' - C_t) / C_t:
   - floor: S' >= S* - delta, where S* is the best incumbent score so far;
   - if dS > delta: dC <= beta0 + beta1 dS;
   - otherwise: w_s dS - w_c dC + w_n nu > 0, where nu counts structural
     component types (skill, memory, client_tool, subagent) never accepted
     before;
   - the admissible candidate with the highest S' becomes the incumbent;
     with none, the incumbent stays.

The outcome's pick is the final incumbent; its nominees are the incumbents
along the accepted path. The default selection policy is `algorithm` (ship
the pick, as the paper does); `selection.policy: validation` instead chooses
among the accepted path on the validation split.

## Score S

S is the weighted mean of trial scores over all trials of an evaluation,
sum(weight x score) / sum(weight), from the adapter's trial facts. With
weight = number of rubric criteria and score = fraction passed, S is the
pooled criterion pass rate, the paper's Harvey LAB metric. Without weights
it is the mean trial score.

Set `objective.check_metric` to the adapter metric S should equal; the run
stops if they differ, which catches a wrong score or weight in the adapter.

## Settings

```yaml
algorithm:
  name: rrsi
  settings:
    variants_per_round: 2          # m
    edit_budget_min: 1             # b_min
    edit_budget_max: 3             # b_max
    stall_window: null             # w; null: max(2, round(0.15 T))
    reserved_exploration_slots: 1  # m_draft
    prune_window: null             # n_prune; null: max(3, round(0.2 T))
    repair_rounds: 5
    beta0: 0.10
    beta1: 35.4
    cost: priced                   # priced | disabled
    noise:
      method: bootstrap            # bootstrap | repeats | fixed
      delta: null                  # only with method: fixed
      repeats: 2                   # only with method: repeats
      z: 2.0
      bootstrap_resamples: 2000
      bootstrap_seed: 7
    within_band:
      w_c: 15.0
      band_edge_cost_allowance: 0.377
      novelty_cost_allowance: 0.0333
      w_s: null                    # explicit override
      w_n: null                    # explicit override
    objective:
      check_metric: null
    roles:
      analyst_max_iterations: 80
      digester_max_iterations: 40
      proposer_max_iterations: 300
      critic_max_iterations: 12
      digests_per_call: 8
      digest_concurrency: null     # null: meta.max_concurrent_model_calls
  role_supplements:                # optional benchmark briefs, per role
    analyst: ./rrsi_supplements/analyst.md
    digester: ./rrsi_supplements/digester.md
    proposer: ./rrsi_supplements/proposer.md
    critic: ./rrsi_supplements/critic.md
```

The trial count k is `evaluation.train_trials`. RRSI requires
`models.evaluated.pricing` unless `cost: disabled`, and `train_trials >= 2`
for the bootstrap noise band. `ahe extend-rounds` is refused: the budget
schedule depends on T, so changing T would change completed rounds.

## Choosing the hyperparameters

| Parameter | Kind | How to set it |
|---|---|---|
| delta | measured | Never tune it: a smaller delta buys evolve-set score on noise alone. `bootstrap` resamples trials within each task of the C0 evaluation, delta = z sqrt(2) se(S); it estimates the difference between two evaluations of the same harness on the same tasks. `repeats` evaluates C0 R times independently (R - 1 extra training evaluations); with R = 2 the estimate rests on one difference. |
| k (`train_trials`) | noise against budget | `algorithm/calibration.json` reports `delta_by_trials`, delta for other trial counts (delta scales about as 1 / sqrt(k)). Choose the smallest k whose delta is below the smallest gain worth keeping. |
| beta0 | preference | Relative cost increase accepted for a gain beyond delta of zero size (paper: 10%). |
| beta1 | preference | Relative cost increase accepted per unit of score. S is a fraction, so 35.4 means about +35% cost per +1 point of S. It transfers between benchmarks that use the same kind of score. |
| w_s, w_c, w_n | derived | Only their ratios matter, and w_s only relative to delta. The paper's workspace values (1414, 15, 0.5 at delta = 0.004) mean: at a gain of exactly delta, up to 37.7% more cost; a never-accepted structural component earns 3.3%; with no gain, cost must not rise. The settings keep those two allowances and derive w_s = 0.377 w_c / delta and w_n = 0.0333 w_c. Set `w_s: 0` for the paper's coding instance, where a within-band gain earns nothing. |
| T, m | budget | Training trials = (1 + T m) k x training tasks, plus repeats. |
| b_min, b_max | rule | 1 and 3 or 4 (paper), far below what a proposer could bundle. |
| w, n_prune | rule | Several rounds, short relative to T; null scales them with T. |
| m_draft, z | rule | 1 of m = 2 slots; z = 2. |

The paper calibrated beta "against the opening rounds", that is, after
looking at early evolve-set results. These defaults avoid that.

The edit-budget schedule follows the reference code exactly. Its ceiling
reaches b_min only at t = T, so with T = 20, b_min = 1, b_max = 3, rounds
0-9 allow 3 edits and rounds 10-19 allow 2; the paper's text says the
closing rounds carry one edit. Lower `edit_budget_max` or raise T if single
edits in late rounds matter for your study.

## Components and tags

Every edit carries an RRSI component tag. The proposer declares it; the
runner keeps it only when the edit's files support it, and otherwise
re-tags the edit from its files:

| NexAU files of the edit | Tags it can carry |
|---|---|
| system prompt | prompt |
| an existing tool's description | prompt |
| an existing tool's implementation, helper modules | control_flow, output_plumbing, context_mgmt |
| middleware | control_flow, context_mgmt, output_plumbing |
| a new tool (description or implementation added) | client_tool |
| skill | skill |
| sub-agent | subagent |
| long-term memory | memory |
| `agent.yaml` only | config |

`agent.yaml` changes that register a new component do not count against the
component's tag. A variant holding a reserved exploration slot must have at
least one edit whose validated tag is untried.

Each edit is also one record in the candidate's `change_manifest.json`, with
its NexAU component class and `tag`, so the runner's generic checks and
history apply to RRSI candidates too.

## Roles and their supplements

| Role | Reads | Writes | Ends with |
|---|---|---|---|
| analyst | the task table; digests it requests | the report | `rrsi_submit_report` |
| digester | the incumbent's training evidence and raw evaluation | one digest (under 6000 characters) | `rrsi_submit_digest` |
| proposer | evidence, raw evaluation, the analysis, NexAU source, the `nexau-evolution-guide` skill | the candidate workspace | `rrsi_submit_candidate` |
| critic | the diff and declared edits | the verdict | `rrsi_submit_review` |

`rrsi_submit_candidate` checks the edit budget, unique edit ids, that the
edits cover every changed file exactly once, the tags, and any reserved
slot; writes `change_manifest.json`; commits; and runs the runner's generic
and the adapter's candidate validation. Any problem is returned to the
proposer to fix.

The prompts are generic. Benchmark knowledge (what the agent does, how it is
graded, what the harness contains, which safety mechanisms must stay) belongs
in `algorithm.role_supplements`, as the reference implementation's per-domain
briefs do.

## Artifacts

```text
campaign-artifacts/<campaign>/
├── algorithm/
│   ├── calibration.json          delta, weights, windows
│   ├── rounds/r000/
│   │   ├── directives.json       b_t, sigma_t, explore, prune set, history
│   │   ├── analysis/attempt-0001/analyst, digests/, analysis.json
│   │   ├── A/propose-0/attempt-0001/  candidate.json, the proposer's run
│   │   ├── A/critic-0/attempt-0001/   verdict.json, candidate/diff.patch
│   │   └── decisions.json        Algorithm 2 for every candidate
│   └── reports/
│       ├── frontier.json         trajectory, accepted path, every round
│       ├── history.jsonl         one record per edit (L_t)
│       └── attribution.jsonl     prediction hits and unpredicted regressions
├── checkpoints/C1 ...            every frozen candidate, with its parent
├── costs/C1-train.json           priced cost per trial
└── stages/rrsi.*                 one durable stage per step
```

## Differences from the reference implementation

| Reference | This runner | Why |
|---|---|---|
| Harness is a Python scaffold in a Git repository; candidates are worktrees and branches | Harness is a NexAU file harness; candidates are runner workspaces and immutable checkpoints in a lineage graph | the runner's harness ABI |
| Roles speak a strict-JSON action protocol | Roles are NexAU tool-calling agents; the same instructions, translated to tools | the runner's meta-agent runtime |
| The proposer sees the whole harness source in its context | The proposer reads files with tools | NexAU harnesses with skills and seeds can be large |
| The analyst digests rendered traces of the 60 worst and 6 best tasks | The analyst sees every training task in the table and requests digests over the runner's evidence bundle | the evidence bundle already covers every task |
| Tags are checked by regular expressions over the diff | Tags are checked against the NexAU component class of each changed file | the harness manifest knows the classes |
| A liveness smoke runs a few tasks | Generic validation (NexAU load, registrations, schemas, leakage) and the adapter's candidate validation | the runner's candidate contract |
| A candidate with more than 10% missing trials after a retry is dropped as `eval_invalid` | Infrastructure failures are retried by the stage; a candidate is never scored on partial coverage | the runner's evaluation contract |
| Cost is mean policy tokens per trial | Cost is priced tokens per trial (input, cache reads, cache writes, output) | output tokens cost several times input |
| w_s, w_c, w_n are fixed numbers per instance | w_s and w_n are derived from delta and two allowances (the paper's values at the paper's delta) | w_s means nothing without delta |
| w, n_prune fixed | default to the paper's values at T = 20, scaled with T | short pilots |
| Domain guards (non-compensatory checks, engineering instance) | not implemented | the workspace instance has none |
| `readjudicate` and `reevaluate` commands | `ahe retry-stage` reopens an infrastructure-blocked stage; re-adjudication with a new delta needs a new campaign | stage identity |
