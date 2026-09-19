#!/usr/bin/env python3
"""Regenerate the state fixtures. Run after any change to the state contract.

    python3 tests/make_fixtures.py

  fixtures/stats-golden.json   the whole journal fixture replayed at a pinned
                               clock -- the frozen shape of stats.json, diffed
                               by test_parsers.TestGoldenContract
  fixtures/states/*.json       one hand-tuned state per bar string, for the
                               display suite (`statePath` setting) and for
                               tests/test_format.mjs

Everything here is derived from real lines: the golden is the fixture journal,
and each state file is the golden with the fields that state implies changed.
"""

import copy
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
COLLECTOR = os.path.join(ROOT, "collect", "omarchy-ninfer-stats-update")
SAMPLE = os.path.join(HERE, "fixtures", "sample.txt")
GOLDEN = os.path.join(HERE, "fixtures", "stats-golden.json")
STATES = os.path.join(HERE, "fixtures", "states")

import importlib.machinery
import importlib.util
_spec = importlib.util.spec_from_loader(
    "ninfer_collect",
    importlib.machinery.SourceFileLoader("ninfer_collect", COLLECTOR))
collect = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(collect)

# Pinned just past the fixture's last line, so the 60 s history window has
# real traffic in it instead of twelve zeroes.
NOW_MS = 1789036660000


def replay():
    with open(SAMPLE, "rb") as handle:
        completed = subprocess.run(
            [sys.executable, COLLECTOR, "--dry-run", "--stdin", "--now-ms", str(NOW_MS)],
            stdin=handle, capture_output=True, check=True)
    stats = json.loads(completed.stdout)
    # Wall-clock cost of the run: real, and different every time.
    stats["debug"].pop("collector_ms", None)
    return stats


def write(path, stats):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(stats, handle, indent=2)
        handle.write("\n")


def quiet(stats):
    stats["server"].update(active_requests=0, queued_requests=0, decode_tok_s=0.0,
                           prefill_tok_s=None, avg_batch=None, host_pct=None,
                           host_work_ms=None)
    stats["history"]["decode_tokens"] = [0] * 12
    stats["history"]["decode_tok_s"] = [0.0] * 12


def gpu_live(stats):
    stats["gpu"].update(util_pct=42, mem_mib=29956, mem_total_mib=32607,
                        process_mem_mib=28960, stale=False, temp_c=47,
                        watts=186.4, name="NVIDIA GeForce RTX 5090",
                        history=[8, 11, 39, 44, 41, 45, 43, 40, 44, 42, 41, 42])


def populated(stats):
    """The replay never probes /health's neighbour or the card, so every
    variant gets the values its own state implies: a live engine answers
    /health, the card reports itself, and the boot flags are a real serve
    line's."""
    state = stats["server"]["state"]
    if state == "down":
        stats["gpu"].update(stale=True, temp_c=None, watts=None, name=None,
                            process_mem_mib=None)
    stats["config"] = {
        "model": "qwen3.8-27b",
        "weight_profile": "nvfp4",
        "max_context_tokens": 131072,
        "kv_dtype": "fp8",
        "kv_capacity_tokens": 181568,
        "max_concurrency": 2,
        "mtp_draft_tokens": 7,
        "lm_head_draft": True,
        "gpu": "NVIDIA GeForce RTX 5090",
        "endpoint": "127.0.0.1:8080",
        "weights_gib": 21.4,
        "kv_gib": 7.31,
        "host_kv_gib": 32.0,
        "host_state_gib": 5.84,
    }


def down(stats):
    quiet(stats)
    stats["server"].update(state="down", healthy=False, active_state="inactive")
    stats["gpu"].update(stale=True, process_mem_mib=None)


def starting(stats):
    quiet(stats)
    stats["server"].update(state="starting", healthy=False, active_state="activating",
                           model=None)
    stats["gpu"].update(util_pct=0, mem_mib=1204, mem_total_mib=32607,
                        process_mem_mib=None, stale=False)


def stopping(stats):
    quiet(stats)
    stats["server"].update(state="stopping", healthy=True, active_state="deactivating")


def queued(stats):
    # A prefill-bound interval: requests waiting, and no decode number at all.
    # That absence is the state, not a data gap.
    gpu_live(stats)
    stats["server"].update(state="queued", active_requests=1, queued_requests=2,
                           decode_tok_s=0.0, prefill_tok_s=9310.0, avg_batch=None,
                           host_pct=2.9, host_work_ms=147.0)


