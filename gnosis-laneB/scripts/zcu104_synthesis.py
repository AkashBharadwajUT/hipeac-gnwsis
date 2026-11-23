#!/usr/bin/env python3
"""
zcu104_synthesis.py

Re-synthesise Nitin's 961 transferred designs on ZCU104 (xczu7ev-ffvc1156-2-e)
with Vitis HLS 2022.2 at 10 ns, to separate tool effect from device effect.

Input:  transfer_designs_full.csv  (lane C, same folder as this script)
Output: zcu104_all_results.csv
        zcu104_runs/<app>/run_NNNN/  (HLS projects, deleted after report parsed)

Usage (on caes-lab2, /local/scratch/$USER/gnwsis/):
    module load xilinx/vitis_hls/2022.2
    python zcu104_synthesis.py --app all
    python zcu104_synthesis.py --app machsuite-stencil2d --dry-run
    python zcu104_synthesis.py --app all --reparse   # re-read existing reports
"""

import argparse
import glob
import os
import re
import shutil
import subprocess
import time

import pandas as pd

# ── constants ──────────────────────────────────────────────────────────────────

DEVICE     = "xczu7ev-ffvc1156-2-e"   # ZCU104 (source / target)
CLOCK_NS   = "10"

ROOT          = os.path.dirname(os.path.abspath(__file__))
DATA          = os.path.join(ROOT, "data", "ApplicationDataset")
APL           = os.path.join(ROOT, "data", "ApplicationAPLMapping")
CSVS_DIR      = os.path.join(ROOT, "CSVS")
TRANSFER_CSV  = os.path.join(ROOT, "transfer_designs_full.csv")
RUNS          = os.path.join(ROOT, "zcu104_runs")
RESULTS_CSV   = os.path.join(ROOT, "zcu104_all_results.csv")
RESULTS_DIR   = os.path.join(ROOT, "zcu104_results")

# All 7 apps from Nitin's transfer study
BENCHMARKS = [
    "machsuite-md-knn",
    "machsuite-spmv-ellpack",
    "machsuite-stencil2d",
    "machsuite-stencil3d",
    "rodinia-knn-0-baseline",
    "rodinia-knn-1-tiling",
    "rodinia_pathfinder_0_baseline_0",
]

SKIP_FILES = {"noDirectives.cpp", "harness.c", "support.c", "local_support.c"}

# ZCU104 total resources (for utilisation %)
ZCU104_RESOURCES = {
    "BRAM": 624,
    "DSP":  1728,
    "FF":   460800,
    "LUT":  230400,
}


# ── helpers ────────────────────────────────────────────────────────────────────

def load_kernel_info(app):
    path = os.path.join(DATA, app, "kernel_info.txt")
    with open(path) as f:
        lines = [l.strip() for l in f if l.strip()]
    top_fn = lines[0]
    label_info = {}
    for line in lines[1:]:
        parts = line.split(',')
        label, ltype = parts[0], parts[1]
        if ltype == "loop":
            label_info[label] = {"type": "loop"}
        elif ltype == "array":
            label_info[label] = {"type": "array", "name": parts[2]}
    return top_fn, label_info


