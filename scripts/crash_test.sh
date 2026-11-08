#!/usr/bin/env bash
# Crash-recovery demonstration (capability C13): kill the controller at a chosen dangerous moment, restart it,
# and PROVE the run finishes with nothing lost and nothing applied twice.
#
#   bash scripts/crash_test.sh public_02_incidents after_put
#   points:  after_intent | after_put | after_post | after_apply      (see controller/chaos.py)
#
# Output: reports/crash_recovery_<point>.txt  (also printed)
set -uo pipefail
SCEN="${1:?usage: bash scripts/crash_test.sh <scenario> <point: after_intent|after_put|after_post|after_apply>}"
POINT="${2:?give a crash point}"
RUN="crash_${POINT}_$(date +%H%M%S)"
URL="http://127.0.0.1:8700"
BASE="runs/base_${SCEN}"
export PYTHONUTF8=1
mkdir -p reports runs
alive() { python -X utf8 -c "import httpx; httpx.get('$URL/health', timeout=2).raise_for_status()" >/dev/null 2>&1; }
alive && { echo "A simulator is already running on $URL. Stop it first." >&2; exit 1; }
[ -d "$BASE" ] || python -X utf8 -m sim baseline --scenario "scenarios/$SCEN.yaml" --run-dir "$BASE" || exit 1

python -X utf8 -m sim serve --scenario "scenarios/$SCEN.yaml" --run-dir "runs/$RUN" > "runs/$RUN.sim.log" 2>&1 &
SIM_PID=$!
trap 'kill $SIM_PID 2>/dev/null || true' EXIT
for i in $(seq 1 90); do alive && break; sleep 1; done
alive || { echo "simulator did not come up" >&2; exit 1; }
rm -f controller_state.db*
OUT="reports/crash_recovery_${POINT}.txt"
{
echo "== crash test: scenario=$SCEN point=$POINT run=$RUN"
echo "== 1st run (will be killed at '$POINT' with exit code 137, like kill -9)"
CHAOS_POINT="$POINT" CHAOS_MARKER="$PWD/runs/$RUN.chaos.marker" ./run.sh "$URL" 2> "reports/$RUN.run1.log"
CODE1=$?
echo "first run exit code: $CODE1  (137 = killed mid-protocol; 0 = the crash point was never reached)"
python -X utf8 -m controller.audit --limit 4 2>/dev/null | head -6
echo
echo "== restart (same database, same simulated world)"
./run.sh "$URL" 2> "reports/$RUN.run2.log"
CODE2=$?
echo "second run exit code: $CODE2"
echo
echo "== verification against the simulator's own records"
python -X utf8 -m controller.verify --url "$URL" --db controller_state.db
V=$?
echo
echo "== what the restart did about the interrupted step (audit):"
python -X utf8 -m controller.audit --limit 300 2>/dev/null | grep -E "controller_started|proposal_(intent|pending|applying|applied)" | head -12
[ "$CODE1" = "137" ] && [ "$CODE2" = "0" ] && [ "$V" = "0" ] && echo "CRASH TEST: PASSED" || echo "CRASH TEST: FAILED"
} 2>&1 | tee "$OUT"