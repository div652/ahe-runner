# AHE Follow-Up: Plain-Language Summary

> **Latest independent check:** `0.3.0` at `25f8c09` fixes the major failures.
> Two smaller edge cases and a release-version mismatch remain; see
> [Current 0.3.0 verdict](#current-030-verdict). The earlier assessment below is
> preserved as history.

> **Repair status:** This document is the historical summary of the audit of
> `5380f65`. Findings F01-F07 and the bundled-Debugger historical correction
> were accepted and repaired on `main`; implementation choices are recorded in
> [`AHE_COMPLIANCE_REAUDIT_REPAIR_PLAN.md`](AHE_COMPLIANCE_REAUDIT_REPAIR_PLAN.md).
> The descriptions below explain the pre-repair behavior.

**Bottom line:** This is a substantial improvement. The basic design is now
much closer to AHE, and the benchmark adapter works with real NexAU execution. I would not,
however, call every repair complete yet. Several checks work on their main
path but miss other supported ways of doing the same thing.

This review covers commit `5380f65`. The detailed findings, source references,
reproductions, and repair criteria are in
[`AHE_COMPLIANCE_REAUDIT.md`](AHE_COMPLIANCE_REAUDIT.md).

## What you really fixed

The system now records a prediction for each edit and compares that prediction
with the next evaluation. It also exposes historical checkpoints and lets
Evolve restore selected files rather than merely telling it to “consider
rollback.” These are meaningful repairs to the evolution process.

The Debugger is now a real investigator with file-reading tools. Benchmark
results are tied much more carefully to the correct checkpoint, tasks, trials,
and artifacts. Public adapter documentation is separated from private adapter
code, all four roles can receive domain guidance, and final exports are
verified before publication.

I also joined the real runtime example to actual NexAU execution and the actual
OpenAI SDK, with model replies simulated locally. Direct tasks and the
adapter-owned `lookup_code` action both worked, including resuming the same
session with the real tool-call ID. So the example is no longer just a
controller demonstration.

## What still needs attention

### 1. Model settings can change after the runner checks them

The runner checks the intended settings, but the SDK can merge additional
settings afterward. A normal candidate configuration passed validation and
sent a request with:

| Setting | Campaign required | Actual request contained |
|---|---:|---:|
| Output-token allowance | 4,096 | 104,096 |
| Temperature | 0.2 | 1.9 |
| Reasoning | Provider default | High |

This matters directly to AHE: a better score should come from a better harness,
not quietly allowing the model more effort or different sampling. Fix the
check at the final request boundary. **This is a high-priority repair.**

### 2. An older supported hook can still make the agent continue too early

The modern middleware path is improved, but NexAU also supports older hook
registrations. Through one of those hooks, I reproduced two model calls before
the adapter received the requested benchmark action.

The second call already told the model that the tool had produced no result,
even though the adapter had not executed it yet. The fix needs to cover every
supported hook path, not just the modern middleware list.

### 3. A configured API key can appear in files Evolve is allowed to read

The Debugger writes a runtime YAML file containing the resolved API key.
Evolve can read that directory through its normal file tool.

I checked this using a fake key, not a real credential. The default local
placeholder is harmless, but a real provider key would be exposed through the
same path. Keep the actual key in memory or in a private location outside
model-readable artifacts.

### 4. Correct file links can still accompany incorrect trial labels

The new checks prove that evidence points to the correct evaluation files.
They do not yet prove that the trial's stated outcome, score, and stopping
reason agree with those files.

For example, I changed a successful trial's evidence to say “failed, score
zero, action limit reached.” The original result and the verified `1/2` task
score were unchanged. The evidence verifier accepted the contradictory
bundle.

This could happen through an ordinary conversion bug. Bind those trial facts
to the benchmark's verified results, without making the generic runner
understand every benchmark's private scoring logic.

### 5. “The Debugger read its evidence” is not fully enforced

The Debugger can now read traces, which is good. But the final check mostly
validates the list of files it claims to have read, not its actual citations.

A real Debugger execution read only the task summary and submitted invented
claims about event 999 in raw traces it had never opened. That diagnosis was
accepted. A separate read returning zero bytes also counted as evidence access.

Check cited files and locations against meaningful reads, and check whether
passing and failing trials really exist before accepting a claim that a
comparison was performed or unavailable.

That cannot prove the model's reasoning is correct. It can prevent it from
satisfying the required procedure with unsupported references.

### 6. One ordinary Python pattern still blocks onboarding

A helper works when imported at the top of a tool's Python file. Move the same
import inside the tool function, and conformance says the helper is unused.
Actual NexAU executes it correctly.

Loading a dependency only when the function runs is a normal Python technique
(a **lazy import**). Supporting it should not require a user to invent a fake
tool registration or rewrite otherwise valid harness code.

### 7. Repeated large file writes can defeat context cleanup

Cleanup works for large old file-read results. But previous file contents stay
in the conversation when they were arguments to `write_file`.

In a bounded example, cleanup left about **158,000 tokens against a 144,000
target**, even though the newest turn alone was only about 32,000 tokens.
More writes can eventually exhaust the hard context limit.

This is a long-run reliability limitation, not proof that original AHE solved
the same problem. It need not block a short pilot, but the current cleanup
should not be described as a general guarantee.

## One historical correction needs correcting

The original public repository's bundled Debugger **was a file-tool agent**.
I checked the original Git revision, not a later local installation. Its
configuration registers read/search/list tools and allows 25 iterations.

The controller calls `adb` once for a task, but that command launches an agent
that can make multiple model calls and inspect files. One command invocation
is not the same as one model response.

The accurate description is that your runner replaces that with a more bounded,
structured Debugger and changes its default iteration budget to 80. The fidelity
documentation should reflect this.

## What I would not change

**Keep agent-decided rollback and immutable Explore outputs.** Those are
reasonable choices here. I am also not asking for separate experiments to
prove the isolated effect of every edit; original AHE's prediction matching
does not provide that proof either.

The benchmark-tool ownership design is right: official schemas are exposed as
structured tools, while their execution stays in the adapter and outside the
editable harness.

The project is now a credible foundation for **text-and-tool benchmarks**.
Screenshot-based UI tasks, audio, changing tool sets, and interruptions may
need a broader adapter/runtime interface; they should not require forking the
central NexAU loop.

**Recommended next move:** close the request, action-handoff, and credential
gaps first; then tighten trial evidence and Debugger checks. Preserve the
working architecture. The remaining work is making its promises hold across
supported paths, not starting over.

## Current 0.3.0 verdict

**Very close, but not quite a complete all-clear.** I checked the repaired
implementation at `25f8c09`. The major model-setting, premature-action,
credential, trial-label, and long-context failures are fixed, and the original
Debugger history is now described correctly. I found no new core AHE-method
deviation in these repairs.

Three localized items remain:

1. **The Debugger can still cite a line it never received at a read boundary.**
   If the byte allowance ends exactly after line 1, the read tool can mark line
   2 as read as well. I reproduced an accepted citation to that entirely unread
   second line. The normal unread-file and empty-read checks work; this is a
   narrower bookkeeping error.
2. **Lazy imports work for simple modules, but not every normal package.**
   Importing a nested helper inside a tool function can make conformance reject
   the package's `__init__.py` as unused, even though Python executes it and the
   NexAU tool works correctly. The dependency scan needs to include those
   package initialization files.
3. **The release identifies itself inconsistently.** The built wheel says
   `0.3.0`, but the runtime constant and fresh campaign records still say
   `0.2.0`. This is a small metadata fix, not a broken evolution algorithm.

The evidence is stronger than a passing test count: I exercised the actual
NexAU runtime and SDK with locally generated responses, including a Debugger
that first submitted unread citations, received an error, then read the files
and successfully repaired its submission.

**My recommendation:** fix these three items, then proceed to a small,
explicitly scoped live pilot rather than redesigning anything. The remaining
live uncertainty is whether the agents produce useful research, diagnoses,
and improvements on your benchmark; offline checks cannot establish that.

Exact source locations, reproductions, and completion criteria are in the
[detailed closure check](AHE_COMPLIANCE_REAUDIT.md#release-030-closure-check).
