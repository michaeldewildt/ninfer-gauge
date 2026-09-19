#!/usr/bin/env python3
"""Line-shape tests for the collector, against real journal lines.

Every sample here was copied out of tests/fixtures/sample.txt -- the shapes
the server actually logs, prefix included. The rule under test throughout:
a field that is absent is null (or zero for `queue`), never a parse failure.
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
COLLECTOR = os.path.join(HERE, "..", "collect", "omarchy-ninfer-stats-update")
FIXTURE = os.path.join(HERE, "fixtures", "sample.txt")
GOLDEN = os.path.join(HERE, "fixtures", "stats-golden.json")

sys.path.insert(0, HERE)
import make_fixtures   # noqa: E402  (the golden's generator, reused below)

spec = importlib.util.spec_from_loader(
    "ninfer_collect",
    importlib.machinery.SourceFileLoader("ninfer_collect", COLLECTOR))
collect = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collect)


class TestScalars(unittest.TestCase):
    def test_thousands_separators(self):
        self.assertEqual(collect.to_int("81,799"), 81799)

    def test_k_suffix_on_rates(self):
        self.assertEqual(collect.to_rate("3.05", "k"), 3050.0)
        self.assertEqual(collect.to_rate("159.7", ""), 159.7)

    def test_durations_normalise_to_ms(self):
        self.assertEqual(collect.to_ms("621 ms"), 621.0)
        self.assertEqual(collect.to_ms("5.3s"), 5300.0)
        self.assertEqual(collect.to_ms("667 us"), 0.667)

    def test_prefix_split(self):
        level, body = collect.split_prefix(
            "2026-09-10 20:19:45.660  INFO  req#1044 done | openai-chat")
        self.assertEqual(level, "INFO")
        self.assertTrue(body.startswith("req#1044 done"))

    def test_line_without_prefix_survives(self):
        level, body = collect.split_prefix("Started NInfer inference server.")
        self.assertIsNone(level)
        self.assertEqual(body, "Started NInfer inference server.")


class TestDoneLines(unittest.TestCase):
    LINE = ("2026-09-10 20:19:45.660  INFO  req#1044 done | openai-chat | "
            "tool calls 1 | prompt 81,799 | output 786 | "
            "cache 80,762 (98.7%, private endpoint) | TTFT 621 ms | total 5.5s | "
            "queue 262 ms | prefill 3.05k tok/s | decode 159.7 tok/s | "
            "dflash2 accepted 528/1,806 (29.2%)")

    def test_full_line(self):
        event = collect.parse_message(self.LINE)
        self.assertEqual(event["kind"], "done")
        self.assertEqual(event["id"], 1044)
        self.assertEqual(event["endpoint"], "openai-chat")
        self.assertEqual(event["outcome"], "tool calls 1")
        self.assertEqual(event["prompt_tokens"], 81799)
        self.assertEqual(event["output_tokens"], 786)
        self.assertEqual(event["cache_pct"], 98.7)
        self.assertEqual(event["ttft_ms"], 621.0)
        self.assertEqual(event["total_ms"], 5500.0)
        self.assertEqual(event["queue_ms"], 262.0)
        self.assertEqual(event["prefill_tok_s"], 3050.0)
        self.assertEqual(event["decode_tok_s"], 159.7)
        self.assertEqual(event["spec_backend"], "dflash2")
        self.assertEqual(event["spec_accepted"], 528)
        self.assertEqual(event["spec_drafts"], 1806)
        self.assertEqual(event["spec_pct"], 29.2)

    def test_absent_queue_reads_as_zero(self):
        line = self.LINE.replace(" queue 262 ms |", "")
        self.assertEqual(collect.parse_message(line)["queue_ms"], 0.0)

    def test_missing_spec_group_is_null_not_a_failure(self):
        line = self.LINE.split(" | dflash2")[0]
        event = collect.parse_message(line)
        self.assertEqual(event["kind"], "done")
        self.assertIsNone(event["spec_backend"])
        self.assertIsNone(event["spec_pct"])

    def test_response_replay_cache_variant(self):
        line = self.LINE.replace("private endpoint", "response replay")
        self.assertEqual(collect.parse_message(line)["cache_pct"], 98.7)

    def test_free_form_outcomes(self):
        for outcome in ("stop token", "tool calls 12", "output limit", "cancelled"):
            line = self.LINE.replace("tool calls 1 |", outcome + " |")
            self.assertEqual(collect.parse_message(line)["outcome"], outcome)


class TestThroughputLines(unittest.TestCase):
    def test_prefill_and_decode(self):
        event = collect.parse_message(
            "2026-09-10 20:19:46.086  INFO  throughput | 5.0s | "
            "prefill 87.2 tok/s (436 tok) | decode 154.6 tok/s (773 tok) | "
            "running 1 (decode-ready 1) | waiting 1 | batch 1.00 | host 0.8% (40.6 ms)")
        self.assertEqual(event["kind"], "throughput")
        self.assertEqual(event["interval_sec"], 5.0)
        self.assertEqual(event["prefill_tokens"], 436)
        self.assertEqual(event["decode_tokens"], 773)
        self.assertEqual(event["decode_tok_s"], 154.6)
        self.assertEqual(event["running"], 1)
        self.assertEqual(event["waiting"], 1)
        self.assertEqual(event["batch"], 1.0)
        self.assertEqual(event["host_pct"], 0.8)
        self.assertEqual(event["host_work_ms"], 40.6)

    def test_decode_only_interval(self):
        event = collect.parse_message(
            "2026-09-10 20:20:16.086  INFO  throughput | 5.0s | "
            "decode 146.8 tok/s (734 tok) | running 1 (decode-ready 1) | "
            "waiting 1 | batch 1.00 | host 0.1% (3.29 ms)")
        self.assertIsNone(event["prefill_tok_s"])
        self.assertIsNone(event["prefill_tokens"])
        self.assertEqual(event["decode_tokens"], 734)

    def test_prefill_bound_interval_has_no_decode(self):
        event = collect.parse_message(
            "2026-09-10 20:21:16.086  INFO  throughput | 5.0s | "
            "prefill 9.31k tok/s (46,550 tok) | running 1 (prefill 1) | "
            "waiting 2 | host 2.9% (147 ms)")
        self.assertIsNone(event["decode_tok_s"])
        self.assertIsNone(event["decode_tokens"])
        self.assertEqual(event["prefill_tok_s"], 9310.0)
        self.assertEqual(event["prefill_tokens"], 46550)
        self.assertEqual(event["waiting"], 2)
        self.assertIsNone(event["batch"])

    def test_microsecond_host_work(self):
        event = collect.parse_message(
            "2026-09-10 20:22:16.086  INFO  throughput | 5.0s | "
            "decode 95.8 tok/s (479 tok) | running 1 | batch 1.00 | host 0.0% (667 us)")
        self.assertEqual(event["host_work_ms"], 0.667)

    def test_absent_waiting_reads_as_zero(self):
        event = collect.parse_message(
            "2026-09-10 20:23:16.086  INFO  throughput | 5.0s | "
            "decode 95.8 tok/s (479 tok) | running 1 | batch 1.00 | host 0.0% (667 us)")
        self.assertEqual(event["waiting"], 0)


class TestTerminalLines(unittest.TestCase):
    def test_rejected_is_a_client_error(self):
        event = collect.parse_message(
            "2026-09-09 13:19:09.736  INFO  req#17 rejected during prepare | "
            "openai-chat stream | HTTP 400 | vision disabled | messages 2 | "
            "media 1 | tools 4")
        self.assertEqual(event["kind"], "terminal")
        self.assertEqual(event["id"], 17)
        self.assertEqual(event["verb"], "rejected")
        self.assertEqual(event["phase"], "prepare")
        self.assertEqual(event["endpoint"], "openai-chat")
        self.assertEqual(event["http"], 400)
        self.assertEqual(event["reason"], "vision disabled")
        self.assertEqual(collect.classify_http(event["http"]), "rejected")

    def test_5xx_is_a_server_failure(self):
        event = collect.parse_message(
            "2026-09-09 13:50:35.070  WARN  req#107 failed during generation | "
            "openai-chat | HTTP 503 | request queue timeout")
        self.assertEqual(event["level"], "WARN")
        self.assertEqual(event["http"], 503)
        self.assertEqual(event["reason"], "request queue timeout")
        self.assertEqual(collect.classify_http(503), "failed")

    def test_499_is_a_client_disconnect_not_an_error(self):
        event = collect.parse_message(
            "2026-09-09 14:04:24.321  INFO  req#180 cancelled during transport | "
            "openai-chat | HTTP 499 | client disconnected")
        self.assertEqual(collect.classify_http(event["http"]), "client_disconnects")

    def test_endpointless_terminal(self):
        event = collect.parse_message(
            "2026-09-09 14:04:26.937  INFO  req#183 response failed during transport | "
            "HTTP 499 | client disconnected")
        self.assertEqual(event["id"], 183)
        self.assertEqual(event["verb"], "response failed")
        self.assertIsNone(event["endpoint"])
        self.assertEqual(event["http"], 499)


class TestStartedLines(unittest.TestCase):
    def test_stream_flag(self):
        event = collect.parse_message(
            "2026-09-10 20:19:45.697  INFO  req#1046 started | openai-chat stream | "
            "169 messages | max output 32,768 | thinking medium | tools 10 | "
            "preserve thinking")
        self.assertEqual(event["kind"], "started")
        self.assertEqual(event["id"], 1046)
        self.assertEqual(event["endpoint"], "openai-chat")
        self.assertTrue(event["stream"])

    def test_non_stream(self):
        event = collect.parse_message(
            "2026-09-09 12:40:00.000  INFO  req#2 started | anthropic non-stream | "
            "1 message | max output 512 | thinking xhigh | preserve thinking")
        self.assertEqual(event["endpoint"], "anthropic")
        self.assertFalse(event["stream"])


class TestDerivedState(unittest.TestCase):
    def test_precedence(self):
        self.assertEqual(collect.derive_state("inactive", False, 0, 0), "down")
        self.assertEqual(collect.derive_state("failed", False, 0, 0), "down")
        self.assertEqual(collect.derive_state("activating", False, 0, 0), "starting")
        self.assertEqual(collect.derive_state("deactivating", True, 0, 0), "stopping")
        self.assertEqual(collect.derive_state("active", True, 1, 2), "queued")
        self.assertEqual(collect.derive_state("active", True, 1, 0), "busy")
        self.assertEqual(collect.derive_state("active", True, 0, 0), "idle")

    def test_down_needs_both_signals(self):
        # A stopped unit whose port still answers is not ours to call dead.
        self.assertEqual(collect.derive_state("inactive", True, 0, 0), "idle")
        # A hung server stays 'active': healthy=false carries that news.
        self.assertEqual(collect.derive_state("active", False, 0, 0), "idle")

    def test_accept_alarm_needs_five_low_samples(self):
        self.assertTrue(collect.accept_alarm([20.0] * 5))
        self.assertFalse(collect.accept_alarm([20.0] * 4 + [40.0]))
        self.assertFalse(collect.accept_alarm([10.0] * 4))
        self.assertFalse(collect.accept_alarm([]))


class TestHistoryBuckets(unittest.TestCase):
    def test_idle_gaps_are_zero_not_null(self):
        now = 1768000000000
        current = now // collect.BUCKET_MS
        buckets = {str(current): 500, str(current - 3): 250}
        rates = collect.render_history(buckets, now)["decode_tok_s"]
        self.assertEqual(len(rates), collect.HISTORY_BUCKETS)
        self.assertEqual(rates[-1], 100.0)   # 500 tok in a 5 s bucket
        self.assertEqual(rates[-4], 50.0)
        self.assertEqual(rates[-2], 0.0)

    def test_the_rate_is_the_newest_interval_only(self):
        """The bar's number is the last measurement, not a smoothed average.

        Distinct values per bucket on purpose: an equal-valued ladder gives
        the same answer for every window width, so it cannot tell them apart.
        """
        now = 1768000000000
        current = now // collect.BUCKET_MS
        buckets = {str(current): 600, str(current - 1): 100, str(current - 2): 100}
        self.assertEqual(collect.window_decode_rate(buckets, now), 120.0)
        # Idle now, however busy the previous minute was: a true zero.
        quiet = {str(current - 1): 900, str(current - 2): 900}
        self.assertEqual(collect.window_decode_rate(quiet, now), 0.0)

    def test_a_prefill_bound_bucket_reads_zero_not_missing(self):
        # The bar shows the word "prefill" for this, never "0.0 t/s" -- but the
        # collector's job is to report the zero honestly.
        now = 1768000000000
        self.assertEqual(collect.window_decode_rate({}, now), 0.0)


class TestTaskFold(unittest.TestCase):
    """A task is a run of consecutive requests, and the verdict -- not the
    lines -- ends it: the verdict has to STAY settled for RUN_SETTLE_MS
    (60 s), so a burst that re-bursts inside the grace is one run; the
    starting/stopping/down verdicts settle it on the spot.

    The fold is what the Telemetry tab's live tiles read, so its arithmetic
    is the contract: effective rate is token-weighted, accept is pooled,
    and the "finished" age comes from the lines' own timestamps.
    """

    T0 = 1768000000000

    def entry(self, at_ms, message):
        return {"cursor": None, "at_ms": at_ms, "message": message,
                "priority": None, "cmdline": ""}

    def started(self, req, at_ms):
        return self.entry(at_ms, "req#%d started | openai-chat stream | "
                                 "42 messages | thinking medium | tools 10" % req)

    def done(self, req, at_ms, outcome="stop token", rate=100):
        return self.entry(at_ms, "req#%d done | openai-chat | %s | prompt 100 | "
                          "output 100 | cache 90 (90.0%%) | TTFT 50 ms | total 1s | "
                          "queue 10 ms | prefill 100 tok/s | decode %s tok/s | "
                          "dflash2 accepted 5/10 (50.0%%)" % (req, outcome, rate))

    def fold(self, *entries):
        cursor = collect.blank_cursor()
        collect.apply_entries(list(entries), cursor, reset=False)
        return cursor

    def test_started_opens_and_done_folds(self):
        cursor = self.fold(self.started(1, self.T0),
                          self.done(1, self.T0 + 5000),
                          self.started(2, self.T0 + 10000),
                          self.done(2, self.T0 + 20000),
                          self.started(3, self.T0 + 30000),
                          self.done(3, self.T0 + 40000))
        task = cursor["task"]
        self.assertIsNone(cursor["last_task"])   # still in flight
        self.assertEqual(task["first_req"], 1)
        self.assertEqual(task["last_req"], 3)
        self.assertEqual(task["requests"], 3)
        self.assertEqual(task["output_tokens"], 300)
        self.assertEqual(task["prompt_tokens"], 100)
        # 100 tok at 100 tok/s per request = 1 s each of decode time.
        self.assertAlmostEqual(task["decode_time_ms"], 3000.0)
        self.assertEqual(task["messages"], 42)
        self.assertEqual(task["thinking"], "medium")
        self.assertEqual(task["tools"], 10)

    def test_settled_verdict_closes_the_open_run(self):
        # A run runs from idle to idle: the verdict, not the lines, closes
        # it, and the run ENDS at the first settled tick (the arm) -- the
        # close itself waits for the verdict to STAY settled for the grace.
        # A gap of two minutes between the requests is one run: the server
        # never settled in between.
        grace = 60000   # settle_ms explicit: pinning the mechanism, not the default
        cursor = self.fold(self.started(1, self.T0),
                          self.done(1, self.T0 + 2000),
                          self.started(2, self.T0 + 120000),
                          self.done(2, self.T0 + 122000))
        settled = self.T0 + 122500
        # The first settled tick arms: the run's end is set, but the close
        # waits the grace.
        self.assertIsNone(collect.close_run_if_settled(
            cursor, "idle", settled, settle_ms=grace))
        summary = collect.close_run_if_settled(
            cursor, "idle", settled + grace, settle_ms=grace)
        self.assertIsNone(cursor["task"])
        self.assertEqual(summary["first_req"], 1)
        self.assertEqual(summary["last_req"], 2)
        self.assertEqual(summary["requests"], 2)
        # The close fired a grace AFTER the arm, but the completed tick is
        # the ARM tick: the run ends where it settled, not where the grace
        # expired.
        self.assertEqual(summary["completed_at_ms"], settled)
        self.assertEqual(summary["duration_ms"], 122500)

    def test_busy_verdict_keeps_the_run_open(self):
        # No quiet is long enough to split a run: while the verdict keeps
        # reading busy or queued, every line folds into the same open one,
        # however long it stayed away. The busy ticks keep the arm disarmed
        # too, so the settle clock only starts when the verdict finally
        # reads settled.
        cursor = self.fold(self.started(1, self.T0),
                          self.done(1, self.T0 + 2000))
        for offset in (70000, 140000, 210000):
            collect.close_run_if_settled(cursor, "busy",
                                         self.T0 + 2000 + offset)
            self.assertIsNotNone(cursor["task"])
        settled = self.T0 + 240000
        grace = 60000   # settle_ms explicit: pinning the mechanism, not the default
        # The first settled tick arms; the run closes one grace later, at
        # the armed tick.
        self.assertIsNone(collect.close_run_if_settled(
            cursor, "idle", settled, settle_ms=grace))
        summary = collect.close_run_if_settled(
            cursor, "idle", settled + grace, settle_ms=grace)
        self.assertIsNotNone(summary)
        self.assertIsNone(cursor["task"])
        self.assertIsNotNone(cursor["last_task"])
        self.assertEqual(summary["completed_at_ms"], settled)

    def test_every_settling_verdict_ends_the_run(self):
        grace = 60000   # settle_ms explicit: pinning the mechanism, not the default
        for state in ("starting", "stopping", "down"):
            # Work cannot continue across a restart: an un-armed run closes
            # AT the restart tick, no grace.
            cursor = self.fold(self.started(1, self.T0),
                              self.done(1, self.T0 + 2000))
            self.assertIsNone(collect.close_run_if_settled(
                cursor, "busy", self.T0 + 3000))
            summary = collect.close_run_if_settled(
                cursor, state, self.T0 + 4000, settle_ms=grace)
            self.assertIsNotNone(summary)
            self.assertEqual(summary["completed_at_ms"], self.T0 + 4000)
            self.assertIsNone(cursor["task"])
        # Idle settles WITH the grace: the first tick arms, and the close
        # one grace later ends the run at the ARM tick.
        cursor = self.fold(self.started(1, self.T0),
                          self.done(1, self.T0 + 2000))
        self.assertIsNone(collect.close_run_if_settled(
            cursor, "busy", self.T0 + 3000))
        self.assertIsNone(collect.close_run_if_settled(
            cursor, "idle", self.T0 + 4000, settle_ms=grace))
        summary = collect.close_run_if_settled(
            cursor, "idle", self.T0 + 4000 + grace, settle_ms=grace)
        self.assertIsNotNone(summary)
        self.assertEqual(summary["completed_at_ms"], self.T0 + 4000)
        self.assertIsNone(cursor["task"])

    def test_summarize_is_token_weighted(self):
        cursor = self.fold(self.started(1, self.T0),
                          self.done(1, self.T0 + 5000),
                          self.started(2, self.T0 + 10000),
                          self.done(2, self.T0 + 15000))
        last = collect.summarize_task(cursor["task"])
        self.assertEqual(last["decode_tok_s"], 100.0)
        self.assertEqual(last["ttft_ms"], 50.0)
        # 5/10 and 5/10 pooled is 50.0, not an average of percentages.
        self.assertEqual(last["accept_pct"], 50.0)

    def test_peak_is_the_fastest_single_request(self):
        # Not token-weighted: the 100-token request at 300 tok/s is the peak
        # even though the 1000-token request at 50 tok/s dominates the average.
        cursor = self.fold(self.started(1, self.T0),
                          self.done(1, self.T0 + 5000, rate=300),
                          self.started(2, self.T0 + 10000),
                          self.done(2, self.T0 + 30000, rate=50))
        last = collect.summarize_task(cursor["task"])
        self.assertEqual(last["peak_decode_tok_s"], 300.0)
        self.assertNotEqual(last["decode_tok_s"], 300.0)

    def test_peak_is_none_when_no_done_carried_a_rate(self):
        cursor = self.fold(self.started(1, self.T0))   # still in flight
        summary = collect.summarize_task(cursor["task"])
        self.assertIsNone(summary["peak_decode_tok_s"])
        self.assertIsNone(summary["profile"])

    def test_profile_attributes_tokens_and_busy_time(self):
        # 100 tokens at 30 tok/s = 3.33 s, ending at T0+7 s: the interval
        # spans two 5 s buckets, each taking its fraction of tokens and its
        # overlap of decode time -- so each bucket reads the request's own 30.
        cursor = self.fold(self.started(1, self.T0),
                          self.done(1, self.T0 + 7000, rate=30))
        profile = collect.summarize_task(cursor["task"])["profile"]
        self.assertEqual(profile["width_ms"], collect.BUCKET_MS)
        non_zero = [v for v in profile["tok_s"] if v]
        self.assertEqual(len(non_zero), 2)
        for value in non_zero:
            self.assertAlmostEqual(value, 30.0, delta=0.5)
        # Tokens are conserved in the fold (the emitted rates are per BUSY
        # second, so the bucket array alone does not re-sum to the output).
        self.assertAlmostEqual(sum(cursor["task"]["profile"]["tokens"]), 100.0)

    def test_profile_coarsens_to_the_cap(self):
        # 20 requests 10 s apart: the last done lands in bucket ~39, past the
        # 32-bucket cap, so the summary coarsens to 10 s buckets and keeps
        # the token sum.
        entries = []
        for req in range(1, 21):
            at = self.T0 + (req - 1) * 10000
            entries += [self.started(req, at),
                        self.done(req, at + 1000, rate=100)]
        cursor = self.fold(*entries)
        profile = collect.summarize_task(cursor["task"])["profile"]
        self.assertLessEqual(len(profile["tok_s"]), collect.TASK_PROFILE_MAX)
        self.assertEqual(profile["width_ms"] % collect.BUCKET_MS, 0)
        self.assertAlmostEqual(sum(cursor["task"]["profile"]["tokens"]), 2000.0)
        # Pair-merging sums tokens AND busy time, so every bucket -- before
        # or after the merge -- still reads each request's own rate. The sum
        # assertion alone cannot catch a misaligned merge.
        for value in profile["tok_s"]:
            if value:
                self.assertAlmostEqual(value, 100.0, delta=0.5)

    def test_profile_coarsens_in_fold_and_keeps_folding(self):
        # Past PROFILE_TRIGGER the open array coarsens to 10 s buckets and
        # LATER requests fold in at the doubled width -- mixed-width
        # accumulation must stay honest. 34 requests 10 s apart push the
        # 34th done into bucket 66 (> 64); the 35th lands after the coarsen.
        entries = []
        for req in range(1, 35):
            at = self.T0 + (req - 1) * 10000
            entries += [self.started(req, at),
                        self.done(req, at + 1000, rate=100)]
        cursor = self.fold(*entries)
        self.assertEqual(cursor["task"]["profile"]["width_ms"], 10000)
        at = self.T0 + 34 * 10000
        collect.apply_entries([self.started(35, at),
                               self.done(35, at + 1000, rate=100)],
                              cursor, reset=False)
        profile = collect.summarize_task(cursor["task"])["profile"]
        self.assertEqual(profile["width_ms"], 20000)
        self.assertLessEqual(len(profile["tok_s"]), collect.TASK_PROFILE_MAX)
        self.assertAlmostEqual(sum(cursor["task"]["profile"]["tokens"]), 3500.0)
        for value in profile["tok_s"]:
            if value:
                self.assertAlmostEqual(value, 100.0, delta=0.5)

    def test_profile_clips_a_partial_interval_and_keeps_the_rate(self):
        # A partial clip: the interval starts before the task and ends after
        # it (a concurrent request whose decode predates the task's first
        # line). The clipped head must lose its tokens along with its time,
        # or the surviving bucket reads more than the request's own rate.
        cursor = self.fold(self.started(1, self.T0),
                           self.done(1, self.T0 + 1000, rate=10))
        # True interval [T0-9000, T0+1000]: only the last 1000 ms is placeable.
        profile = collect.summarize_task(cursor["task"])["profile"]
        for value in profile["tok_s"]:
            if value:
                self.assertAlmostEqual(value, 10.0, delta=0.5)
        self.assertAlmostEqual(sum(cursor["task"]["profile"]["tokens"]), 10.0)

    def test_profile_clamps_an_interval_older_than_the_task(self):
        # A done without its started opens the task AT the done: that decode
        # happened before the task began, so it cannot be placed. Without the
        # clamp its negative bucket indices corrupt the array (or crash it).
        # A later request in the same task still attributes, and the totals
        # account for exactly the placeable requests.
        cursor = self.fold(self.done(7, self.T0),
                           self.started(8, self.T0 + 2000),
                           self.done(8, self.T0 + 4000, rate=50),
                           self.started(9, self.T0 + 52000),
                           self.done(9, self.T0 + 53000, rate=100))
        profile = collect.summarize_task(cursor["task"])["profile"]
        self.assertEqual(profile["tok_s"][10], 100.0)   # request 9, bucket 10
        # Request 7's decode is unplaceable: the fold accounts for exactly
        # the placeable requests.
        self.assertAlmostEqual(sum(cursor["task"]["profile"]["tokens"]), 200.0)

    def test_a_task_dict_predating_the_profile_field(self):
        cursor = self.fold(self.started(1, self.T0))
        del cursor["task"]["profile"]
        collect.apply_entries([self.done(1, self.T0 + 5000, rate=120)],
                              cursor, reset=False)
        self.assertIsNotNone(cursor["task"]["profile"])
        self.assertIsNotNone(collect.summarize_task(cursor["task"])["profile"])

    def test_a_task_dict_predating_the_peak_field(self):
        # cursor.json persists the open task: the first run after this field
        # lands reads a task dict without it. Neither the fold nor the
        # summary may raise -- that would wedge the collector until restart.
        cursor = self.fold(self.started(1, self.T0))
        del cursor["task"]["peak_decode_tok_s"]
        collect.apply_entries([self.done(1, self.T0 + 5000, rate=222.7)],
                              cursor, reset=False)
        self.assertAlmostEqual(cursor["task"]["peak_decode_tok_s"], 222.7)
        self.assertEqual(collect.summarize_task(
                              {"first_req": 1, "last_req": 1, "requests": 0,
                               "started_at_ms": self.T0, "last_event_at_ms": 0,
                               "last_done_at_ms": None, "output_tokens": 0,
                               "prompt_tokens": None, "cache_pct": None,
                               "decode_time_ms": 0.0, "ttft_sum_ms": 0.0,
                               "ttft_n": 0, "spec_accepted": 0, "spec_drafts": 0,
                               "endpoint": None, "outcome": None, "messages": None,
                               "thinking": None, "tools": None})["peak_decode_tok_s"],
                         None)

    def test_done_without_a_started_starts_a_task(self):
        # A cursor rebuilt mid-run sees dones whose started lines are gone.
        cursor = self.fold(self.done(7, self.T0))
        task = cursor["task"]
        self.assertEqual(task["first_req"], 7)
        self.assertEqual(task["requests"], 1)
        self.assertEqual(task["messages"], None)

    def test_a_stale_done_folds_into_the_open_run(self):
        # Journal lines can arrive out of order, and the fold follows
        # content, not time: the stale line lands in the task that is open
        # now. A settled verdict, not a late line, is what closes runs.
        t1 = self.T0
        t2 = t1 + 120000
        cursor = self.fold(self.started(1, t1),
                          self.done(1, t1 + 2000),
                          self.started(2, t2),
                          self.done(2, t2 + 2000),
                          self.done(1, t1 + 3000))
        self.assertIsNone(cursor["last_task"])
        self.assertEqual(cursor["task"]["first_req"], 1)
        self.assertEqual(cursor["task"]["requests"], 3)

    def test_reset_drops_both_tasks(self):
        cursor = self.fold(self.started(1, self.T0), self.done(1, self.T0 + 2000))
        cursor["last_task"] = {"first_req": 0}
        collect.apply_entries([], cursor, reset=True)
        self.assertIsNone(cursor["task"])
        self.assertIsNone(cursor["last_task"])
        # ... and the next line after the reset starts a fresh task, not a resumption.
        collect.apply_entries([self.started(1, self.T0 + 2000)], cursor, reset=False)
        self.assertEqual(cursor["task"]["requests"], 0)

    def test_terminal_keeps_the_task_open(self):
        # A failure is activity: the run closes on the settled verdict,
        # not on the failure line.
        t = self.T0
        terminal = self.entry(t + 2000,
                              "req#1 rejected during prefill | openai-chat | HTTP 400")
        cursor = self.fold(self.started(1, t), terminal)
        self.assertIsNotNone(cursor["task"])
        self.assertEqual(cursor["task"]["last_event_at_ms"], t + 2000)

    def test_busy_mid_grace_disarms_and_idle_re_arms(self):
        # The settle clock does not survive activity: a busy tick inside
        # the grace clears the arm, and a later idle re-arms from ITS OWN
        # tick. Closing on the original arm tick would end the run at a
        # moment the verdict has already read settled AND re-bursted.
        grace = 60000   # settle_ms explicit: pinning the mechanism, not the default
        cursor = self.fold(self.started(1, self.T0),
                          self.done(1, self.T0 + 2000))
        armed = self.T0 + 10000
        self.assertIsNone(collect.close_run_if_settled(
            cursor, "idle", armed, settle_ms=grace))
        # Busy mid-grace: the arm dies with the settle clock.
        self.assertIsNone(collect.close_run_if_settled(
            cursor, "busy", armed + 10000, settle_ms=grace))
        self.assertIsNone(cursor["settling_since_ms"])
        # The later idle re-arms from its own tick, not the old arm.
        re_arms = armed + 20000
        self.assertIsNone(collect.close_run_if_settled(
            cursor, "idle", re_arms, settle_ms=grace))
        summary = collect.close_run_if_settled(
            cursor, "idle", re_arms + grace, settle_ms=grace)
        self.assertIsNotNone(summary)
        self.assertEqual(summary["completed_at_ms"], re_arms)

    def test_sub_grace_gap_merges_and_over_grace_gap_splits(self):
        # The settle grace is what splits runs, not the lines: a request
        # that lands inside the grace of the last settled tick folds into
        # the open run (the fold follows content, so no verdict tick fired
        # in the gap), and one that lands after the grace elapsed is the
        # first of the next. The merged run still ends at the FIRST
        # settled tick (the arm), however late its last request was.
        grace = 60000   # settle_ms explicit: pinning the mechanism, not the default
        # Under the grace: one run, two requests.
        cursor = self.fold(self.started(1, self.T0),
                          self.done(1, self.T0 + 2000))
        # The first settled tick arms at T0+2000...
        self.assertIsNone(collect.close_run_if_settled(
            cursor, "idle", self.T0 + 2000, settle_ms=grace))
        # ...and the second request, 28 s on, lands inside the grace: it
        # joins the open run.
        collect.apply_entries([self.started(2, self.T0 + 30000),
                               self.done(2, self.T0 + 32000)],
                              cursor, reset=False)
        summary = collect.close_run_if_settled(
            cursor, "idle", self.T0 + 62000, settle_ms=grace)
        self.assertIsNotNone(summary)
        self.assertEqual(summary["requests"], 2)
        self.assertEqual(summary["completed_at_ms"], self.T0 + 2000)
        self.assertEqual(len(cursor["runs"]), 1)
        # Over the grace: the first run rings before the second request
        # even starts, and the two settle into two rows.
        cursor = self.fold(self.started(1, self.T0),
                          self.done(1, self.T0 + 2000))
        self.assertIsNone(collect.close_run_if_settled(
            cursor, "idle", self.T0 + 2000, settle_ms=grace))
        first = collect.close_run_if_settled(
            cursor, "idle", self.T0 + 82000, settle_ms=grace)
        self.assertIsNotNone(first)
        self.assertEqual(first["completed_at_ms"], self.T0 + 2000)
        collect.apply_entries([self.started(2, self.T0 + 90000),
                               self.done(2, self.T0 + 92000)],
                              cursor, reset=False)
        # 88 s after the last settled tick: past the grace, a fresh run.
        self.assertIsNone(collect.close_run_if_settled(
            cursor, "idle", self.T0 + 92000, settle_ms=grace))
        second = collect.close_run_if_settled(
            cursor, "idle", self.T0 + 152000, settle_ms=grace)
        self.assertIsNotNone(second)
        self.assertEqual(second["completed_at_ms"], self.T0 + 92000)
        # The ring keeps both, newest first.
        self.assertEqual([run["completed_at_ms"] for run in cursor["runs"]],
                         [second["completed_at_ms"],
                          first["completed_at_ms"]])

    def test_terminal_verdict_mid_grace_closes_at_the_arm(self):
        # A restart that lands INSIDE the grace still ends the run at the
        # ARM tick, not the restart tick: the run's end stays the first
        # settled tick, so duration_ms is not inflated and no cool-down
        # samples fold into the temp window. Un-armed, the restart tick is
        # the end.
        grace = 60000   # settle_ms explicit: pinning the mechanism, not the default
        cursor = self.fold(self.started(1, self.T0),
                          self.done(1, self.T0 + 2000))
        arm = self.T0 + 10000
        self.assertIsNone(collect.close_run_if_settled(
            cursor, "idle", arm, settle_ms=grace))
        # "down" 30 s into the grace (< grace): closes at the arm.
        summary = collect.close_run_if_settled(
            cursor, "down", arm + 30000, settle_ms=grace)
        self.assertIsNotNone(summary)
        self.assertEqual(summary["completed_at_ms"], arm)
        # Un-armed: nothing settled before the restart, so the restart
        # tick is the end.
        cursor = self.fold(self.started(1, self.T0),
                          self.done(1, self.T0 + 2000))
        now = self.T0 + 40000
        summary = collect.close_run_if_settled(cursor, "down", now,
                                               settle_ms=grace)
        self.assertIsNotNone(summary)
        self.assertEqual(summary["completed_at_ms"], now)

    def test_a_merged_run_past_the_profile_trigger_keeps_a_sane_tok_s_max(self):
        # Three requests 180 s apart span ~362 s, past the 64-bucket
        # PROFILE_TRIGGER: the open profile coarsens IN PLACE to 10 s
        # windows so the run's profile stays bounded no matter how long the
        # merged run runs. The coarsening pairs SUM tokens and busy time,
        # so each merged window still reads the decode rate it folded --
        # the Max is the busiest merged window (~the request's own 100
        # tok/s), never a diluted or inflated guess. No exact value: the
        # window coarsens to keep the profile bounded, and the test pins
        # only that the rate survives it.
        entries = []
        for req in (1, 2, 3):
            at = self.T0 + (req - 1) * 180000
            entries += [self.started(req, at),
                        self.done(req, at + 2000, rate=100)]
        cursor = self.fold(*entries)
        # The fold itself coarsened past the trigger.
        self.assertEqual(cursor["task"]["profile"]["width_ms"],
                         2 * collect.BUCKET_MS)
        grace = 60000   # settle_ms explicit: pinning the mechanism, not the default
        arm = self.T0 + 3 * 180000 + 2000
        self.assertIsNone(collect.close_run_if_settled(
            cursor, "idle", arm, settle_ms=grace))
        summary = collect.close_run_if_settled(
            cursor, "idle", arm + grace, settle_ms=grace)
        self.assertIsNotNone(summary)
        self.assertIsNotNone(summary["tok_s_max"])
        self.assertGreater(summary["tok_s_max"], 50.0)

    def test_a_cursor_without_the_arm_key_closes_cleanly(self):
        # A cursor.json written by a build without the settling_since_ms
        # key: the close must treat the absent key as un-armed (and
        # re-create it), not KeyError -- that would wedge the collector
        # until restart.
        cursor = collect.blank_cursor()
        del cursor["settling_since_ms"]
        collect.apply_entries([self.started(1, self.T0),
                               self.done(1, self.T0 + 2000)],
                              cursor, reset=False)
        # settle_ms=0: same-tick close, so no grace is in play.
        summary = collect.close_run_if_settled(cursor, "idle", self.T0 + 2500,
                                               settle_ms=0)
        self.assertIsNotNone(summary)
        self.assertIsNone(cursor["task"])

    def test_a_stale_arm_dies_with_the_reset(self):
        # The invocation reset drops the open task AND the arm: a stale
        # arm that survived the reset would predate the new task's start
        # and close it at a negative duration the first grace-elapsed
        # tick -- the corruption variant the invariant exists to kill.
        grace = 60000   # settle_ms explicit: pinning the mechanism, not the default
        cursor = self.fold(self.started(1, self.T0),
                          self.done(1, self.T0 + 2000))
        # Arm the pre-restart run...
        self.assertIsNone(collect.close_run_if_settled(
            cursor, "idle", self.T0 + 10000, settle_ms=grace))
        # ...then the invocation restarts: the reset clears the arm with
        # the task, and the new started line opens a fresh run.
        fresh_start = self.T0 + 130000
        collect.apply_entries([self.started(1, fresh_start)],
                              cursor, reset=True)
        self.assertEqual(cursor["task"]["started_at_ms"], fresh_start)
        # The first idle tick after the reset must ARM (task still open):
        # had the stale arm survived, this same tick -- 180 s after it --
        # would have closed the new run at the pre-restart tick.
        arm_tick = self.T0 + 190000
        self.assertIsNone(collect.close_run_if_settled(
            cursor, "idle", arm_tick, settle_ms=grace))
        summary = collect.close_run_if_settled(
            cursor, "idle", arm_tick + grace, settle_ms=grace)
        self.assertIsNotNone(summary)
        self.assertEqual(summary["completed_at_ms"], arm_tick)
        self.assertGreaterEqual(summary["completed_at_ms"],
                                summary["started_at_ms"])

    def test_the_arm_survives_the_cursor_round_trip(self):
        # The arm is carried in cursor.json between ticks: serialize it
        # with the collector's own writer and read it back exactly as
        # main() does, then the close one grace after the arm ends the run
        # AT the arm. In-process on purpose: --stdin cannot carry a
        # cursor, and a live subprocess would probe the machine's real unit
        # and health.
        cursor = self.fold(self.started(1, self.T0),
                          self.done(1, self.T0 + 2000))
        arm_tick = self.T0 + 5000
        # No settle_ms: the default RUN_SETTLE_MS path the deployment runs.
        self.assertIsNone(collect.close_run_if_settled(
            cursor, "idle", arm_tick))
        self.assertEqual(cursor["settling_since_ms"], arm_tick)
        path = os.path.join(tempfile.mkdtemp(), "cursor.json")
        collect.write_json_atomic(path, cursor)
        fresh = collect.blank_cursor()
        fresh.update(collect.read_json(path))
        fresh = collect.migrate_cursor(fresh)
        self.assertEqual(fresh["settling_since_ms"], arm_tick)
        summary = collect.close_run_if_settled(
            fresh, "idle", arm_tick + collect.RUN_SETTLE_MS)
        self.assertIsNotNone(summary)
        self.assertEqual(summary["completed_at_ms"], arm_tick)


class TestSchema9Folds(unittest.TestCase):
    """The previous-runs table folds: per-run peaks, accept spread, queue
    waits, and the run's own thermal samples (schema 9)."""

    T0 = 1768000000000

    def entry(self, at_ms, message):
        return {"cursor": None, "at_ms": at_ms, "message": message,
                "priority": None, "cmdline": ""}

    def started(self, req, at_ms):
        return self.entry(at_ms, "req#%d started | openai-chat stream | "
                                 "42 messages | thinking medium | tools 10" % req)

    def done(self, req, at_ms, rate=100, queue="10 ms", accept=None, accepted="5"):
        queue_part = " | queue %s" % queue if queue else ""
        accept_part = (" | dflash2 accepted %s/10 (%s%%)" % (accepted, accept)
                       if accept else "")
        return self.entry(at_ms, "req#%d done | openai-chat | stop token | "
                          "prompt 100 | output 100 | cache 90 (90.0%%) | "
                          "TTFT 50 ms | total 1s%s | prefill 100 tok/s | "
                          "decode %s tok/s%s" % (req, queue_part, rate, accept_part))

    def fold(self, *entries):
        cursor = collect.blank_cursor()
        collect.apply_entries(list(entries), cursor, reset=False)
        return cursor

    def test_tok_s_max_is_the_sampled_peak(self):
        # Not the fastest single request: 100 tokens at 300 tok/s and 1000
        # tokens at 50 tok/s -- the table's Max is what the busiest 5 s of
        # the run actually looked like, and its Avg is the token-weighted
        # effective rate. Both come from the same sampling. (The profile
        # reads its buckets as tokens per busy millisecond, so the sample may
        # overshoot the request's own rate by the ms rounding -- never by
        # more than a token.)
        cursor = self.fold(self.started(1, self.T0),
                           self.done(1, self.T0 + 5000, rate=300),
                           self.started(2, self.T0 + 10000),
                           self.done(2, self.T0 + 30000, rate=50))
        last = collect.summarize_task(cursor["task"])
        self.assertEqual(last["peak_decode_tok_s"], 300.0)
        self.assertIsNotNone(last["tok_s_max"])
        self.assertAlmostEqual(last["tok_s_max"], 300.0, delta=1.0)
        self.assertGreater(last["tok_s_max"], last["decode_tok_s"])
        self.assertNotEqual(last["decode_tok_s"], 300.0)

    def test_accept_avg_and_max_are_per_request(self):
        # The pooled accept_pct weights every draft equally; the table's
        # Avg/Max are over the per-request percentages.
        cursor = self.fold(self.started(1, self.T0),
                           self.done(1, self.T0 + 5000, accept=50.0),
                           self.started(2, self.T0 + 10000),
                           self.done(2, self.T0 + 15000, accept=10.0,
                                     accepted="1"))
        last = collect.summarize_task(cursor["task"])
        self.assertEqual(last["accept_pct_avg"], 30.0)
        self.assertEqual(last["accept_pct_max"], 50.0)
        self.assertEqual(last["accept_pct"], 30.0)   # 6/20 pooled, same here

    def test_accept_is_null_when_no_request_reported_spec(self):
        cursor = self.fold(self.started(1, self.T0), self.done(1, self.T0 + 5000))
        last = collect.summarize_task(cursor["task"])
        self.assertIsNone(last["accept_pct_avg"])
        self.assertIsNone(last["accept_pct_max"])

    def test_queue_wait_is_the_slowest_request(self):
        # 12-27 ms is the scheduler's noise floor when nothing waits (the
        # UI draws a dash below QUEUE_WAIT_MIN_MS); a request that genuinely
        # queued shows up as the run's slowest wait. The 12 ms sample counts
        # for the max but never for the avg: the mean counts only the
        # requests that crossed the floor, so here it equals the max.
        cursor = self.fold(self.started(1, self.T0),
                           self.done(1, self.T0 + 5000, queue="12 ms"),
                           self.started(2, self.T0 + 10000),
                           self.done(2, self.T0 + 20000, queue="2.5s"))
        last = collect.summarize_task(cursor["task"])
        self.assertEqual(last["queue_wait_ms_max"], 2500.0)
        self.assertEqual(last["queue_wait_ms_avg"], 2500.0)

    def test_queue_wait_is_zero_when_absent(self):
        # `queue N ms` is logged only when non-zero; absent means zero, and
        # zero must read zero, not a parse failure. No request crossed the
        # noise floor, so the avg is None (a dash), not zero.
        cursor = self.fold(self.started(1, self.T0),
                           self.done(1, self.T0 + 5000, queue=None))
        last = collect.summarize_task(cursor["task"])
        self.assertEqual(last["queue_wait_ms_max"], 0.0)
        self.assertIsNone(last["queue_wait_ms_avg"])

    def test_queue_wait_avg_counts_only_requests_that_crossed_the_floor(self):
        # The 15 ms sample is scheduler noise: it counts for the max, but
        # the avg is the mean of the requests that genuinely queued.
        cursor = self.fold(self.started(1, self.T0),
                           self.done(1, self.T0 + 5000, queue="15 ms"),
                           self.started(2, self.T0 + 10000),
                           self.done(2, self.T0 + 15000, queue="150 ms"),
                           self.started(3, self.T0 + 20000),
                           self.done(3, self.T0 + 25000, queue="240 ms"))
        last = collect.summarize_task(cursor["task"])
        self.assertEqual(last["queue_wait_ms_max"], 240.0)
        self.assertEqual(last["queue_wait_ms_avg"], 195.0)

    def test_a_task_dict_predating_the_queue_wait_avg_field(self):
        # cursor.json persists the open task: the first run after this field
        # lands reads a task dict without it. Neither the fold nor the
        # summary may raise -- that would wedge the collector until restart.
        # Only sub-floor waits fold in, so the fold never recreates the
        # deleted keys: the summary must read them through or-defaults.
        cursor = self.fold(self.started(1, self.T0))
        del cursor["task"]["queue_wait_ms_sum"]
        del cursor["task"]["queue_wait_n"]
        collect.apply_entries([
            self.done(1, self.T0 + 5000, queue="12 ms"),
            self.started(2, self.T0 + 10000),
            self.done(2, self.T0 + 15000, queue="27 ms"),
        ], cursor, reset=False)
        last = collect.summarize_task(cursor["task"])
        self.assertEqual(last["queue_wait_ms_max"], 27.0)
        self.assertIsNone(last["queue_wait_ms_avg"])

    def test_run_temps_come_from_the_runs_own_span(self):
        # The card's samples outside the run's window -- its idle before,
        # its cool-down after -- do not belong to the run.
        cursor = self.fold(self.started(1, self.T0),
                           self.done(1, self.T0 + 5000))
        cursor["temp_samples"] = [[self.T0 - 10000, 70.0],
                                  [self.T0 + 1000, 45.0],
                                  [self.T0 + 4000, 50.0],
                                  [self.T0 + 90000, 65.0]]
        summary = collect.close_run(cursor, cursor["task"], self.T0 + 5500)
        self.assertEqual(summary["temp_avg_c"], 47.5)
        self.assertEqual(summary["temp_max_c"], 50.0)

    def test_run_temps_are_null_without_samples(self):
        # No card probe (or none inside the span): the Temp cell is a dash,
        # never a guess.
        cursor = self.fold(self.started(1, self.T0),
                           self.done(1, self.T0 + 5000))
        summary = collect.summarize_task(cursor["task"])
        self.assertIsNone(summary["temp_avg_c"])
        self.assertIsNone(summary["temp_max_c"])

    def test_runs_ring_keeps_five_newest_first(self):
        # Seven runs, each opened by its lines and closed by a settled
        # verdict: the ring keeps the five newest, newest first.
        cursor = collect.blank_cursor()
        for req in range(1, 8):
            at = self.T0 + (req - 1) * 100000
            collect.apply_entries(
                [self.started(req, at), self.done(req, at + 2000)],
                cursor, reset=False)
            # settle_ms=0: same-tick close -- this test pins the ring, not
            # the settle grace.
            collect.close_run_if_settled(cursor, "idle", at + 2500,
                                         settle_ms=0)
        self.assertEqual(len(cursor["runs"]), collect.RUNS_KEEP)
        completed = [run["completed_at_ms"] for run in cursor["runs"]]
        self.assertEqual(completed, sorted(completed, reverse=True))
        # The ring holds the projected row, not the whole summary.
        self.assertEqual(set(cursor["runs"][0]), {
            "completed_at_ms", "duration_ms", "tok_s_avg", "tok_s_max",
            "accept_pct_avg", "accept_pct_max", "temp_avg_c", "temp_max_c",
            "queue_wait_ms_avg", "queue_wait_ms_max"})

    def test_runs_ring_ignores_doneless_runs(self):
        # A run that settles with no done line is not a run: a failed burst
        # lands in last_task only, and the ring keeps its five real runs.
        cursor = collect.blank_cursor()
        collect.apply_entries([self.started(1, self.T0)], cursor, reset=False)
        collect.close_run_if_settled(cursor, "down", self.T0 + 2000)
        self.assertEqual(cursor["runs"], [])
        self.assertIsNotNone(cursor["last_task"])
        at = self.T0 + 100000
        collect.apply_entries([self.started(2, at), self.done(2, at + 2000)],
                              cursor, reset=False)
        # settle_ms=0: same-tick close -- this test pins the ring, not the
        # settle grace (the "down" close above is unchanged: it is
        # immediate either way).
        collect.close_run_if_settled(cursor, "idle", at + 2500, settle_ms=0)
        self.assertEqual(len(cursor["runs"]), 1)
        self.assertEqual(cursor["runs"][0]["completed_at_ms"], at + 2500)