def load_apl(app):
    path = os.path.join(APL, f"{app}.txt")
    ap2l, l2ap = {}, {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            ap, label = line.split(',')
            ap2l[ap] = label
            l2ap[label] = ap
    return ap2l, l2ap


def directive_to_pragma(directive, label, label_info):
    if not directive or directive == "NDIR":
        return None
    if isinstance(directive, float):
        return None
    info = label_info.get(label, {})
    t = info.get("type", "loop")
    if t == "loop":
        if directive == "pipeline":
            return "#pragma HLS pipeline"
        if directive == "pipeline_1":
            return "#pragma HLS pipeline II=1"
        if directive == "unroll":
            return "#pragma HLS unroll"
        m = re.match(r'^unroll_(\d+)$', directive)
        if m:
            return f"#pragma HLS unroll factor={m.group(1)}"
    elif t == "array":
        name = info.get("name", "arr")
        if re.match(r'^complete_\d+$', directive):
            dim = directive.split("_")[1]
            return f"#pragma HLS array_partition variable={name} complete dim={dim}"
        m = re.match(r'^(block|cyclic)_(\d+)_(\d+)$', directive)
        if m:
            typ, factor, dim = m.groups()
            return f"#pragma HLS array_partition variable={name} {typ} factor={factor} dim={dim}"
    print(f"  [WARN] Unknown directive '{directive}' for label {label} (type={t})")
    return None


def inject_pragmas(app, label_pragmas, run_id):
    src_dir = os.path.join(DATA, app)
    run_dir = os.path.join(RUNS, app, f"run_{run_id:04d}")
    os.makedirs(run_dir, exist_ok=True)
    source_files = []
    for fname in sorted(os.listdir(src_dir)):
        if not (fname.endswith('.c') or fname.endswith('.cpp') or fname.endswith('.h')):
            continue
        src_path = os.path.join(src_dir, fname)
        dst_path = os.path.join(run_dir, fname)
        is_source = fname.endswith('.c') or fname.endswith('.cpp')
        if fname in SKIP_FILES:
            shutil.copy2(src_path, dst_path)
            continue
        if is_source and label_pragmas:
            with open(src_path) as fr, open(dst_path, 'w') as fw:
                cnt = 1
                for line in fr:
                    stripped = line.replace(' ', '').replace('\n', '').replace('\t', '')
                    pattern = f'L{cnt}'
                    if pattern in stripped:
                        fw.write(line)
                        pragma = label_pragmas.get(pattern)
                        if pragma:
                            fw.write(f'    {pragma}\n')
                        cnt += 1
                    else:
                        fw.write(line)
        else:
            shutil.copy2(src_path, dst_path)
        if is_source:
            source_files.append(dst_path)
    return run_dir, source_files


def make_tcl(app, top_fn, run_dir, source_files, run_id):
    proj_name = f"proj_{run_id:04d}"
    runs_app_dir = os.path.join(RUNS, app)
    proj_dir = os.path.join(runs_app_dir, proj_name)
    tcl_path = os.path.join(runs_app_dir, f"run_{run_id:04d}.tcl")
    lines = [
        f'cd {runs_app_dir}',
        f'open_project -reset {proj_name}',
        f'set_top {top_fn}',
    ]
    for fpath in source_files:
        if os.path.basename(fpath) in SKIP_FILES:
            continue
        lines.append(f'add_files {fpath} -cflags "-I{run_dir}"')
    lines += [
        'open_solution sol1 -flow_target vivado',
        f'set_part {DEVICE}',
        f'create_clock -period {CLOCK_NS} -name default',
        'csynth_design',
        'exit',
    ]
    with open(tcl_path, 'w') as f:
        f.write('\n'.join(lines) + '\n')
    return tcl_path, proj_dir


def parse_csynth_report(proj_dir, top_fn=None):
    # Accept either a full project dir (proj_dir/sol1/syn/report/) or a
    # direct report dir (the saved report_NNNN/ from a previous run).
    report_dir = os.path.join(proj_dir, "sol1", "syn", "report")
    if not os.path.isdir(report_dir):
        report_dir = proj_dir
    if not os.path.isdir(report_dir):
        return None

    rpt_path = None
    if top_fn:
        candidate = os.path.join(report_dir, f'{top_fn}_csynth.rpt')
        if os.path.exists(candidate):
            rpt_path = candidate
    if rpt_path is None:
        all_rpts = glob.glob(os.path.join(report_dir, '*_csynth.rpt'))
        if not all_rpts:
            return None
        rpt_path = min(all_rpts, key=lambda p: len(os.path.basename(p)))

    with open(rpt_path) as f:
        txt = f.read()

    # Latency: read worst-case (max) — col order: min_cyc | max_cyc | min_time | max_time
    lat_m = re.search(
        r'\|\s*\d[\d,]*\s*\|\s*(\d[\d,]+)\s*\|\s*[\d.]+\s*(?:ns|us|ms|s)\s*\|\s*([\d.]+)\s*(ns|us|ms|s)\s*\|',
        txt)
    if not lat_m:
        return None
    cycles = int(lat_m.group(1).replace(',', ''))
    time_val = float(lat_m.group(2))
    unit = lat_m.group(3)
    to_ms = {'ns': 1e-6, 'us': 1e-3, 'ms': 1.0, 's': 1e3}
    latency_ms = time_val * to_ms[unit]

    # Resources: col order in 2022.2 is BRAM_18K | DSP | FF | LUT (URAM optional)
    # Read the Total (used) and Available rows from the Summary table.
    if "Available" not in txt:
        return None

    total_m = re.search(
        r'\|\s*Total\s*\|\s*(\d[\d,]*)\s*\|\s*(\d[\d,]*)\s*\|\s*(\d[\d,]*)\s*\|\s*(\d[\d,]*)\s*\|', txt)
    avail_m = re.search(
        r'\|\s*Available\s*\|\s*(\d[\d,]*)\s*\|\s*(\d[\d,]*)\s*\|\s*(\d[\d,]*)\s*\|\s*(\d[\d,]*)\s*\|', txt)

    result = {"latency_ms": latency_ms, "cycles": cycles}

    if total_m and avail_m:
        res_names = ["BRAM", "DSP", "FF", "LUT"]
        for i, name in enumerate(res_names):
            used  = int(total_m.group(i + 1).replace(',', ''))
            avail = int(avail_m.group(i + 1).replace(',', ''))
            result[f"{name}_used"]  = used
            result[f"{name}_avail"] = avail
            if avail > 0:
                result[f"{name}_pct"] = round(100.0 * used / avail, 2)

    return result


# ── design loading from transfer CSV + CSVS ────────────────────────────────────

def load_transfer_rows(app, top_n=0):
    """Return list of row IDs and their dataset latencies for this app.

    If top_n > 0, restrict to the N fastest designs by 2022.2 latency
    (reads RESULTS_CSV which must already exist from a previous full run).
    """
    df = pd.read_csv(TRANSFER_CSV)
    sub = df[df['app'] == app].copy()

    base_results = os.path.join(ROOT, "zcu104_all_results.csv")
    if top_n > 0 and os.path.exists(base_results):
        prev = pd.read_csv(base_results)
        prev_app = prev[(prev['App'] == app) & (prev['Synth_OK'] == True)].copy()
        prev_app = prev_app.sort_values('Latency_ZCU104_ms').head(top_n)
        keep = set(prev_app['Version_ZCU104'].values)
        sub = sub[sub['row'].isin(keep)]

    return list(sub['row'].values), dict(zip(sub['row'], sub['ds_latency_ms']))


def find_csv_for_app(app):
    """Find the Pareto/full CSV for this app in the CSVS directory."""
    # Try exact match first
    exact = os.path.join(CSVS_DIR, f"{app}.csv")
    if os.path.exists(exact):
        return exact
    # Try glob with underscores/dashes
    pattern = os.path.join(CSVS_DIR, f"*{app.replace('-', '_')}*.csv")
    matches = glob.glob(pattern)
    if matches:
        return matches[0]
    pattern2 = os.path.join(CSVS_DIR, f"*{app.replace('_', '-')}*.csv")
    matches2 = glob.glob(pattern2)
    if matches2:
        return matches2[0]
    return None


def load_designs(app, row_ids, label_info, ap2l, l2ap):
    """
    Load directive configs for the given row_ids from the app's CSVS file.
    Returns list of (run_id, version, label_pragmas, ds_latency_ms, pragmas_str).
    """
    csv_path = find_csv_for_app(app)
    if csv_path is None:
        print(f"  [ERROR] No CSV found for {app} in {CSVS_DIR}")
        return []

    df = pd.read_csv(csv_path)
    print(f"  CSV: {os.path.basename(csv_path)}  shape={df.shape}")

    # Filter to only the rows Nitin transferred
    row_id_set = set(row_ids)
    version_col = None
    for col in ['Version', 'version', 'row', 'Row']:
        if col in df.columns:
            version_col = col
            break
    if version_col is None:
        print(f"  [ERROR] No Version column in {csv_path}. Columns: {list(df.columns[:10])}")
        return []

    df_filtered = df[df[version_col].isin(row_id_set)].copy()
    print(f"  Matched {len(df_filtered)} / {len(row_ids)} transfer rows in CSV")

    # Identify label columns (L1, L2, ...)
    label_cols = [c for c in df.columns if re.match(r'^L\d+$', c)]

    designs = []
    for run_id, (_, row) in enumerate(df_filtered.iterrows()):
        label_pragmas = {}
        pragma_parts = []
        for lc in label_cols:
            val = row.get(lc)
            pragma = directive_to_pragma(val, lc, label_info)
            if pragma:
                label_pragmas[lc] = pragma
                pragma_parts.append(f"'{lc}': '{pragma}'")

        ds_lat = row.get('Latency_ms') or row.get('latency_ms') or row.get('Latency')
        version = row[version_col]
        designs.append((run_id, version, label_pragmas, ds_lat, "{" + ", ".join(pragma_parts) + "}"))

    return designs


# ── synthesis runner ───────────────────────────────────────────────────────────

def synthesize_app(app, dry_run=False, reparse=False, top_n=0):
    print(f"\n{'='*60}")
    print(f"App: {app}  device={DEVICE}  clock={CLOCK_NS}ns")
    print(f"{'='*60}")

    top_fn, label_info = load_kernel_info(app)
    ap2l, l2ap = load_apl(app)

    row_ids, ds_lat_map = load_transfer_rows(app, top_n=top_n)
    print(f"  Transfer rows to synthesise: {len(row_ids)}"
          + (f" (top {top_n} by 2022.2 latency)" if top_n else ""))

    designs = load_designs(app, row_ids, label_info, ap2l, l2ap)
    if not designs:
        print("  [SKIP] No designs found.")
        return []

    os.makedirs(os.path.join(RUNS, app), exist_ok=True)

    if dry_run:
        print(f"\n  [DRY RUN] Would synthesise {len(designs)} designs:")
        for run_id, version, lp, ds_lat, pragmas_str in designs[:5]:
            print(f"    run_{run_id:04d}  {version}  pragmas={pragmas_str}")
        if len(designs) > 5:
            print(f"    ... ({len(designs)-5} more)")
        return []

    results = []
    for run_id, version, label_pragmas, ds_lat, pragmas_str in designs:
        run_dir, source_files = inject_pragmas(app, label_pragmas, run_id)
        tcl_path, proj_dir = make_tcl(app, top_fn, run_dir, source_files, run_id)
        log_path = os.path.join(RUNS, app, f"run_{run_id:04d}.log")

        if reparse:
            # Project dir is deleted; look in the saved report_NNNN/ directory.
            saved_rpt = os.path.join(RUNS, app, f"report_{run_id:04d}")
            qor = parse_csynth_report(saved_rpt, top_fn)
            synth_ok = qor is not None
        else:
            t0 = time.time()
            with open(log_path, 'w') as logf:
                ret = subprocess.run(
                    ["vitis_hls", "-f", tcl_path, "-l", log_path],
                    stdout=logf, stderr=subprocess.STDOUT,
                    timeout=3700   # 1h + small buffer
                )
            elapsed = time.time() - t0
            synth_ok = (ret.returncode == 0)
            qor = parse_csynth_report(proj_dir, top_fn) if synth_ok else None
            status = "OK" if synth_ok else "FAIL"
            print(f"  run_{run_id:04d}  {version:<30}  {status}  {elapsed:.0f}s")

            # Delete project folder to save disk space; keep log and report
            if synth_ok and qor:
                rpt_src = os.path.join(proj_dir, "sol1", "syn", "report")
                rpt_dst = os.path.join(RUNS, app, f"report_{run_id:04d}")
                if os.path.isdir(rpt_src) and not os.path.isdir(rpt_dst):
                    shutil.copytree(rpt_src, rpt_dst)
                shutil.rmtree(proj_dir, ignore_errors=True)

        row = {
            "App": app,
            "Run_ID": run_id,
            "Version_ZCU104": version,
            "Latency_DS_ms": ds_lat_map.get(version, ds_lat),
            "Pragmas": pragmas_str,
            "Synth_OK": synth_ok,
        }
        if qor:
            row.update({
                "Latency_ZCU104_ms":  qor.get("latency_ms"),
                "Cycles_ZCU104":      qor.get("cycles"),
                "BRAM_ZCU104_pct":    qor.get("BRAM_pct"),
                "DSP_ZCU104_pct":     qor.get("DSP_pct"),
                "FF_ZCU104_pct":      qor.get("FF_pct"),
                "LUT_ZCU104_pct":     qor.get("LUT_pct"),
            })
        results.append(row)

    return results


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', choices=BENCHMARKS + ['all'], default='all')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--reparse', action='store_true',
                        help='Re-read existing reports without re-synthesising')
    parser.add_argument('--top-n', type=int, default=0,
                        help='Only run the N fastest designs per app (requires a prior full run)')
    parser.add_argument('--out-prefix', default='',
                        help='Prefix for output dirs/files, e.g. "2025" gives zcu104_2025_runs/')
    args = parser.parse_args()

    # Override output paths when a prefix is given
    if args.out_prefix:
        global RUNS, RESULTS_CSV, RESULTS_DIR
        RUNS        = os.path.join(ROOT, f"zcu104_{args.out_prefix}_runs")
        RESULTS_CSV = os.path.join(ROOT, f"zcu104_{args.out_prefix}_all_results.csv")
        RESULTS_DIR = os.path.join(ROOT, f"zcu104_{args.out_prefix}_results")

    apps = BENCHMARKS if args.app == 'all' else [args.app]

    all_results = []
    for app in apps:
        rows = synthesize_app(app, dry_run=args.dry_run, reparse=args.reparse,
                              top_n=args.top_n)
        all_results.extend(rows)

    if all_results and not args.dry_run:
        os.makedirs(RESULTS_DIR, exist_ok=True)
        for app in apps:
            app_rows = [r for r in all_results if r["App"] == app]
            if app_rows:
                app_csv = os.path.join(RESULTS_DIR, f"{app}.csv")
                pd.DataFrame(app_rows).to_csv(app_csv, index=False)
                print(f"  {app}: {len(app_rows)} rows → {app_csv}")

        # Merge all per-app CSVs into the combined file
        parts = []
        for f in sorted(glob.glob(os.path.join(RESULTS_DIR, "*.csv"))):
            parts.append(pd.read_csv(f))
        if parts:
            merged = pd.concat(parts, ignore_index=True)
            merged.to_csv(RESULTS_CSV, index=False)
            print(f"\nResults saved to {RESULTS_CSV}  ({len(merged)} rows total)")


if __name__ == "__main__":
    main()
