# Runtime-Backed Evaluated-Agent Example

The generated project contains two intentionally separate configurations:

| Configuration | Purpose | Evaluated-model calls |
|---|---|---:|
| `ahe.yaml` | Offline controller/lifecycle rehearsal with phrase scoring and the scripted meta backend | 0 |
| `ahe.runtime.yaml` | Genuine NexAU evaluated-agent sessions plus model-backed meta-agents | Yes |

Changing only the offline file's meta backend is not a valid live campaign.
The benchmark adapter must call the supplied `EvaluatedAgentRuntime`.

## What the runtime example executes

`RuntimeExampleAdapter` has one task of each kind per split:

1. **Direct task:** the evaluated agent must return one exact short response.
2. **Protected-action task:** the evaluated agent must call
   `lookup_code(label)`, wait for the adapter-owned result, and ground its final
   response in that result.

The action schema is injected into the in-memory NexAU config and sent through
the model API `tools` field. It is not appended to prompt text or written into
the harness. The adapter executes the action and resumes the same session with
the exact tool-call ID.

Each trial persists:

- the runtime session manifest and recorded model calls;
- a task-local result;
- a JSONL trace with user input, model outputs, action arguments/results, and
  final text;
- an independently recomputed exact-match reward;
- schema-V3 evidence linked to the verified evaluation artifacts.

Validation and test prompts, protected code values, and verifier fixtures are
not exposed through meta-agent source roots.

## Run it

Install the production Web Explore dependency:

```bash
pip install -e '.[web,dev]'
```

Create a project:

```bash
ahe init-project ./my-ahe-project
```

Edit `my-ahe-project/ahe.runtime.yaml`:

1. set `transport.base_url` and, if needed, `transport.api_key_env`;
2. replace both exact model IDs;
3. choose explicit reasoning, temperature, output, concurrency, and round
   budgets;
4. confirm `meta.backend: nexau` and
   `meta.explore.web_search_backend: ddgs`;
5. review the public source roots, role supplements, and research seed URLs.

Then run:

```bash
ahe conformance --config ./my-ahe-project/ahe.runtime.yaml
ahe validate --config ./my-ahe-project/ahe.runtime.yaml
ahe run --config ./my-ahe-project/ahe.runtime.yaml
```

The adapter uses only the public `BenchmarkAdapterV2`,
`EvaluatedAgentRuntime.open_session()`, `AgentSessionRequest`, `AgentInput`, and
`AgentOutput` contracts. It does not patch runner internals.

## Inspect the proof

Under `campaign-artifacts/runtime-example-campaign/`, inspect:

```text
evaluations/C0-train/runtime/
evaluations/C0-train/units/*/attempt-*/trace.jsonl
evidence/C0-train/tasks/
debugger/C1/
evolve/C1/
checkpoints/C1/
selection/
final/
```

An evidence-linked edit is evaluated again at the next checkpoint. If no
candidate beats the seed under the adapter's selection key, selecting C0 is a
correct and fully verified outcome.

## Scope

This is a small text-interactive contract example, not a research benchmark.
It demonstrates stateful session continuation and one protected structured
action. Typed image/audio messages, dynamic tool-set changes, asynchronous
interruptions, and event-stream protocols require additional adapter/runtime
contracts.
