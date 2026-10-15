# Validation Plan

Validation is organized from smallest invariant to complete installed use.

## Static checks

1. `ruff check .`
2. `ruff format --check .`
3. `python -m compileall -q src tests run_ahe.py`

These catch import, syntax, annotation, formatting, and packaged-source issues
before any campaign is run.

## Focused tests

The pytest suite covers:

- strict configuration and generated-project loading;
- harness path, symlink, protected-field, runtime-owned-file, and training-ID
  guards;
- immutable stage identities, interrupted attempts, cached-output hashing, and
  semantic drift;
- Git continuation from the prior Evolve attempt's committed `HEAD` while
  archiving dirty and untracked state;
- evaluation run/unit identity, contiguous absolute attempts, completed valid
  zero outcomes, and full-completion finalization;
- exact model request and response labels, reasoning injection, durable call
  records, timeout classification, and positive/negative preflight controls;
- complete meta-backend abstract-method coverage;
- tool descriptor and NexAU `agent_state` binding compatibility;
- substantive full prompts and native `LoadSkill` handoff requirements;
- full deterministic C0-to-CN training, validation selection, final test,
  selected-harness export, and idempotent resume;
- an injected Evolve infrastructure failure followed by recovery from the last
  committed partial improvement.

## Packaging and installed CLI

1. Build sdist and wheel.
2. Install the wheel into a fresh virtual environment with `[web]`.
3. Run `ahe init-project`.
4. Run `ahe validate`.
5. Run the deterministic full campaign.
6. Run `ahe resume` and verify no completed stage gains another attempt.
7. Inspect the wheel to ensure prompts, tool YAML, templates, and method
   reference are present.

Dependency installation must resolve NexAU from its exact upstream `v0.3.9`
Git tag rather than assuming a registry package exists.

## Production backend smoke without campaign spend

The installed package must:

- import `NexAUMetaBackend`;
- load every tool descriptor;
- instantiate generated NexAU configs using the installed `nexau==0.3.9`
  schema;
- reject a missing web extra for `meta.backend: nexau`;
- preserve the configured exact model, endpoint, reasoning, non-streaming
  setting, bounded hung-call retry policy, and context-accounting policy;
- reject provider context overflow as infrastructure and preserve every
  timeout resend artifact;
- rebuild an evaluated runtime in a spawned child without repeating preflight,
  losing protected project imports, or bypassing the endpoint request queue;
- persist protected root/sub-agent tool wall times and expose activation-local
  timing events through `AgentOutput`.

A live model-backed campaign is intentionally separate because it requires the
operator to confirm the current endpoint instance, advertised exact model,
reasoning effort, API mode, attempt scope, concurrency, near-limit prompt
retention, and model-specific token-counting unit before any call.