def with_runs(stats):
    # The Telemetry tab's table: previously settled runs, newest first. The
    # replay ends mid-run (nothing has settled at the pinned clock), so the
    # ring is populated the way settled bursts would populate it --
    # projected rows with the age label computed at the fixture's own
    # clock, exactly as the collector writes them. Durations span the
    # session, not the burst: the last row is a merged multi-minute one.
    now = stats["generated_at_ms"]
    # (age, duration, tok_s_avg, tok_s_max, acc_avg, acc_max, temp_avg,
    #  temp_max, wait_avg, wait_max). wait_avg is the mean of the requests
    # that crossed the 100 ms noise floor: row 1 queued two (1100 of 1800),
    # row 2 queued none, row 3 queued one (avg == max).
    rows = [
        (120000, 48000, 187.2, 210.4, 64.1, 71.3, 48.2, 52.9, 1100.0, 1800.0),
        (720000, 300000, 176.9, 198.0, 61.4, 66.8, 46.1, 49.3, None, None),
        (5400000, 920000, 152.6, 171.2, 58.0, 63.4, 44.0, 47.8, 420.0, 420.0),
    ]
    stats["runs"] = [
        {"completed_at_ms": now - age,
         "duration_ms": duration,
         "when_label": collect.age_label(age),
         "tok_s_avg": avg, "tok_s_max": peak,
         "accept_pct_avg": aavg, "accept_pct_max": amax,
         "temp_avg_c": tavg, "temp_max_c": tmax,
         "queue_wait_ms_avg": wavg, "queue_wait_ms_max": wmax}
        for age, duration, avg, peak, aavg, amax, tavg, tmax, wavg, wmax in rows
    ]


def busy(stats):
    gpu_live(stats)
    with_runs(stats)
    stats["server"].update(state="busy", active_requests=2, queued_requests=0,
                           decode_tok_s=193.4, prefill_tok_s=8330.0, avg_batch=1.0,
                           host_pct=2.9, host_work_ms=147.0)


def busy_single(stats):
    busy(stats)
    stats["server"]["active_requests"] = 1


def idle(stats):
    quiet(stats)
    gpu_live(stats)
    stats["server"].update(state="idle", active_state="active")
    stats["gpu"].update(util_pct=3, history=[41, 38, 12, 4, 3, 2, 3, 3, 2, 3, 3, 3])


def unhealthy(stats):
    # The unit is up and the journal is flowing, but /health stopped
    # answering. The state text does not lie about that; the tint carries it.
    idle(stats)
    stats["debug"]["health_error"] = "<urlopen error [Errno 111] Connection refused>"


def accept_degraded(stats):
    busy(stats)
    stats["requests"]["accept_alarm"] = True
    stats["requests"]["accept_pct"] = 18.2


def gpu_stale(stats):
    busy(stats)
    stats["gpu"].update(stale=True, history=[])
    stats["debug"]["gpu_error"] = "nvidia-smi: command not found"


def journal_stale(stats):
    idle(stats)
    stats["server"].update(stale=True, journal_stale=True)
    stats["debug"]["journal_error"] = "Failed to seek to cursor"


VARIANTS = [
    ("down", down), ("starting", starting), ("stopping", stopping),
    ("queued", queued), ("busy", busy), ("busy-single", busy_single),
    ("idle", idle), ("unhealthy", unhealthy), ("accept-degraded", accept_degraded),
    ("gpu-stale", gpu_stale), ("journal-stale", journal_stale),
    # Colour states: each Telemetry tab threshold, exercised once.
    ("temp-danger", lambda s: (busy(s), s["gpu"].update(temp_c=88, watts=402.1))),
]

# The golden comes from a replay, where /health is never probed and so reads
# false. A display fixture says what its own state implies instead.
UNHEALTHY = {"down", "starting", "unhealthy"}


def main():
    golden = replay()
    write(GOLDEN, golden)
    os.makedirs(STATES, exist_ok=True)
    for name, mutate in VARIANTS:
        stats = copy.deepcopy(golden)
        stats["server"]["healthy"] = name not in UNHEALTHY
        mutate(stats)
        populated(stats)
        write(os.path.join(STATES, name + ".json"), stats)
    print("wrote %s and %d state fixtures" % (os.path.relpath(GOLDEN, ROOT), len(VARIANTS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
