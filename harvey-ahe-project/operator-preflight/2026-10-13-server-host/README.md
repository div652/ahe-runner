# Operator model preflight, 2026-10-13 (server-hosted endpoint)

These reports repeat the `2026-10-13/` checks after moving the GH Copilot Server
endpoint off the operator's laptop VS Code window and onto a VS Code instance
that runs on the model host itself. The goal is an endpoint that survives the
laptop's network drops. Command, models, reasoning, and file names are the same
as in `../2026-10-13/README.md`; response text is not stored.

| File | Model | Reasoning | Positive probe | Negative control |
|---|---|---|---|---|
| `evaluated-gpt-5.4-mini.json` | `gpt-5.4-mini` | provider default | 200, returned `gpt-5.4-mini` | rejected, error named the fake ID |
| `meta-gpt-5.4-high.json` | `gpt-5.4` | `high` | 200, returned `gpt-5.4` | rejected, error named the fake ID |
| `judge1-helper-claude-sonnet-5.json` | `claude-sonnet-5` | provider default | 200, returned `claude-sonnet-5` | rejected, error named the fake ID |
| `judge2-gpt-5.5.json` | `gpt-5.5` | provider default | 200, returned `gpt-5.5` | rejected, error named the fake ID |

`GET /v1/models` advertised 31 IDs (27 in the earlier preflight), including all
four campaign models.

## Endpoint host

- VS Code desktop 1.141.0, commit `2a59476c9bfcb90b3ddc372c36762471b7dfad1c`,
  the same build as the operator's VS Code Remote extension host at the time,
  unpacked in `~/copilot-host/VSCode-linux-x64` (tarball SHA-256
  `d77d588f0454cbaaa71e723497de4160d15b95fba4988abe177b1432e6bd570a`).
  Setup first used 1.138.0 (Copilot Chat 0.66.0, kept in
  `~/copilot-host/VSCode-linux-x64-1.138.0`); all four probes passed there too,
  then the host was upgraded to match the laptop build and the reports in this
  folder were regenerated on 1.141.0.
- Runs under `Xvfb :99` inside tmux session `copilot-host`, started by
  `~/copilot-host/start-copilot-host.sh`, with its own profile
  (`--user-data-dir ~/copilot-host/user-data`,
  `--extensions-dir ~/copilot-host/extensions`, `--password-store=basic`).
  A loopback-only, password-protected x11vnc/noVNC viewer (127.0.0.1:6080) is
  used for one-time setup.
- Copilot Chat is the built-in `GitHub.copilot-chat` 0.69.0, signed in with
  the operator's GitHub account (Copilot Enterprise seat) via the device-code
  flow. Profile settings disable VS Code and extension auto-updates.
- Listener: `127.0.0.1:3141`, owned by that VS Code's extension host (tmux →
  `code`), not by the laptop's VS Code Remote extension host. The laptop
  window's server was stopped first.

Extension `gh-copilot-server.gh-copilot-server-0.1.0`, installed from a VSIX
rebuilt with the operator's patched `out/` files (SHA-256):

```text
68fb9235d48f491e6a945fe79d2a2944603f756a541be41ea7c54bea4092f6fe  package.json (differs only in VS Code's install __metadata)
9090e9ebf4aff5214c0e19617cdbd362f0ee528336b4c9846bbb93f29bae7a45  out/server.js (same as before)
c7ef05f74d3645f7daac63cc3fdc230fd3eaf9a7bc689c65c92ab5ec06e3f5ab  out/extension.js (same as before)
d0129020afe54265f4d9f777481684ebaffabf7baf0acf28e606be6b9e3c2c29  gh-copilot-server-0.1.0-patched.vsix
```

## Setup notes

- A fresh profile starts with the built-in Copilot Chat extension disabled; it
  was enabled by removing it from `extensionsIdentifiers/disabled` in the
  profile's `state.vscdb` while VS Code was stopped (backup kept).
- The first model request from the extension opens a one-time language-model
  access consent dialog. Until it is accepted, requests hang until the
  extension's 300 s timeout. The first run of these four probes failed that way
  and was rerun after the operator clicked Allow. The consent persisted across
  the 1.141.0 upgrade.
- Disconnect test (`~/copilot-host/disconnect_probe.py`, one `gpt-5.4-mini`
  request every 20 s): the operator's laptop disconnected and VS Code Remote
  started a new extension host at 13:46:21; all requests from 13:43:55 to
  13:46:35 returned 200 with `gpt-5.4-mini` in under 1 s, and 3141 stayed with
  the server-side VS Code. The only failures in the log are the two
  connection-refused probes during the deliberate 1.141.0 restart.
- "GH Copilot Server: Start Server" can be triggered without the viewer:
  `DISPLAY=:99 xdotool key F1`, type the command name, press Return.
