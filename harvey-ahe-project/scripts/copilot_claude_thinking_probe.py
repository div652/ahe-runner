"""Probe GH Copilot Server with a Claude model (runbook 5p).

Usage, from the repo root:
  .venv/bin/python harvey-ahe-project/scripts/copilot_claude_thinking_probe.py <MODEL_ID> <OUT.json> <MODE> [...]

Modes:
  effort                  The same puzzle 4 times: no effort, "high", no effort, "high".
                          If thinking is on, "high" takes clearly longer (answer: 1477).
  tools                   A tool loop with effort "high". The tool results are sent back
                          without thinking blocks, as the harness does. Every step must succeed.
  calibrate <N> <TRACE>...
                          One prompt of about N o200k tokens built from real tool results in
                          the given trace.jsonl files, with a code at the start. Shows if the
                          prompt is accepted whole (code recalled), cut (UNKNOWN) or rejected
                          (the provider error may give the Claude token count).
"""
import json
import sys
import time
import urllib.request

import tiktoken

BASE = "http://127.0.0.1:3141/v1"
MODEL, OUT, MODE = sys.argv[1], sys.argv[2], sys.argv[3]
enc = tiktoken.get_encoding("o200k_base")


def post(body, timeout=900):
    t = time.time()
    req = urllib.request.Request(BASE + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.load(r)
            ok = True
    except urllib.error.HTTPError as e:
        d = {"http_status": e.code, "body": e.read().decode()[:2000]}
        ok = False
    out = {"ok": ok, "seconds": round(time.time() - t, 2)}
    if ok:
        msg = d["choices"][0]["message"]
        out.update(model=d.get("model"), text=msg.get("content"), tool_calls=msg.get("tool_calls"))
    else:
        out["error"] = d
    return out


results = {"model": MODEL, "base_url": BASE, "mode": MODE,
           "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

if MODE == "effort":
    puzzle = ("Count the integers n with 1 <= n <= 3000 such that n^2 + 1 is divisible by 5 or by 13. "
              "Reply with only the number.")
    results["runs"] = []
    for effort in [None, "high", None, "high"]:
        body = {"model": MODEL, "messages": [{"role": "user", "content": puzzle}],
                "max_tokens": 16000, "stream": False}
        if effort:
            body["reasoning"] = {"effort": effort}
        r = post(body)
        r["requested_effort"] = effort
        results["runs"].append(r)
        print("effort", effort, r["seconds"], r.get("model"), r.get("text"), r.get("error"), flush=True)

elif MODE == "tools":
    balances = {"A-17": "1234.50", "B-4": "88.25", "C-9": "410.00"}
    tools = [{"type": "function", "function": {
        "name": "get_balance", "description": "Return the balance of one account.",
        "parameters": {"type": "object", "properties": {"account_id": {"type": "string"}},
                       "required": ["account_id"]}}}]
    msgs = [{"role": "system", "content": "You are a careful assistant. Use the tool for every balance; "
                                          "call it for one account at a time."},
            {"role": "user", "content": "First get the balance of A-17. If it is above 1000, also get B-4, "
                                        "then C-9. Reply with only the sum of the balances you fetched."}]
    results["steps"] = []
    for step in range(6):
        r = post({"model": MODEL, "messages": msgs, "tools": tools, "reasoning": {"effort": "high"},
                  "max_tokens": 16000, "stream": False})
        r["step"] = step + 1
        results["steps"].append(r)
        print("step", step + 1, r["ok"], r["seconds"], r.get("model"), r.get("text"),
              [c["function"]["arguments"] for c in r.get("tool_calls") or []], r.get("error"), flush=True)
        if not r["ok"] or not r.get("tool_calls"):
            break
        msgs.append({"role": "assistant", "content": r.get("text"), "tool_calls": r["tool_calls"]})
        for c in r["tool_calls"]:
            acct = json.loads(c["function"]["arguments"]).get("account_id", "")
            msgs.append({"role": "tool", "tool_call_id": c["id"], "content": balances.get(acct, "unknown account")})
    results["expected_final"] = "1732.75"

elif MODE == "calibrate":
    target = int(sys.argv[4])
    texts = []
    for path in sys.argv[5:]:
        for line in open(path):
            m = json.loads(line).get("message")
            if not isinstance(m, dict) or not isinstance(m.get("content"), list):
                continue
            for c in m["content"]:
                if c.get("type") == "tool_result":
                    raw = c.get("content")
                    try:
                        raw = json.loads(raw).get("result", raw)
                    except Exception:
                        pass
                    if isinstance(raw, str) and len(raw) > 2000:
                        texts.append(raw)
    secret = "PELICAN-4821"
    msgs = [{"role": "system", "content": f"You are a careful assistant. The project reference code is {secret}. "
                                          "Remember it exactly."}]
    question = ("What is the project reference code mentioned at the start of this conversation? "
                "Reply with only the code, or UNKNOWN if you cannot see it.")
    total = len(enc.encode(msgs[0]["content"])) + len(enc.encode(question))
    seen = set()
    for t in texts:
        if t in seen:
            continue
        seen.add(t)
        t = enc.decode(enc.encode(t)[:60000])
        n = len(enc.encode(t))
        if total + n > target:
            t = enc.decode(enc.encode(t)[:target - total])
            n = len(enc.encode(t))
        msgs.append({"role": "user", "content": "Tool output:\n" + t})
        msgs.append({"role": "assistant", "content": "Noted."})
        total += n + len(enc.encode("Tool output:\n")) + 1
        if total >= target - 50:
            break
    msgs.append({"role": "user", "content": question})
    total = sum(len(enc.encode(m["content"])) for m in msgs)
    results.update(target_o200k=target, prompt_tokens_o200k=total, n_messages=len(msgs),
                   prompt_chars=sum(len(m["content"]) for m in msgs))
    print("prompt", total, "o200k tokens,", len(msgs), "messages", flush=True)
    if total < 0.97 * target:
        sys.exit("not enough text in the given traces; nothing sent")
    r = post({"model": MODEL, "messages": msgs, "reasoning": {"effort": "high"}, "max_tokens": 16000,
              "stream": False})
    r["secret_recalled"] = bool(r.get("text") and secret in r["text"])
    results["run"] = r
    print("calibrate", r["ok"], r["seconds"], r.get("model"), r.get("text"), r.get("error"), flush=True)

else:
    sys.exit(f"unknown mode {MODE}")

results["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
json.dump(results, open(OUT, "w"), indent=2)
