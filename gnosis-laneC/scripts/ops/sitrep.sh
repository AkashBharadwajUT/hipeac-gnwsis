#!/usr/bin/env bash
# sitrep.sh - one-screen status of lane C on caes-lab1
echo "=== $(date '+%a %d %b %H:%M')   load: $(cut -d' ' -f1-3 /proc/loadavg)"
free -g | awk '/Mem:/{printf "memory: %d GB used, %d GB available%s\n", $3, $7, ($7<60?"   <-- LOW: pkill -u $USER -f \"xargs -P 4 -L 1\"":"")}'
echo "disk: /local $(df -h /local | awk 'NR==2{print $4" free ("$5" used)"}'), home $(du -sh ~ 2>/dev/null | cut -f1) of 10G"
echo "guards (want 2): $(pgrep -u $USER -f 'while true' | wc -l)   [memguard + reaper]"
echo "OOM kills since 11:00: $(dmesg -T 2>/dev/null | grep -c 'Out of memory' ) total in dmesg (last: $(dmesg -T 2>/dev/null | grep 'Out of memory' | tail -1 | cut -c2-25))"
echo
echo "--- GA runs (designs in database | syntheses running now | process alive)"
declare -A RUN
for p in $(pgrep -u $USER -f "unwrapped.*vitis_hls"); do
  d=$(readlink /proc/$p/cwd 2>/dev/null | sed -n 's#.*/ga/\([^/]*\).*#\1#p'); [ -n "$d" ] && RUN[$d]=$(( ${RUN[$d]:-0} + 1 ))
done
for d in /local/scratch/$USER/ga/*_g24; do
  n=$(basename $d); app=${n%_xck26_10_s42_g24}
  c=$(~/gnosis/venv36/bin/python -c "from sqlitedict import SqliteDict; import glob; print(len(SqliteDict(glob.glob('$d/Databases/*.sqlite')[0])))" 2>/dev/null)
  alive=$(pgrep -u $USER -f "ga_kv260.sh .* $app( |$)" >/dev/null && echo RUNNING || echo done)
  printf "  %-34s %5s designs  %3s running  %s\n" "$app" "$c" "${RUN[$n]:-0}" "$alive"
done
echo "  other vitis_hls (not in a GA folder): $(( $(pgrep -u $USER -fc 'unwrapped.*vitis_hls') - $(IFS=+; echo $(( ${RUN[*]:-0} )) ) ))"
echo
echo "--- re-scoring"
for f in ~/gnosis/ga_rescore*.log; do echo "  $(basename $f): $(grep -h 're-scored' $f | tail -1)"; done
echo "--- other queues: $(pgrep -u $USER -af 'xargs -P|board_build|ga_rescore.sh' | grep -v -e pgrep -e 'while true' | wc -l) running"
echo "--- re-scoring progress (designs with a result / designs to do)"
for d in ~/gnosis/ga_rescore/*/; do
  [ -f "$d/rows.txt" ] || continue
  printf "  %-34s %4s / %s\n" "$(basename $d)" "$(grep -l '^Ours' $d/ga_*.txt 2>/dev/null | wc -l)" "$(wc -l < $d/rows.txt)"
done
