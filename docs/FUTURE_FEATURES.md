# Future Features

These ideas are intentionally outside the AHE Runner V2 contract.

## Partial-episode checkpointing

Long or expensive interactive benchmarks may eventually persist a standard
mid-episode checkpoint containing completed turns, pending external action
IDs, benchmark environment state references, agent-session state references,
and partial reward.

V2 instead treats an evaluation unit attempt as atomic. A failed or interrupted
unit can start another immutable attempt from the beginning.

## Explore-seed quality checks

V2 requires the operator to provide benchmark-specific Source and Web Explore
guidance and requires the Explore agents to produce substantive skills. It
does not attempt to decide whether the original guidance is sufficiently
specific or insightful.

A future optional check may warn about empty or obviously generic objectives,
failure taxonomies, protected-protocol descriptions, or source/research
scopes. Such a check must remain advisory because benchmark expertise cannot
be established from string length or keyword rules.

## Shared cross-stage evaluation scheduler

Validation checkpoint evaluations, final-test evaluations, and initial
exploration are currently scheduled sequentially. Running them concurrently
could reduce wall-clock time, but each adapter evaluation already receives its
own concurrency budget and may invoke additional benchmark-owned judge models.
Blind cross-stage parallelism would multiply those limits and can overload the
model endpoint or benchmark service.

A future scheduler should allocate one explicit campaign-wide budget across
checkpoint evaluations, exploration, evaluated-agent calls, and adapter-owned
provider calls. It must preserve immutable stage evidence, deterministic
selection, and the endpoint-wide request limiter. Until that shared scheduler
exists, the sequential order is an intentional safety tradeoff rather than a
correctness defect.
