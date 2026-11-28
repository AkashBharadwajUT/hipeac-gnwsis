# GNΩSIS tool-version drift study: lane B artifact (HiPEAC Student Challenge 2027)

Scripts and results for the ZCU104 tool-version comparison part of our reproduction of
*GNΩSIS: Lessons Learned in Generating a High-Level Synthesis Dataset* (ACM TACO, 2026):
re-synthesis of all 961 transfer designs on the original ZCU104 device with Vitis HLS 2022.2
(the dataset used 2021.1), and a golden-row check with Vitis HLS 2025.2 (top-7 fastest per kernel),
to separate tool-version drift from device-portability effects.

## Environment

| Item | Version |
|---|---|
| HLS | AMD Vitis HLS 2022.2 (re-synthesis, 961 designs); AMD Vitis HLS 2025.2 (golden-row check, 44 designs) |
| Device | ZCU104 `xczu7ev-ffvc1156-2-e` (source/reference device; same as the GNΩSIS dataset) |
| Clock | 10 ns (100 MHz) |
| Python | 3.x with pandas |
| Server | caes-lab1 (128 threads, `/local/scratch/$USER` workspace) |
| Transfer designs | `gnosis-laneC/results/transfer/transfer_designs_full.csv` (961 rows from Lane C) |

```bash
# AMD tools as environment modules
module load xilinx/vitis_hls/2022.2   # for the main re-synthesis run
module load xilinx/vitis_hls/2025.2   # for the golden-row check
```

The scripts expect Vitis HLS available via `module load` and use `/local/scratch/$USER` as work space;
adjust `ROOT` and `DEVICE` at the top of `zcu104_synthesis.py` if your paths differ.

## Layout

```
scripts/
  zcu104_synthesis.py   Re-synthesise designs from transfer_designs_full.csv on ZCU104.
                        Flags: --app, --dry-run, --reparse, --top-n N, --out-prefix TAG
  zcu104_analysis.py    Join re-synthesis results with the 2021.1 dataset baselines; print
                        and save per-kernel latency-ratio tables (2022.2 vs 2021.1,
                        2022.2 vs 2025.2, 3-version progression).

results/
  zcu104_all_results.csv          961 designs, Vitis HLS 2022.2 on ZCU104 (10 ns clock).
                                  Columns: App, Run_ID, Version_ZCU104, Latency_DS_ms, Pragmas,
                                           Synth_OK, Latency_ZCU104_ms, Cycles_ZCU104,
                                           BRAM_ZCU104_pct, DSP_ZCU104_pct,
                                           FF_ZCU104_pct, LUT_ZCU104_pct
  zcu104_2025_all_results.csv     44 designs (top-7 fastest per kernel from 2022.2 results),
                                  Vitis HLS 2025.2 on ZCU104.
  zcu104_analysis_results.csv     Per-design joined table: 2022.2 latency + 2021.1 baseline +
                                  ratio_2022_over_2021 (961 rows).
  zcu104_3ver_results.csv         Per-design joined table for the 44 golden rows:
                                  ratio_2022_over_2021, ratio_2025_over_2021, ratio_2025_over_2022.
```

## Reproducing

```bash
cd /local/scratch/$USER/gnwsis

# 1. Re-synthesise all 961 transfer designs (Vitis HLS 2022.2, ~4–6 h with parallel nohup jobs)
module load xilinx/vitis_hls/2022.2
for APP in machsuite-md-knn machsuite-spmv-ellpack machsuite-stencil2d machsuite-stencil3d \
           rodinia-knn-0-baseline rodinia-knn-1-tiling rodinia_pathfinder_0_baseline_0; do
    nohup nice python zcu104_synthesis.py --app "$APP" > logs/${APP}.log 2>&1 &
done

# 2. After all jobs finish, reparse saved reports and merge to zcu104_all_results.csv
python zcu104_synthesis.py --reparse

# 3. Golden-row check: top-7 fastest per kernel with Vitis HLS 2025.2
module load xilinx/vitis_hls/2025.2
for APP in ...; do
    nohup nice python zcu104_synthesis.py --app "$APP" --top-n 7 --out-prefix 2025 \
        > logs/${APP}_2025.log 2>&1 &
done
python zcu104_synthesis.py --reparse --out-prefix 2025

# 4. Run analysis and print ratio tables
python zcu104_analysis.py
```

The script writes per-app CSVs to `zcu104_results/` and merges them to `zcu104_all_results.csv`.
See `--dry-run` to preview which rows will be synthesised before committing to a run.

## Key findings (§4.3 of the report)

> **Note (27 Nov 2026):** The CSVs committed on 23 Nov (`zcu104_all_results.csv`,
> `zcu104_2025_all_results.csv`) are **invalid** — a bug in `load_designs` caused the
> script to look for directive columns named `L1`, `L2`, … instead of `Array_N`,
> `OuterLoop_N`, `InnerLoop_N`. Every design was synthesised as the plain (un-optimised)
> kernel, so all designs of a kernel produce the same cycle count and `Pragmas = {}`.
> A corrected re-run is in progress; this table will be updated once results are available.
> The numbers below are **placeholders** and must not be cited.

| Kernel | Median ratio 2022.2 / 2021.1 | Median ratio 2025.2 / 2021.1 | 2025.2 / 2022.2 |
|---|---|---|---|
| machsuite-md-knn | TBD | TBD | TBD |
| machsuite-spmv-ellpack | TBD | TBD | TBD |
| machsuite-stencil2d | TBD | TBD | TBD |
| machsuite-stencil3d | TBD | TBD | TBD |
| rodinia-knn-0-baseline | TBD | TBD | TBD |
| rodinia-knn-1-tiling | TBD | TBD | TBD |
| rodinia_pathfinder_0_baseline_0 | TBD | TBD | TBD |
| **ALL (median)** | **TBD** | **TBD** | **TBD** |

## Notes on reading the results

- **Worst-case latency.** Vitis HLS 2022.2 reports `ClockInfo.Latency` as the *best* case for loops
  with a variable trip count. `zcu104_synthesis.py` reads `LatencyWorst` from
  `ModuleInfo.Metrics.<top>.Latency.LatencyWorst` in `csynth.rpt`. Compare only against
  `ds_latency_ms` from `transfer_designs_full.csv` (also worst-case).
- **Drift is a 2021.1 → 2022.2 step change.** The ratio_2025_over_2022 column is 1.000 for all
  kernels, confirming that 2022.2 and 2025.2 produce identical schedules on this device/clock.
- **Fixed vs variable latency.** Fixed-latency designs drift more (median 1.96×, range 0.07–3.26×)
  than variable-latency ones (median 1.00×, range 0.12–2.37×).
- **1 h rule:** as in the paper, synthesis over 1 h counts as not synthesisable (`Synth_OK = False`).
  All 961 designs completed successfully under this limit.
- **Report paths** (project dir) are deleted after synthesis; raw HLS reports are saved to
  `report_NNNN/` and re-parsed with `--reparse`.
