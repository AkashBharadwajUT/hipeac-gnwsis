"""transfer_summary.py [transfer-dir] [results-dir]
Summarise the transfer study (paper §5.5 extended to the KV260):
ZCU104 dataset Pareto designs (Vitis 2021.1) re-synthesised on the KV260 part with Vitis 2022.2,
compared with the KV260 GA's own designs.

Reads  <transfer-dir>/rows.txt (all designs) and <app>_<row>.txt (smoke_hls.sh outputs)
       <results-dir>/<app>_xck26_10_s42_g24.csv (ga_export.py outputs)
Writes <transfer-dir>/transfer_designs.csv  (one row per transferred design)
       <transfer-dir>/transfer_summary.csv  (one row per kernel) and prints the summary table.
"""
import ast, csv, glob, json, os, re, sys

tdir = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/gnosis/transfer")
rdir = sys.argv[2] if len(sys.argv) > 2 else os.path.expanduser("~/gnosis/results")
RES = ("BRAM", "DSP", "FF", "LUT")
scratch = os.environ.get("SCRATCH", f"/local/scratch/{os.environ.get('USER', '')}")
gnwsis = os.environ.get("GNWSIS", os.path.expanduser("~/gnosis/GNWSIS"))
tops = {}
for kd in glob.glob(os.path.join(gnwsis, "data", "ApplicationDataset", "*", "kernel_info.txt")):
    tops[os.path.basename(os.path.dirname(kd))] = open(kd).readline().strip()

rows = []
# Master list: rows.txt ("<app> <row>"); output file = <app>_<row without .cpp>.txt (".c" rows keep their suffix)
master = [l.split() for l in open(os.path.join(tdir, "rows.txt")) if l.strip()]
for app, row in master:
    stem = row[:-4] if row.endswith(".cpp") else row
    path = os.path.join(tdir, f"{app}_{stem}.txt")
    r = {"app": app, "row": row, "status": "not started"}
    if not os.path.exists(path):
        rows.append(r); continue
    text = open(path, errors="replace").read()
    r["status"] = "running/unknown"
    m = re.search(r"^Dataset: latency=([\d.]+) ms\s+util%=(\{[^}]*\})", text, re.M)
    if m:
        r["ds_latency_ms"] = float(m.group(1))
        du = ast.literal_eval(m.group(2))
        for k in RES:
            r[f"ds_{k}"] = du.get(k)
    m = re.search(r"^Ours:\s+latency=([\d.]+) ms \((\d+) cycles x ([\d.]+) ns.*util%=(\{[^}]*\})", text, re.M)
    if m:
        r["kv_latency_ms"] = float(m.group(1))
        r["kv_cycles"] = int(m.group(2))
        period = float(m.group(3))
        # worst-case latency from the kept JSON (ClockInfo.Latency = best case for variable-trip loops)
        jp = os.path.join(scratch, f"smoke_{app}_{stem[:-2] if stem.endswith('.c') else stem}_2022.2_xck26_10",
                          "reports", "solution1_data.json")
        if os.path.exists(jp):
            dj = json.load(open(jp))
            top = tops.get(app)
            lat = dj["ModuleInfo"]["Metrics"].get(top, {}).get("Latency", {}) if top else {}
            b, wv = lat.get("LatencyBest"), lat.get("LatencyWorst")
            if b and wv:
                r["kv_best_cycles"], r["kv_worst_cycles"] = int(b), int(wv)
                r["variable_latency"] = int(b) != int(wv)
                r["kv_cycles"] = int(wv)
                r["kv_latency_ms"] = int(wv) * period / 1e6
        else:
            r["variable_latency"] = "unknown (no JSON)"
        ou = ast.literal_eval(m.group(4))
        for k in RES:
            r[f"kv_{k}"] = ou.get(k)
        r["fits_kv260"] = all(ou.get(k, 0) <= 100 for k in RES)
        r["status"] = "done"
        if "ds_latency_ms" in r and r["ds_latency_ms"] > 0:
            r["latency_ratio_kv_over_ds"] = round(r["kv_latency_ms"] / r["ds_latency_ms"], 4)
    elif "Timed out" in text or "No solution1_data.json" in text:
        r["status"] = "not synthesised within 1 h / killed"
        r["fits_kv260"] = False
    rows.append(r)

