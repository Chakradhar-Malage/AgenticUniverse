# #!/usr/bin/env bash
# # Replace this with your controller. Contract: ./run.sh <SIM_URL> runs until the scenario finishes.
# set -euo pipefail
# SIM_URL="${1:?usage: ./run.sh <SIM_URL>}"
# echo "No controller yet — running the naive example against $SIM_URL" >&2
# exec python examples/naive_controller.py --url "$SIM_URL"



#!/usr/bin/env bash
# Contract: ./run.sh <SIM_URL> starts the controller and runs until the scenario finishes.
# Needs: python3.11+, `pip install -r requirements.txt`. State lives in controller_state.db
# (delete it for a clean start; keep it to resume after a crash).
set -euo pipefail
SIM_URL="${1:?usage: ./run.sh <SIM_URL>}"
cd "$(dirname "$0")"
exec python -m controller.main --url "$SIM_URL" --db "${CONTROLLER_DB:-controller_state.db}"