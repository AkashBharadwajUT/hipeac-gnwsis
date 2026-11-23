"""pnr_select.py [gnosis-dir]  (default ~/gnosis)
Pick the place-and-route designs: for each of the six transfer kernels, the KV260 GA Pareto front over
(worst-case latency, BRAM, DSP, FF, LUT), feasible designs only; then fastest, smallest (lowest BRAM+DSP+FF+LUT %),
and the 25th / 50th / 75th latency percentile of the front (duplicates removed).
Worst-case latency comes from ga_rescore/<app>_rescored.csv; KNN (T) has fixed latency (no re-scoring needed).
Writes <gnosis>/pnr/pnr_batch1.txt (fastest + smallest) and pnr_batch2.txt (percentiles): "<app> ga_<index> <tag>".
"""
import csv, os, sys

base = os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else "~/gnosis")
APPS = ["machsuite-md-knn", "machsuite-spmv-ellpack", "machsuite-stencil2d", "machsuite-stencil3d",
        "rodinia-knn-0-baseline", "rodinia-knn-1-tiling"]
RES = ("BRAM", "DSP", "FF", "LUT")
out_dir = os.path.join(base, "pnr")
os.makedirs(out_dir, exist_ok=True)
b1, b2 = [], []

for app in APPS:
    ga = [r for r in csv.DictReader(open(os.path.join(base, "results", app + "_xck26_10_s42_g24.csv")))
          if r["feasible"] == "True"]
    worst = {}
    p = os.path.join(base, "ga_rescore", app + "_rescored.csv")
    if os.path.exists(p):
        for r in csv.DictReader(open(p)):
            if r.get("worst_latency_ms"):
                worst[r["design"]] = float(r["worst_latency_ms"])
    pts = []
    for r in ga:
        d = "ga_" + r["index_vector"].replace(" ", "_")
        lat = worst.get(d, float(r["latency_ms"]) if not os.path.exists(p) else None)
        if lat is None:
            continue  # variable-latency kernel but no worst case (e.g. undef): skip
        pts.append((d, lat, tuple(float(r[k + "_pct"]) for k in RES)))
    obj = lambda t: (t[1],) + t[2]
    front = [t for t in pts if not any(all(a <= b for a, b in zip(obj(q), obj(t))) and obj(q) != obj(t) for q in pts)]
    # one representative per distinct objective vector (integer-% ties)
    seen, uniq = set(), []
    for t in sorted(front, key=obj):
        if obj(t) not in seen:
            seen.add(obj(t)); uniq.append(t)
    front = uniq
    n = len(front)
    picks = [("fastest", front[0]), ("smallest", min(front, key=lambda t: (sum(t[2]), t[1])))]
    for q in (25, 50, 75):
        picks.append(("p%d" % q, front[round(q / 100 * (n - 1))]))
    chosen = {}
    for tag, t in picks:
        if t[0] not in chosen:
            chosen[t[0]] = tag
    for d, tag in chosen.items():
        (b1 if tag in ("fastest", "smallest") else b2).append("%s %s %s" % (app, d, tag))
    print("%-24s front %3d -> %s" % (app, n, ", ".join("%s=%s" % (tag, d) for d, tag in chosen.items())))

for name, rows in (("pnr_batch1.txt", b1), ("pnr_batch2.txt", b2)):
    open(os.path.join(out_dir, name), "w").write("".join(l + "\n" for l in rows))
    print(name, len(rows), "designs")
