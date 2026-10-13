# Middleware

Place NexAU middleware modules here and register them under `middlewares` in
`agent.yaml`.

- Middleware runs in the host Python process, so the same import bans as
  `tools/` apply. Reach the trial sandbox only through
  `harvey_ahe.workspace.handle_for(agent_state)`.
- NexAU logs and ignores exceptions raised from ordinary before/after hooks. Do
  not rely on raising from a hook to block an action.
- Keep middleware stateless at module level. Several trials can run
  concurrently in one process; keep per-trial state in the agent state or the
  session's global storage.
