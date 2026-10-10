# Production NexAU Meta-Agent Backend

This change completes the model-backed side of the reusable runner. The same
controller can now use either:

- `scripted`: deterministic, server-free lifecycle verification;
- `nexau`: production Source Explore, Web Explore, per-task Debugger, and
  global Evolve execution.

## Exact model transport

`RecordedExactModelClient` is the only client exposed to NexAU meta-agents.
It:

- sends non-streaming chat completions;
- requires the exact configured unprefixed model ID on every request;
- injects the configured reasoning effort;
- disables OpenAI SDK retries;
- records every request, response, error, requested model, and returned model;
- fails closed on a missing or mismatched observable model label;
- classifies timeouts, connectivity failures, rate limits, and server-side
  failures as resumable infrastructure errors.

Every model-backed stage attempt receives a fresh preflight. It records model
catalog evidence when available, an exact-model positive probe, and a random
nonexistent-model negative control. Random IDs and timestamps are attempt
evidence only; they do not alter semantic stage or campaign identity.

The preflight and call logs explicitly state the observable-identity
limitation: a proxy that synthesizes `response.model` may not expose
transparent upstream substitution.

The runner disables retries in its OpenAI client. A configured endpoint can
still perform its own internal retries; for example, a particular proxy
implementation may retry the same selected model before returning one HTTP
response. That endpoint behavior must be inspected and reported separately.

OpenAI-compatible transports are not assumed to preserve native system-role
semantics. When an endpoint translates system messages into ordinary user
messages, the campaign report must retain that protocol limitation.

## Explore lifecycle

The controller runs Source and Web Explore once before the first evolution
round.

The Source Explore Agent can read:

- installed NexAU source;
- the benchmark adapter implementation;
- the immutable seed harness;
- the harness manifest;
- the packaged AHE method reference.

The Web Explore Agent uses benchmark-safe context and public research tools
with configured leakage-term guards.

Each agent must write and validate a substantial `SKILL.md` with YAML
frontmatter. The runner then removes write permission and records file/tree
hashes. Evolve receives read-only attempt-local copies registered in its
NexAU `skills` list. It must call `LoadSkill`; the guides are not merely plain
files mounted beside the harness.

Partially completed Explore work is reusable. A valid skill is hash-recorded
and skipped when only the other explorer needs to resume.

## Debugger lifecycle

The Debugger operates only on adapter-materialized training evidence. Each task
has a stable hash-derived directory containing:

- the exact request and request hash;
- all recorded model calls;
- the validated structured diagnosis;
- the raw final response.

The output contract identifies the earliest failure, direct evidence, root
cause, recurrent-pattern key, confidence, falsifier, and bounded
component-level recommendations. Invalid JSON receives at most two explicit
correction turns; transport calls themselves have no hidden retries.

Task results resume independently. A result is reused only when its internal
request hash matches the current prompt, evidence, model settings, checkpoint,
and benchmark context.

## Evolve lifecycle

Evolve receives:

- full campaign history;
- current aggregate and per-task training evidence;
- all Debugger results;
- prior transition attribution;
- both native Explore skills;
- the current workspace and parent checkpoint;
- archived evidence from an interrupted prior attempt;
- installed framework and adapter source;
- the method reference.

Only the Git workspace is writable. Validation/test artifacts are never
mounted.

The full system prompt requires evidence synthesis, one coherent intervention,
early durable Git progress, final candidate validation, an accurate committed
`change_manifest.json`, a clean workspace, and explicit completion through the
stop tool.

If an attempt loses the endpoint or times out, the controller archives dirty
state and call evidence. The next attempt starts from the prior attempt's last
committed `HEAD` and can inspect the recovery archive. A low benchmark reward
does not trigger this retry path.

## Restricted tool surface

Meta-agents use virtual roots rather than unrestricted filesystem access.
Path traversal and unknown roots are rejected. The tool surface provides:

- bounded list/read/search;
- exact write/replace and empty-directory deletion;
- Git status, diff, and coherent commit;
- generic candidate validation;
- leakage-guarded public web search/read;
- role-specific completion validation.

The Evolve progress gate counts research after mutations and directs the agent
to commit coherent work before continuing indefinitely. It does not treat a
half-valid commit as success; final validation and clean committed state remain
mandatory.

## Context management

The NexAU middleware counts model-context tokens. Above the configured
threshold it replaces only older tool-result contents, preserving recent
iterations, tool-call structure, assistant reasoning, and the ability to
re-read authoritative artifacts.

## Durable artifacts

Each meta-agent runtime preserves:

- generated agent YAML;
- full system prompt;
- exact model-call records;
- complete NexAU conversation trace;
- in-memory NexAU span snapshot;
- final response;
- preflight;
- role-specific outputs.

These artifacts support exact recovery and post-run diagnosis without turning
partial output into a successful checkpoint.
