"""pnr_collect.py [gnosis-dir]  (default ~/gnosis)
Collect ~/gnosis/pnr/<app>_<row>_<clk>.txt into pnr/pnr_results.csv: HLS worst-case cycles and util %, post-synthesis and
post-route resources, CP required/achieved, WNS, achieved Fmax, timing met, and effective latency
L_eff = cycles x max(target period, achieved period). Prints a short per-kernel summary.
"""
import csv, glob, os, re, sys

base = os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else "~/gnosis")
rows = []
for p in sorted(glob.glob(os.path.join(base, "pnr", "*.txt"))):
    if os.path.basename(p).startswith("pnr_"):
        continue
    t = open(p, errors="replace").read()
    m = re.search(r"^PNR (\S+) (\S+) (\S+) clk=([\d.]+)ns", t, re.M)
    if not m:
        continue
    r = {"app": m.group(1), "design": m.group(2), "tag": m.group(3), "clk_ns": float(m.group(4))}
    m = re.search(r"^Ours:\s+latency=([\d.]+) ms \((\d+) cycles.*util%=(\{[^}]*\})", t, re.M)
    if m:
        r["hls_latency_ms"] = float(m.group(1)); r["cycles"] = int(m.group(2))
        for k, v in eval(m.group(3)).items():
            r["hls_%s_pct" % k] = v
    # the export log lists resources twice: post-synthesis first, post-implementation last
    for k in ("SLICE", "LUT", "FF", "DSP", "BRAM", "URAM", "SRL"):
        v = re.findall(r"^\s*%s\s*:\s*(\d+)" % k, t, re.M)
        if v:
            r["impl_" + k] = int(v[-1])
            if len(v) > 1:
                r["synth_" + k] = int(v[0])
    m = re.search(r"CP required\s*:\s*([\d.]+)", t); req = float(m.group(1)) if m else None
    m = re.search(r"CP achieved post-implementation\s*:\s*([\d.]+)", t); ach = float(m.group(1)) if m else None
    if req and ach:
        r.update({"cp_required_ns": req, "cp_achieved_ns": ach, "wns_ns": round(req - ach, 3),
                  "fmax_mhz": round(1000 / ach, 1), "timing_met": ach <= req})
        if "cycles" in r:
            r["eff_latency_ms"] = round(r["cycles"] * max(req, ach) / 1e6, 5)
    m = re.search(r"IMPL_EXIT (\d+)\s+IMPL_WALL_S (\d+)", t)
    if m:
        r["impl_exit"] = int(m.group(1)); r["impl_wall_s"] = int(m.group(2))
    r["status"] = "ok" if "cp_achieved_ns" in r else ("no_hls" if "PNR_FAIL" in t else ("aborted" if "PNR_ABORTED" in t else
                  ("running" if "PNR_DONE" not in t else "impl_failed")))
    rows.append(r)

cols = ["app", "design", "tag", "clk_ns", "status", "cycles", "hls_latency_ms", "hls_BRAM_pct", "hls_DSP_pct",
        "hls_FF_pct", "hls_LUT_pct", "impl_LUT", "impl_FF", "impl_DSP", "impl_BRAM", "impl_URAM", "impl_SLICE",
        "impl_SRL", "synth_LUT", "synth_FF", "synth_DSP", "synth_BRAM", "cp_required_ns", "cp_achieved_ns", "wns_ns", "fmax_mhz", "timing_met", "eff_latency_ms",
        "impl_exit", "impl_wall_s"]
out = os.path.join(base, "pnr", "pnr_results.csv")
with open(out, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader(); w.writerows(rows)
for r in rows:
    print("%-24s %-9s %5s ns  %-11s cycles %-10s  achieved %-6s ns  Fmax %-6s  met %-5s  L_eff %s ms" % (
        r["app"][:24], r["tag"], r["clk_ns"], r["status"], r.get("cycles", "-"), r.get("cp_achieved_ns", "-"),
        r.get("fmax_mhz", "-"), r.get("timing_met", "-"), r.get("eff_latency_ms", "-")))
print("->", out, len(rows), "runs")
