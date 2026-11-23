# knn_board.py - run a Rodinia KNN overlay on the KV260 with PYNQ; paste into a Jupyter cell or %run it.
# Upload <name>.bit and <name>.hwh (same base name) next to this file first.
#   result = run_knn("rodinia_knn_1_tiling_baseline_100.bit", reps=30)
import time, csv, os
import numpy as np
from pynq import Overlay, allocate, Clocks

NUM_FEATURE = 2                 # from knn.h
NUM_PT = 1024 * 1024            # NUM_PT_IN_SEARCHSPACE

def _set_ptr(ip, name, addr):
    rm = ip.register_map
    if hasattr(rm, name + "_1"):            # 64-bit pointer split into two 32-bit registers
        setattr(rm, name + "_1", addr & 0xFFFFFFFF)
        setattr(rm, name + "_2", addr >> 32)
    else:
        setattr(rm, name, addr)

def run_knn(bitfile, reps=30, mhz=None, out_csv="board_results.csv"):
    ol = Overlay(bitfile)
    if mhz:
        Clocks.fclk0_mhz = mhz
    ip = ol.kernel_0
    q = allocate((NUM_FEATURE,), dtype=np.float32)
    s = allocate((NUM_PT * NUM_FEATURE,), dtype=np.float32)
    d = allocate((NUM_PT,), dtype=np.float32)
    rng = np.random.default_rng(0)
    q[:] = rng.random(NUM_FEATURE, dtype=np.float32)
    s[:] = rng.random(NUM_PT * NUM_FEATURE, dtype=np.float32)
    q.sync_to_device(); s.sync_to_device()
    _set_ptr(ip, "inputQuery", q.device_address)
    _set_ptr(ip, "searchSpace", s.device_address)
    _set_ptr(ip, "distance", d.device_address)
    ref = ((s.reshape(NUM_PT, NUM_FEATURE) - q) ** 2).sum(axis=1)
    times = []
    for _ in range(reps):
        d[:] = 0; d.sync_to_device()
        t0 = time.perf_counter()
        ip.write(0x00, 0x01)                  # ap_start
        while (ip.read(0x00) & 0x02) == 0:    # wait for ap_done
            pass
        times.append((time.perf_counter() - t0) * 1e3)
    d.sync_from_device()
    ok = bool(np.allclose(d, ref, rtol=1e-4, atol=1e-5))
    res = {"bitfile": os.path.basename(bitfile), "fclk0_mhz": round(Clocks.fclk0_mhz, 2), "reps": reps,
           "median_ms": float(np.median(times)), "min_ms": float(np.min(times)), "max_ms": float(np.max(times)),
           "correct": ok}
    new = not os.path.exists(out_csv)
    with open(out_csv, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(res)); new and w.writeheader(); w.writerow(res)
    print(res)
    for b in (q, s, d):
        b.freebuffer()
    return res
