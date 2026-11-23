#!/usr/bin/env bash
# pnr_one.sh <app> <ga_row> <tag> <clock-ns>
# One KV260 GA design through Vitis HLS 2022.2 at the given clock (smoke_hls.sh, KEEP=1), then out-of-context
# place-and-route with Vivado 2022.2 via `export_design -flow impl`. Output: ~/gnosis/pnr/<app>_<row>_<clk>.txt
# (HLS result + post-implementation timing and resources) and a copy of the export report.
APP=$1; ROW=$2; TAG=$3; CLK=$4
OUT_DIR=~/gnosis/pnr; mkdir -p "$OUT_DIR"
OUT="$OUT_DIR/${APP}_${ROW}_${CLK}.txt"
echo "PNR $APP $ROW $TAG clk=${CLK}ns start $(date -Is)" > "$OUT"

# 1. HLS at this clock (smoke_hls loads the vitis_hls module itself)
GA_CSV=~/gnosis/results/${APP}_xck26_10_s42_g24.csv APP=$APP ROW=$ROW KEEP=1 \
  bash ~/gnosis/v5/laneC/smoke_hls.sh ~/gnosis/GNWSIS 2022.2 xck26-sfvc784-2LV-c "$CLK" >> "$OUT" 2>&1
WORK=$(sed -n 's/^Work dir: //p' "$OUT" | tail -1)
if [ -z "$WORK" ] || [ ! -f "$WORK/smoke/solution1/solution1_data.json" ]; then
  echo "PNR_FAIL no HLS result" >> "$OUT"; exit 1
fi

# 2. Place-and-route (needs Vivado on PATH next to Vitis HLS)
type module >/dev/null 2>&1 || source /etc/profile.d/modules.sh 2>/dev/null || source /usr/share/lmod/lmod/init/bash 2>/dev/null
module purge; module load xilinx/vitis_hls/2022.2 xilinx/vivado/2022.2 >/dev/null 2>&1
cd "$WORK" || exit 1
cat > export_impl.tcl <<'EOF'
open_project smoke
open_solution solution1
export_design -flow impl -rtl verilog -format ip_catalog
exit
EOF
T0=$(date +%s)
timeout 7200 vitis_hls -f export_impl.tcl -l export_impl.log > export_impl.out 2>&1
echo "IMPL_EXIT $?  IMPL_WALL_S $(( $(date +%s) - T0 ))" >> "$OUT"
grep -E "CP required|CP achieved|Timing (met|not met)|^ *(SLICE|LUT|FF|DSP|SRL|BRAM|URAM|LATCH|CLB) *:" export_impl.log >> "$OUT"
grep -E "^ERROR|CRITICAL WARNING" export_impl.log | head -5 >> "$OUT"
RPT=$(ls smoke/solution1/impl/report/verilog/*export.rpt 2>/dev/null | head -1)
[ -n "$RPT" ] && cp "$RPT" "$OUT_DIR/${APP}_${ROW}_${CLK}_export.rpt"
echo "PNR_DONE $(date -Is)" >> "$OUT"
