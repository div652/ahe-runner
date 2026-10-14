# Tool descriptions

Model-facing NexAU tool YAML files, registered under `tools` in `agent.yaml`.

- `name` must equal the registration name, and `description` must be
  substantive.
- `input_schema` must be a JSON Schema object. The schemas here are LAB's,
  copied exactly, so they omit `additionalProperties`. This is allowed only
  because campaign 9 uses `harness_manifest_lab.yaml`. An explicit value other
  than `false` is still rejected. An unexpected argument gives the model a tool
  error, whereas LAB silently ignores it.
- Its properties must equal the bound function's parameters, excluding the
  injected `agent_state`. The required properties must equal the parameters
  without defaults.
- `binding` has the form `tools.<module>:<function>`. Tools are not lazy.
- `disable_parallel: true` makes the tools run one call at a time, as in LAB's
  native loop.