class TestSchema9Misc(unittest.TestCase):
    """The schema 9 pieces that are not task folds: the age label and the
    engine's identity line."""

    def test_age_label_matches_the_qml(self):
        # Same readings as format.js's fmtAge: both sides of the display rule
        # must agree, including at the boundaries.
        self.assertEqual(collect.age_label(500), "now")
        self.assertEqual(collect.age_label(999), "now")
        self.assertEqual(collect.age_label(45000), "45s ago")
        self.assertEqual(collect.age_label(4 * 60000), "4m ago")
        self.assertEqual(collect.age_label(3 * 3600000), "3h ago")
        self.assertIsNone(collect.age_label(None))

    def test_engine_ready_line_parses(self):
        event = collect.parse_message(
            "engine ready | qwen3.8-27b/nvfp4 | pages 2,837/4,096")
        self.assertEqual(event["kind"], "engine_ready")
        self.assertEqual(event["model"], "qwen3.8-27b")
        self.assertEqual(event["profile"], "nvfp4")
        self.assertNotIn("weights_gib", event)

    def test_memory_budget_lines_parse(self):
        # The engine's own measurements of what the boot parameters
        # committed: settled numbers only. The in-progress `pinning` and
        # `loading` lines are not the budget, and `free` is live, not boot.
        event = collect.parse_message(
            "weights ready | 21.4 GiB | 2.0s | 10.7 GiB/s")
        self.assertEqual((event["kind"], event["gib"]), ("weights_ready", 21.4))
        event = collect.parse_message(
            "capacity | KV 181,568 tokens, fp8, explicit | pages 2,837/4,096"
            " | runtime 7.31 GiB | free 2.30 GiB")
        self.assertEqual((event["kind"], event["runtime_gib"]),
                         ("capacity", 7.31))
        event = collect.parse_message("host KV pinned | 32.0 GiB | 4.0s")
        self.assertEqual((event["kind"], event["what"], event["gib"]),
                         ("host_pinned", "kv", 32.0))
        event = collect.parse_message("host state pinned | 5.84 GiB | 741 ms")
        self.assertEqual((event["kind"], event["what"], event["gib"]),
                         ("host_pinned", "state", 5.84))
        self.assertIsNone(collect.parse_message("pinning host KV | 32.0 GiB"))
        self.assertIsNone(collect.parse_message("pinning host state | 5.84 GiB"))
        self.assertIsNone(collect.parse_message("loading weights | 21.4 GiB"))

    def test_memory_budget_folds_latest_wins(self):
        entries = [
            {"cursor": None, "at_ms": 0, "priority": None, "cmdline": "",
             "message": "weights ready | 20.0 GiB | 2.0s | 10.7 GiB/s"},
            {"cursor": None, "at_ms": 1, "priority": None, "cmdline": "",
             "message": "capacity | KV 100,000 tokens, fp8, explicit"
                        " | pages 1/2 | runtime 5.0 GiB | free 1.0 GiB"},
            {"cursor": None, "at_ms": 2, "priority": None, "cmdline": "",
             "message": "engine ready | qwen3.8-27b/nvfp4 | total 7.7s"
                        " | weights 21.4 GiB"},
        ]
        cursor = collect.blank_cursor()
        collect.apply_entries(entries, cursor, reset=False)
        # The engine's own identity line carries the settled weights number:
        # it wins over the earlier `weights ready` reading.
        self.assertEqual(cursor["mem"], {"weights_gib": 21.4, "kv_gib": 5.0})
        # A second boot reissues the lines: the host readings land next to
        # the device ones, all in one budget.
        entries = [
            {"cursor": None, "at_ms": 3, "priority": None, "cmdline": "",
             "message": "host KV pinned | 32.0 GiB | 4.0s"},
            {"cursor": None, "at_ms": 4, "priority": None, "cmdline": "",
             "message": "host state pinned | 5.84 GiB | 741 ms"},
        ]
        collect.apply_entries(entries, cursor, reset=False)
        self.assertEqual(cursor["mem"],
                         {"weights_gib": 21.4, "kv_gib": 5.0,
                          "host_kv_gib": 32.0, "host_state_gib": 5.84})

    def test_weight_profile_hint_from_artifact_name(self):
        # The artifact's trailing token is a hint, used only until the
        # engine's identity line has been seen. Names without a known
        # trailing token get no hint -- the hint is a name convention,
        # never a guess, and the kv-dtype flag is a different knob.
        self.assertEqual(
            collect.WEIGHT_PROFILE_HINT_RE.search("qwen3_8_27b_nvfp4").group(1),
            "nvfp4")
        self.assertIsNone(collect.WEIGHT_PROFILE_HINT_RE.search("llama_8b"))
        self.assertIsNone(
            collect.WEIGHT_PROFILE_HINT_RE.search("qwen3_8_27b"))

    def test_v7_cursor_task_is_discarded(self):
        # The upgrade path: a cursor written by the schema 7 collector,
        # mid-run, with the old task field names. The journal position and
        # the health fold must survive; the half-open run must not crash
        # the new fold (KeyError on `accept_pct_sum` is the bug this
        # guard exists for).
        cursor = collect.blank_cursor()
        cursor["cursor"] = "_123456_abcdef"
        cursor["last_entry_at_ms"] = 1700000000000
        cursor["healthy"] = True
        cursor["task"] = {
            "first_req": 7, "last_req": 9, "started_at_ms": 1,
            "last_event_at_ms": 2, "requests": 3, "output_tokens": 100,
            "spec_accepted": 20, "spec_drafts": 35,
        }
        cursor["buckets"] = {"1700000000000": 12}
        cursor["pending"] = {"10": 1700000001000}
        migrated = collect.migrate_cursor(cursor)
        self.assertIsNone(migrated["task"])
        self.assertIsNone(migrated["last_task"])
        self.assertEqual(migrated["runs"], [])
        self.assertEqual(migrated["buckets"], {})
        self.assertEqual(migrated["pending"], {})
        self.assertEqual(migrated["accept_pcts"], [])
        self.assertEqual(
            migrated["counters"],
            {"failed": 0, "rejected": 0, "client_disconnects": 0})
        self.assertEqual(migrated["cursor"], "_123456_abcdef")
        self.assertEqual(migrated["last_entry_at_ms"], 1700000000000)
        self.assertTrue(migrated["healthy"])

    def test_migrate_renames_old_ring_rows_in_place(self):
        # schema 12: rows projected before the queue-wait split carry the
        # old key; the rename keeps their Max instead of letting them dash
        # until they rotate out. Idempotent: a second pass is a no-op, and
        # a row that already has the new shape is left alone.
        cursor = collect.blank_cursor()
        cursor["runs"] = [
            {"completed_at_ms": 1000, "queue_wait_ms": 1800.0},
            {"completed_at_ms": 900, "queue_wait_ms_avg": 500.0,
             "queue_wait_ms_max": 1800.0},
        ]
        migrated = collect.migrate_cursor(cursor)
        self.assertEqual(migrated["runs"][0]["queue_wait_ms_max"], 1800.0)
        self.assertNotIn("queue_wait_ms", migrated["runs"][0])
        self.assertEqual(migrated["runs"][1]["queue_wait_ms_max"], 1800.0)
        again = collect.migrate_cursor(migrated)
        self.assertEqual(again["runs"][0]["queue_wait_ms_max"], 1800.0)
        self.assertNotIn("queue_wait_ms", again["runs"][0])

    def test_v8_cursor_task_survives_migration(self):
        cursor = collect.blank_cursor()
        cursor["cursor"] = "_123456_abcdef"
        cursor["task"] = dict(collect.blank_task(
            {"id": 1}, 1700000000000))
        before = json.dumps(cursor, sort_keys=True)
        collect.migrate_cursor(cursor)
        self.assertEqual(json.dumps(cursor, sort_keys=True), before)

    def test_engine_identity_survives_in_the_cursor(self):
        entry = {"cursor": None, "at_ms": 0, "priority": None,
                 "cmdline": "",
                 "message": "engine ready | qwen3.8-27b/nvfp4 | pages 1/1"}
        cursor = collect.blank_cursor()
        collect.apply_entries([entry], cursor, reset=False)
        self.assertEqual(cursor["engine_identity"],
                         {"model": "qwen3.8-27b", "profile": "nvfp4"})


