#!/usr/bin/env bash
# board_build.sh - one dataset design -> HLS IP for the KV260 -> bitstream + hwh for PYNQ.
#
# Usage: bash board_build.sh <GNWSIS-clone> <app> <row> [pl-clock-MHz]
#   <row>: baseline | fastest | workload_N.cpp (rows are taken from the ZCU104 dataset, as in smoke_hls.sh)
# Example (the three designs for the board experiment):
#   bash board_build.sh ~/gnosis/GNWSIS rodinia-knn-1-tiling baseline
#   bash board_build.sh ~/gnosis/GNWSIS rodinia-knn-1-tiling workload_433.cpp
#   bash board_build.sh ~/gnosis/GNWSIS rodinia-knn-1-tiling workload_470.cpp
# SCRATCH=<dir> overrides the scratch folder (on caes-lab2: the Scratchpad Manager folder).
# Needs smoke_hls.sh next to this script. Output: /local/scratch/$USER/board/<name>.{bit,hwh,_timing.rpt,_util.rpt}
set -o pipefail
G=$(cd "${1:?GNWSIS clone}" && pwd) || exit 1; APP=${2:?app}; ROW=${3:?row}; MHZ=${4:-100}
HERE=$(cd "$(dirname "$0")" && pwd)
CLK=$(python3 -c "print(f'{1000/$MHZ:.2f}'.rstrip('0').rstrip('.'))")
TAG=${ROW%.cpp}; NAME=$(echo "${APP}_${TAG}_${MHZ}" | tr -c 'A-Za-z0-9_\n' '_')
OUT=${SCRATCH:-/local/scratch/${USER:-$(whoami)}}/board; mkdir -p "$OUT" || exit 1

# 1. HLS synthesis for the KV260 part, keeping the exported IP (KEEP=1); reused if it already exists (REBUILD=1 redoes it)
cd "$OUT" || exit 1
EXIST="${SCRATCH:-/local/scratch/${USER:-$(whoami)}}/smoke_${APP}_${TAG}_2022.2_xck26_${CLK}/smoke/solution1/impl/ip"
if [ "${REBUILD:-0}" != 1 ] && [ -f "$EXIST/component.xml" ]; then
  echo "Reusing HLS IP: $EXIST"
else
  KEEP=1 APP=$APP ROW=$ROW bash "$HERE/smoke_hls.sh" "$G" 2022.2 xck26-sfvc784-2LV-c "$CLK" | tee "${NAME}_hls.txt"
fi
# smoke_hls.sh works in /local/scratch/$USER (or the current folder if that is not writable)
IP=$(ls -d "${SCRATCH:-/local/scratch/${USER:-$(whoami)}}/smoke_${APP}_${TAG}_2022.2_xck26_${CLK}/smoke/solution1/impl/ip" \
           "$OUT/smoke_${APP}_${TAG}_2022.2_xck26_${CLK}/smoke/solution1/impl/ip" 2>/dev/null | head -1)
[ -n "$IP" ] && [ -f "$IP/component.xml" ] || { echo "No exported IP found; see ${NAME}_hls.txt"; exit 1; }

# 2. Block design, place-and-route, bitstream
type module >/dev/null 2>&1 || source /etc/profile.d/modules.sh 2>/dev/null
module purge; module load xilinx/vivado/2022.2
nice -n 10 vivado -mode batch -nojournal -nolog -source "$HERE/build_overlay.tcl" -tclargs "$IP" "$NAME" "$MHZ" 4 \
  > "${NAME}_vivado.log" 2>&1
grep -E "^DONE|^ERROR|^WARNING: (KV260|automation)" "${NAME}_vivado.log"
ls -la "$OUT/$NAME.bit" "$OUT/$NAME.hwh" 2>/dev/null && rm -rf "$OUT/ov_$NAME"
