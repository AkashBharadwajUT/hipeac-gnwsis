#!/usr/bin/env bash
# ga_rescore.sh - re-synthesise every feasible design our KV260 GA found for a kernel and record the WORST-case
# latency (the GA itself optimised ClockInfo.Latency, which Vitis 2022.2 reports as the best case for loops with a
# variable trip count).
#
# Usage: bash ga_rescore.sh <GNWSIS-clone> <app> [parallel=20]
# Reads  ~/gnosis/results/<app>_xck26_10_s42_g24.csv
# Writes ~/gnosis/ga_rescore/<app>/<index>.txt  then  ~/gnosis/ga_rescore/<app>_rescored.csv + a printed summary.
set -o pipefail
G=$(cd "${1:?GNWSIS clone}" && pwd) || exit 1; APP=${2:?app}; P=${3:-20}
HERE=$(cd "$(dirname "$0")" && pwd)
CSV=${GA_CSV:-$HOME/gnosis/results/${APP}_xck26_10_s42_g24.csv}
[ -f "$CSV" ] || { echo "No GA CSV: $CSV"; exit 1; }
OUT=$HOME/gnosis/ga_rescore/$APP; mkdir -p "$OUT"
python3 - "$CSV" > "$OUT/rows.txt" <<'PY'
import csv, sys
for r in csv.DictReader(open(sys.argv[1])):
    if r["feasible"] == "True":
        print("ga_" + r["index_vector"].replace(" ", "_"))
PY
N=$(wc -l < "$OUT/rows.txt"); echo "$APP: $N feasible GA designs to re-score, $P at a time"
export G APP CSV OUT HERE
xargs -P "$P" -I{} sh -c '[ -f "$OUT/{}.txt" ] && grep -q "^Ours" "$OUT/{}.txt" && exit 0
  GA_CSV=$CSV APP=$APP ROW={} bash $HERE/smoke_hls.sh $G 2022.2 xck26-sfvc784-2LV-c 10 > $OUT/{}.txt 2>&1' < "$OUT/rows.txt"
python3 "$HERE/ga_rescore_summary.py" "$APP" "$CSV" "$OUT"
