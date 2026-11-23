"""ga_export.py <ga-work-dir> <app> <part> <clock-ns>
Decode a GenHLSOptimizer SQLite database into a CSV: one row per synthesised design,
the directive applied at every action point (L1..Ln), latency in ms, utilisation %,
synthesis time, feasibility, and whether the design is on the latency-resource Pareto front."""
import csv, glob, os, re, sys
from sqlitedict import SqliteDict
work, app, part, clk = sys.argv[1:5]
sys.path.insert(0, work)
from modules.preprocessor import Preprocessor

_, _, _, top, opts = Preprocessor(os.path.join(work, "kernel_info.txt")).preprocess()
labels = [l.split(",")[0] for l in open(os.path.join(work, "kernel_info.txt")).read().split("\n")[1:] if l.strip()]
dbs = glob.glob(os.path.join(work, "Databases", "*.sqlite"))
if not dbs:
    sys.exit("No database found")
rows = []
for key, v in SqliteDict(dbs[0]).items():
    idx = [int(t) for t in re.findall(r"-?\d+", key)]
    lat = float(v["latency"])
    util = {k: float(v[f"util_{k.lower()}"]) for k in ("BRAM", "DSP", "FF", "LUT", "URAM")}
    feasible = lat > 0 and all(u <= 100 for u in util.values())
    r = {"app": app, "part": part, "clock_ns": clk, "index_vector": " ".join(map(str, idx)),
         "latency_ms": lat, **{f"{k}_pct": u for k, u in util.items()},
         "synth_time_s": v.get("synth_time", ""), "feasible": feasible}
    for lab, i, o in zip(labels, idx, opts):
        r[lab] = o[i].replace("#pragma HLS ", "")
    rows.append(r)
# Pareto front over (latency, BRAM, DSP, FF, LUT), feasible designs only
obj = lambda r: (r["latency_ms"], r["BRAM_pct"], r["DSP_pct"], r["FF_pct"], r["LUT_pct"])
feas = [r for r in rows if r["feasible"]]
for r in rows:
    r["pareto"] = r["feasible"] and not any(
        all(a <= b for a, b in zip(obj(o), obj(r))) and obj(o) != obj(r) for o in feas)
name = os.path.basename(os.path.normpath(work))
out = os.path.join(work, name + ".csv")
cols = list(rows[0].keys()) if rows else []
for r in rows:
    for k in r:
        if k not in cols:
            cols.append(k)
with open(out, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
print(f"{len(rows)} designs, {len(feas)} feasible, {sum(r['pareto'] for r in rows)} on the Pareto front -> {out}")
