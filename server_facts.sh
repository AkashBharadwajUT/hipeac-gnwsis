#!/usr/bin/env bash
# server_facts.sh - collect the facts WS0 needs from the CAES server.
#
# Usage:
#   bash server_facts.sh                   # quick facts (about 1 min)
#   bash server_facts.sh --license-check   # also synthesise a tiny design per part (adds a few min per Vivado version)
#   bash server_facts.sh --board           # also look for the KV260 over JTAG (hw_server)
#   XILINX_ROOTS="/eda/xilinx /opt/Xilinx" bash server_facts.sh   # extra install roots to search
#
# Writes everything to server_facts_<host>_<date>.txt in the current directory.
# Read-only: it creates files only under a temporary directory, which it removes at the end.

set -o pipefail

LICENSE_CHECK=0
BOARD_CHECK=0
for arg in "$@"; do
  case "$arg" in
    --license-check) LICENSE_CHECK=1 ;;
    --board) BOARD_CHECK=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

PARTS=("xczu7ev-ffvc1156-2-e" "xck26-sfvc784-2LV-c" "xcu200-fsgd2104-2-e")
HOST=$(hostname -s 2>/dev/null || hostname)
OUT="server_facts_${HOST}_$(date +%Y%m%d_%H%M).txt"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

section() { printf '\n==================== %s ====================\n' "$1"; }
run() {  # run a command, show it, never abort the script
  printf '$ %s\n' "$*"
  bash -c "$*" 2>&1 | sed 's/^/  /'
}
have() { command -v "$1" >/dev/null 2>&1; }

