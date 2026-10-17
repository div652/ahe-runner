#!/usr/bin/env bash
# Run a C0-only TRAPI evaluation (scripts/run_c0_train_only.py) until it
# completes, without ever flooding the provider.
#
# Usage, from the repository root:
#   export AHE_AZURE_BASE_URL=<https endpoint ending in /openai/v1>
#   export AHE_AZURE_TOKEN_SCOPE=<Entra scope, e.g. api://<app>/.default>
#   bash harvey-ahe-project/scripts/launch_trapi_c0.sh \
#       harvey-ahe-project/ahe-trapi-c0b.yaml \
#       harvey-ahe-project/campaign-artifacts/c13b-run.log
#
# Each invocation:
#   1. sends ONE probe request (scripts/probe_trapi.py);
#        blocked (403 PrincipalBlocked) -> wait BLOCK_PAUSE_SECONDS, re-probe;
#        rate limited or other error    -> wait PAUSE_SECONDS, re-probe;
#   2. runs the evaluation; only pending units run, finished ones are kept;
#   3. on success stops; on StageBlockedError (stage attempts used up) stops
#      for the operator; on a provider block waits BLOCK_PAUSE_SECONDS; on any
#      other failure waits PAUSE_SECONDS.
# Never relaunches immediately. At most MAX_INVOCATIONS probes in total.
set -u

CONFIG=${1:?usage: launch_trapi_c0.sh <config.yaml> <log file>}
LOG=${2:?usage: launch_trapi_c0.sh <config.yaml> <log file>}
MAX_INVOCATIONS=${MAX_INVOCATIONS:-16}
PAUSE_SECONDS=${PAUSE_SECONDS:-600}
BLOCK_PAUSE_SECONDS=${BLOCK_PAUSE_SECONDS:-1800}
PYTHON=${PYTHON:-.venv/bin/python}
: "${AHE_AZURE_BASE_URL:?set AHE_AZURE_BASE_URL}"
: "${AHE_AZURE_TOKEN_SCOPE:?set AHE_AZURE_TOKEN_SCOPE}"
export PYTHONPATH="$PWD/src:$PWD/harvey-ahe-project${PYTHONPATH:+:$PYTHONPATH}"
MARKER="model provider blocked this principal"

log() { echo "[launcher $(date -u +%FT%TZ)] $*" | tee -a "$LOG"; }

for i in $(seq 1 "$MAX_INVOCATIONS"); do
  probe=$("$PYTHON" harvey-ahe-project/scripts/probe_trapi.py 2>/dev/null)
  rc=$?
  log "probe $i/$MAX_INVOCATIONS exit=$rc $probe"
  if [ "$rc" -eq 3 ]; then
    log "TRAPI blocked this principal; waiting ${BLOCK_PAUSE_SECONDS}s"
    sleep "$BLOCK_PAUSE_SECONDS"
    continue
  elif [ "$rc" -ne 0 ]; then
    log "probe failed; waiting ${PAUSE_SECONDS}s"
    sleep "$PAUSE_SECONDS"
    continue
  fi

  log "invocation $i: run_c0_train_only.py --config $CONFIG"
  start=$(wc -l < "$LOG")
  "$PYTHON" harvey-ahe-project/scripts/run_c0_train_only.py --config "$CONFIG" \
    >> "$LOG" 2>&1
  rc=$?
  tail_text=$(tail -n "+$((start + 1))" "$LOG" | tail -n 200)
  if [ "$rc" -eq 0 ]; then
    log "invocation $i: evaluation complete"
    exit 0
  fi
  if grep -q '"type": "StageBlockedError"' <<< "$tail_text"; then
    log "invocation $i: stage blocked (attempts used up); operator needed"
    exit 1
  fi
  if grep -q "$MARKER" <<< "$tail_text"; then
    log "invocation $i: provider block; waiting ${BLOCK_PAUSE_SECONDS}s"
    sleep "$BLOCK_PAUSE_SECONDS"
  else
    log "invocation $i: exit $rc; waiting ${PAUSE_SECONDS}s"
    sleep "$PAUSE_SECONDS"
  fi
done
log "gave up after $MAX_INVOCATIONS probes"
exit 1
