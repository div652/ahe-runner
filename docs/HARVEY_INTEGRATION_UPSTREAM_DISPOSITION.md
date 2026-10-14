# Harvey Integration Upstream Disposition

This document records the upstream review of AHE-001 through AHE-020 from
`HARVEY_INTEGRATION_UPSTREAM_RECOMMENDATIONS.md`. The findings were checked
against the generic runner rather than accepted as Harvey-specific
requirements.

The resulting changes intentionally contain no Harvey task data, prompts,
metrics, adapter code, campaign artifacts, or benchmark-specific policy. They
form the AHE Runner 0.4.x hardening boundary.

## Summary

| Item | Assessment | Upstream disposition |
|---|---|---|
| AHE-001 | Correctness gap | Fixed: evaluated-model catalog, positive, negative, and observable-identity preflight is preserved per evaluation stage attempt. |
| AHE-002 | Conformance gap | Fixed: every regular asset in a registered skill directory is an active, hashed skill component. |
| AHE-003 | Import-integrity risk | Fixed at validation boundary: reject candidate top-level names that shadow standard-library, loaded, or importable host modules. Separate-process grading remains the stronger future boundary. |
| AHE-004 | Packaging/onboarding gap | Documented: adapters must not resolve a second runner/NexAU stack; NexAU 0.3.9 remains a direct Git dependency and `ag-ui-protocol` is constrained below 1.0. |
| AHE-005 | Capability-routing gap | Fixed: object-valued session resources use per-session NexAU `GlobalStorage`, shared only with that root agent's sub-agents. |
| AHE-006 | Cross-session state risk | Fixed with AHE-005: each runtime session creates a distinct Agent and resource store. This is isolation of accidental state routing, not a hostile-Python sandbox. |
| AHE-007 | Static-import bug | Fixed: `from package import helper` now resolves `package.helper` instead of splitting the imported name into characters. |
| AHE-008 | Workspace contamination | Fixed: every meta-agent receives a private runtime sandbox/work directory. |
| AHE-009 | Prompt mutation/failure | Fixed: resolved core prompts and role supplements are passed to NexAU as literal strings; the prompt file remains as immutable attempt evidence. |
| AHE-010 | Oversized file-search output | Fixed through the AHE-016 tool/middleware replacement: guarded native file tools use long-output handling and private full-output artifacts. |
| AHE-011 | Recovery limitation | Fixed with explicit `ahe retry-stage`. Prior attempts and errors remain immutable, a reason is required, and exhausted stages cannot be reopened. Identity-changing runner-patch amendments were rejected. |
| AHE-012 | Performance limitation | Deferred: cross-checkpoint concurrency requires one campaign-wide scheduler so adapter, judge, evaluated-agent, and meta-agent limits are not multiplied accidentally. |
| AHE-013 | Directory-listing overflow | Fixed through AHE-016: guarded listing/glob tools participate in bounded output handling. |
| AHE-014 | Evidence/citation mismatch | Fixed through AHE-016: full tool outputs are durable, while Debugger citations are captured from the post-truncation text actually shown to the model. |
| AHE-015 | Endpoint pressure | Fixed for runner clients with one process-wide semaphore per endpoint; adapters can join the same queue through `ahe_runner.model_requests.model_request_slot`. |
| AHE-016 | Meta-agent fidelity gap | Fixed: original-AHE-style guarded tools, per-role settings, optional Docker shell, long-output handling, context compaction, Evolve skill, and Debugger failure policy are restored generically. |
| AHE-017 | Attribution bug | Fixed: Debugger task keys, benchmark task IDs, and evaluation task keys resolve to one stable attribution key, with unresolved references reported. |
| AHE-018 | Metric-guidance drift | Fixed as an explicit process and warning contract: the adapter guide defines the full metric-change checklist and local checks warn on configured deprecated metric terms in role supplements. Arbitrary prose is not claimed to be semantically verified. |
| AHE-019 | Prompt-assembly bug | Fixed: empty memory files add nothing, nonempty memory is separated from the rendered system prompt, and the generated memory starts empty instead of containing instructions meant for Evolve. |
| AHE-020 | Benchmark-parity limitation | Fixed as a protected opt-in: strict schemas remain the default, while a manifest may allow omission of `additionalProperties`; explicit open schemas and `**kwargs` bindings remain invalid. |

