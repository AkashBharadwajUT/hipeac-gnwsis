#!/usr/bin/env python3
"""
zcu104_analysis.py

Three-version tool comparison on ZCU104 (xczu7ev-ffvc1156-2-e, 10 ns):
  2021.1 (GNWSIS dataset)  vs  2022.2 (our re-synthesis, 961 designs)
  vs  2025.2 (golden rows, top-7 per kernel)

Outputs:
  zcu104_analysis_results.csv   -- per-design joined table (2022.2, all 961)
  zcu104_3ver_results.csv       -- per-design joined table (2025.2, 44 designs)
  printed summary tables for the report

Usage:
  python zcu104_analysis.py
"""

import os
import pandas as pd

ROOT   = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(ROOT)

ZCU22_CSV   = os.path.join(PARENT, "zcu104_all_results.csv")
ZCU25_CSV   = os.path.join(PARENT, "zcu104_2025_all_results.csv")
TRANSFER_CSV = os.path.join(PARENT, "hipeac-gnwsis", "laneC", "transfer",
                             "transfer_designs_full.csv")
OUT22_CSV   = os.path.join(PARENT, "zcu104_analysis_results.csv")
OUT3VER_CSV = os.path.join(PARENT, "zcu104_3ver_results.csv")

KERNEL_ORDER = [
    "machsuite-md-knn",
    "machsuite-spmv-ellpack",
    "machsuite-stencil2d",
    "machsuite-stencil3d",
    "rodinia-knn-0-baseline",
    "rodinia-knn-1-tiling",
    "rodinia_pathfinder_0_baseline_0",
]

# ── load & join ───────────────────────────────────────────────────────────────

zcu22 = pd.read_csv(ZCU22_CSV)
tr    = pd.read_csv(TRANSFER_CSV)

tr_lookup = tr.set_index(['app', 'row'])[
    ['ds_latency_ms', 'ds_BRAM', 'ds_DSP', 'ds_FF', 'ds_LUT',
     'variable_latency', 'kv_latency_ms', 'fits_kv260', 'exit_code']
].copy()

df22 = zcu22.rename(columns={'App': 'app', 'Version_ZCU104': 'row'}).merge(
    tr_lookup.reset_index(), on=['app', 'row'], how='left'
)
df22['ratio_2022_over_2021'] = df22['Latency_ZCU104_ms'] / df22['ds_latency_ms']
df22.to_csv(OUT22_CSV, index=False)
print(f"2022.2 joined table: {OUT22_CSV}  ({len(df22)} rows)")

# 2025.2 — merge with 2022.2 results for the same designs
have_2025 = os.path.exists(ZCU25_CSV)
if have_2025:
    zcu25 = pd.read_csv(ZCU25_CSV).rename(
        columns={'App': 'app', 'Version_ZCU104': 'row',
                 'Latency_ZCU104_ms': 'Latency_2025_ms',
                 'Synth_OK': 'Synth_OK_2025'}
    )[['app', 'row', 'Latency_2025_ms', 'Synth_OK_2025']]

    df3 = df22.merge(zcu25, on=['app', 'row'], how='inner')
    df3['ratio_2025_over_2021'] = df3['Latency_2025_ms'] / df3['ds_latency_ms']
    df3['ratio_2025_over_2022'] = df3['Latency_2025_ms'] / df3['Latency_ZCU104_ms']
    df3.to_csv(OUT3VER_CSV, index=False)
    print(f"3-version joined table: {OUT3VER_CSV}  ({len(df3)} rows)\n")
else:
    df3 = None
    print("(2025.2 CSV not found — skipping 3-version table)\n")


# ── helper ────────────────────────────────────────────────────────────────────

def ratio_summary(series):
    s = series.dropna()
    if len(s) == 0:
        return len(s), float('nan'), 0.0, 0.0
    le1 = 100 * s.between(0.99, 1.01).mean()
    le5 = 100 * s.between(0.95, 1.05).mean()
    return len(s), s.median(), le1, le5


# ── 2022.2 vs 2021.1 (all 961 designs) ───────────────────────────────────────

