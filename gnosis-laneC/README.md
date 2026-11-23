# GNΩSIS on the Kria KV260: lane C artifact (HiPEAC Student Challenge 2027)

Scripts, results and figures for the KV260 part of our reproduction of
*GNΩSIS: Lessons Learned in Generating a High-Level Synthesis Dataset* (ACM TACO, 2026):
a design-space exploration on the KV260 with the original GenHLSOptimizer, the transfer of the ZCU104 dataset's
Pareto designs to the KV260, worst-case re-scoring, out-of-context place-and-route at 100 and 300 MHz, and on-board
measurements with PYNQ.

## Environment

| Item | Version |
|---|---|
| HLS / implementation | AMD Vitis HLS 2022.2, Vivado 2022.2 (the dataset used Vitis 2021.1, which was not available) |
| Devices | KV260 `xck26-sfvc784-2LV-c` (new runs); ZCU104 `xczu7ev-ffvc1156-2-e` (dataset, and a few checks) |
| Clock | 10 ns (100 MHz); 3.33 ns (300 MHz) for place-and-route and board builds |
| Python | 3.6.8 with `requirements.txt` (pymoo 0.5.0 for GenHLSOptimizer) |
| Board | Kria KV260, PYNQ 3.0.1 (Ubuntu 22.04) |
| Upstream | [GNWSIS](https://github.com/aferikoglou/GNWSIS) @ `8888db17a6f344307de0a27cb431582f3f6fd093`, [GenHLSOptimizer](https://github.com/aferikoglou/GenHLSOptimizer) @ `7acb82f43f1bd94cf502e4fb384ca6c648e5a11c` (unmodified; `ga_kv260.sh` patches a private copy per run) |

```bash
python3.6 -m venv venv36 && venv36/bin/pip install -r requirements.txt
git clone https://github.com/aferikoglou/GNWSIS && git -C GNWSIS checkout 8888db17a6f344307de0a27cb431582f3f6fd093
git clone https://github.com/aferikoglou/GenHLSOptimizer && git -C GenHLSOptimizer checkout 7acb82f43f1bd94cf502e4fb384ca6c648e5a11c
```
The scripts expect the AMD tools as environment modules (`module load xilinx/vitis_hls/2022.2`,
`xilinx/vivado/2022.2`) and use `~/gnosis/` as base folder and `/local/scratch/$USER` as work space; adjust paths at
the top of each script if yours differ.

## Layout

```
scripts/hls/       smoke_hls.sh        one dataset row / GA design through Vitis HLS as GenHLSOptimizer builds it
                   server_facts.sh     collect server facts (CPU, RAM, tools, parts, licences)
scripts/ga/        ga_kv260.sh         GenHLSOptimizer (NSGA-II, pop 40, 24 gens, seed, 1 h timeout) on a part/clock
                   ga_export.py        GA SQLite database -> CSV (decoded directives, latency, util, feasible, Pareto)
                   ga_rescore.sh       re-synthesise feasible GA designs, record the worst-case latency
                   ga_rescore_summary.py
scripts/analysis/  transfer_summary.py ZCU104 Pareto designs re-synthesised on KV260 -> per-design + per-kernel CSV
                   add_run_info.py     add tool/part/clock, synthesis time, peak RAM, exit code -> *_full.csv
                   make_figures.py     figures/ and tables from the result files
scripts/board/     board_build.sh, build_overlay.tcl   HLS IP -> Vivado block design -> bitstream (PYNQ overlay)
                   knn_board.py        PYNQ driver for the KNN (tiled) overlays (timing + NumPy check)
scripts/pnr/       pnr_select.py, pnr_one.sh, pnr_collect.py   place-and-route of KV260 Pareto designs
scripts/ops/       sitrep.sh           one-screen status of the runs
results/ga/        KV260 GA designs per kernel (*_g24.csv raw, *_full.csv with run info); seeds 7/123 for 3 kernels
results/ga_databases/  GenHLSOptimizer SQLite databases
results/ga_rescore/    worst-case re-scoring: <app>_rescored(_full).csv and one log per design
results/transfer/      650 + 311 ZCU104 Pareto designs on KV260: transfer_designs(_full).csv, summary, logs, rows.txt
results/board/         board_results_v3.csv (all board runs), timing/util reports, HLS summaries, run_knn.ipynb
results/pnr/           pnr_results.csv, per-run logs, export reports, reports/ (Vivado timing/util, csynth)
results/findings/      finding_470 (KNN (T) outer-loop pipeline), l9_test, pf_zcu104_check.txt
figures/           fig_board, fig_ga_best_vs_true, fig_transfer_ratio (+ CSV tables)
logs/              GA run logs (v2_ = first runs, v3_/v5_ = resumed and later runs), re-scoring and board build logs
```

## Reproducing (in order)

```bash
S=scripts; G=~/gnosis/GNWSIS; O=~/gnosis/GenHLSOptimizer
# 1. one design, e.g. the dataset baseline of a kernel on the KV260 part
APP=rodinia-knn-1-tiling ROW=baseline bash $S/hls/smoke_hls.sh $G 2022.2 xck26-sfvc784-2LV-c 10
# 2. KV260 exploration for one kernel (threads, gens, seed optional)
bash $S/ga/ga_kv260.sh $G $O machsuite-md-knn 6 24 42
# 3. worst-case re-scoring of its feasible designs
bash $S/ga/ga_rescore.sh $G machsuite-md-knn 8
# 4. transfer: list ZCU104 Pareto rows in rows.txt ("<app> <Version>"), then per row
APP=<app> ROW=<workload_N.cpp> bash $S/hls/smoke_hls.sh $G 2022.2 xck26-sfvc784-2LV-c 10 > <app>_<row>.txt
python3 $S/analysis/transfer_summary.py <transfer-dir> <results-dir>
# 5. place-and-route selection and runs, then collection
python3 $S/pnr/pnr_select.py ~/gnosis
bash $S/pnr/pnr_one.sh <app> <ga_row> <tag> <clock-ns>
python3 $S/pnr/pnr_collect.py ~/gnosis
# 6. board: bitstream, then on the KV260 (PYNQ): %run knn_board.py; run_knn("<name>.bit", mhz=100)
bash $S/board/board_build.sh $G rodinia-knn-1-tiling baseline 100
# 7. run info and figures
python3 $S/analysis/add_run_info.py ~/gnosis; python3 $S/analysis/make_figures.py ~/gnosis
```
Each script's header documents its options (e.g. `GA_CSV`, `ROW=ga_<index>`, `REF_CLK`, `KEEP`, `RESUME`).

## Notes on reading the results

- **Worst-case latency.** Vitis HLS 2022.2 reports `ClockInfo.Latency` as the *best* case for loops with a variable
  trip count; the GA optimised it. Use `worst_latency_ms` in `results/ga_rescore/`. Some K-means (T) designs have an
  undefined (`undef`) latency and no worst case.
- **Feasible** means every resource ≤ 100 % (GenHLSOptimizer's own statistics count 101 % as feasible).
- **1 h rule:** as in the paper, a run over 1 h counts as not synthesisable (`exit_code` 124/143 in `*_full.csv`).
- **Effective latency** in `pnr_results.csv`: cycles × max(target period, achieved period).
- Paths in logs were anonymised (user name replaced by `user`).
- Bitstreams (`.bit`/`.hwh`) are not in the repository (large binaries); they can be rebuilt with `board_build.sh`.
