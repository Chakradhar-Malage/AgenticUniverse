#!/usr/bin/env bash
# One-command evaluation of the controller on a scenario. Avoids every manual-order mistake:
#   baseline (once) -> fresh simulator -> controller -> grade (UTF-8 safe) -> audit dump.
#
#   bash scripts/eval.sh public_02_incidents [mine05]
#                        ^ scenario name     ^ optional NEW run-folder name (auto-generated if omitted)
#
# Output: reports/<scenario>.md (grade), reports/<scenario>.audit.txt, reports/<run>.controller.log
set -uo pipefail
SCEN="${1:?usage: bash scripts/eval.sh <scenario e.g. public_02_incidents> [new run name]}"
RUN="${2:-auto_$(date +%H%M%S)}"          # optional: a fresh name is generated if omitted
URL="http://127.0.0.1:8700"
BASE="runs/base_${SCEN}"
export PYTHONUTF8=1
mkdir -p reports runs

alive() { python -X utf8 -c "import httpx,sys; httpx.get('$URL/health', timeout=2).raise_for_status()" >/dev/null 2>&1; }

if alive; then
  echo "A simulator is already running on $URL. Stop it first (Ctrl+C in the terminal running 'sim serve')." >&2
  exit 1
fi
if [ -e "runs/$RUN" ]; then
  echo "runs/$RUN already exists. A run folder holds exactly one run: pick a new name." >&2
  exit 1
fi
if [ ! -d "$BASE" ]; then
  echo "== baseline for $SCEN"
  python -X utf8 -m sim baseline --scenario "scenarios/$SCEN.yaml" --run-dir "$BASE" || exit 1
fi

echo "== starting a fresh simulator: runs/$RUN"
python -X utf8 -m sim serve --scenario "scenarios/$SCEN.yaml" --run-dir "runs/$RUN" > "runs/$RUN.sim.log" 2>&1 &
SIM_PID=$!
trap 'kill $SIM_PID 2>/dev/null || true' EXIT
for i in $(seq 1 90); do alive && break; sleep 1; done
alive || { echo "simulator did not come up; see runs/$RUN.sim.log" >&2; exit 1; }

echo "== running controller"
rm -f controller_state.db*
START=$(date +%s)
./run.sh "$URL" 2> "reports/$RUN.controller.log"
CODE=$?
END=$(date +%s)
echo "controller exit code $CODE, wall-clock $((END-START))s"
if [ $CODE -ne 0 ]; then echo "---- last lines of reports/$RUN.controller.log"; tail -25 "reports/$RUN.controller.log"; exit $CODE; fi

echo "== grading"
python -X utf8 -m sim grade --run "runs/$RUN" --baseline "$BASE" --out "reports/$SCEN.md" || exit 1
python -X utf8 -m controller.audit --limit 300 > "reports/$SCEN.audit.txt" 2>&1
python -X utf8 -m controller.status | tail -3
echo "== loop health"
python -X utf8 -m controller.status --loop | tee "reports/$SCEN.loop.txt"
echo "== done. Read reports/$SCEN.md and reports/$SCEN.audit.txt"