print("=" * 76)
print("2022.2 vs 2021.1 on ZCU104 (worst-case latency, 961 designs)")
print("=" * 76)
print(f"  {'Kernel':<40} {'N':>4}  {'Med ratio':>9}  {'<=1%':>6}  {'<=5%':>6}")
print("  " + "-" * 72)

for app in KERNEL_ORDER:
    sub = df22[(df22['app'] == app) & (df22['Synth_OK'] == True)]
    n, med, le1, le5 = ratio_summary(sub['ratio_2022_over_2021'])
    print(f"  {app:<40} {n:>4}  {med:>9.4f}  {le1:>5.1f}%%  {le5:>5.1f}%%")

print("  " + "-" * 72)
n, med, le1, le5 = ratio_summary(
    df22[df22['Synth_OK'] == True]['ratio_2022_over_2021'])
print(f"  {'ALL':<40} {n:>4}  {med:>9.4f}  {le1:>5.1f}%%  {le5:>5.1f}%%")


# ── 3-version comparison (top-7 golden rows) ─────────────────────────────────

if df3 is not None:
    print()
    print("=" * 90)
    print("Tool-version progression on ZCU104: 2021.1 -> 2022.2 -> 2025.2  (top-7 per kernel)")
    print("=" * 90)
    print(f"  {'Kernel':<40} {'N':>3}  "
          f"{'2022/2021':>9}  {'2025/2021':>9}  {'2025/2022':>9}")
    print("  " + "-" * 86)

    for app in KERNEL_ORDER:
        sub = df3[(df3['app'] == app) & (df3['Synth_OK'] == True) & (df3['Synth_OK_2025'] == True)]
        _, med22, _, _ = ratio_summary(sub['ratio_2022_over_2021'])
        _, med25_21, _, _ = ratio_summary(sub['ratio_2025_over_2021'])
        n, med25_22, _, _ = ratio_summary(sub['ratio_2025_over_2022'])
        print(f"  {app:<40} {n:>3}  {med22:>9.4f}  {med25_21:>9.4f}  {med25_22:>9.4f}")

    print("  " + "-" * 86)
    sub_all = df3[(df3['Synth_OK'] == True) & (df3['Synth_OK_2025'] == True)]
    _, med22, _, _ = ratio_summary(sub_all['ratio_2022_over_2021'])
    _, med25_21, _, _ = ratio_summary(sub_all['ratio_2025_over_2021'])
    n, med25_22, _, _ = ratio_summary(sub_all['ratio_2025_over_2022'])
    print(f"  {'ALL':<40} {n:>3}  {med22:>9.4f}  {med25_21:>9.4f}  {med25_22:>9.4f}")
    print()
    print("  Interpretation: ratio > 1.0 means the newer tool is SLOWER on this kernel.")
    print("  ratio_2025/2022 close to 1.0 means no further drift from 2022.2 to 2025.2.")


# ── variable vs fixed latency (2022.2) ───────────────────────────────────────

print()
print("--- Fixed vs variable latency (2022.2 vs 2021.1) ---")
for label, mask in [("Fixed latency ", df22['variable_latency'] == False),
                    ("Variable latency", df22['variable_latency'] == True)]:
    sub = df22[mask & (df22['Synth_OK'] == True)]
    n, med, le1, _ = ratio_summary(sub['ratio_2022_over_2021'])
    r = sub['ratio_2022_over_2021'].dropna()
    if n:
        print(f"  {label}  n={n:>4}  median={med:.4f}  "
              f"<=1%={le1:.1f}%%  min={r.min():.4f}  max={r.max():.4f}")

# ── timeout designs ───────────────────────────────────────────────────────────

print()
to = df22[df22['exit_code'] == 143]
print(f"--- KV260 timeout designs (exit_code 143): {len(to)} total ---")
print(f"  ZCU104 2022.2 Synth_OK: {(to['Synth_OK']==True).sum()} / {len(to)}")

print(f"\nOutputs: {OUT22_CSV}")
if df3 is not None:
    print(f"         {OUT3VER_CSV}")
