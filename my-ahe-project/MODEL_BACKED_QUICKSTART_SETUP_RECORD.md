# Model-backed quick-start setup record

This records the operator-selected configuration for checking AHE Runner's
model-backed README example. It explains the setup commit and how to recreate
the configuration. It is not required by AHE, does not replace the official
README, and does not implement or run a Harvey Labs benchmark.

This snapshot was prepared on 2026-10-12, before the setup commit and before
running the model-backed `conformance`, `validate`, or `run` commands. The
earlier offline example completed successfully. No model completions have
been requested during this walkthrough yet; the proxy catalog was queried.

## Repository and configuration

| Item | Recorded value |
|---|---|
| Repository | `https://github.com/div652/ahe-runner` |
| Local checkout | `~/AHE_Harvey` |
| Local branch | `harveybench` |
| Source commit before the setup commit | `591aef6b8f43c0808959cced91a33fc105e157cb` |
| Operator project | `my-ahe-project/` |
| Runtime configuration | `my-ahe-project/ahe.runtime.yaml` |
| Runtime adapter | `runtime_benchmark_adapter:RuntimeExampleAdapter` |
| Campaign | `runtime-example-campaign` |

The operator explicitly selected these models and reasoning settings:

| Role | Exact model ID | Reasoning effort | Output-token limit |
|---|---|---|---:|
| Evaluated agent and its advisory sub-agents | `gpt-5.4-mini` | Intentional provider default | 4096 |
| Source/Web Explore, Debugger, and Evolve | `gpt-5.4` | `high` | 32000 |

Only three generated configuration values were changed:

| Setting | Generated value | Selected value |
|---|---|---|
| `models.evaluated.model` | `replace-with-exact-evaluated-model` | `gpt-5.4-mini` |
| `models.meta.model` | `replace-with-exact-meta-model` | `gpt-5.4` |
| `models.meta.reasoning_effort` | `provider-default` | `high` |

All remaining generated settings were retained with operator approval:

| Setting | Value |
|---|---|
| Endpoint | `http://127.0.0.1:3141/v1` |
| API mode / streaming | `chat_completions` / `false` |
| API-key environment variable | `null`; no provider credential configured |
| Request timeout | 300 seconds |
| Observable model policy | `exact` |
| Model catalog / nonexistent-model control | Both `required` |
| Evolution rounds / root seed | 1 / 42 |
| Task scope | Two tasks per split: one direct-response task and one protected-action task |
| Trials | One trial per task in each of train, validation, and test |
| Evaluation / Debugger concurrency | 2 / 2 |
| Maximum concurrent model calls | 2 |
| Configured temperature | 0.2 for both model roles |
| Meta-agent context limit | 200000 tokens |
| Debugger / Explore / Evolve iteration ceilings | 80 / 200 / 500 |
| Maximum running sub-agents | 5 |
| Maximum stage attempts | 3; incomplete attempts are archived |
| Meta backend | `nexau` |
| Source / Web Explore | Both enabled; Web uses `ddgs` |
| Research seed URL | `https://github.com/nex-agi/NexAU` |
| Public meta-agent source root | `benchmark_public/RUNTIME_ADAPTER.md` |
| Role supplements | The four generated files under `runtime_meta_supplements/` |

The harness, adapter, protected controller, core meta-agent prompts, and
generated budgets were not edited. The `UNCONFIGURED` model values in the
harness YAML are intentional protected seed values: the runner injects the
approved model settings recursively at runtime. They should not be replaced
in the seed files.

Provider-default reasoning is normalized to no explicit reasoning override.
For the meta model, the recorded client sends nested `reasoning.effort: high`
through the OpenAI-compatible request body. The runner rejects missing or
different observable model labels on recorded completions.

## Installed environment

These versions were read from the existing environment when preparing this
record; no packages were installed or upgraded for the record.

| Component | Version |
|---|---|
| Python | 3.12.3 |
| uv | 0.12.16 |
| ahe-runner | 0.3.0, editable installation |
| NexAU | 0.3.9 |
| OpenAI SDK | 3.29.0 |
| Pydantic | 2.14.1 |
| PyYAML | 6.0.3 |
| greenlet | 3.5.6 |
| ddgs | 9.16.0 |
| beautifulsoup4 | 4.15.0 |
| httpx | 0.28.1 |
| pytest | 9.1.1 |
| Ruff | 0.17.0 |

NexAU was installed from Git tag `v0.3.9`, resolving to commit
`35ee1861546db3cb280a6e17e38a74060d7c96c3`.

This is an inventory of the directly relevant packages, not a complete
dependency lockfile. A future installation can resolve different transitive
versions; this record does not claim bit-for-bit environment reproduction.