class TestBootParse(unittest.TestCase):
    """What the binary was loaded with: the serve line, in order."""

    # A real serve invocation, as the journal's _CMDLINE carries it.
    SERVE = ("/usr/bin/ninfer-serve models/qwen3_8_27b_nvfp4.ninfer "
             "--host 127.0.0.1 --port 8080 --max-context 131072 "
             "--kv-capacity 181568 --max-concurrency 2 --kv-dtype fp8 "
             "--device-state-slots 1 --host-state-slots 32 "
             "--host-kv-mib 32768 --spec dflash2 --draft-tokens 7 "
             "--lm-head-draft --preserve-thinking --cors")

    def test_full_line_in_command_line_order(self):
        boot = collect.parse_boot(self.SERVE)
        self.assertEqual(list(boot),
                         ["model", "host", "port", "max-context",
                          "kv-capacity", "max-concurrency", "kv-dtype",
                          "device-state-slots", "host-state-slots",
                          "host-kv-mib", "spec", "draft-tokens",
                          "lm-head-draft", "preserve-thinking", "cors"])
        self.assertEqual(boot["model"], "models/qwen3_8_27b_nvfp4.ninfer")
        self.assertEqual(boot["port"], 8080)
        self.assertEqual(boot["max-context"], 131072)
        self.assertEqual(boot["kv-dtype"], "fp8")
        self.assertEqual(boot["lm-head-draft"], True)

    def test_numbers_are_numbers_strings_stay(self):
        boot = collect.parse_boot(self.SERVE)
        for flag in ("port", "max-context", "kv-capacity", "max-concurrency",
                     "device-state-slots", "host-state-slots", "host-kv-mib",
                     "draft-tokens"):
            self.assertIsInstance(boot[flag], int, flag)
        for flag in ("model", "host", "kv-dtype", "spec"):
            self.assertIsInstance(boot[flag], str, flag)

    def test_a_line_that_is_not_the_serve_line_is_refused(self):
        # ExecStartPre's wait loop and the shell wrapper carry cmdlines too;
        # none of them has the models/ token.
        self.assertIsNone(
            collect.parse_boot(
                "/bin/sh -c 'for i in $(seq 1 60); do nvidia-smi -L >/dev/null 2>&1 && exit 0; sleep 1; done'"))
        self.assertIsNone(collect.parse_boot("./serve.sh"))
        self.assertIsNone(collect.parse_boot(""))

    def test_trailing_flag_is_a_boolean(self):
        boot = collect.parse_boot("./x models/a.ninfer --cors")
        self.assertEqual(boot, {"model": "models/a.ninfer", "cors": True})


