#!/usr/bin/env bash
# smoke_hls.sh - run one GNOSIS design point through Vitis HLS exactly as GenHLSOptimizer builds it,
# measure peak RAM and run time, and compare the result with the dataset.
#
# Usage:
#   bash smoke_hls.sh <path-to-GNWSIS-clone> [vitis-version] [part] [clock-ns]
#
# Environment options:
#   APP=<folder in data/ApplicationDataset>   kernel (default rodinia-knn-0-baseline)
#   ROW=baseline      no directives; compare with ApplicationBaselineInformation.csv (default)
#   ROW=fastest       the dataset's lowest-latency feasible design for this kernel
#   ROW=largest       the dataset's most resource-hungry feasible design (worst case for RAM)
#   ROW=random        a random feasible design (SEED=<n> to repeat it)
#   ROW=workload_17.cpp  a specific row, by its Version column
#   ROW=ga_fastest    with GA_CSV=<ga_export.py CSV>: the fastest feasible design our GA found
#   REF_CLK=<ns>      dataset clock the row is taken from (default: the synthesis clock); rows are numbered per
#                     (device, clock), so set REF_CLK=10 to build a 100 MHz row at another clock
#   REF_PART=<part>   dataset device the row is taken from (default xczu7ev-ffvc1156-2-e);
#                     with part=xck26-... this applies a ZCU104 configuration to the KV260
#
# Examples:
#   bash smoke_hls.sh ~/gnosis/GNWSIS 2022.2
#   APP=rodinia-knn-1-tiling ROW=fastest bash smoke_hls.sh ~/gnosis/GNWSIS 2022.2
#   APP=rodinia_lavaMD_1_tiling_0 ROW=largest bash smoke_hls.sh ~/gnosis/GNWSIS 2022.2
#   APP=rodinia-knn-1-tiling ROW=fastest bash smoke_hls.sh ~/gnosis/GNWSIS 2022.2 xck26-sfvc784-2LV-c 10
#
# SCRATCH=<dir> overrides the scratch folder (e.g. the Scratchpad Manager folder on caes-lab2).
# Work happens in /local/scratch/$USER/smoke_... (CAES scratch; clean up after) if writable, otherwise in the current directory.
# KEEP=1 keeps the HLS project folder; by default only the reports and logs are kept.

