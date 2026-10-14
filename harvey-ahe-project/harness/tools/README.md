# Tool implementations

Python implementations of the tools described in `tool_descriptions/`. They port
LAB's native workspace tools to NexAU.

- Each tool receives NexAU's `agent_state` and reaches the trial's podman sandbox
  only through `harvey_ahe.workspace.handle_for(agent_state)`. The handle
  provides `exec`, `read_file`, `write_file`, `exists`, `to_host`,
  `is_writable`, `assert_sandbox_path`, `mark_finished`, and `shell_timeout`.
- Tool code runs in the host Python process. It must not import network,
  process, or dynamic-import modules (for example `subprocess`, `socket`,
  `requests`, `importlib`), call `eval`/`exec`/`compile`/`__import__` or
  `os.system`-style functions, or import protected benchmark packages. Run
  commands in the sandbox with `handle.exec`.
- Import shared helpers as `from tools._common import ...`.
- Do not name a parameter `sandbox` or `global_storage`, because NexAU injects
  its own objects under those names.
- A returned string reaches the model as `{"result": "..."}`. For the stop tool
  `finish`, return `{"status": "error", "error": "..."}` to reject a call
  without ending the run.
- Keep module-level state out of tools. Several trials can run concurrently in
  one process; keep per-trial state in the session's global storage.
