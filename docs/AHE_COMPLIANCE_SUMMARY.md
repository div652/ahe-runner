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

One shared harness is improved across the task set, rather than being tailored
separately to each example. Version and result records make decisions
inspectable. Selecting candidates on tasks kept separate from training is
also a useful addition, even though it is not the paper's exact experiment.
Not every difference from the original is a problem.

## The most important AHE deviations

**1. The Debugger cannot investigate like the original.** Original AHE lets it
inspect execution records and compare successful and unsuccessful attempts.
Here it receives one bundle of text and returns a diagnosis. If that bundle
contains a path to a trace, it cannot open the trace. Other research tools
also refuse large files even when asked for just a few lines.

For example, comparing a successful attempt with a failed one should reveal
where their actions diverged and why that mattered. Without access to those
records, a convincing diagnosis can still be a guess. The fix is better access
to evidence, not simply a more detailed instruction to "diagnose carefully."

**2. The original's per-edit prediction check is weakened.** Original AHE asks
for predicted fixes and risks for each edit. After the next evaluation, its
code compares those expectations with task results and gives each edit a
verdict (**per-change attribution**). This is actually implemented, not just
requested in a prompt.

In `ahe-runner`, the editing agent is told to write predictions in one format,
but the comparison code expects another. Predictions following the prompt are
ignored, and results are summarized for the whole update instead of each edit.

Importantly, original AHE evaluates all edits together, not one at a time.
If two edits both predict that task X will improve, and X improves, both can
receive credit. Its check is a useful but imperfect signal, not proof of which
edit caused the result. The needed fix is to restore that prediction check,
not to demand a stronger causal experiment that original AHE does not perform.

**3. Undoing a bad idea is harder than in original AHE.** Old versions exist,
but the editing agent does not get the historical files and Git operations
needed to conveniently restore them. The original instructions to keep,
improve, or undo-and-change-direction are also weakened (**rollback and
pivot**).

The agent should be able to undo one harmful edit while keeping an unrelated
improvement. Retaining backup copies for a human is not enough.

One important nuance: the paper describes automatic rollback, but the public
code normally leaves that decision to the editing agent. Not adding automatic
rollback is therefore not, by itself, a new departure from the public code.

## Separate problems with plug-and-play operation

Several ordinary integration paths need fixing:

- A failed "I am finished" tool call can stop the agent instead of letting it
  repair missing work. A benchmark action can also be followed by another
  model call before the benchmark has supplied its actual result. For an
  account lookup, that means reasoning further before the account information
  has arrived.
- Files available to the research/editing agents include the sample adapter's
  hidden evaluation cases. Keeping result folders hidden is not sufficient
  (**held-out data leakage**). A better score on those cases could then reflect
  prior exposure rather than an ability to handle genuinely unseen tasks.
- Some fixed model settings are not fully enforced, and evidence can be
  accepted without checking which evaluation it came from. That makes
  comparisons less trustworthy. Ordinary helper files and valid alternative
  config filenames can also fail unexpectedly.
- Task-specific guidance exists, but there is no complete project-level way
  to customize all four roles while preserving the fixed AHE instructions.
  The useful middle ground is to keep the core procedure fixed while letting
  each project add its terminology, research sources, and domain guidance.

The starter example is useful for rehearsing the workflow, but it scores
phrases in prompt files rather than actually running the evaluated agent.
Changing the meta backend does not change that. The README's live-backend
switch also misses a required web-search setting.

So the example demonstrates that the stages can be connected, not that they
can improve a real agent. The missing demonstration is an actual task run
producing execution records, an edit based on those records, and another run
showing what changed.

## Where benchmark tools should live

**Keep their definitions and implementations owned by the benchmark.**
Expose their public schemas to the model when constructing the session,
without editing the harness files (**runtime tool overlay**). Prompt text can
explain how to use them, but should not replace structured tool declarations.

For example, the benchmark owns `cancel_order(order_id)`. The harness can
improve when it requests confirmation or checks the result; it must not
redefine cancellation to make itself score better. The runner already makes
the right architectural choice here; its execution boundary needs repair.

The separation is about ownership, not hiding tools from the agent: it should
know how to call an official action, but cannot rewrite what that action does.

## What I would do next

First restore the Debugger's evidence access, repair prediction checking,
and make rollback usable. Fix the demonstrated runtime and evidence-boundary
failures. Then provide one small, genuine benchmark that runs entirely through
the public adapter interface, with matching newcomer instructions.

That is a more useful next milestone than adding many more features. It would
show that a user can supply a harness and benchmark without patching runner
internals, and that the resulting edits actually follow the intended process.

Your idea does not need a new foundation. It needs a narrower initial promise:
**bring a compatible NexAU harness and benchmark, search for improvements,
and receive the best verified candidate, possibly the original.**

Do not promise inevitable improvement or universal support for every UI and
multimodal agent. Track cost and reliability alongside score, and separate
trusted local use from accepting arbitrary uploaded code. Those are practical
boundaries, not reasons to delay building a useful first version. A score gain
from extra retries may be useful, for example, but its additional cost should
be visible rather than mistaken for a free improvement.