set -o pipefail
REPO=${1:?usage: bash smoke_hls.sh <path-to-GNWSIS-clone> [vitis-version] [part] [clock-ns]}
VER=${2:-}
PART=${3:-xczu7ev-ffvc1156-2-e}
CLK=${4:-10}
APP=${APP:-rodinia-knn-0-baseline}
ROW=${ROW:-baseline}
REF_PART=${REF_PART:-xczu7ev-ffvc1156-2-e}
REPO=$(cd "$REPO" && pwd) || { echo "Repo not found: $1"; exit 1; }
SRC_DIR="$REPO/data/ApplicationDataset/$APP"
[ -f "$SRC_DIR/kernel_info.txt" ] || { echo "Kernel not found: $SRC_DIR"; exit 1; }
TOP=$(head -n 1 "$SRC_DIR/kernel_info.txt" | tr -d '\r')
# GenHLSOptimizer adds only the source file that contains the top-level function
SRC=$(grep -l "$TOP" "$SRC_DIR"/*.c "$SRC_DIR"/*.cpp 2>/dev/null | head -1)
[ -n "$SRC" ] || { echo "No source file contains $TOP"; exit 1; }
SRC=$(basename "$SRC")
echo "Kernel: $APP  top: $TOP  source: $SRC  row: $ROW"

# 1. Load the requested Vitis version through environment modules
if [ -n "$VER" ]; then
  type module >/dev/null 2>&1 || source /etc/profile.d/modules.sh 2>/dev/null \
    || source /usr/share/lmod/lmod/init/bash 2>/dev/null
  module purge
  module load "xilinx/vitis_hls/$VER" || { echo "module load xilinx/vitis_hls/$VER failed"; exit 1; }
fi

# 2. Pick the HLS command: vitis_hls (2021-2024), or vitis-run (2025.x, if vitis_hls is gone)
if command -v vitis_hls >/dev/null 2>&1; then
  HLS=(vitis_hls -f run_hls.tcl -l vitis_hls.log)
elif command -v vitis-run >/dev/null 2>&1; then
  HLS=(vitis-run --mode hls --tcl run_hls.tcl)
else
  echo "Neither vitis_hls nor vitis-run is on PATH; load a module first."; exit 1
fi
echo "HLS command: ${HLS[*]}  ($(command -v "${HLS[0]}"))"

# 3. Work directory: copy the kernel, apply the row's directives, write the Tcl GenHLSOptimizer writes
BASE=${SCRATCH:-/local/scratch/${USER:-$(whoami)}}; mkdir -p "$BASE" 2>/dev/null && [ -w "$BASE" ] || { echo "WARNING: $BASE not writable (set SCRATCH=...); using $PWD"; BASE=$PWD; }
TAG=${ROW%.cpp}; TAG=${TAG%.c}
WORK="$BASE/smoke_${APP}_${TAG}_${VER:-path}_${PART%%-*}_${CLK}"
rm -rf "$WORK"; mkdir -p "$WORK"; cd "$WORK" || exit 1
cp "$SRC_DIR"/*.c "$SRC_DIR"/*.cpp "$SRC_DIR"/*.h "$SRC_DIR"/*.hpp . 2>/dev/null

python3 - "$REPO" "$APP" "$SRC" "$ROW" "$REF_PART" "${REF_CLK:-$CLK}" "${SEED:-0}" "${GA_CSV:-}" <<'PY' || exit 1
# Select a dataset row and insert its directives the way GenHLSOptimizer.apply_directives does:
# walk the source line by line; the first line containing "L1" (spaces removed) gets L1's pragma
# written after it, then "L2", and so on.
import csv, json, random, sys
repo, app, src, row_sel, ref_part, clk, seed, ga_csv = sys.argv[1:]
data = f"{repo}/data"
info = [l.strip() for l in open(f"{data}/ApplicationDataset/{app}/kernel_info.txt") if l.strip()]
labels = {}
for l in info[1:]:
    p = l.split(",")
    labels[p[0]] = {"type": p[1], "name": p[2] if p[1] == "array" else None}
expected = {"row": row_sel}
if row_sel == "baseline":
    directives = {}
elif row_sel.startswith("ga_"):
    # A design from our own GA (ga_export.py CSV): ROW=ga_fastest, or ROW=ga_<index_vector with _ for spaces>
    if not ga_csv:
        sys.exit("ROW=ga_... needs GA_CSV=<path to the GA's CSV>")
    rows = list(csv.DictReader(open(ga_csv)))
    feasible = [r for r in rows if r["feasible"] == "True"]
    if row_sel == "ga_fastest":
        row = min(feasible, key=lambda r: float(r["latency_ms"]))
    else:
        want = row_sel[3:].replace("_", " ")
        match = [r for r in rows if r["index_vector"] == want]
        if not match:
            sys.exit(f"No GA row with index vector {want}")
        row = match[0]
    print(f"GA row [{row['index_vector']}] from {ga_csv} ({row['part']}, {row['clock_ns']} ns), {len(feasible)} feasible of {len(rows)}")
    directives = {lab: "#pragma HLS " + row[lab] for lab in labels if row.get(lab)}
    expected.update({"version": "GA " + row["index_vector"], "latency_ms": float(row["latency_ms"]),
                     "util": {k: float(row[f"{k}_pct"]) for k in ("BRAM", "DSP", "FF", "LUT")},
                     "synth_s": float(row["synth_time_s"] or 0), "source": f"our GA, {row['part']}"})
else:
    try:
        rows = [r for r in csv.DictReader(open(f"{data}/CSVS/{app}.csv"))
                if r["Device"] == ref_part and float(r["Clock_Period_nsec"]) == float(clk)]
    except FileNotFoundError:
        sys.exit(f"No dataset CSV for {app}; use ROW=baseline")
    util_cols = ["BRAM_Utilization_percentage", "DSP_Utilization_percentage",
                 "FF_Utilization_percentage", "LUT_Utilization_percentage"]
    feasible = [r for r in rows if float(r["Latency_msec"]) > 0 and all(float(r[c]) <= 100 for c in util_cols)]
    if row_sel == "fastest":
        row = min(feasible, key=lambda r: float(r["Latency_msec"]))
    elif row_sel == "largest":
        row = max(feasible, key=lambda r: sum(float(r[c]) for c in util_cols))
    elif row_sel == "random":
        row = random.Random(int(seed)).choice(feasible)
    else:
        match = [r for r in rows if r["Version"] == row_sel]
        if not match:
            sys.exit(f"No row {row_sel} for {app} on {ref_part} at {clk} ns")
        row = match[0]
    print(f"Dataset row {row['Version']} ({ref_part}, {clk} ns), {len(feasible)} feasible of {len(rows)} rows")
    mapping = dict(l.strip().split(",") for l in open(f"{data}/ApplicationAPLMapping/{app}.txt") if l.strip())
    directives = {}
    for col, label in mapping.items():
        v = row.get(col, "NDIR")
        if not v or v == "NDIR":
            continue
        t = v.split("_")
        if t[0] == "pipeline":
            d = "#pragma HLS pipeline" + (f" II={t[1]}" if len(t) > 1 else "")
        elif t[0] == "unroll":
            d = "#pragma HLS unroll" + (f" factor={t[1]}" if len(t) > 1 else "")
        elif t[0] == "complete":
            d = f"#pragma HLS array_partition variable={labels[label]['name']} complete dim={t[1]}"
        elif t[0] in ("block", "cyclic"):
            d = f"#pragma HLS array_partition variable={labels[label]['name']} {t[0]} factor={t[1]} dim={t[2]}"
        else:
            sys.exit(f"Unknown directive encoding {v} in column {col}")
        directives[label] = d
    unrecorded = [lab for lab in labels if lab not in mapping.values()]
    if unrecorded:
        print(f"  WARNING: the dataset records no directive for {', '.join(unrecorded)}; "
              "the original run applied one there, so this row may not reproduce exactly")
    expected.update({"version": row["Version"], "latency_ms": float(row["Latency_msec"]),
                     "util": {k: float(row[f"{k}_Utilization_percentage"]) for k in ("BRAM", "DSP", "FF", "LUT")},
                     "synth_s": float(row["Synthesis_Time_sec"])})
# Apply, mirroring GenHLSOptimizer's sequential label matching
lines = open(src).read().splitlines(keepends=True)
out, count, n = [], 1, len(info) - 1
for line in lines:
    out.append(line)
    if count <= n and f"L{count}" in line.replace(" ", "").replace("\t", "").replace("\n", ""):
        d = directives.get(f"L{count}")
        if d:
            out.append(d + "\n")
            print(f"  L{count}: {line.strip()[:60]:<60} -> {d}")
        count += 1
if count <= n:
    print(f"  WARNING: only matched labels up to L{count - 1} of L{n}")
open(src, "w").writelines(out)
json.dump(expected, open("expected.json", "w"))
PY

cat > run_hls.tcl <<EOF
open_project smoke
set_top $TOP
add_files $SRC
open_solution "solution1" -flow_target vivado
set_part {$PART}
create_clock -period $CLK -name default
config_array_partition -complete_threshold 0 -throughput_driven off
config_compile -pipeline_loops 0
csynth_design
export_design -format ip_catalog
exit
EOF
echo "Work dir: $WORK"

# 4. Run it with the paper's 1 h timeout, measuring peak RAM and wall time
if [ -x /usr/bin/time ]; then
  timeout 3600 /usr/bin/time -v "${HLS[@]}" > smoke.log 2> smoke.time
  RC=$?
  PEAK_KB=$(awk -F': ' '/Maximum resident set size/ {print $2}' smoke.time)
  WALL=$(awk -F': ' '/Elapsed \(wall clock\)/ {print $2}' smoke.time)
else
  read -r RC PEAK_KB WALL < <(timeout 3600 python3 - "${HLS[@]}" <<'PY'
import resource, subprocess, sys, time
t0 = time.time()
with open("smoke.log", "w") as log:
    rc = subprocess.call(sys.argv[1:], stdout=log, stderr=subprocess.STDOUT)
peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss  # kB on Linux
print(rc, peak, f"{time.time() - t0:.0f}s")
PY
)
fi
[ "$RC" = 124 ] && echo "Timed out after 1 h (the dataset would record this as not synthesisable)"
echo "Exit code: $RC   Peak RAM: $(( ${PEAK_KB:-0} / 1024 )) MB   Wall time: $WALL"
grep -E '^(ERROR|CRITICAL WARNING)' smoke.log vitis_hls.log 2>/dev/null | head -10

# 5. Read results the way GenHLSOptimizer does (solution1_data.json) and compare with the dataset
python3 - "$WORK" "$REPO" "$APP" "$REF_PART" "$CLK" "$TOP" "$PART" <<'PY'
import csv, json, os, sys
work, repo, app, ref_part, clk, top, part = sys.argv[1:]
path = f"{work}/smoke/solution1/solution1_data.json"
if not os.path.exists(path):
    sys.exit("No solution1_data.json: synthesis did not finish; see smoke.log")
d = json.load(open(path))
ci = d["ClockInfo"]
cycles, period = int(ci["Latency"]), float(ci["ClockPeriod"])
area = d["ModuleInfo"]["Metrics"][top]["Area"]
def pct(v):  # GenHLSOptimizer's rule: "~0" counts as 0
    return 0 if str(v).startswith("~") else int(v)
util = {k: pct(area.get(f"UTIL_{k}", "0")) for k in ("BRAM", "DSP", "FF", "LUT")}
# ClockInfo.Latency is the BEST case when a loop has a variable trip count (2022.2); use the worst case
lat_info = d["ModuleInfo"]["Metrics"][top].get("Latency", {})
best = int(lat_info.get("LatencyBest") or cycles)
worst = int(lat_info.get("LatencyWorst") or cycles)
cycles = worst
lat_ms = cycles * period / 1e6
rng = "" if best == worst else f"  [latency range: best {best}, worst {worst} cycles; worst used]"
print(f"\nOurs:    latency={lat_ms:.5f} ms ({cycles} cycles x {period} ns)  util%={util}{rng}")
exp = json.load(open(f"{work}/expected.json"))
if exp["row"] == "baseline":
    ref = [r for r in csv.DictReader(open(f"{repo}/data/ApplicationBaselineInformation.csv"))
           if r["app_name"] == app and r["device_id"] == ref_part and float(r["clock_period_nsec"]) == float(clk)]
    if not ref:
        sys.exit("No dataset baseline row for this kernel/clock.")
    r = ref[0]
    exp.update({"latency_ms": float(r["design_latency_msec"]), "synth_s": float(r["synthesis_time_sec"]),
                "util": {k: float(r[f"{k.lower()}_utilization"]) for k in ("BRAM", "DSP", "FF", "LUT")}})
if exp["latency_ms"] <= 0:
    sys.exit("Dataset row records a failed synthesis; nothing to compare.")
if "source" in exp:
    label, src_note = "GA row:", f"({exp['source']})"
else:
    label, src_note = "Dataset:", "(Vitis 2021.1)" + ("" if part == ref_part else f"  [dataset is {ref_part}; ours is {part}]")
print(f"{label} latency={exp['latency_ms']:.5f} ms  util%={ {k: int(v) for k, v in exp['util'].items()} }  "
      f"synth={exp['synth_s']:.0f} s {src_note}")
print(f"Ratio ours/dataset latency: {lat_ms / exp['latency_ms']:.4f}   "
      f"exact resource match: {all(util[k] == int(exp['util'][k]) for k in util)}")
PY

# 6. Keep reports and logs, drop the bulky project unless KEEP=1
if [ "${KEEP:-0}" != 1 ] && [ -d smoke ]; then
  mkdir -p reports
  cp smoke/solution1/solution1_data.json smoke/solution1/syn/report/*.rpt reports/ 2>/dev/null
  rm -rf smoke
  echo "Kept reports in $WORK/reports (KEEP=1 keeps the whole project)"
fi
