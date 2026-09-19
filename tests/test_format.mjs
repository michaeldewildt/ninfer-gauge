// Bar and popout strings, tested against the same state fixtures the display
// suite uses. Run: node tests/test_format.mjs
//
// util/format.js is QML JS (`.pragma library`); stripping that one line makes
// it a plain script this runner can evaluate.

import { test } from "node:test"
import assert from "node:assert/strict"
import { readFileSync, readdirSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import vm from "node:vm"

const here = dirname(fileURLToPath(import.meta.url))
const source = readFileSync(join(here, "..", "util", "format.js"), "utf8")
  .replace(/^\.pragma library\s*$/m, "")
const Format = {}
vm.runInNewContext(source + "\nthis.exports = { fmtTokS, fmtInt, fmtPct, fmtDur,"
  + " fmtAge, barText, barPhrase, barDimmed, barUrgent,"
  + " serverState,"
  + " TEMP_DANGER_C, QUEUE_WAIT_MIN_MS,"
  + " tempTone, fmtTemp, fmtWatts,"
  + " runWaitText, liveTiles, committedTiles, runRow, settingsRows, errorBannerText,"
  + " BOLT, WARN }", Format)
const F = Format.exports

const states = {}
const statesDir = join(here, "fixtures", "states")
for (const name of readdirSync(statesDir)) {
  states[name.replace(/\.json$/, "")] = JSON.parse(
    readFileSync(join(statesDir, name), "utf8"))
}

test("rates cross into k the way the server's own log does", () => {
  assert.equal(F.fmtTokS(193.4), "190")
  assert.equal(F.fmtTokS(8330), "8.3k")
  assert.equal(F.fmtTokS(46550), "47k")
  assert.equal(F.fmtTokS(9.13), "9.1")
  assert.equal(F.fmtTokS(null), "—")
})

test("fmtTokS rounds to ten at 50 and above -- jitter, not signal", () => {
  assert.equal(F.fmtTokS(49), "49")
  assert.equal(F.fmtTokS(50), "50")
  assert.equal(F.fmtTokS(54), "50")
  assert.equal(F.fmtTokS(55), "60")
  assert.equal(F.fmtTokS(219.4), "220")
  assert.equal(F.fmtTokS(158.4), "160")
})

test("durations keep the server's vocabulary", () => {
  assert.equal(F.fmtDur(0.667), "667 us")
  assert.equal(F.fmtDur(621), "621 ms")
  assert.equal(F.fmtDur(5500), "5.5s")
  assert.equal(F.fmtDur(94000), "1m 34s")
  assert.equal(F.fmtDur(undefined), "—")
})

test("thousands group the way the server's own log does", () => {
  assert.equal(F.fmtInt(97), "97")
  assert.equal(F.fmtInt(64601), "64,601")
  assert.equal(F.fmtInt(1234567), "1,234,567")
  assert.equal(F.fmtInt(0), "0")
  assert.equal(F.fmtInt(null), "—")
  assert.equal(F.fmtInt(undefined), "—")
})

test("the bar prefixes the mark onto the phrase the popup shows alone", () => {
  // The popup used to recover this by regex-stripping barText's output.
  for (const name of ["down", "starting", "queued", "busy", "idle"]) {
    assert.ok(F.barText(states[name]).endsWith(F.barPhrase(states[name])),
      name + ": barText must end with barPhrase")
  }
  // The queue's mark and count ride beside the rate; the queued fixture is
  // the prefill-only shape (decode_tok_s 0.0), so the word covers the rate.
  assert.equal(F.barPhrase(states["queued"]), "prefill · \u26A0\uFE0E 2")
  assert.equal(F.barPhrase(states["busy"]), "190 t/s")
  assert.equal(F.barPhrase(states["idle"]), "idle")
  // An empty phrase is what makes barText fall back to "✕ ninfer".
  assert.equal(F.barPhrase(null), "")
})

test("every state fixture renders its own bar string", () => {
  assert.equal(F.barText(states["down"]), "\u26A1\uFE0E down")
  assert.equal(F.barText(states["starting"]), "\u26A1\uFE0E starting")
  assert.equal(F.barText(states["stopping"]), "\u26A1\uFE0E stopping")
  assert.equal(F.barText(states["queued"]), "\u26A1\uFE0E prefill · \u26A0\uFE0E 2")
  assert.equal(F.barText(states["busy"]), "\u26A1\uFE0E 190 t/s")
  // Renders like busy: the lane suffix is gone, and the fixture guards
  // that it never comes back.
  assert.equal(F.barText(states["busy-single"]), "\u26A1\uFE0E 190 t/s")
  assert.equal(F.barText(states["idle"]), "\u26A1\uFE0E idle")
})

test("a prefill-only bucket reads prefill, not 0.0 t/s", () => {
  const s = JSON.parse(JSON.stringify(states["busy"]))
  s.server.decode_tok_s = 0
  assert.equal(F.barPhrase(s), "prefill")
  assert.equal(F.barText(s), "\u26A1\uFE0E prefill")
  s.server.decode_tok_s = null
  assert.equal(F.barPhrase(s), "prefill")
})

test("the queue rides beside the rate instead of replacing it", () => {
  // The golden shape: a request waiting while another decodes.
  const s = JSON.parse(JSON.stringify(states["queued"]))
  s.server.decode_tok_s = 193.4
  assert.equal(F.barPhrase(s), "190 t/s · \u26A0\uFE0E 2")
  assert.equal(F.barText(s), "\u26A1\uFE0E 190 t/s · \u26A0\uFE0E 2")
  // A file that claims queued with nothing waiting is corrupt: the mark
  // must not read as a queue with no queue.
  s.server.queued_requests = 0
  assert.equal(F.barPhrase(s), "190 t/s")
  s.server.decode_tok_s = 0
  assert.equal(F.barPhrase(s), "prefill")
})

test("queued is plain, never dimmed, and the /health fault still tints", () => {
  assert.equal(F.barDimmed(states["queued"]), false)
  // The /health fault still tints the queue: the tint is orthogonal to the
  // mark, and a queue that cannot reach /health is news either way.
  const s = JSON.parse(JSON.stringify(states["queued"]))
  s.server.healthy = false
  assert.equal(F.barUrgent(s), true)
})

test("a state file nobody is updating is not a reading", () => {
  // The collector exits 0 on failure by design, so a frozen file is the
  // shape a dead collector takes. A stale `idle` must not read as healthy.
  assert.equal(F.barText(states["idle"], true), "✕ ninfer")
  assert.equal(F.barDimmed(states["idle"], true), true)
  // ... and a fresh one is untouched by the same argument.
  assert.equal(F.barText(states["idle"], false), F.barText(states["idle"]))
  assert.equal(F.barDimmed(states["idle"], false), false)
})

test("no state file at all still says something honest", () => {
  assert.equal(F.barText(null), "✕ ninfer")
  assert.equal(F.barText({}), "✕ ninfer")
  assert.ok(F.barDimmed(null))
})

test("tint is orthogonal to the state text", () => {
  assert.equal(F.barUrgent(states["idle"]), false)
  assert.equal(F.barUrgent(states["busy"]), false)
  assert.equal(F.barUrgent(states["down"]), true)
  // A queue is normal load on a single-GPU box, not a fault: the mark and
  // count ride in plain foreground.
  assert.equal(F.barUrgent(states["queued"]), false)
  // A unit that is up but has stopped answering /health is news.
  assert.equal(F.barUrgent(states["unhealthy"]), true)
  // Speculative accept sitting under 25% tints without changing the words.
  assert.equal(F.barText(states["accept-degraded"]), "\u26A1\uFE0E 190 t/s")
  assert.equal(F.barUrgent(states["accept-degraded"]), true)
  // ... and the setting switches that tint off without touching the rest.
  assert.equal(F.barUrgent(states["accept-degraded"], false), false)
})

test("booting and shutting down are dim, not urgent", () => {
  assert.equal(F.barDimmed(states["starting"]), true)
  assert.equal(F.barDimmed(states["stopping"]), true)
  assert.equal(F.barUrgent(states["starting"]), false)
  assert.equal(F.barUrgent(states["stopping"]), false)
  assert.equal(F.barDimmed(states["idle"]), false)
})

// ------------------------------------------------------------ popout tiles

test("the live tiles are the one source of truth for the Telemetry tab", () => {
  const s = F.liveTiles(states["busy"])
  assert.equal(s.tokS.text, "190")
  assert.equal(s.tokS.tone, "ok")
  // Acceptance shows while generating: the pooled value of the run.
  assert.equal(s.accept.text, F.fmtPct(states["busy"].requests.accept_pct, 1))
  assert.equal(s.temp.text, "47\u00B0")
  assert.equal(s.watts.text, "186 W")
  assert.equal(F.liveTiles(null), null)
  assert.equal(F.liveTiles({}), null)
})

test("idle reads dashes for generation, real numbers for the GPU", () => {
  const s = F.liveTiles(states["idle"])
  assert.equal(s.tokS.text, "\u2014")
  assert.equal(s.accept.text, "\u2014")
  // Temp and watts are live even when idle: the GPU is still there.
  assert.notEqual(s.temp.text, "\u2014")
  assert.notEqual(s.watts.text, "\u2014")
})

test("temp danger is one threshold, shared by tile and runs table", () => {
  assert.equal(F.tempTone(86), "ok")
  assert.equal(F.tempTone(87), "danger")
  assert.equal(F.tempTone(null), "ok")
  assert.equal(F.liveTiles(states["temp-danger"]).temp.tone, "danger")
  assert.equal(F.liveTiles(states["temp-danger"]).temp.text, "88\u00B0")
})

test("temp and watts format, and dash on absence", () => {
  assert.equal(F.fmtTemp(47.4), "47\u00B0")
  assert.equal(F.fmtWatts(186.4), "186 W")
  assert.equal(F.fmtTemp(null), "\u2014")
  assert.equal(F.fmtWatts(undefined), "\u2014")
})

// ---------------------------------------------------------- popout runs

test("run rows project avg or max, every stat column at once", () => {
  const run = { when_label: "2 min ago", duration_ms: 94000,
                tok_s_avg: 187.2, tok_s_max: 210.4,
                accept_pct_avg: 64.1, accept_pct_max: 71.3,
                temp_avg_c: 48.2, temp_max_c: 52.9,
                queue_wait_ms_avg: 1100, queue_wait_ms_max: 1800 }
  const avg = F.runRow(run, "avg")
  assert.equal(avg.when, "2 min ago")
  // Duration is the run's own span, not a projection: the toggle does
  // not switch it, and it reads the fmtDur vocabulary (1m 34s).
  assert.equal(avg.duration, "1m 34s")
  assert.equal(avg.tokS, "190")
  assert.equal(avg.accept, "64.1%")
  assert.equal(avg.temp, "48\u00B0")
  // Wait toggles like the rest: two queued requests in this run, 1100 of
  // the 1800 longest.
  assert.equal(avg.wait, "1.1s")
  const max = F.runRow(run, "max")
  assert.equal(max.duration, "1m 34s")
  assert.equal(max.tokS, "210")
  assert.equal(max.accept, "71.3%")
  assert.equal(max.temp, "53\u00B0")
  assert.equal(max.wait, "1.8s")
  assert.equal(F.runRow(null, "avg"), null)
  // A row written before the field existed (or without a span) dashes,
  // like every other absent reading.
  const { duration_ms, ...noDuration } = run
  assert.equal(F.runRow(noDuration, "avg").duration, "\u2014")
})

test("a run that was never queued reads a muted dash in Wait", () => {
  // The journal logs a queue time for every request, and a request that
  // did not wait still carries one -- below the noise floor it is "not
  // queued at all", not "queued for 18 ms".
  assert.equal(F.runWaitText(18), "\u2014")
  assert.equal(F.runWaitText(99), "\u2014")
  assert.equal(F.runWaitText(100), "100 ms")
  assert.equal(F.runWaitText(1800), "1.8s")
  assert.equal(F.runWaitText(null), "\u2014")
  assert.equal(F.runWaitText(undefined), "\u2014")
})

test("a wait max without its avg reads a number and a dash", () => {
  // A ring row projected before schema 12 carries only the max; so does an
  // in-flight run whose avg never crossed the noise floor. The max cell
  // reads its number, the avg cell dashes -- the display never invents an
  // average the collector never computed.
  const run = { when_label: "now", duration_ms: 30000,
                tok_s_avg: 100, tok_s_max: 120,
                accept_pct_avg: 60, accept_pct_max: 65,
                temp_avg_c: 48, temp_max_c: 52,
                queue_wait_ms_avg: null, queue_wait_ms_max: 1800 }
  assert.equal(F.runRow(run, "avg").wait, "\u2014")
  assert.equal(F.runRow(run, "max").wait, "1.8s")
})

test("a run's temp column keeps the live tile's red threshold", () => {
  const cool = { when_label: "now", tok_s_avg: 100, tok_s_max: 120,
                 accept_pct_avg: 60, accept_pct_max: 65,
                 temp_avg_c: 48, temp_max_c: 86.9 }
  const hot = { when_label: "now", tok_s_avg: 100, tok_s_max: 120,
                 accept_pct_avg: 60, accept_pct_max: 65,
                 temp_avg_c: 87.1, temp_max_c: 91 }
  // The displayed value is what tints: avg 48 / max 86.9 both read ok...
  assert.equal(F.runRow(cool, "avg").tempTone, "ok")
  assert.equal(F.runRow(cool, "max").tempTone, "ok")
  // ...and the same run reads danger the moment the displayed side
  // crosses 87.
  assert.equal(F.runRow(hot, "avg").tempTone, "danger")
  assert.equal(F.runRow(hot, "max").tempTone, "danger")
})

test("the fixture runs table newest-first renders through runRow", () => {
  const runs = states["busy"].runs
  assert.ok(Array.isArray(runs) && runs.length > 0)
  for (const r of runs) {
    const row = F.runRow(r, "avg")
    assert.ok(row.when && row.tokS && row.accept, "row cells present")
  }
  // Wait follows the toggle like the other stats: the newest row queued
  // two requests (avg 1.1s of max 1.8s); the oldest queued one, so its
  // avg and max agree.
  assert.equal(F.runRow(runs[0], "avg").wait, "1.1s")
  assert.equal(F.runRow(runs[0], "max").wait, "1.8s")
  assert.equal(F.runRow(runs[2], "avg").wait, "420 ms")
  assert.equal(F.runRow(runs[2], "max").wait, "420 ms")
  // Newest first: completed_at_ms is non-increasing down the table.
  for (let i = 1; i < runs.length; i++)
    assert.ok(runs[i].completed_at_ms <= runs[i - 1].completed_at_ms)
})

// ------------------------------------------------------- popout settings

test("the settings rows are the ten engine arguments, in launch order", () => {
  const rows = F.settingsRows(states["busy"].config)
  // Cross-realm objects from the vm context: compare as JSON strings, the
  // way this file has always compared assembled lines.
  assert.equal(
    JSON.stringify(rows.map(r => r.label)),
    JSON.stringify(["Model", "Weight profile", "Max context", "KV dtype",
      "KV capacity", "Max concurrency", "MTP draft tokens", "LM head draft",
      "GPU", "Endpoint"]))
  // Tokens keep their separators; every row renders like the rest.
  assert.equal(rows[2].value, "131,072 tokens")
  assert.equal(rows[4].value, "181,568 tokens")
  assert.equal(rows[7].value, "on")
})

test("the committed tiles carry the budget readings in the Telemetry tab's tile shape", () => {
  // { text, tone } pairs, the same contract as liveTiles: the QML's Tile
  // renders text and reads tone, nothing else.
  const t = F.committedTiles(states["busy"].config)
  assert.equal(t.weights.text, "21.4 GiB")
  assert.equal(t.kvPool.text, "7.31 GiB")
  assert.equal(t.hostKV.text, "32 GiB")
  assert.equal(t.hostState.text, "5.84 GiB")
  // Static facts, not health: every tone is ok.
  assert.ok(Object.keys(t).every(k => t[k].tone === "ok"))
  // A missing config reads dashes, not blanks; a partial one, per tile.
  const none = F.committedTiles(null)
  assert.ok(Object.keys(none).every(k => none[k].text === "\u2014"))
  const partial = F.committedTiles({ weights_gib: 21.4 })
  assert.equal(partial.weights.text, "21.4 GiB")
  assert.equal(partial.kvPool.text, "\u2014")
})

test("a missing config renders dashes, not blanks", () => {
  const rows = F.settingsRows(null)
  assert.equal(rows.length, 10)
  assert.ok(rows.every(r => r.value === "\u2014"))
})

test("the error banner names the endpoint it could not reach", () => {
  assert.equal(F.errorBannerText(states["down"].config),
    "Can't reach ninfer at 127.0.0.1:8080")
  assert.equal(F.errorBannerText(null), "Can't reach ninfer at \u2014")
})

test("the warn glyph carries the text-presentation selector like the bolt", () => {
  assert.equal(F.WARN, "\u26A0\uFE0E")
  assert.equal(F.BOLT, "\u26A1\uFE0E")
})

test("the settings tab renders the invocation the way the collector carries it", () => {
  // The real serve line of this box, as the collector's config fold
  // carries it: numbers keep their separators, booleans read on/off, the
  // GPU name comes from nvidia-smi.
  const config = { model: "qwen3.8-27b", weight_profile: "nvfp4",
                   max_context_tokens: 131072, kv_dtype: "fp8",
                   kv_capacity_tokens: 181568, max_concurrency: 2,
                   mtp_draft_tokens: 7, lm_head_draft: true,
                   gpu: "NVIDIA GeForce RTX 5090", endpoint: "127.0.0.1:8080" }
  // The committed budget rides the tiles, not these rows: the args-only
  // fold is what the settings rows read.
  const flat = F.settingsRows(config).map(r => r.label + "=" + r.value)
  assert.equal(flat[0], "Model=qwen3.8-27b")
  // Tokens are contracts, not rates: separators, unit word.
  assert.equal(flat[2], "Max context=131,072 tokens")
  assert.equal(flat[4], "KV capacity=181,568 tokens")
  assert.equal(flat[7], "LM head draft=on")
  assert.equal(flat[8], "GPU=NVIDIA GeForce RTX 5090")
  assert.equal(flat[9], "Endpoint=127.0.0.1:8080")
  // A flag not passed reads off; a missing field reads a dash, not a
  // blank or a zero.
  const off = F.settingsRows({ lm_head_draft: false })
  assert.equal(off[7].value, "off")
  assert.equal(F.settingsRows({})[3].value, "\u2014")
  assert.equal(F.settingsRows(null)[5].value, "\u2014")
})