## Copilot proxy observations

During the earlier preflight, the operator confirmed ownership of the
endpoint, the listener bound only to `127.0.0.1:3141`, and `GET /v1/models`
advertised both selected exact model IDs. This was a catalog check, not proof
that either model had generated a response.

The inspected extension was `gh-copilot-server.gh-copilot-server`, version
`0.1.0`. Its files were under
`~/.vscode-server/extensions/gh-copilot-server.gh-copilot-server-0.1.0/`.
The following SHA-256 hashes were recorded during that preflight:

| File | SHA-256 |
|---|---|
| `package.json` | `85545ac5e6cb1d3b05e2a14cf7ca4e438c89c529157945042f7e056988ab31d5` |
| `out/server.js` | `9090e9ebf4aff5214c0e19617cdbd362f0ee528336b4c9846bbb93f29bae7a45` |
| `out/extension.js` | `c7ef05f74d3645f7daac63cc3fdc230fd3eaf9a7bc689c65c92ab5ec06e3f5ab` |

The installed server code included a 300-second request deadline and
cancellation on client disconnect. No extension or port changes were made
in this walkthrough. These are historical observations; do not assume an
unchanged server instance or catalog for a later run.

Important limitations of this proxy:

- Its response `model` label comes from the selected VS Code model object,
  not independent upstream execution telemetry. Exact labels cannot rule out
  transparent upstream substitution.
- It forwards reasoning and output-budget options but does not forward
  temperature. The configured temperature and root seed do not establish
  deterministic remote model sampling.
- It maps system/developer messages to VS Code user messages, so this is not
  protocol-equivalent to native OpenAI system-message handling.
- It reports synthesized zero token usage; actual usage and cost are
  unavailable, not proven zero.
- It may retry a request up to three times on the same selected model object,
  in addition to the runner's configured stage-attempt policy.

Before model-backed stages, the runner is configured to require a fresh
positive completion probe and a nonexistent-model negative control. A passing
negative control must reject the nonexistent ID and identify it in the error.
Those completion controls have not run yet in this walkthrough.

## Recreating the setup

For a fresh checkout, start from the recorded source commit rather than
assuming that the local, unpublished `harveybench` branch exists remotely:

```bash
git clone https://github.com/div652/ahe-runner AHE_Harvey
cd AHE_Harvey
git switch -c harveybench 591aef6b8f43c0808959cced91a33fc105e157cb
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e '.[web,dev]'
.venv/bin/ahe init-project ./my-ahe-project
```

Apply the three runtime configuration substitutions listed above. Keep the
remaining generated configuration unchanged, and confirm endpoint ownership,
exact advertised model IDs, reasoning, API mode, and scope for the new run.

The original setup encountered a partial `.venv` left by a failed
`python3.12 -m venv` command. After explicit approval, it was recreated with:

```bash
uv venv --python 3.12 --clear .venv
```

Use `--clear` only when intentionally replacing that exact virtual
environment; it is not a requirement for a fresh directory.

The walkthrough uses explicit `.venv/bin/ahe` paths because tool-launched
shell activations do not persist between commands. This targets the same
installed executable as `ahe` in an activated interactive shell.

After documenting and committing the setup, the pending README commands are:

```bash
.venv/bin/ahe conformance --config ./my-ahe-project/ahe.runtime.yaml
.venv/bin/ahe validate --config ./my-ahe-project/ahe.runtime.yaml
.venv/bin/ahe run --config ./my-ahe-project/ahe.runtime.yaml
```

These commands are listed for reproduction, not recorded as completed.
During this walkthrough, execute one command at a time and obtain approval
before the next. The run can make many meta-agent calls despite having only
one evolution round; Source/Web Explore also access public research sources.

Expected runtime artifacts will be under
`my-ahe-project/campaign-artifacts/runtime-example-campaign/`, including
checkpoint/evaluation records, model-call proofs, selection records, and
`final/selected-harness/`. Selecting the seed checkpoint or obtaining a low
task reward is a valid benchmark outcome, not automatically a command error.

## Observed README results and failure policy

The separate offline configuration, `my-ahe-project/ahe.yaml`, passed
conformance and validation, then completed all 34 stages and attempts. It
produced checkpoints `C0` through `C3` and exported `C3` under
`campaign-artifacts/example-ahe-campaign/final/selected-harness/`. This used
the scripted meta backend and did not exercise live model generation.

