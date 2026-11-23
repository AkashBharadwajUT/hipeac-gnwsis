#!/usr/bin/env bash
# ga_kv260.sh - run GenHLSOptimizer (NSGA-II, the paper's GA) for one GNOSIS kernel on a new part.
#
# Usage:
#   bash ga_kv260.sh <GNWSIS-clone> <GenHLSOptimizer-clone> <app> [threads] [generations] [seed] [part] [clock-ns]
# Defaults: threads=5, generations=24 (paper), seed=42 (paper), part=xck26-sfvc784-2LV-c, clock=10 (100 MHz)
#
# Examples:
#   bash ga_kv260.sh ~/gnosis/GNWSIS ~/gnosis/GenHLSOptimizer rodinia-knn-1-tiling 10 1      # smoke test: 1 generation (40 designs)
#   bash ga_kv260.sh ~/gnosis/GNWSIS ~/gnosis/GenHLSOptimizer rodinia-knn-1-tiling           # full run
#
# Environment:
#   VER=2022.2            Vitis HLS module version
#   VENV=~/gnosis/venv36  Python 3.6 venv (created on first use: pymoo 0.5.0, sqlitedict 2.0.0, psutil 5.9.0)
#   SCRATCH=<dir>         scratch folder (default /local/scratch/$USER; on caes-lab2 use the Scratchpad Manager folder)
#   RESUME=1              continue a stopped run from its saved database (same seed and generations)
#   EXPORT=0              1 keeps GenHLSOptimizer's per-design ip_catalog export (slower, results unchanged)
#
# Each run gets its own folder in /local/scratch/$USER/ga/, because GenHLSOptimizer writes and deletes
# files in its working directory. Results: Databases/<name>.sqlite, ga.log, and <name>.csv (from ga_export.py).

set -o pipefail
GNWSIS=$(cd "${1:?GNWSIS clone}" && pwd) || exit 1
GHO=$(cd "${2:?GenHLSOptimizer clone}" && pwd) || exit 1
APP=${3:?app name from data/ApplicationDataset}
THREADS=${4:-5}; GENS=${5:-24}; SEED=${6:-42}
PART=${7:-xck26-sfvc784-2LV-c}; CLK=${8:-10}
VER=${VER:-2022.2}; VENV=${VENV:-$HOME/gnosis/venv36}; EXPORT=${EXPORT:-0}
HERE=$(cd "$(dirname "$0")" && pwd)

SRC_DIR="$GNWSIS/data/ApplicationDataset/$APP"
[ -f "$SRC_DIR/kernel_info.txt" ] || { echo "No kernel $APP in $SRC_DIR"; exit 1; }
TOP=$(head -n 1 "$SRC_DIR/kernel_info.txt" | tr -d '\r')
SRC=$(basename "$(grep -l "$TOP" "$SRC_DIR"/*.c "$SRC_DIR"/*.cpp 2>/dev/null | head -1)")
[ -n "$SRC" ] || { echo "No source file contains $TOP"; exit 1; }
EXT=".${SRC##*.}"

# 1. Tools
type module >/dev/null 2>&1 || source /etc/profile.d/modules.sh 2>/dev/null || source /usr/share/lmod/lmod/init/bash 2>/dev/null
module purge; module load "xilinx/vitis_hls/$VER" || { echo "module load xilinx/vitis_hls/$VER failed"; exit 1; }
command -v vitis_hls >/dev/null || { echo "vitis_hls not on PATH after module load"; exit 1; }

# 2. Python 3.6 environment (once)
if [ ! -x "$VENV/bin/python" ]; then
  echo "Creating $VENV ..."
  python3.6 -m venv "$VENV" || exit 1
  "$VENV/bin/pip" install -q --upgrade "pip<22" "setuptools<60" wheel || exit 1
  "$VENV/bin/pip" install -q -r "$GHO/requirements.txt" || exit 1
fi
"$VENV/bin/python" -c "import pymoo, sqlitedict, psutil; print('pymoo', pymoo.__version__)" || exit 1

# 3. Private copy of the optimizer, patched
NAME="${APP}_${PART%%-*}_${CLK}_s${SEED}_g${GENS}"
WORK="${SCRATCH:-/local/scratch/${USER:-$(whoami)}}/ga/$NAME"
mkdir -p "$(dirname "$WORK")" 2>/dev/null && [ -w "$(dirname "$WORK")" ] || {
  echo "Scratch folder $(dirname "$WORK") is not writable. Set SCRATCH=<your scratch dir> (on caes-lab2: the folder"
  echo "Scratchpad Manager gives you). Not falling back to your home folder: a GA run would fill its 10 GB quota."; exit 1; }
