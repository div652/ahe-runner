# Model providers: GH Copilot Server or an Azure Entra gateway

Status: design, branch `harveybench-trapi`. The default provider and every
existing configuration behave exactly as before.

## Why

Every model call in the runner goes to one OpenAI-compatible endpoint
(`transport` in `ahe.yaml`). Until now that endpoint was always the local
GH Copilot Server, a VS Code extension that turns chat-completions requests
into VS Code language-model calls. A colleague ran the same Harvey LAB tasks
through an Azure-hosted OpenAI gateway that is authenticated with Microsoft
Entra ID and speaks the OpenAI Responses API. The two paths differ in ways
that can change what the model sees:

| | GH Copilot Server | Azure Entra gateway |
|---|---|---|
| Wire API | chat completions | Responses |
| System prompt | turned into an ordinary user message | sent as `instructions`, a real system-level prompt |
| Reasoning between turns | never returned, so the model loses its earlier reasoning every turn | returned as reasoning items (summary plus encrypted content) and sent back on the next turn |
| Model ID | family name (`gpt-5.6-sol`); Copilot picks the build | dated snapshot (`gpt-5.6-sol_2026-07-09`) |
| Middle layer | Copilot Chat renders and budgets the prompt | none |
| Token usage | always 0 | real counts |
| Rate limits | Copilot quota, not reported | per-deployment requests and tokens per minute, reported in headers; HTTP 429 with `Retry-After` |

The hypothesis: the provider path, not only the harness, changes scores. To test it, the runner must be able to send the same harness to the
gateway. This document describes that option.

## Configuration

New `transport` fields. Their defaults reproduce the current behavior, and
they are left out of the transport's serialized form while at their
defaults, so existing campaign and evaluation identities do not change.

```yaml
transport:
  provider: azure_entra          # default: gh_copilot_server
  base_url: ${AHE_AZURE_BASE_URL}  # from the environment; not committed
  token_scope: ${AHE_AZURE_TOKEN_SCOPE}
  api_mode: responses            # default: chat_completions
  request_timeout_seconds: 600
  model_catalog: disabled
  negative_control: disabled
  max_concurrent_requests: 16
  hung_call_retries: 2
  transient_retries: 40          # new; default 0
  transient_max_wait_seconds: 120  # new
```

- `provider: azure_entra` gets a bearer token for `token_scope` from the
  Azure CLI login (`az account get-access-token`) in each process. The token
  is cached and refreshed five minutes before it expires. It is never written
  to disk or logs. `api_key_env` must be unset. No new Python dependency is
  needed.
- `api_mode: responses` sends every model call through the Responses API:
  the evaluated agent (NexAU `api_type: openai_responses`), the meta agents,
  the preflight probes, the judges and the filename helper.
- `transient_retries` resends a call that failed with HTTP 429, a 5xx status
  or a connection error, waiting for the server's `Retry-After` (capped by
  `transient_max_wait_seconds`) or else `min(60, 2^n)` seconds plus up to
  one second of jitter. Each failed attempt is kept as
  `transient-attempt-N.json` in the call directory. When the retries run
  out, the call fails as an infrastructure error: the trial is not scored
  and the stage can be resumed. A valid low score is never retried.
- `ModelSettings.temperature` may now be `null`, which sends no temperature.
  Reasoning models on the Responses API reject `temperature`; LAB's OpenAI
  adapter and judge also omit it whenever reasoning is set.

The base URL and token scope come from environment variables, so the
internal endpoint is not in the repository.

## What the evaluated agent sends in Responses mode

NexAU's built-in Responses path does the protocol work:

- system messages become `instructions`;
- `max_tokens` becomes `max_output_tokens`;
- each assistant turn keeps its output items (reasoning, messages, function
  calls), and NexAU sends them back on the next turn without their item IDs,
  using `include: ["reasoning.encrypted_content"]`.

The runner adds `reasoning: {effort, summary: "auto"}` (LAB's OpenAI
adapter sends the same) and enforces the exact model, `max_output_tokens`,
temperature and reasoning on every call. Each call's request, response,
returned model and the gateway's rate-limit headers
(`x-ratelimit-remaining-*`, `x-ratelimit-reset-*`, request ID) are recorded.
The gateway's backend-URL header is not recorded.

## Model identity on the gateway

The runner's preflight normally needs two checks the gateway cannot pass:

- `GET /models` returns 404 (there is no catalog);
- a request for a nonexistent model returns 429 "Rate Limit Exceeded", not
  "model not found", so the negative control cannot identify the fake ID.

With `model_catalog: disabled` and `negative_control: disabled`, the
preflight sends one positive probe through the Responses API and requires
the response's `model` to equal the exact requested ID. Every later call is
checked the same way. Unlike GH Copilot Server, the gateway reports the
model of the deployment that answered, not a label copied from the request.
A misspelled model ID surfaces as repeated 429s and then an infrastructure
failure, which is why the preflight runs before any trial.

## Harvey grading on the gateway

New adapter setting `judge_api`:

- `copilot_chat` (default): unchanged. Two distinct judges, chat
  completions, `max_tokens` 64000, temperature 0, catalog and negative
  control required.
- `openai_responses`: LAB's own OpenAI judge path (`Judge._evaluate_openai`)
  with the same prompts, JSON parsing and parsing retries. `input` is the
  formatted rubric prompt; the first of the two attempts asks for strict
  `json_schema` output and the last does not. `max_output_tokens` comes from
  the judge's `max_tokens`; `reasoning: {effort, summary: "auto"}` is sent
  when the judge has an effort; temperature only when it is not null. The
  transport must use `api_mode: responses`.

`judges` may now hold one judge or two. With one judge, the aggregate file is
still `scores_dual.json` and its fields keep their names, so the selection
and reporting code is unchanged: `dual_criterion_pass` is that judge's
criterion pass rate, `dual_all_pass_rate` is its all-pass (0 or 1), and
`judges_agree` is always true. Two-judge campaigns are unchanged.

`helper` may now be `null`. Then LAB's last-resort filename matching makes
no model call and returns no match; each time this happens it is recorded
under `filename_helper/disabled/`. (LAB's default helper calls Anthropic's
API directly, which is not available here.) With a helper set and
`judge_api: openai_responses`, the helper uses the Responses API with
strict `json_schema` output.

## Planned run: C0 baseline on the gateway

The first use is a C0-only evaluation on all 250 firm-knowledge tasks, as in
campaign 9b, with only the provider and grading changed:

| | Campaign 9b | Gateway run |
|---|---|---|
| Harness | `harness_lab` (C0) | same |
| Agent model | `gpt-5.6-sol`, effort high | `gpt-5.6-sol_2026-07-09`, effort high, summary auto |
| Agent output cap | 128,000 tokens | 32,768 (the colleague's value) |
| Context cap (NexAU) | 911,793 tokens | same |
| Judge | claude-sonnet-5 + gpt-5.5 via Copilot | `gpt-6.1-sol_2026-09-29`, effort high, `max_output_tokens` 8,192 (the colleague's judge) |
| Trials | 1 per task | same |

Differences that remain against the colleague's run (his code is LAB's own
loop): NexAU loop instead of LAB's; reasoning items are sent back by their
encrypted content without item IDs (his are sent with IDs and looked up from
storage); our runner reruns infrastructure failures until every task is
graded (his excludes a task after three failed attempts); no 2-hour wall
clock per run; our judge prompt length cap (LAB-002).

## Validation

1. Unit tests for the new paths; the full existing suite must still pass.
2. Live smoke: preflight for both models, then one or two tasks end to end
   with grading.
3. Ask the operator before the 250-task run.
