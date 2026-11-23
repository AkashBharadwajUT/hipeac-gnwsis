"""make_figures.py [gnosis-dir]  (default ~/gnosis)
Report figures and tables for lane C from the result files. Safe to rerun; missing inputs are skipped.
Outputs go to <gnosis-dir>/figures/: PNG + PDF figures and CSV tables.

Inputs used (all optional):
  results/<app>_xck26_10_s42_g24.csv        KV260 GA designs (ga_export.py)
  ga_rescore/<app>_rescored.csv             worst-case re-scoring of GA designs (ga_rescore.sh)
  transfer/transfer_designs.csv             transfer study (transfer_summary.py)
  results/board/board_results*.csv          board measurements (knn_board.py; newest file used)
"""
import csv, os, sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

base = os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else "~/gnosis")
out = os.path.join(base, "figures"); os.makedirs(out, exist_ok=True)
plt.rcParams.update({"font.size": 9, "figure.dpi": 150, "savefig.bbox": "tight"})
SHORT = {"machsuite-md-knn": "MD KNN", "machsuite-spmv-ellpack": "SPMV", "machsuite-stencil2d": "Stencil2D",
         "machsuite-stencil3d": "Stencil3D", "rodinia-knn-0-baseline": "KNN (B)", "rodinia-knn-1-tiling": "KNN (T)",
         "rodinia-backprop-1-tiling-back": "Backprop (T)", "rodinia-kmeans-1-tiling": "K-means (T)",
         "rodinia-backprop-0-baseline-back": "Backprop (B)", "rodinia_pathfinder_0_baseline_0": "Pathfinder (B)"}
# Paper Table 2 totals (6 scenarios each) for the per-scenario comparison; kernels missing here get "" (fill in from Table 2)
PAPER = {"machsuite-md-knn": 288, "machsuite-spmv-ellpack": 240, "machsuite-stencil2d": 1437,
         "machsuite-stencil3d": 5032, "rodinia-knn-0-baseline": 144, "rodinia-knn-1-tiling": 4506,
         "rodinia-backprop-1-tiling-back": 5140, "rodinia-kmeans-1-tiling": 5702}

FIXED_LATENCY = {"rodinia-knn-1-tiling"}

def save(fig, name):
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(out, f"{name}.{ext}"))
    plt.close(fig); print("figure:", name)

