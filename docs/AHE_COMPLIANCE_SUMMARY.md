# AHE Runner: short audit report

**Bottom line:** The idea is sound, and `ahe-runner` has much of the right
structure. But it is not yet a faithful, ready-to-use version of original AHE.
The biggest gaps are in how it investigates failures, checks whether edits
worked as predicted, and lets the next agent undo harmful changes.

This assessment covers implementation `a7704c4`, compared with the supplied
paper and original AHE code, not the later Tau2 adaptations.
[The detailed report](AHE_COMPLIANCE_AUDIT.md) has source references,
reproductions, and a prioritized repair plan. No implementation fixes were
made as part of this audit.

## What it already gets right

The runner takes an initial harness, evaluates it, researches improvements,
creates edited versions, evaluates those versions, and selects a result.
It evaluates the final edited version too, rather than leaving that last
change unchecked.

Users can provide NexAU prompts, tools, skills, middleware, sub-agents, and
memory. Their benchmark can sit above the existing agent loop through a
bridge they implement (an **adapter**). That is the right overall design.
Keeping the original harness as a possible final selection is also good.

## The most important AHE deviations

**1. The Debugger cannot investigate like the original.** Original AHE lets it
inspect execution records and compare successful and unsuccessful attempts.
Here it receives one bundle of text and returns a diagnosis. If that bundle
contains a path to a trace, it cannot open the trace. Other research tools
also refuse large files even when asked for just a few lines.

**2. Predictions do not reliably connect to results.** The editing agent is
told to write predictions in one format, but the comparison code expects
another. Predictions following the prompt are ignored. Results are summarized
for the whole update instead of each meaningful edit (**per-change
attribution**).

**3. Undoing a bad idea is harder than in original AHE.** Old versions exist,
but the editing agent does not get the historical files and Git operations
needed to conveniently restore them. The original instructions to keep,
improve, or undo-and-change-direction are also weakened (**rollback and
pivot**).

One important nuance: the paper describes automatic rollback, but the public
code normally leaves that decision to the editing agent. Not adding automatic
rollback is therefore not, by itself, a new departure from the public code.

## Separate problems with plug-and-play operation

Several ordinary integration paths need fixing:

- A failed "I am finished" tool call can stop the agent instead of letting it
  repair missing work. A benchmark action can also be followed by another
  model call before the benchmark has supplied its actual result.
- Files available to the research/editing agents include the sample adapter's
  hidden evaluation cases. Keeping result folders hidden is not sufficient
  (**held-out data leakage**).
- Some settings and evidence records are trusted more than the documentation
  suggests. Valid helper modules and nondefault config filenames can also
  fail unexpectedly.
- Task-specific guidance exists, but there is no complete project-level way
  to customize all four roles while preserving the fixed AHE instructions.

The starter example is useful for rehearsing the workflow, but it scores
phrases in prompt files rather than actually running the evaluated agent.
Changing the meta backend does not change that. The README's live-backend
switch also misses a required web-search setting.

## Where benchmark tools should live

**Keep their definitions and implementations owned by the benchmark.**
Expose their public schemas to the model when constructing the session,
without editing the harness files (**runtime tool overlay**). Prompt text can
explain how to use them, but should not replace structured tool declarations.

For example, the benchmark owns `cancel_order(order_id)`. The harness can
improve when it requests confirmation or checks the result; it must not
redefine cancellation to make itself score better. The runner already makes
the right architectural choice here; its execution boundary needs repair.

## What I would do next

First restore the Debugger's evidence access, repair prediction checking,
and make rollback usable. Fix the demonstrated runtime and evidence-boundary
failures. Then provide one small, genuine benchmark that runs entirely through
the public adapter interface, with matching newcomer instructions.

Your idea does not need a new foundation. It needs a narrower initial promise:
**bring a compatible NexAU harness and benchmark, search for improvements,
and receive the best verified candidate, possibly the original.**

Do not promise inevitable improvement or universal support for every UI and
multimodal agent. Track cost and reliability alongside score, and separate
trusted local use from accepting arbitrary uploaded code. Those are practical
boundaries, not reasons to delay building a useful first version.