class TestOpenTaskEmitted(unittest.TestCase):
    """The Telemetry tab's live tiles read the open run the way the
    previous-runs row reads the closed one.

    The verdict at the pinned clock decides: a fresh sample with a request
    running keeps the run open (summarised live, duration still null);
    nothing running settles it at that tick.
    """

    def replay(self, lines):
        # --settle-ms 0: every replay in this class closes same-tick -- the
        # class pins the open-run contract, not the settle grace.
        completed = subprocess.run(
            [sys.executable, COLLECTOR, "--dry-run", "--stdin",
             "--now-ms", str(make_fixtures.NOW_MS),
             "--settle-ms", "0"],
            input="\n".join(lines) + "\n",
            capture_output=True, text=True, check=True)
        return json.loads(completed.stdout)

    def line(self, at_ms, message):
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(at_ms / 1000.0))
        return "%s.%03d  INFO  %s" % (stamp, at_ms % 1000, message)

    def test_a_run_in_flight_reads_busy_and_does_not_ring(self):
        # The fresh sample says a request is running at the pinned clock:
        # the verdict reads busy, so the run is still open -- not in the
        # ring, and (schema 10) not in the display file at all.
        t = make_fixtures.NOW_MS - 5000
        stats = self.replay([
            self.line(t, "req#1 started | openai-chat stream | "
                         "42 messages | thinking medium | tools 10"),
            self.line(t + 3000, "req#1 done | openai-chat | stop token | "
                         "prompt 100 | output 100 | cache 90 (90.0%) | "
                         "TTFT 50 ms | total 1s | queue 10 ms | "
                         "prefill 100 tok/s | decode 100 tok/s | "
                         "dflash2 accepted 5/10 (50.0%)"),
            self.line(make_fixtures.NOW_MS - 1000,
                      "throughput | 5.0s | decode 100.0 tok/s (500 tok) | "
                      "running 1 (decode-ready 1) | waiting 0 | batch 1.00 | "
                      "host 0.1% (5.0 ms)"),
        ])
        self.assertEqual(stats["server"]["state"], "busy")
        self.assertEqual(stats["runs"], [])
        self.assertNotIn("task", stats)
        self.assertNotIn("last_task", stats)

    def test_a_fresh_gauge_gap_does_not_settle_the_run(self):
        # The gauge is fresh and says nothing is running, but the journal
        # still holds a started req# without its terminal: the request
        # started just after the gauge's own sample, so the sample cannot
        # have seen it. The verdict must not read idle on that -- a run
        # must not settle while the journal says work is open.
        t = make_fixtures.NOW_MS - 5000
        stats = self.replay([
            self.line(t, "req#1 started | openai-chat stream"),
            self.line(make_fixtures.NOW_MS - 1000,
                      "throughput | 5.0s | decode 0.0 tok/s (0 tok) | "
                      "running 0 (decode-ready 0) | waiting 0 | batch 1.00 | "
                      "host 0.1% (5.0 ms)"),
        ])
        self.assertEqual(stats["server"]["state"], "busy")
        self.assertEqual(stats["server"]["active_requests"], 1)
        self.assertEqual(stats["runs"], [])

    def test_busy_reads_the_engine_rate_not_the_empty_bucket(self):
        # The decode line lands just before the bucket boundary, so the
        # window (the in-progress bucket only) is empty at the pinned clock.
        # The reading is the engine's own rate from the fresh sample, not a
        # zero: this is the gap that made the bar flicker rate -> "busy".
        stats = self.replay([
            self.line(make_fixtures.NOW_MS - 1000,
                      "throughput | 5.0s | decode 154.1 tok/s (770 tok) | "
                      "running 1 (decode-ready 1) | waiting 0 | batch 1.00 | "
                      "host 0.1% (5.0 ms)"),
        ])
        self.assertEqual(stats["server"]["state"], "busy")
        self.assertEqual(stats["server"]["decode_tok_s"], 154.1)

    def test_a_prefill_only_interval_falls_back_to_the_bucket(self):
        # No decode group in the fresh line: the reading falls back to the
        # bucket window, so the bar holds the last rate across a
        # prefill-only interval instead of dropping to the word "prefill".
        # The decode line is timestamped at the pinned clock so its tokens
        # sit in the current bucket at that clock (it is processed first:
        # the sample is whichever line the journal saw last).
        stats = self.replay([
            self.line(make_fixtures.NOW_MS,
                      "throughput | 5.0s | decode 200.0 tok/s (1000 tok) | "
                      "running 1 (decode-ready 1) | waiting 0 | batch 1.00 | "
                      "host 0.1% (5.0 ms)"),
            self.line(make_fixtures.NOW_MS - 1000,
                      "throughput | 5.0s | prefill 900.0 tok/s (4500 tok) | "
                      "running 1 (prefill 1) | waiting 0 | batch 1.00 | "
                      "host 0.1% (5.0 ms)"),
        ])
        self.assertEqual(stats["server"]["state"], "busy")
        self.assertEqual(stats["server"]["decode_tok_s"], 200.0)

    def test_a_run_settled_at_idle_lands_in_the_ring(self):
        # Nothing is running at the pinned clock and the journal holds no
        # open work: the verdict reads idle, the run settles at that tick
        # and lands in the ring with the settled tick as its end. The
        # summary itself (schema 10) stays in the cursor, not the display
        # file.
        t = make_fixtures.NOW_MS - 90000
        stats = self.replay([
            self.line(t, "req#1 started | openai-chat stream"),
            self.line(t + 2000, "req#1 done | openai-chat | stop token | "
                         "prompt 100 | output 100 | TTFT 50 ms | "
                         "decode 100 tok/s"),
        ])
        self.assertEqual(stats["server"]["state"], "idle")
        self.assertNotIn("task", stats)
        self.assertNotIn("last_task", stats)
        self.assertEqual(len(stats["runs"]), 1)
        self.assertEqual(stats["runs"][0]["completed_at_ms"],
                         make_fixtures.NOW_MS)