| Observation | Outcome | Action taken |
|---|---|---|
| `python3.12 -m venv .venv` lacked `ensurepip` | Exit 1; partial environment remained | Reported; operator approved the documented uv route |
| `uv venv --python 3.12 .venv` found the partial environment | Exit 2 | Reported; operator approved the `--clear` command |
| Generated-project and offline CLI commands printed `E2B SDK not installed` | Commands completed successfully | Message reported; no E2B SDK installed |
| Offline validation warned that host-imported middleware is not OS-sandboxed | Validation succeeded | Warning reported; no sandbox or middleware changes |
| Extra setup-commit command lacked Git author identity | Exit 128 on two attempts | Reported; operator approved repository-local author configuration; global Git settings and GitHub sign-in were left unchanged |

The setup commit is an extra operator workflow step, not a README or AHE
command. If a fresh checkout lacks Git author identity, configure your own
repository-local commit metadata before attempting that commit:

```bash
git config --local user.name "YOUR_AUTHOR_NAME"
git config --local user.email "YOUR_COMMIT_EMAIL"
```

These values identify commits; they are not GitHub sign-in credentials.
The operator's actual contact details are intentionally not repeated here.

At the recorded source commit, the README documented the uv alternative but
did not mention clearing the partial environment after the Python-venv
failure. This was observed, not repaired in the README.

The two virtual-environment repairs were explicitly approved before the
operator requested report-only handling. For remaining checks, report
command errors and failed attempts, then pause without applying repairs.
The runner's already-approved internal recovery policy remains unchanged.

For version control, track the generated configurations, adapters, harness,
manifest, public documents, role supplements, and this setup record. Stage
those source paths explicitly; do not stage `my-ahe-project/` wholesale,
because it also contains campaign artifacts and checkpoint Git repositories.

## Completed quick-start checkpoint (2026-10-12)

The sections above preserve the pre-run setup snapshot. The operator later
approved and completed the model-backed checks using setup commit `1839473`.
This section records the resulting state; it does not change the runner,
benchmark adapters, harness, prompts, or campaign configuration.

| Command or result | Observed outcome |
|---|---|
| Runtime `conformance` | Passed |
| Runtime `validate` | Passed |
| Runtime `run` | Exit 0; 17 stages and 17 attempts completed |
| Runtime `status` | Exit 0; 17 completed stages; no blocked or interrupted stages |
| Produced checkpoints | `C0`, `C1` |
| Validation | Both checkpoints scored `mean_exact_success: 1.0`, or 2/2 tasks |
| Selection | `C0`, using the earliest-checkpoint tie-breaker |
| Final test | Selected `C0` passed 2/2 tasks; `mean_exact_success: 1.0` |
| Export | 14 files; export hash matched the selected checkpoint |
| Runtime `resume` | Not run; campaign was complete and the operator declined the optional step |

The offline rehearsal is a separate campaign: 34 completed stages and
attempts, checkpoints `C0` through `C3`, and selected/exported `C3`. These
counts must not be combined with the model-backed campaign.

The recorded runtime configuration SHA-256 was:

```text
2fcf2a080d10ce09a69d1ef340c13f62222a601ecdff5b39ea1c248c7d50d803
```

The selected/exported runtime harness SHA-256 was:

```text
420837f4a04f9d7c34fab4eaea3b7fe84c57e89cfad154c2bb63659f30459c45
```

The local evidence is under
`campaign-artifacts/runtime-example-campaign/`: `campaign_manifest.json`,
`campaign_state.json`, checkpoint/evaluation records, recorded model calls,
and `final/final_test.json`. The exported harness is
`final/selected-harness/`. The project ignore entry keeps raw campaign
outputs and nested checkpoint Git repositories out of the setup/results
commits; these artifacts remain local, not part of the published checkpoint.

The earlier model-record inspection found 68 unique normal recorded
completion requests/responses: 53 labelled `gpt-5.4` and 15 labelled
`gpt-5.4-mini`. Requested/returned labels, output limits, non-streaming
settings, and reasoning settings matched the approved configuration.
No recorded model-call errors or failed stage attempts were found.
These counts exclude standalone preflight calls and do not count possible
upstream retries. Proxy labels still cannot independently exclude upstream
model substitution, and actual token usage/cost remains unavailable.

Three standalone positive/negative preflight reports were observed for the
meta model: Explore, round-1 Debugger, and C1 Evolve. Evaluated-model
completions were label-checked but did not receive equivalent standalone
probes. This is narrower than the operator guide's broad per-stage wording.
The discrepancy was reported, not repaired.

The non-fatal `E2B SDK not installed` message continued to appear, including
on `status`. The host-middleware isolation warning was also reported.
No SDK, sandbox, source, or documentation repair was applied for either
warning. This successful sample walkthrough is not a Harvey benchmark run
and does not demonstrate that evolution improved a real benchmark.
Do not include `.venv/`, `__pycache__/`, or generated campaign outputs.
