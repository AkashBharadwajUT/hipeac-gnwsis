"""ga_rescore_summary.py <app> <ga-csv> <rescore-dir>: compare the GA's own latency (best case) with the worst case."""
import csv, glob, os, re, sys
app, ga_csv, out = sys.argv[1:4]
ga = {"ga_" + r["index_vector"].replace(" ", "_"): r for r in csv.DictReader(open(ga_csv)) if r["feasible"] == "True"}
rows = []
for key, g in ga.items():
    p = os.path.join(out, key + ".txt")
    t = open(p, errors="replace").read() if os.path.exists(p) else ""
    m = re.search(r"^Ours:\s+latency=([\d.]+) ms \((\d+) cycles.*util%=\{'BRAM': (\d+), 'DSP': (\d+), 'FF': (\d+), 'LUT': (\d+)\}(.*)$", t, re.M)
    r = {"design": key, "ga_latency_ms": float(g["latency_ms"]), "ga_pareto": g["pareto"]}
    if m:
        r.update({"worst_latency_ms": float(m.group(1)), "worst_cycles": int(m.group(2)),
                  "BRAM": int(m.group(3)), "DSP": int(m.group(4)), "FF": int(m.group(5)), "LUT": int(m.group(6)),
                  "variable_latency": "latency range" in m.group(7)})
        b = re.search(r"best (\d+), worst (\d+)", m.group(7))
        if b:
            r["best_cycles"] = int(b.group(1))
    rows.append(r)
done = [r for r in rows if "worst_latency_ms" in r]
cols = ["design", "ga_latency_ms", "ga_pareto", "worst_latency_ms", "worst_cycles", "best_cycles", "variable_latency",
        "BRAM", "DSP", "FF", "LUT"]
with open(os.path.join(os.path.dirname(out.rstrip("/")), f"{app}_rescored.csv"), "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader(); w.writerows(rows)
if not done:
    sys.exit(f"{app}: nothing re-scored yet")
ga_best = min(rows, key=lambda r: r["ga_latency_ms"])
true_best = min(done, key=lambda r: r["worst_latency_ms"])
var = sum(1 for r in done if r["variable_latency"])
print(f"{app}: {len(done)}/{len(rows)} re-scored, {var} with variable latency")
print(f"  GA's believed best : {ga_best['design']}  {ga_best['ga_latency_ms']:.5f} ms (best case)"
      + (f" -> worst case {ga_best['worst_latency_ms']:.5f} ms" if "worst_latency_ms" in ga_best else ""))
print(f"  true best (worst-case latency): {true_best['design']}  {true_best['worst_latency_ms']:.5f} ms")