class TestAgainstTheFixture(unittest.TestCase):
    """Every retained line must parse or be deliberately ignored."""

    @classmethod
    def setUpClass(cls):
        with open(FIXTURE, "r", encoding="utf-8") as handle:
            cls.lines = [line.rstrip("\n") for line in handle if line.strip()]

    def test_every_request_line_parses(self):
        unparsed = []
        for line in self.lines:
            if "req#" not in line and "throughput |" not in line:
                continue
            event = collect.parse_message(collect.split_prefix(line)[1])
            if event is None or event["kind"] == "unseen":
                unparsed.append(line)
        self.assertEqual(unparsed, [], "unparsed lines: %r" % unparsed[:3])

    def test_counts_match_the_fixture(self):
        kinds = {}
        for line in self.lines:
            event = collect.parse_message(collect.split_prefix(line)[1])
            if event:
                kinds[event["kind"]] = kinds.get(event["kind"], 0) + 1
        self.assertEqual(kinds.get("started"), 1413)
        self.assertEqual(kinds.get("done"), 1409)
        self.assertEqual(kinds.get("terminal"), 7)
        self.assertEqual(kinds.get("throughput"), 1371)

    def test_only_zero_output_requests_lack_a_decode_rate(self):
        """A `done` line without `decode` is a real shape, not a miss.

        req#183 in the fixture was cancelled after prefill: `output 0`, no
        decode group, no spec group, and a bare `cache 0 (0.0%)` with no
        second parenthetical. It must parse, with nulls where the fields are.
        """
        for line in self.lines:
            event = collect.parse_message(collect.split_prefix(line)[1])
            if not event or event["kind"] != "done":
                continue
            if event["decode_tok_s"] is None:
                self.assertEqual(event["output_tokens"], 0, line)
                self.assertEqual(event["outcome"], "cancelled")
                self.assertEqual(event["cache_pct"], 0.0)
                self.assertIsNone(event["spec_backend"])
            else:
                self.assertGreater(event["output_tokens"], 0, line)


