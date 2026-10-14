# GH Copilot Server patch of 2026-10-14 (AHE-021)

File patched (host instance that owns 127.0.0.1:3141):
`~/copilot-host/extensions/gh-copilot-server.gh-copilot-server-0.1.0/out/server.js`

| | SHA-256 of `server.js` |
|---|---|
| Before | `9090e9ebf4aff5214c0e19617cdbd362f0ee528336b4c9846bbb93f29bae7a45` |
| After | `5dea2428c405ea3f31178f5244fdad69e418320ebea0cd709796e9081321384d` |

Change, after the `max_tokens` block: set `options.configuration.contextSize`
to the model's `maxInputTokens`, and `options.configuration.reasoningEffort`
to `request.reasoning.effort` when the request has one.

Probe: `scripts/copilot_context_effort_probe.py`, model gpt-5.4.

| File | What it shows |
|---|---|
| `before-effort-gpt-5.4.json` | Effort "none" and "high" both take 8.5-13 s and both answer 1477: effort ignored |
| `before-context-gpt-5.4.json` | 5k-token prompt recalls the code; 317k-token prompt answers `UNKNOWN`: start of prompt cut |
| `after-gpt-5.4.json` | "none" 0.9-1.2 s with wrong answers, "high" about 12 s and right; 317k prompt recalls the code |

The context part of the first "before" run was invalid (too small
`max_tokens`, unclear wording) and was removed from `before-effort-gpt-5.4.json`.
