# Migrating the Tau2/NexAU Experiment

The existing specialized experiment can be moved into this runner without
moving its protected benchmark logic into the harness.

## Harness input

Use the clean Tau2 NexAU harness as the operator seed:

```text
project/harness/
```

Copy only candidate-owned files such as:

- `agent.yaml`;
- system prompt;
- tool descriptions and implementations;
- middleware;
- skills;
- sub-agents;
- long-term memory;
- runtime-owned short-term memory template.

Do not copy the Tau2 environment, simulator, reward, task loader, evolution
controller, or model relay into `harness/`.

## Manifest mapping

Map the current Tau2 candidate surfaces to the seven component globs. Preserve
these invariants as protected fields:

- exact evaluated-agent model placeholder/routing contract;
- `stream: false`;
- OpenAI chat-completion API type;
- protected tool-call mode;
- runtime-owned short-term memory;
- any adapter-owned middleware or boundary field that Evolve must not alter.

## Adapter mapping

The specialized implementation maps to `BenchmarkAdapter` as follows:

| Reusable method | Tau2 responsibility |
|---|---|
| `identity()` | Tau2 version, telecom domain, reward/metric protocol, supervision policy |
| `split()` | Pinned train/validation/test task IDs |
| `evaluate()` | Tau2 rollout execution, customer simulator, agent, exact-model relay, seeds, concurrency |
| `verify_evaluation()` | Rollout completeness, model labels, trace/result hashes, aggregate recomputation |
| `materialize_training_evidence()` | Dense or sanitized train trajectories and paired-trial states |
| `validate_candidate()` | Tau2 tool/config/boundary invariants without held-out execution |
| `compare()` | pass^1/pass^2 and per-task transition attribution |
| `selection_key()` | Validation-native checkpoint ranking |
| `meta_prompt_context()` | Telecom dialogue-agent terminology and protected protocol |

Use `EvaluationStore` instead of reimplementing rollout IDs and attempts.
Semantic inputs should include checkpoint hash, split identity, task IDs,
trials, seeds, exact model settings, Tau2 version, and adapter version.

Do not include fresh endpoint preflight timestamps or negative-control nonces.

## Model-calling roles

Fail closed on the observable exact model label for:

- evaluated assistant;
- customer/user simulator;
- Debugger;
- Source and Web Explore;
- Evolve;
- any helper, summarizer, validator, or sub-agent that calls a model.

If the host extension internally retries one selected model object, document
that endpoint behavior separately. The runner itself uses zero SDK retries for
meta calls.

## Evidence conversion

For each train task, preserve:

- all expected trials;
- valid zero rewards;
- reward and termination reason;
- assistant and customer trajectories;
- tool calls and results;
- environment state transitions allowed by the supervision policy;
- requested and returned observable model labels;
- infrastructure failures separately from valid outcomes;
- source artifact hashes.

Validation and test never call `materialize_training_evidence()`.

## Explore delivery

The reusable runner fixes the earlier Tau2 adaptation difference:

```text
Source Explore -> source-exploration/SKILL.md
Web Explore    -> web-research/SKILL.md
```

Both are copied read-only into Evolve's generated NexAU config:

```yaml
skills:
  - ./skills/source-exploration
  - ./skills/web-research
```

Evolve receives short descriptions first and calls `LoadSkill` for full
content. Live explorer sub-agents are not the primary handoff.

## Recovery mapping

The specialized C3 repair lessons are built into the generic store:

- semantic run ID survives fresh preflights;
- persisted run ID determines unit IDs;
- absolute attempts continue contiguously;
- completed valid zero outcomes are skipped;
- completion requires artifact hashes;
- incomplete failure summaries remain until every unit verifies.

Evolve recovery is more permissive than the old one-shot controller while
preserving the logical one-shot method:

- one logical Evolve stage may have several infrastructure attempts;
- each attempt is preserved;
- retry starts from prior committed `HEAD`;
- dirty edits are archived as evidence;
- only committed state can freeze.

## Existing C0-C5/C6 campaign

Do not copy the partial specialized campaign directory into a new generic
campaign and pretend identities match. Its controller, evidence schemas, and
stage records differ.

Two safe choices are:

1. start a fresh generic campaign from the chosen clean harness checkpoint;
2. write an explicit one-time importer that verifies every old checkpoint and
   evaluation before creating equivalent generic artifacts.

The current runner does not include that importer. The preserved partial C6
workspace remains historical evidence, not a generic checkpoint.