class TestGoldenContract(unittest.TestCase):
    """The state file is a contract; the QML reads it and cannot ask again.

    Replaying the whole journal fixture at a pinned clock must reproduce
    tests/fixtures/stats-golden.json byte for byte. When a change here is
    deliberate, `python3 tests/make_fixtures.py` re-freezes it and the diff
    shows exactly what the widget's inputs gained or lost.

    The replay itself comes from make_fixtures, so the file and the test that
    checks it cannot be produced by two subtly different procedures.
    """

    def replay(self):
        return make_fixtures.replay()

    def test_replay_matches_the_frozen_state_file(self):
        with open(GOLDEN, "r", encoding="utf-8") as handle:
            golden = json.load(handle)
        self.assertEqual(self.replay(), golden,
                         "state contract drifted; rerun tests/make_fixtures.py")

    def test_the_golden_is_what_the_journal_says(self):
        golden = self.replay()
        requests = golden["requests"]
        # 7 terminal lines in the fixture: 2x HTTP 400, 2x HTTP 503, 3x HTTP 499.
        self.assertEqual(requests["rejected"], 2)
        self.assertEqual(requests["failed"], 2)
        self.assertEqual(requests["client_disconnects"], 3)
        # req# is the server's own counter, so the high water mark is the count.
        self.assertEqual(requests["total_this_invocation"], 1162)
        self.assertIsNotNone(requests["accept_pct"])
        self.assertEqual(len(golden["history"]["decode_tok_s"]), collect.HISTORY_BUCKETS)
        # The replay ends mid-run: at the pinned clock the verdict still
        # reads queued, so the run is open and nothing has settled. The
        # open run's summary is not part of the display contract (schema
        # 10) -- the table reads `runs`, and the run will ring when it
        # settles; the summary itself stays in the cursor.
        self.assertEqual(golden["server"]["state"], "queued")
        self.assertNotIn("task", golden)
        self.assertNotIn("last_task", golden)
        self.assertEqual(golden["runs"], [])
        # Schema 10: the Settings tab's config is part of the contract. The
        # replay probes nothing, so the config reads its endpoint from the
        # default port only.
        self.assertEqual(set(golden["config"]), {
            "model", "weight_profile", "max_context_tokens", "kv_dtype",
            "kv_capacity_tokens", "max_concurrency", "mtp_draft_tokens",
            "lm_head_draft", "gpu", "endpoint",
            "weights_gib", "kv_gib", "host_kv_gib", "host_state_gib"})
        # The budget rows are the fixture journal's own boot readings.
        self.assertEqual(golden["config"]["weights_gib"], 21.4)
        self.assertEqual(golden["config"]["kv_gib"], 7.31)
        self.assertEqual(golden["config"]["host_kv_gib"], 32.0)
        self.assertEqual(golden["config"]["host_state_gib"], 5.84)
        # Closed runs, when there are any, project to the table's own
        # columns, newest first, trimmed to the cap at the tail.
        for run in golden["runs"]:
            self.assertEqual(set(run), {
                "completed_at_ms", "when_label", "tok_s_avg", "tok_s_max",
                "accept_pct_avg", "accept_pct_max", "temp_avg_c",
                "temp_max_c", "queue_wait_ms_avg", "queue_wait_ms_max"})

    def test_the_state_file_carries_only_what_the_widget_reads(self):
        # Everything here has a reader in Panel.qml or util/format.js. The
        # request records that used to make up 80% of the file do not, and
        # neither (schema 10) do the per-run summaries task and last_task --
        # they were the file's heaviest sections and nothing displayed read
        # them; they stay one cat away in the cursor.
        golden = self.replay()
        self.assertNotIn("task", golden)
        self.assertNotIn("last_task", golden)
        self.assertEqual(set(golden["requests"]), {
            "total_this_invocation", "failed", "rejected", "client_disconnects",
            "accept_pct", "accept_alarm"})
        self.assertEqual(set(golden["history"]), {"window_sec", "decode_tok_s"})

    def test_the_state_file_stays_small(self):
        # Written once a second, parsed by the QML once a second. The previous
        # runs table and the config block are contract (schema 10); even so
        # the file stays an order of magnitude under the 16 KiB budget.
        payload = json.dumps(self.replay(), separators=(",", ":"))
        self.assertLess(len(payload.encode("utf-8")), 4 * 1024)