{
section "1. Host and OS"
run "date -Is"
run "hostname -f"
run "uname -srm"
run "grep -E '^(PRETTY_NAME|VERSION_ID)=' /etc/os-release"
run "whoami; id"

section "2. CPU"
run "nproc"
run "lscpu | grep -E 'Model name|^CPU\\(s\\)|Thread|Core|Socket|NUMA node\\(s\\)|MHz'"

section "3. Memory"
run "free -g"
run "grep -E 'MemTotal|MemAvailable|SwapTotal' /proc/meminfo"
MEM_GB=$(awk '/MemTotal/ {printf "%d", $2/1024/1024}' /proc/meminfo)
JOBS=$(( (MEM_GB - 16) / 8 )); [ "$JOBS" -lt 1 ] && JOBS=1
echo "  -> Suggested parallel HLS jobs at 8 GB each, leaving 16 GB free: $JOBS (refine after measuring peak RAM in the smoke test)"

section "4. Load and other users (is the server shared?)"
run "uptime"
run "who | awk '{print \$1}' | sort | uniq -c"
run "ps -eo user,pcpu,pmem,comm --sort=-pcpu | head -15"

section "5. Disk and quotas"
run "df -h \"\$HOME\" /tmp ${SCRATCH:-} 2>/dev/null"
for d in /scratch /data /local /mnt/scratch; do
  [ -d "$d" ] && run "df -h $d"
done
have quota && run "quota -s"
run "ulimit -a"

section "6. Job scheduler"
for c in sbatch squeue sinfo qsub condor_submit; do
  have "$c" && echo "  found: $c -> $(command -v $c)"
done
have sinfo && run "sinfo -o '%P %a %l %D %c %m'"
have squeue && run "squeue -u \"\$USER\""
have sacctmgr && run "sacctmgr show assoc user=\$USER format=account,partition,maxjobs,maxwall,grptres -P"
have sinfo || have qsub || echo "  no scheduler found: jobs run directly on the host"

section "7. Containers and Python"
for c in docker podman apptainer singularity conda mamba micromamba; do
  if have "$c"; then
    run "$c --version"
  else
    echo "  $c: not found"
  fi
done
have docker && run "docker info --format '{{.ServerVersion}} rootless={{.SecurityOptions}}' 2>&1 | head -2"
for p in python3 python3.6 python3.8 python3.10; do
  have "$p" && run "$p --version"
done

section "8. AMD/Xilinx tools"
ROOTS=(${XILINX_ROOTS:-} /tools/Xilinx /opt/Xilinx /opt/xilinx /eda/xilinx /usr/local/Xilinx "$HOME/Xilinx")
env | grep -iE '^(XILINX|LM_LICENSE|XILINXD)' | sed 's/^/  env: /'
for r in "${ROOTS[@]}"; do
  [ -d "$r" ] || continue
  echo "  root: $r"
  ls -1 "$r" 2>/dev/null | sed 's/^/    /'
done
# Every vivado / vitis_hls binary we can find, one per version
find "${ROOTS[@]}" -maxdepth 4 -type f \( -path '*/bin/vivado' -o -path '*/bin/vitis_hls' \) 2>/dev/null | sort > "$TMP/bins.txt"
for b in $(command -v vivado vitis_hls 2>/dev/null); do echo "$b" >> "$TMP/bins.txt"; done
sort -u "$TMP/bins.txt" -o "$TMP/bins.txt"
if [ ! -s "$TMP/bins.txt" ]; then
  echo "  no vivado or vitis_hls found; set XILINX_ROOTS or ask the admins where the tools live"
fi
while read -r b; do
  echo "  binary: $b"
  "$b" -version 2>&1 | head -2 | sed 's/^/    /'
done < "$TMP/bins.txt"

section "9. Device support per Vivado version (parts we need)"
cat > "$TMP/parts.tcl" <<EOF
foreach p {${PARTS[*]}} {
  set found [get_parts -quiet \$p]
  if {[llength \$found] > 0} { puts "PART_OK \$p" } else { puts "PART_MISSING \$p" }
}
exit
EOF
grep '/bin/vivado$' "$TMP/bins.txt" | while read -r v; do
  echo "  $v"
  (cd "$TMP" && timeout 600 "$v" -mode batch -nojournal -nolog -source "$TMP/parts.tcl" 2>&1 \
    | grep -E 'PART_(OK|MISSING)' | sed 's/^/    /')
done
echo "  (Vitis HLS uses the same device files as the Vivado of the same version.)"

section "10. Licences"
for f in "$HOME"/.Xilinx/*.lic; do [ -f "$f" ] && echo "  licence file: $f"; done
LMUTIL=$(find "${ROOTS[@]}" -maxdepth 6 -type f -name lmutil 2>/dev/null | head -1)
if [ -n "$LMUTIL" ] && [ -n "${XILINXD_LICENSE_FILE:-${LM_LICENSE_FILE:-}}" ]; then
  run "\"$LMUTIL\" lmstat -a -c \"${XILINXD_LICENSE_FILE:-$LM_LICENSE_FILE}\" | head -60"
else
  echo "  no lmutil or licence server variable; licence check needs --license-check"
fi
if [ "$LICENSE_CHECK" = 1 ]; then
  echo "module t(input a, b, output y); assign y = a & b; endmodule" > "$TMP/t.v"
  grep '/bin/vivado$' "$TMP/bins.txt" | while read -r v; do
    for p in "${PARTS[@]}"; do
      cat > "$TMP/lic.tcl" <<EOF
read_verilog $TMP/t.v
if {[catch {synth_design -top t -part $p -mode out_of_context} msg]} { puts "LIC_FAIL $p \$msg" } else { puts "LIC_OK $p" }
exit
EOF
      echo "  $v  $p"
      (cd "$TMP" && timeout 900 "$v" -mode batch -nojournal -nolog -source "$TMP/lic.tcl" 2>&1 \
        | grep -E 'LIC_(OK|FAIL)|license|License' | head -5 | sed 's/^/    /')
    done
  done
else
  echo "  skipped tiny synthesis per part (run with --license-check)"
fi

section "11. KV260 access"
have lsusb && run "lsusb | grep -iE 'xilinx|future|digilent|ftdi' || echo 'no JTAG cable on this host'"
run "ls /dev/ttyUSB* 2>/dev/null || echo 'no /dev/ttyUSB* serial ports'"
if [ "$BOARD_CHECK" = 1 ]; then
  cat > "$TMP/hw.tcl" <<'EOF'
open_hw_manager
if {[catch {connect_hw_server -allow_non_jtag} msg]} { puts "HW_FAIL $msg" } else {
  foreach t [get_hw_targets -quiet] { puts "HW_TARGET $t" }
  puts "HW_DONE"
}
exit
EOF
  v=$(grep '/bin/vivado$' "$TMP/bins.txt" | tail -1)
  [ -n "$v" ] && (cd "$TMP" && timeout 300 "$v" -mode batch -nojournal -nolog -source "$TMP/hw.tcl" 2>&1 \
    | grep -E 'HW_' | sed 's/^/  /')
else
  echo "  skipped JTAG scan (run with --board); for a networked KV260 ask for its hostname and ssh/PYNQ access"
fi

section "Done"
echo "  report: $OUT"
} 2>&1 | tee "$OUT"