## Important design decisions

### Candidate imports

The runner still loads candidate Python in the host process where NexAU
requires it. The new validation guard closes accidental and straightforward
module replacement, including names such as `json.py` or an installed package
root. It does not turn host-imported middleware into an OS sandbox. A future
stronger design would load candidate code and grading code in separate
processes with a narrow serialization boundary.

### Session resources

`AgentSessionRequest.session_resources` is for object capabilities that are
intentionally available to evaluated harness tools or sub-agents. It avoids
putting arbitrary Python objects into NexAU's string template context and
avoids process-global cross-trial routing. Adapters must never put hidden
answers, verifier state, credentials, or protected benchmark action
implementations into this store.

### Meta-agent tools

The final implementation uses the original NexAU file tools behind runner path
guards instead of retaining the interim Harvey-specific search/list wrappers.
Read and write roots are explicit. Long outputs are saved in private attempt
artifacts and replaced with bounded model-visible text. Shell access remains
disabled by default and can be enabled only through an explicit Docker
configuration.

### Recovery

`retry-stage` changes only the stage's execution authorization:

```bash
ahe retry-stage --config ahe.yaml \
  --stage round.2.evolve \
  --reason "Corrected the adapter-side parser"
ahe resume --config ahe.yaml
```

It does not delete an attempt, change semantic inputs, rewrite campaign
identity, or bypass `maximum_stage_attempts`. This keeps recovery useful
without weakening checkpoint reproducibility.

### Concurrency

`transport.max_concurrent_requests` limits recorded model calls across all
runner clients for one endpoint. Benchmark-owned clients are outside the
runner's interception boundary, so they must explicitly wrap calls with:

```python
from ahe_runner.model_requests import model_request_slot

with model_request_slot(request.transport):
    ...
```

Cross-stage scheduling remains sequential until the runner can allocate one
shared campaign budget instead of multiplying each adapter evaluation's local
concurrency.

### Metric changes

Metric semantics cannot be proven from unrestricted natural-language
supplements. The upstream contract therefore combines:

1. a required adapter/documentation checklist;
2. campaign identity changes when metric inputs change;
3. optional literal stale-term warnings through
   `meta.deprecated_metric_terms`.

This catches known obsolete vocabulary while avoiding false claims of semantic
validation.

### Long-term memory

NexAU concatenates `system_prompt_suffix` directly. The runner therefore adds
the separator itself and skips empty memory files. A generated project keeps
`LongTermMEMORY.md` empty until Evolve has an evidence-supported reusable
lesson; instructions about the component belong in Evolve's protected
guidance, not in evaluated-agent prompt content.

### Tool-schema parity

`additionalProperties: false` remains the safe default because it tells the
model that undeclared arguments are invalid. Some benchmark-native schemas
omit the keyword, and exact harness comparisons must preserve that observable
schema. `tool_schemas.allow_omitted_additional_properties` permits omission
only, is frozen outside the evolvable harness, and never permits an explicit
open schema or a catch-all Python binding.

## Generic regression coverage

The 0.4.x hardening changes add or extend coverage for:

- evaluated-model preflight caching, proofs, and failures;
- per-session resources and isolation;
- shared endpoint request slots, including adapter participation;
- skill-directory assets and `from package import helper`;
- candidate top-level module shadowing;
- literal meta prompts containing Jinja-like text;
- private meta-agent sandboxes, guarded tools, long outputs, and compaction;
- explicit blocked-stage retry and exhausted-attempt rejection;
- task-reference attribution aliases;
- deprecated metric-term warnings;
- readable evidence artifacts and imported branch campaign behavior;
- separator-safe, empty-aware long-term-memory attachment;
- strict-by-default benchmark tool-schema parity mode.

Validation results are recorded separately after the implementation commit so
the tested source identity remains explicit.