class TestStateFixtures(unittest.TestCase):
    """Each display fixture must be a state file the widget could really see."""

    KEYS = ("schema_version", "generated_at_ms", "server", "gpu", "requests",
            "config", "runs", "history", "debug")

    def fixtures(self):
        directory = os.path.join(HERE, "fixtures", "states")
        for name in sorted(os.listdir(directory)):
            with open(os.path.join(directory, name), "r", encoding="utf-8") as handle:
                yield name, json.load(handle)

    def test_shape(self):
        seen = 0
        for name, stats in self.fixtures():
            seen += 1
            for key in self.KEYS:
                self.assertIn(key, stats, name)
            self.assertEqual(stats["schema_version"], collect.SCHEMA_VERSION, name)
            self.assertEqual(len(stats["history"]["decode_tok_s"]),
                             collect.HISTORY_BUCKETS, name)
            self.assertEqual(set(stats["config"]), {
                "model", "weight_profile", "max_context_tokens", "kv_dtype",
                "kv_capacity_tokens", "max_concurrency", "mtp_draft_tokens",
                "lm_head_draft", "gpu", "endpoint",
                "weights_gib", "kv_gib", "host_kv_gib", "host_state_gib"}, name)
        self.assertEqual(seen, 12)

    def test_named_state_matches_the_state_field(self):
        for name, stats in self.fixtures():
            expected = {"busy-single": "busy", "accept-degraded": "busy",
                        "gpu-stale": "busy",
                        "temp-danger": "busy",
                        "unhealthy": "idle",
                        "journal-stale": "idle"}.get(name[:-5], name[:-5])
            self.assertEqual(stats["server"]["state"], expected, name)

    def test_each_fixture_is_reachable_from_its_own_signals(self):
        # The fixtures are hand-tuned; the derivation must still agree that
        # those signals produce that state, or the display suite would be
        # testing against states the collector can never emit.
        for name, stats in self.fixtures():
            server = stats["server"]
            self.assertEqual(
                collect.derive_state(server["active_state"], server["healthy"],
                                     server["active_requests"], server["queued_requests"]),
                server["state"], name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
