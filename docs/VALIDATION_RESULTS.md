# Validation Results

Validated at `2026-10-10T05:20:06Z`.

## Source identity

- Git commit: `92f1b79a0ec7c2defdc713f0c798b3b27cf3bd12`
- Package: `ahe-runner 0.1.0`
- Python: `3.12.3`
- NexAU: `0.3.9`, upstream commit
  `35ee1861546db3cb280a6e17e38a74060d7c96c3`
- OpenAI Python: `3.28.0`
- Pydantic: `2.14.0`
- PyYAML: `6.0.3`

The source commit above is the code tested before this documentation-only
record was added.

## Static checks

```text
ruff check .
All checks passed!

ruff format --check .
57 files already formatted

python -m compileall -q src tests run_ahe.py
exit 0
```

## Tests

Focused invariants:

```text
14 passed in 9.11s
```

End-to-end campaign tests:

```text
3 passed in 36.61s
```

Final complete suite after production-config regressions:

```text
18 passed in 46.83s
```

The suite includes an injected Evolve timeout after one committed partial
change and one uncommitted change. Resume retained the commit, excluded the
dirty edit from the next workspace, and preserved that edit in the recovery
patch.

## Distribution build

```text
ahe_runner-0.1.0-py3-none-any.whl
sha256: d767f009417aad8947705f5ce2b1a4a27b984e1238315ab65da9fbc1f6b0e4a5

ahe_runner-0.1.0.tar.gz
sha256: daadbe18f12ef6740dc429230c78b7bbbcd394fff9d5fdec7dac47e7c931b032
```

The wheel was installed into a clean `uv` environment with the `web` extra.
The installation resolved exact NexAU tag `v0.3.9` and the explicitly declared
`greenlet` runtime dependency.

Wheel inspection verified packaged:

- all four full meta-agent prompts;
- all restricted tool descriptors;
- generated project templates;
- harness component-directory README placeholders;
- AHE method reference.

## Installed CLI campaign

Using only the clean-wheel installation:

1. `ahe init-project`
2. set `campaign.rounds: 1`
3. `ahe validate`
4. `ahe run`
5. `ahe resume`

Observed:

- checkpoints `C0`, `C1`;
- selected checkpoint `C1`;
- 18 completed stages;
- no blocked or interrupted stage;
- selected harness exported;
- `resume` added no attempts and preserved identical selection/final results.

## Production NexAU configuration smoke

Without making a model call, the clean-wheel installation generated and loaded
a production NexAU meta-agent config containing:

- packaged full Evolve prompt;
- restricted `complete_task` binding;
- context-compaction middleware;
- in-memory tracer;
- exact model transport fields;
- one genuine registered `SKILL.md`.

NexAU emitted its informational warning that the optional E2B SDK was not
installed. E2B is not used by this runner's meta-agent runtime, and config
loading completed successfully.

## Live endpoint scope

No live model-backed evolution campaign was executed during this validation.
That run requires fresh operator confirmation of endpoint instance, advertised
exact model, reasoning effort, chat-completions mode, task/attempt scope, and
concurrency.

The deterministic lifecycle, exact-model client with fakes, positive/negative
preflight controls with fakes, and production NexAU config loading were all
validated.