# RESUME=1 keeps the existing database: designs already synthesised are read back instead of re-run,
# so the GA replays (same seed) up to where it stopped and continues; THREADS may differ.
if [ "${RESUME:-0}" = 1 ] && [ -d "$WORK/Databases" ]; then
  KEEP_DB=$(mktemp -d); cp -a "$WORK/Databases" "$KEEP_DB/"
  echo "Resuming: $(ls "$KEEP_DB/Databases") kept"
fi
rm -rf "$WORK"; mkdir -p "$WORK"; cd "$WORK" || exit 1
[ -n "${KEEP_DB:-}" ] && mv "$KEEP_DB/Databases" . && rmdir "$KEEP_DB"
cp -r "$GHO"/GenHLSOptimizer.py "$GHO"/modules "$GHO"/OperatorConfigurations .
cp "$SRC_DIR"/*.c "$SRC_DIR"/*.cpp "$SRC_DIR"/*.h "$SRC_DIR"/*.hpp "$SRC_DIR"/kernel_info.txt . 2>/dev/null
P=modules/hlsDirectiveOptimizationProblem.py
# (a) the timeout loop polls with no sleep: every worker would keep a core at 100 %
python3 - "$P" <<'PY'
import sys; p = sys.argv[1]; s = open(p).read()
old = "        while (True):\n"
assert s.count(old) == 1, "patch (a) failed"
s = s.replace(old, old + "            time.sleep(1)\n")
open(p, "w").write(s)
PY
# (a2) failed or timed-out syntheses return early and leave their project folder behind; delete it first
python3 - "$P" <<'PY'
import re, sys; p = sys.argv[1]; s = open(p).read()
pat = re.compile(r"^(\s+)return \[0, 101, 101, 101, 101, 101\]", re.M)
clean = "os.system(f'rm -rf GENETIC_DSE_{my_i} ./kernel_{my_i}{self.SRC_EXTENSION} ./script_{my_i}.tcl ./vitis_hls_{my_i}.log')"
s, n = pat.subn(lambda m: f"{m.group(1)}{clean}\n{m.group(0)}", s)
assert n == 2, f"patch (a2) matched {n} returns"
open(p, "w").write(s)
PY
# (b) per-design ip_catalog export does not change csynth results; skip it unless EXPORT=1
[ "$EXPORT" = 1 ] || sed -i '/export_design -format ip_catalog/d' "$P"
# (c) seed from the environment (paper uses 42)
sed -i "s/seed=42/seed=int(os.environ.get('GA_SEED', '42'))/" GenHLSOptimizer.py
grep -q "time.sleep(1)" "$P" && grep -q "rm -rf GENETIC_DSE_{my_i}" "$P" && grep -q GA_SEED GenHLSOptimizer.py || { echo "Patching failed"; exit 1; }

echo "GA: $APP top=$TOP src=$SRC part=$PART clk=${CLK}ns gens=$GENS threads=$THREADS seed=$SEED vitis=$VER"
echo "Work dir: $WORK"
echo "$(date -Is) start host=$(hostname -s)" > run_info.txt

# 4. Run (reduced priority; shared server)
GA_SEED=$SEED nice -n 10 "$VENV/bin/python" GenHLSOptimizer.py \
  --INPUT_SOURCE_PATH "./$SRC" --INPUT_SOURCE_INFO_PATH ./kernel_info.txt --SRC_EXTENSION "$EXT" \
  --DB_NAME "$NAME" --DEVICE_ID "$PART" --CLK_PERIOD "$CLK" --THREADS "$THREADS" \
  --GENERATIONS "$GENS" --TIMEOUT 3600 > ga.log 2>&1
RC=$?
echo "$(date -Is) end rc=$RC" >> run_info.txt
tail -n 20 ga.log

# 5. Export the database to CSV with readable directives
"$VENV/bin/python" "$HERE/ga_export.py" "$WORK" "$APP" "$PART" "$CLK" && echo "CSV: $WORK/$NAME.csv"
rm -rf GENETIC_DSE_* kernel_*"$EXT" script_*.tcl vitis_hls_*.log 2>/dev/null