cols = ["app", "row", "status", "ds_latency_ms"] + [f"ds_{k}" for k in RES] + \
       ["kv_latency_ms", "kv_cycles", "kv_best_cycles", "kv_worst_cycles", "variable_latency"] + [f"kv_{k}" for k in RES] + ["fits_kv260", "latency_ratio_kv_over_ds"]
with open(os.path.join(tdir, "transfer_designs.csv"), "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader(); w.writerows(rows)

def ga_best(app):
    p = os.path.join(rdir, f"{app}_xck26_10_s42_g24.csv")
    if not os.path.exists(p):
        return None, None
    g = [x for x in csv.DictReader(open(p)) if x["feasible"] == "True"]
    if not g:
        return None, 0
    return min(float(x["latency_ms"]) for x in g), len(g)

summary = []
for app in sorted({r["app"] for r in rows}):
    rs = [r for r in rows if r["app"] == app]
    done = [r for r in rs if r["status"] == "done"]
    failed = [r for r in rs if r["status"].startswith("not synth")]
    fits = [r for r in done if r["fits_kv260"]]
    best_tr = min((r["kv_latency_ms"] for r in fits), default=None)
    best_ds = min((r["ds_latency_ms"] for r in rs if "ds_latency_ms" in r), default=None)
    gb, gn = ga_best(app)
    ratios = sorted(r["latency_ratio_kv_over_ds"] for r in done if "latency_ratio_kv_over_ds" in r)
    med = ratios[len(ratios) // 2] if ratios else None
    exact = sum(1 for r in done if abs(r.get("latency_ratio_kv_over_ds", 0) - 1) < 0.005)
    summary.append({
        "app": app, "zcu104_pareto_designs": len(rs), "resynthesised": len(done),
        "not_synth_1h": len(failed), "still_running_or_unknown": len(rs) - len(done) - len(failed),
        "not_started": sum(1 for r in rs if r["status"] == "not started"),
        "fit_kv260": len(fits), "dont_fit_kv260": len(done) - len(fits) + len(failed),
        "best_ds_latency_ms_zcu104_2021_1": best_ds,
        "best_transferred_latency_ms_kv260_2022_2": best_tr,
        "kv260_ga_best_latency_ms": gb, "kv260_ga_feasible_designs": gn,
        "ga_speedup_over_best_transferred": round(best_tr / gb, 3) if best_tr and gb else None,
        "median_latency_ratio_kv_over_ds": med,
        "min_ratio": ratios[0] if ratios else None, "max_ratio": ratios[-1] if ratios else None,
        "same_latency_within_0.5pct": exact,
        "variable_latency_designs": sum(1 for r in done if r.get("variable_latency") is True),
    })
with open(os.path.join(tdir, "transfer_summary.csv"), "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(summary[0].keys())); w.writeheader(); w.writerows(summary)

fmt = lambda v: "-" if v is None else (f"{v:.3f}" if isinstance(v, float) else str(v))
hdr = ["kernel", "pareto", "done", "fit", "no-fit", "pending", "best DS (ms)", "best transf. (ms)",
       "GA best (ms)*", "GA gain*", "ratio med [min-max]", "same lat.", "var. lat."]
print(" | ".join(hdr))
for s in summary:
    print(" | ".join([s["app"], str(s["zcu104_pareto_designs"]), str(s["resynthesised"]), str(s["fit_kv260"]),
                      str(s["dont_fit_kv260"]), str(s["still_running_or_unknown"]),
                      fmt(s["best_ds_latency_ms_zcu104_2021_1"]), fmt(s["best_transferred_latency_ms_kv260_2022_2"]),
                      fmt(s["kv260_ga_best_latency_ms"]), fmt(s["ga_speedup_over_best_transferred"]),
                      f'{fmt(s["median_latency_ratio_kv_over_ds"])} [{fmt(s["min_ratio"])}-{fmt(s["max_ratio"])}]',
                      str(s["same_latency_within_0.5pct"]), str(s["variable_latency_designs"])]))
print("\nLatencies are worst case (LatencyWorst). * GA values are the GA's own objective = ClockInfo.Latency, which is the")
print("  BEST case for designs with variable-trip loops; re-score GA Pareto designs with ga_rescore.sh before trusting them.")
print(f"Wrote {os.path.join(tdir, 'transfer_designs.csv')} and transfer_summary.csv")