def write_table(name, rows):
    if not rows: return
    with open(os.path.join(out, name), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    print("table: ", name)

def read(path):
    return list(csv.DictReader(open(path))) if os.path.exists(path) else []

# 1. GA exploration size vs paper, and believed vs true best latency
ga_rows = []
for app, short in SHORT.items():
    g = read(os.path.join(base, "results", f"{app}_xck26_10_s42_g24.csv"))
    if not g: continue
    feas = [r for r in g if r["feasible"] == "True"]
    believed = min((float(r["latency_ms"]) for r in feas), default=None)
    rs = [r for r in read(os.path.join(base, "ga_rescore", f"{app}_rescored.csv")) if r.get("worst_latency_ms")]
    true_best = min((float(r["worst_latency_ms"]) for r in rs), default=None)
    var = sum(1 for r in rs if r.get("variable_latency") == "True")
    # Kernels with fixed trip counts (e.g. KNN (T), board-checked) need no re-scoring: best case = worst case.
    # Mark them by listing them here after checking the HLS reports; their GA objective is then the true best.
    if not rs and app in FIXED_LATENCY:
        true_best, var, rs = believed, 0, [None]
    ga_rows.append({"kernel": short, "ga_designs": len(g), "paper_per_scenario": round(PAPER[app] / 6) if app in PAPER else "",
                    "feasible": len(feas), "variable_latency": var if rs else "",
                    "ga_believed_best_ms": believed, "true_best_worst_case_ms": true_best if rs else ""})
write_table("table_ga.csv", ga_rows)
gr = [r for r in ga_rows if r["true_best_worst_case_ms"] not in ("", None)]
if gr:
    fig, ax = plt.subplots(figsize=(4.2, 2.6))
    x = range(len(gr))
    ax.bar([i - 0.2 for i in x], [r["ga_believed_best_ms"] for r in gr], 0.4, label="GA objective (best case)")
    ax.bar([i + 0.2 for i in x], [r["true_best_worst_case_ms"] for r in gr], 0.4, label="true best (worst case)")
    ax.set_yscale("log"); ax.set_xticks(list(x)); ax.set_xticklabels([r["kernel"] for r in gr], rotation=30, ha="right")
    ax.set_ylabel("latency (ms, log)"); ax.legend(frameon=False, fontsize=7)
    ax.set_title("KV260 GA, Vitis 2022.2: reported vs true best latency")
    save(fig, "fig_ga_best_vs_true")

# 2. Transfer study: latency ratio KV260 (2022.2) / dataset ZCU104 (2021.1) per kernel
tr = read(os.path.join(base, "transfer", "transfer_designs.csv"))
if tr:
    apps = [a for a in SHORT if any(r["app"] == a for r in tr)]
    data, labels, tab = [], [], []
    for a in apps:
        rs = [r for r in tr if r["app"] == a]
        ratios = [float(r["latency_ratio_kv_over_ds"]) for r in rs if r.get("latency_ratio_kv_over_ds")]
        done = [r for r in rs if r["status"] == "done"]
        fail = [r for r in rs if r["status"].startswith("not synth")]
        fits = [r for r in done if r["fits_kv260"] == "True"]
        same = sum(1 for v in ratios if abs(v - 1) < 0.005)
        if ratios: data.append(ratios); labels.append(SHORT[a])
        tab.append({"kernel": SHORT[a], "zcu104_pareto": len(rs), "resynthesised": len(done),
                    "fit_kv260": len(fits), "no_fit_or_not_synth": len(done) - len(fits) + len(fail),
                    "same_latency": same, "median_ratio": sorted(ratios)[len(ratios)//2] if ratios else "",
                    "min_ratio": min(ratios) if ratios else "", "max_ratio": max(ratios) if ratios else ""})
    write_table("table_transfer.csv", tab)
    if data:
        fig, ax = plt.subplots(figsize=(4.2, 2.6))
        ax.boxplot(data, showfliers=True, flierprops={"markersize": 2})
        ax.set_xticks(range(1, len(labels) + 1)); ax.set_xticklabels(labels)
        ax.axhline(1, color="grey", lw=0.8, ls="--")
        ax.set_ylabel("latency KV260 2022.2 / ZCU104 2021.1")
        ax.set_title("ZCU104 Pareto designs transferred to KV260")
        plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
        save(fig, "fig_transfer_ratio")

# 3. Board: HLS-estimated vs measured cycles, and correctness vs clock
HLS = {"rodinia_knn_1_tiling_baseline_100.bit": ("baseline", 38805531),
       "rodinia_knn_1_tiling_workload_433_100.bit": ("ZCU104 design 433", 4290575),
       "rodinia_knn_1_tiling_ga_fastest_100.bit": ("KV260 GA design", 2752500)}
FMAX = {"baseline": 172, "ZCU104 design 433": 160, "KV260 GA design": 122}
# Bitstreams whose HLS ran at 3.33 ns (from results/board/*_300_hls.txt): table only, with their own HLS cycles
HLS300 = {"rodinia_knn_1_tiling_baseline_300.bit": ("baseline, HLS+Vivado at 300 MHz", 76554267, "+0.224 ns"),
          "rodinia_knn_1_tiling_workload_433_300.bit": ("ZCU104 design 433, HLS+Vivado at 300 MHz", 4341775, "+0.211 ns"),
          "rodinia_knn_1_tiling_ga_fastest_300.bit": ("KV260 GA design, HLS+Vivado at 300 MHz", 2418700, "-1.533 ns")}
import glob
cands = sorted(glob.glob(os.path.join(base, "results", "board", "board_results*.csv")), key=os.path.getmtime)
bd = read(cands[-1]) if cands else []
if cands: print("board data:", os.path.basename(cands[-1]))
if bd:
    tab = []
    fig, ax = plt.subplots(figsize=(4.2, 2.6))
    for bit, (name, hls) in HLS.items():
        rs = sorted([r for r in bd if r["bitfile"] == bit], key=lambda r: float(r["fclk0_mhz"]))
        seen = {}
        for r in rs: seen[float(r["fclk0_mhz"])] = r      # last run per clock
        fx, dev, ok = [], [], []
        for f, r in sorted(seen.items()):
            cyc = float(r["median_ms"]) * 1e-3 * f * 1e6
            fx.append(f); dev.append((cyc / hls - 1) * 100); ok.append(r["correct"] == "True")
            tab.append({"design": name, "fclk_mhz": f, "median_ms": round(float(r["median_ms"]), 3),
                        "cycles": int(cyc), "vs_hls_pct": round((cyc / hls - 1) * 100, 2),
                        "correct": r["correct"], "static_fmax_mhz": FMAX[name], "wns_at_build_clock": ""})
        line, = ax.plot(fx, dev, "-", lw=1, label=f"{name} (static Fmax ~{FMAX[name]} MHz)")
        ax.scatter([f for f, o in zip(fx, ok) if o], [d for d, o in zip(dev, ok) if o], color=line.get_color(), s=12)
        ax.scatter([f for f, o in zip(fx, ok) if not o], [d for d, o in zip(dev, ok) if not o], color=line.get_color(),
                   marker="x", s=30)
    # bitstreams built for another clock (the 300 MHz builds) or unknown: table only
    for bit in sorted({r["bitfile"] for r in bd} - set(HLS)):
        seen = {}
        for r in bd:
            if r["bitfile"] == bit: seen[float(r["fclk0_mhz"])] = r
        name, hls, wns = HLS300.get(bit, (bit.replace(".bit", ""), None, ""))
        for f, r in sorted(seen.items()):
            cyc = float(r["median_ms"]) * 1e-3 * f * 1e6
            tab.append({"design": name, "fclk_mhz": f, "median_ms": round(float(r["median_ms"]), 3),
                        "cycles": int(cyc), "vs_hls_pct": round((cyc / hls - 1) * 100, 2) if hls else "",
                        "correct": r["correct"], "static_fmax_mhz": "", "wns_at_build_clock": wns})
    ax.axhline(0, color="grey", lw=0.8, ls="--")
    ax.set_xlabel("PL clock (MHz)"); ax.set_ylabel("measured vs HLS cycles (%)")
    ax.set_title("KV260 board: KNN (T), bitstreams timed at 100 MHz\n(x = wrong output; GA design hangs at 300 MHz)")
    ax.legend(frameon=False, fontsize=6.5, loc="lower right", bbox_to_anchor=(1, 0.06))
    save(fig, "fig_board")
    write_table("table_board.csv", tab)
print("done ->", out)
