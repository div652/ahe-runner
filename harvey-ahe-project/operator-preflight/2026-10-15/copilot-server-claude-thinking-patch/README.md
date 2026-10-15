# GH Copilot Server patch of 2026-10-15: Claude thinking (campaign 9c)

File patched (host instance that owns 127.0.0.1:3141):
`~/copilot-host/extensions/gh-copilot-server.gh-copilot-server-0.1.0/out/server.js`
Backup: `server.js.bak-2026-10-15-before-claude-thinking` in the same folder.

| | SHA-256 of `server.js` |
|---|---|
| Before (2026-10-14 patch only) | `5dea2428c405ea3f31178f5244fdad69e418320ebea0cd709796e9081321384d` |
| After | `ed67ab4cba805fc8e32cf7f6b223dc07650f61e24b727930532e3ff0ad6b3640` |

Why: Copilot Chat 0.69.0 turns on Claude thinking, and only then sends the
reasoning effort, when the caller sets `modelOptions._enableThinking`. The
server never set it, so a Claude request with an effort ran with no thinking.

Changes:

1. After the 2026-10-14 `configuration` block: if the model id or family starts
   with `claude` and the request has `reasoning.effort`, set
   `options.modelOptions._enableThinking = true` and log
   `thinking: enabled, effort=<effort>`. GPT requests and requests without an
   effort (the judges) are unchanged.
2. In the response loop, count parts that are neither text nor tool calls
   (thinking parts) and log `thinking parts=N chars=M (not returned)`. Copilot
   asks for summarized thinking, so M is the summary length. Thinking is not
   returned to the client, so the harness never sends it back.

Applied by reloading the host window and running Start Server (new listener
pid 3844825, `/proc/<pid>/exe` = `~/copilot-host/VSCode-linux-x64/code`, 33
models). Probe: `scripts/copilot_claude_thinking_probe.py`, model
claude-opus-4.8, concurrency 1.

| File | What it shows |
|---|---|
| `before-effort-claude-opus-4.8.json` | No effort 9.5 / 7.9 s, "high" 8.4 / 9.9 s; all four write their working as visible text: effort ignored |
| `after-effort-claude-opus-4.8.json` | No effort 11.5 / 9.6 s with visible working, one wrong (1476); "high" 16.4 / 15.6 s, answer only "**1477**", log shows 75 and 93 thinking parts |
| `after-tools-claude-opus-4.8.json` | Effort "high", 3 steps, tool results sent back without thinking blocks: no error, correct sum 1732.75; thinking parts on steps 1 and 2 |
| `model-preflight-claude-opus-4.8-high.json` | Skill verifier: positive probe returns `claude-opus-4.8`; nonexistent model rejected with its id |

No "Dropping reasoning effort" warning in the Copilot Chat log.

## Context limit calibration

Copilot Chat counts tokens only with o200k_base or cl100k_base, and cuts or
rejects a prompt over the model's `max_input_tokens` (935,793 for
claude-opus-4.8) in that count. Anthropic counts with Claude's own tokenizer
and rejects a prompt over 1,000,000 tokens with
`model_max_prompt_tokens_exceeded: prompt is too long: N tokens > 1000000 maximum`.
Each probe sent real tool output from campaign 9b traces as user messages
(one long prompt, effort "high"); each was rejected in 6-7 s, before any
generation.

| File | Text | o200k tokens | Claude tokens | Ratio |
|---|---|---|---|---|
| `calibrate-925k-claude-opus-4.8.json` | 6 context-overflow traces | 925,080 | 1,530,317 | 1.654 |
| `calibrate-925k-D-claude-opus-4.8.json` | finished traces, seeded shuffle | 925,175 | 1,549,297 | 1.675 |
| `calibrate-631k-B-claude-opus-4.8.json` | 3 other overflow traces | 631,109 | 1,058,298 | 1.677 |

So the same corpus text costs about 1.65-1.68 times as many Claude tokens,
and Anthropic's limit is reached at about 597k o200k tokens, well before
Copilot's own 935,793 limit. Copilot therefore never cuts a Claude prompt;
the provider rejects it first.
