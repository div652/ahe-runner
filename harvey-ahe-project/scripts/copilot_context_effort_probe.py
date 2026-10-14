"""Probe GH Copilot Server: (A) is reasoning effort applied? (B) is a >272k prompt pruned?

Usage: .venv/bin/python scripts/copilot_context_effort_probe.py <MODEL_ID> <OUT.json> [--context-only]

(A) If effort is applied, "none" answers in about 1 s and is usually wrong, while "high"
    takes 10+ s and answers 1477. (B) If the long prompt is not pruned, the over_272k case
    recalls the code from the first message. See runbook section 5m and AHE-021.
"""
import json, random, sys, time, urllib.request
import tiktoken
BASE = "http://127.0.0.1:3141/v1"
MODEL = sys.argv[1]
OUT = sys.argv[2]
enc = tiktoken.get_encoding("o200k_base")

def post(body, timeout=600):
    t = time.time()
    req = urllib.request.Request(BASE + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.load(r); ok = True
    except urllib.error.HTTPError as e:
        d = {"http_status": e.code, "body": e.read().decode()[:500]}; ok = False
    dt = round(time.time() - t, 2)
    text = d["choices"][0]["message"]["content"] if ok else None
    return {"ok": ok, "seconds": dt, "model": d.get("model") if ok else None, "text": text, "error": None if ok else d}

def filler(n_tokens, seed):
    rnd = random.Random(seed)
    words = ["contract", "party", "clause", "notice", "term", "payment", "agreement", "section", "liability",
             "indemnity", "warranty", "breach", "remedy", "schedule", "obligation", "license", "consent", "assign"]
    out = []
    while True:
        chunk = " ".join(rnd.choice(words) for _ in range(5000))
        out.append(chunk)
        if len(enc.encode(" ".join(out))) >= n_tokens: return " ".join(out)

results = {"model": MODEL, "base_url": BASE, "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
puzzle = ("Count the integers n with 1 <= n <= 3000 such that n^2 + 1 is divisible by 5 or by 13. "
          "Reply with only the number.")
results["effort"] = []
for effort in ([] if "--context-only" in sys.argv else ["none", "high", "none", "high"]):
    r = post({"model": MODEL, "messages": [{"role": "user", "content": puzzle}],
              "reasoning": {"effort": effort}, "max_tokens": 4000, "stream": False})
    r["requested_effort"] = effort; results["effort"].append(r); print("effort", effort, r["seconds"], r["text"], flush=True)

SECRET = "PELICAN-4821"
def ctx_messages(n_fill, block):
    msgs = [{"role": "system", "content": f"You are a careful assistant. The project reference code is {SECRET}. Remember it exactly."}]
    for i in range(n_fill):
        msgs.append({"role": "user", "content": f"Background document {i+1}:\n" + filler(block, i)})
        msgs.append({"role": "assistant", "content": "Noted."})
    msgs.append({"role": "user", "content": "What is the project reference code mentioned at the start of this conversation? "
                 "Reply with only the code, or UNKNOWN if you cannot see it."})
    return msgs
results["context"] = []
for label, n_fill, block in [("control_small", 1, 2000), ("over_272k", 3, 101000)]:
    msgs = ctx_messages(n_fill, block)
    toks = sum(len(enc.encode(m["content"])) for m in msgs)
    r = post({"model": MODEL, "messages": msgs, "max_tokens": 4000, "stream": False})
    r.update(label=label, prompt_tokens_o200k=toks, secret_recalled=bool(r["text"] and SECRET in r["text"]))
    results["context"].append(r); print("context", label, toks, r["seconds"], r["text"], r["error"], flush=True)
json.dump(results, open(OUT, "w"), indent=2)
