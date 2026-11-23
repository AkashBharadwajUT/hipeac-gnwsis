"""add_run_info.py [gnosis-dir]  (default ~/gnosis)
Add synthesis time, peak RAM and the run constants (tool version, devices, clock) to the lane C result CSVs,
so each row is self-describing. Reads the per-design smoke_hls.sh logs; never modifies the input CSVs.

  transfer/transfer_designs.csv      + transfer/<app>_<row>.txt     -> transfer/transfer_designs_full.csv
  ga_rescore/<app>_rescored.csv      + ga_rescore/<app>/<design>.txt -> ga_rescore/<app>_rescored_full.csv
  results/<app>_xck26_10_s42_g24*.csv                                -> results/<same name>_full.csv

Added columns: tool_version, target_part, target_clock_ns, synth_wall_s, peak_ram_mb (both from /usr/bin/time in
the log; blank if the run was killed), exit_code (0 = normal; 143 = killed by the 63-min reaper, 124 = 1 h timeout:
a killed run that still has kv_ latency finished csynth after more than ~1 h but was stopped during IP export), and for the transfer study source_part, source_clock_ns,
source_tool_version, ds_synth_s (dataset synthesis time). GA CSVs get only the tool version and target columns
(their synth_time_s is GenHLSOptimizer's own measurement).
"""
import csv, glob, os, re, sys

base = os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else "~/gnosis")
TOOL, PART, CLK = "Vitis HLS 2022.2", "xck26-sfvc784-2LV-c", "10"
SRC_PART, SRC_TOOL = "xczu7ev-ffvc1156-2-e", "Vitis HLS 2021.1 (dataset)"


def wall_seconds(s):
    """'0:57.64' (m:ss), '1:02:03' (h:mm:ss) from /usr/bin/time, or '57s' from the Python fallback."""
    s = s.strip().rstrip("s")
    parts = [float(p) for p in s.split(":")]
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return round(sec, 1)


def log_info(path):
    info = {"synth_wall_s": "", "peak_ram_mb": "", "ds_synth_s": "", "exit_code": ""}
    if not os.path.exists(path):
        return info
    text = open(path, errors="replace").read()
    m = re.findall(r"Exit code: (\S*)[ \t]+Peak RAM: (\d+) MB[ \t]+Wall time: ?(\S*)", text)
    if m:  # last run if a file holds two (the transfer queue was restarted once)
        rc, ram, wall = m[-1]
        info["exit_code"] = rc
        if wall:
            try:
                info["synth_wall_s"] = wall_seconds(wall)
            except ValueError:
                pass
        if int(ram) > 0:
            info["peak_ram_mb"] = int(ram)
    m = re.findall(r"synth=(\d+) s \(Vitis 2021\.1\)", text)
    if m:
        info["ds_synth_s"] = int(m[-1])
    return info


def write(path, rows, cols):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def report(path, rows):
    t = [r["synth_wall_s"] for r in rows if r.get("synth_wall_s") != ""]
    print("%-70s %5d rows, %5d with synthesis time" % (os.path.relpath(path, base), len(rows), len(t)))


# 1. Transfer study
p = os.path.join(base, "transfer", "transfer_designs.csv")
if os.path.exists(p):
    rows = list(csv.DictReader(open(p)))
    cols = list(rows[0].keys())
    for r in rows:
        row = r["row"]
        stem = row[:-4] if row.endswith(".cpp") else row
        r.update(log_info(os.path.join(base, "transfer", "%s_%s.txt" % (r["app"], stem))))
        r.update({"source_part": SRC_PART, "source_clock_ns": CLK, "source_tool_version": SRC_TOOL,
                  "target_part": PART, "target_clock_ns": CLK, "tool_version": TOOL})
    cols = cols[:2] + ["source_part", "source_clock_ns", "source_tool_version", "target_part", "target_clock_ns",
                       "tool_version"] + cols[2:] + ["ds_synth_s", "synth_wall_s", "peak_ram_mb", "exit_code"]
    out = p.replace(".csv", "_full.csv")
    write(out, rows, cols)
    report(out, rows)

# 2. GA re-scoring
for p in sorted(glob.glob(os.path.join(base, "ga_rescore", "*_rescored.csv"))):
    app = os.path.basename(p)[:-len("_rescored.csv")]
    rows = list(csv.DictReader(open(p)))
    if not rows:
        continue
    cols = list(rows[0].keys())
    for r in rows:
        i = log_info(os.path.join(base, "ga_rescore", app, r["design"] + ".txt"))
        r.update({"app": app, "target_part": PART, "target_clock_ns": CLK, "tool_version": TOOL,
                  "synth_wall_s": i["synth_wall_s"], "peak_ram_mb": i["peak_ram_mb"], "exit_code": i["exit_code"]})
    cols = ["app"] + cols[:1] + ["target_part", "target_clock_ns", "tool_version"] + cols[1:] + \
           ["synth_wall_s", "peak_ram_mb", "exit_code"]
    out = p.replace(".csv", "_full.csv")
    write(out, rows, cols)
    report(out, rows)

# 3. GA CSVs (final and partial): add tool version only
for p in sorted(glob.glob(os.path.join(base, "results", "*_xck26_10_s*_g*.csv"))):
    if p.endswith("_full.csv"):
        continue
    rows = list(csv.DictReader(open(p)))
    if not rows:
        continue
    cols = list(rows[0].keys())
    for r in rows:
        r["tool_version"] = TOOL
    cols = cols[:3] + ["tool_version"] + cols[3:]
    out = p.replace(".csv", "_full.csv")
    write(out, rows, cols)
    print("%-70s %5d rows" % (os.path.relpath(out, base), len(rows)))
