#!/usr/bin/env python3
"""
kv260_synthesis.py

Reproduces GNWSIS Pareto-frontier configurations from ZCU104 on KV260
(xck26-sfvc784-2LV-c) by injecting HLS pragmas, running Vitis HLS, and
collecting QoR results.

Run from the GNWSIS repo root with vitis_hls 2022.2 on the module path:
    module load xilinx/vitis_hls/2022.2
    source gnwsis_env/bin/activate
    python kv260_synthesis.py --app rodinia-knn-0-baseline [--dry-run]
    python kv260_synthesis.py --app all
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

DEVICE     = "xck26-sfvc784-2LV-c"
CLOCK_NS   = "10"
SRC_DEVICE = "xczu7ev-ffvc1156-2-e"   # transfer FROM ZCU104

ROOT   = os.path.dirname(os.path.abspath(__file__))
DATA   = os.path.join(ROOT, "data", "ApplicationDataset")
APL    = os.path.join(ROOT, "data", "ApplicationAPLMapping")
OUTPUT = os.path.join(ROOT, "output")
RUNS   = os.path.join(ROOT, "kv260_runs")

BENCHMARKS = [
    "rodinia-knn-0-baseline",
    "machsuite-stencil2d",
    "machsuite-gemm-ncubed",
]

# Files that belong to the testbench, not the HLS kernel
SKIP_FILES = {"noDirectives.cpp", "harness.c", "support.c", "local_support.c"}


# ── helpers ────────────────────────────────────────────────────────────────────

def load_kernel_info(app):
    """Return (top_function_name, {label: {type, [name]}})."""
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
    """Return ({action_point → label}, {label → action_point})."""
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
    """
    Reverse-translate a directive label string (as stored in the Pareto CSV)
    back to a #pragma HLS string.

    Loop examples:   "pipeline"   → "#pragma HLS pipeline"
                     "pipeline_1" → "#pragma HLS pipeline II=1"
                     "unroll"     → "#pragma HLS unroll"
                     "unroll_4"   → "#pragma HLS unroll factor=4"

    Array examples:  "complete_1" → "#pragma HLS array_partition variable=A complete dim=1"
                     "block_8_2"  → "#pragma HLS array_partition variable=A block factor=8 dim=2"
                     "cyclic_4_1" → "#pragma HLS array_partition variable=A cyclic factor=4 dim=1"
    """
    if not directive or directive == "NDIR":
        return None
    if isinstance(directive, float):   # NaN from missing column
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
    """
    Copy every source file for <app> into kv260_runs/<app>/run_<run_id>/.
    For the main kernel file, inject pragmas immediately after each L-marker line.

    This replicates the logic of DirectivesManipulator.apply_directives():
      - scan each line
      - if stripped line contains 'L{cnt}', write the line then write the pragma
      - increment cnt to look for the next label
    """
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
    """Generate a Vitis HLS TCL script for one synthesis run."""
    proj_name = f"proj_{run_id:04d}"
    runs_app_dir = os.path.join(RUNS, app)
    proj_dir = os.path.join(runs_app_dir, proj_name)   # full path for report parsing
    tcl_path = os.path.join(runs_app_dir, f"run_{run_id:04d}.tcl")

    # open_project requires a name relative to CWD, not an absolute path.
    # cd to the per-app run directory first so the project lands there.
    lines = [
        f'cd {runs_app_dir}',
        f'open_project -reset {proj_name}',
        f'set_top {top_fn}',
    ]
    for fpath in source_files:
        if os.path.basename(fpath) in SKIP_FILES:
            continue
        lines.append(f'add_files {fpath} -cflags "-I{run_dir}"')   # space before quote

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


def run_hls(tcl_path, app, run_id):
    """Run vitis_hls on <tcl_path>; return (success, elapsed_seconds)."""
    log_path = os.path.join(RUNS, app, f"run_{run_id:04d}.log")
    t0 = time.time()
    try:
        result = subprocess.run(
            ['vitis_hls', tcl_path],
            capture_output=True, text=True, timeout=7200
        )
        ok = result.returncode == 0
        stdout = result.stdout
        stderr = result.stderr
    except subprocess.TimeoutExpired:
        ok = False
        stdout = ""
        stderr = "TIMEOUT after 7200s"

    elapsed = time.time() - t0

    with open(log_path, 'w') as f:
        f.write(stdout)
        if stderr:
            f.write('\n--- STDERR ---\n')
            f.write(stderr)

    return ok, elapsed


def parse_csynth_report(proj_dir, top_fn=None):
    """
    Parse the top-level kernel *_csynth.rpt for latency and resource utilization.

    Vitis HLS writes one report per module (top-level + sub-modules like
    stencil_Pipeline_*_csynth.rpt).  Sub-module reports show µs latency and
    have no 'Available' resource row, so we must read the top-function report.
    """
    report_dir = os.path.join(proj_dir, 'sol1', 'syn', 'report')

    rpt_path = None
    if top_fn:
        candidate = os.path.join(report_dir, f'{top_fn}_csynth.rpt')
        if os.path.exists(candidate):
            rpt_path = candidate

    if rpt_path is None:
        all_rpts = glob.glob(os.path.join(report_dir, '*_csynth.rpt'))
        if not all_rpts:
            return None
        # Shortest filename is the top-level report (sub-modules have longer names)
        rpt_path = min(all_rpts, key=lambda p: len(os.path.basename(p)))

    with open(rpt_path) as f:
        txt = f.read()

    # Latency data row: | cycles_min | cycles_max | time_val unit | ...
    # Unit can be ns, us, ms, or s depending on design latency magnitude.
    lat_m = re.search(
        r'\|\s*(\d[\d,]+)\s*\|\s*\d[\d,]+\s*\|\s*([\d.]+)\s*(ns|us|ms|s)\s*\|',
        txt
    )
    if not lat_m:
        return None

    cycles = int(lat_m.group(1).replace(',', ''))
    time_val = float(lat_m.group(2))
    unit = lat_m.group(3)
    to_ms = {'ns': 1e-6, 'us': 1e-3, 'ms': 1.0, 's': 1e3}
    latency_ms = time_val * to_ms[unit]

    # Resource summary table: BRAM_18K | DSP | FF | LUT [| URAM]
    total_m = re.search(
        r'\|Total\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|', txt
    )
    avail_m = re.search(
        r'\|Available\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|', txt
    )

    if not total_m or not avail_m:
        return {
            'latency_ms': latency_ms, 'cycles': cycles,
            'util_bram': None, 'util_dsp': None,
            'util_ff': None, 'util_lut': None,
        }

    totals = [int(x) for x in total_m.groups()]   # BRAM_18K, DSP, FF, LUT
    avails = [int(x) for x in avail_m.groups()]

    def pct(i):
        return round(totals[i] / avails[i] * 100, 2) if avails[i] > 0 else 0.0

    return {
        'latency_ms': latency_ms,
        'cycles':     cycles,
        'util_bram':  pct(0),
        'util_dsp':   pct(1),
        'util_ff':    pct(2),
        'util_lut':   pct(3),
    }


# ── per-benchmark ──────────────────────────────────────────────────────────────

def process_app(app, dry_run=False):
    print(f"\n{'='*60}\nApp: {app}\n{'='*60}")

    top_fn, label_info = load_kernel_info(app)
    ap2l, _ = load_apl(app)

    pareto_csv = os.path.join(OUTPUT, app, "03_pareto_frontiers.csv")
    if not os.path.exists(pareto_csv):
        print(f"  [ERROR] Pareto CSV not found: {pareto_csv}")
        return None

    df = pd.read_csv(pareto_csv)

    # Debug: show what's actually in the CSV
    print(f"  CSV shape: {df.shape}")
    if 'Device' in df.columns:
        print(f"  Devices:  {df['Device'].unique()}")
    else:
        print("  [WARN] No 'Device' column — skipping device filter")
    if 'Clock_Period_nsec' in df.columns:
        print(f"  Clocks:   {df['Clock_Period_nsec'].unique()}")

    # Filter to ZCU104 at 10 ns — robust to int/float/string storage
    if 'Device' in df.columns:
        df = df[df['Device'] == SRC_DEVICE]
    if 'Clock_Period_nsec' in df.columns:
        clock_col = pd.to_numeric(df['Clock_Period_nsec'], errors='coerce')
        df = df[clock_col == float(CLOCK_NS)]
    df = df.reset_index(drop=True)

    print(f"  {len(df)} Pareto configs to synthesize for KV260")

    # Only the action-point columns that have an APL entry for this benchmark
    ap_cols = [c for c in df.columns if c in ap2l]

    os.makedirs(os.path.join(RUNS, app), exist_ok=True)

    results = []

    for run_id, row in df.iterrows():
        version_label = row.get('Version', f'run_{run_id}')
        print(f"\n  [{run_id + 1}/{len(df)}] {version_label}")

        # Build {L1: pragma, L2: pragma, ...}
        label_pragmas = {}
        for ap_col in ap_cols:
            directive = row[ap_col]
            label = ap2l[ap_col]
            pragma = directive_to_pragma(directive, label, label_info)
            if pragma:
                label_pragmas[label] = pragma
                print(f"    {label} ({ap_col}={directive}): {pragma}")
            else:
                print(f"    {label} ({ap_col}={directive}): NDIR")

        if dry_run:
            continue

        run_dir, src_files = inject_pragmas(app, label_pragmas, run_id)
        syn_files = [f for f in src_files if os.path.basename(f) not in SKIP_FILES]
        tcl_path, proj_dir = make_tcl(app, top_fn, run_dir, syn_files, run_id)

        print(f"    Synthesizing... ", end='', flush=True)
        ok, elapsed = run_hls(tcl_path, app, run_id)
        print(f"{'OK' if ok else 'FAILED'} ({elapsed:.0f}s)")

        qor = parse_csynth_report(proj_dir, top_fn) if ok else None

        if qor:
            print(
                f"    Latency={qor['latency_ms']:.4f}ms ({qor['cycles']} cycles)  "
                f"LUT={qor['util_lut']:.1f}%  DSP={qor['util_dsp']:.1f}%  "
                f"BRAM={qor['util_bram']:.1f}%  FF={qor['util_ff']:.1f}%"
            )

        entry = {
            'App':               app,
            'Run_ID':            run_id,
            'Version_ZCU104':    version_label,
            'Latency_ZCU104_ms': row.get('Latency_msec'),
            'BRAM_ZCU104_pct':   row.get('BRAM_Utilization_percentage'),
            'DSP_ZCU104_pct':    row.get('DSP_Utilization_percentage'),
            'FF_ZCU104_pct':     row.get('FF_Utilization_percentage'),
            'LUT_ZCU104_pct':    row.get('LUT_Utilization_percentage'),
            'Pragmas':           str(label_pragmas),
            'Synth_OK':          ok,
            'Synth_Time_sec':    round(elapsed, 1),
        }
        if qor:
            entry.update({
                'Latency_KV260_ms': qor['latency_ms'],
                'Cycles_KV260':     qor['cycles'],
                'BRAM_KV260_pct':   qor['util_bram'],
                'DSP_KV260_pct':    qor['util_dsp'],
                'FF_KV260_pct':     qor['util_ff'],
                'LUT_KV260_pct':    qor['util_lut'],
            })

        results.append(entry)

    if dry_run:
        print("\n  [DRY RUN] No synthesis performed.")
        return None

    out_df = pd.DataFrame(results)
    out_path = os.path.join(OUTPUT, app, "kv260_results.csv")
    out_df.to_csv(out_path, index=False)
    print(f"\n  Saved → {out_path}")
    return out_df


# ── reparse mode ──────────────────────────────────────────────────────────────

def reparse_app(app):
    """Re-parse csynth reports for already-synthesised runs (no re-synthesis)."""
    print(f"\n{'='*60}\nReparse: {app}\n{'='*60}")

    top_fn, _ = load_kernel_info(app)
    out_path = os.path.join(OUTPUT, app, "kv260_results.csv")

    if not os.path.exists(out_path):
        print(f"  [SKIP] No results CSV at {out_path}")
        return None

    df = pd.read_csv(out_path)

    for idx, row in df.iterrows():
        if not row['Synth_OK']:
            continue
        run_id = int(row['Run_ID'])
        proj_dir = os.path.join(RUNS, app, f"proj_{run_id:04d}")
        qor = parse_csynth_report(proj_dir, top_fn)
        if qor:
            df.at[idx, 'Latency_KV260_ms'] = qor['latency_ms']
            df.at[idx, 'Cycles_KV260']     = qor['cycles']
            df.at[idx, 'BRAM_KV260_pct']   = qor['util_bram']
            df.at[idx, 'DSP_KV260_pct']    = qor['util_dsp']
            df.at[idx, 'FF_KV260_pct']     = qor['util_ff']
            df.at[idx, 'LUT_KV260_pct']    = qor['util_lut']
            print(f"  [{run_id}] {row['Version_ZCU104']}: "
                  f"Latency={qor['latency_ms']:.4f}ms  "
                  f"LUT={qor['util_lut']:.1f}%  DSP={qor['util_dsp']:.1f}%")
        else:
            print(f"  [{run_id}] {row['Version_ZCU104']}: parse FAILED")

    df.to_csv(out_path, index=False)
    print(f"  Updated → {out_path}")
    return df


# ── entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Run Vitis HLS synthesis for KV260 using ZCU104 Pareto configs."
    )
    parser.add_argument(
        '--app', default='rodinia-knn-0-baseline',
        choices=BENCHMARKS + ['all'],
        help='Benchmark to process (default: rodinia-knn-0-baseline)',
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Print directive mapping only; skip actual synthesis',
    )
    parser.add_argument(
        '--reparse', action='store_true',
        help='Re-parse existing csynth reports without re-running synthesis',
    )
    args = parser.parse_args()

    apps = BENCHMARKS if args.app == 'all' else [args.app]
    all_results = []

    if args.reparse:
        for app in apps:
            df = reparse_app(app)
            if df is not None:
                all_results.append(df)
    else:
        for app in apps:
            df = process_app(app, dry_run=args.dry_run)
            if df is not None:
                all_results.append(df)

    if all_results:
        combined = pd.concat(all_results, ignore_index=True)
        combined_path = os.path.join(ROOT, "kv260_all_results.csv")
        combined.to_csv(combined_path, index=False)
        print(f"\nAll results saved → {combined_path}")


if __name__ == "__main__":
    main()
