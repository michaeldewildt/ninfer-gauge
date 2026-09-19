.pragma library

// Every number the widget shows passes through here, so the bar and the
// popout can never disagree about what 8330 tok/s or 29877 MiB reads as.
// The popout is a two-tab surface (Telemetry: live tiles and the previous-runs
// table, Settings: the server's boot parameters).
// Pure functions only -- `.pragma library` code sees no QML singletons, and
// tests/test_format.mjs runs this same file under node.

var DASH = "—"   // em dash: the one thing we print for "no number"

function has(value) {
  return value !== undefined && value !== null && value === value
}

// 190 / 8.3k -- rates cross into k at four digits, matching the server's own
// log formatting (`prefill 3.05k tok/s`). At 50 and above, the last digit
// is jitter, not signal (a decode rate lands once per request, then holds);
// rounding to ten kills the per-second twitch without hiding the number.
function fmtTokS(value) {
  if (!has(value)) return DASH
  var n = Number(value)
  if (n < 0) return DASH
  if (n >= 1000) return (n / 1000).toFixed(n >= 10000 ? 0 : 1) + "k"
  if (n >= 50) return String(Math.max(10, Math.round(n / 10) * 10))
  if (n >= 10) return n.toFixed(0)
  return n.toFixed(1)
}

function fmtInt(value) {
  if (!has(value)) return DASH
  // Manual, not toLocaleString: Qt's JS engine locale handling varies by
  // build, and this file must read the same under node and under Quickshell.
  var negative = Number(value) < 0
  var text = String(Math.round(Math.abs(Number(value))))
  var out = ""
  while (text.length > 3) {
    out = "," + text.slice(-3) + out
    text = text.slice(0, -3)
  }
  return (negative ? "-" : "") + text + out
}

function fmtPct(value, digits) {
  if (!has(value)) return DASH
  return Number(value).toFixed(digits === undefined ? 0 : digits) + "%"
}

// Durations keep the server's own vocabulary: us / ms / s / m.
function fmtDur(ms) {
  if (!has(ms)) return DASH
  var n = Number(ms)
  if (n < 1) return Math.round(n * 1000) + " us"
  if (n < 1000) return Math.round(n) + " ms"
  if (n < 60000) return (n / 1000).toFixed(1) + "s"
  var minutes = Math.floor(n / 60000)
  return minutes + "m " + Math.round((n % 60000) / 1000) + "s"
}

function fmtAge(ms) {
  if (!has(ms)) return DASH
  var n = Math.max(0, Number(ms))
  if (n < 1000) return "now"
  if (n < 60000) return Math.round(n / 1000) + "s ago"
  if (n < 3600000) return Math.round(n / 60000) + "m ago"
  return Math.round(n / 3600000) + "h ago"
}

// ------------------------------------------------------------ bar strings
//
// Precedence is the state file's, not ours: the collector already resolved
// down > starting > queued > busy > idle. The bar only chooses wording.

function serverState(snapshot) {
  if (!snapshot || !snapshot.server) return "unknown"
  return String(snapshot.server.state || "unknown")
}

// ⚡ and ⚠ with U+FE0E (text presentation): without it the shaper may
// route either to the colour-emoji font (Noto Color Emoji is installed),
// drawing a yellow bolt or a yellow triangle instead of the tinted glyph.
var BOLT = "\u26A1\uFE0E"
var WARN = "\u26A0\uFE0E"

// The mark and count that report the waiting queue, riding beside the rate
// instead of replacing it. Appended only for a positive count: a file that
// claims queued with zero waiting is corrupt, and "⚠ 0" would read as a
// queue with nothing in it.
function queueSuffix(server) {
  var waiting = Number(server.queued_requests || 0)
  return waiting > 0 ? " · " + WARN + " " + waiting : ""
}

// What the state reads as, without the mark. barText prefixes the bolt onto
// it and the probe reads the button text, so no caller has to take the
// bar's output apart to get at the phrase.
function barPhrase(snapshot) {
  if (!snapshot || !snapshot.server) return ""
  var server = snapshot.server
  var state = serverState(snapshot)
  if (state === "busy" || state === "queued") {
    // The rate is the bar's headline in both states; the word only covers
    // the prefill phase, when a request is in flight and no decode bucket
    // has landed yet.
    var text = Number(server.decode_tok_s || 0) > 0
      ? fmtTokS(server.decode_tok_s) + " t/s"
      : "prefill"
    if (state === "queued") text += queueSuffix(server)
    return text
  }
  if (state === "down" || state === "starting" || state === "stopping" || state === "idle")
    return state
  return ""
}

// No phrase means no usable state file: name the widget and mark it wrong
// rather than dressing a blank up as a reading. `stale` is the same verdict
// arrived at differently -- the file is there but nobody is updating it, so
// what it says is history, not news. A widget whose whole job is answering
// "is the backend up" must not answer from a frozen file.
function barText(snapshot, stale) {
  var phrase = barPhrase(snapshot)
  return (stale === true || phrase === "") ? "✕ ninfer" : BOLT + " " + phrase
}

// Dim states read as "nothing to see": booting, shutting down, or no state
// file at all. The bar renders these at reduced opacity.
function barDimmed(snapshot, stale) {
  if (stale === true) return true
  var state = serverState(snapshot)
  return state === "starting" || state === "stopping" || state === "unknown"
}

// Tint is orthogonal to the state text: a healthy-looking `busy` bar still
// goes urgent when the server stopped answering /health or when
// speculative accept has been sitting under 25%. The queue's mark stays
// plain: a backlog is normal load on a single-GPU box, not a fault.
function barUrgent(snapshot, showAccept) {
  if (!snapshot || !snapshot.server) return false
  var state = serverState(snapshot)
  if (state === "down") return true
  if (snapshot.server.healthy === false && state !== "starting" && state !== "stopping")
    return true
  if (showAccept !== false && snapshot.requests && snapshot.requests.accept_alarm === true)
    return true
  return false
}

// ------------------------------------------------------------- the popout
//
// The popout is a two-tab surface. Telemetry: four live tiles in a 2-column grid
// plus the previous-runs table (the `runs` rows, newest first); the error
// state replaces the tile grid with a banner while the runs table stays.
// Settings: the server's ten boot parameters, read-only. These return the
// strings and tones each QML surface renders; the thresholds live here so
// the node suite and the QML cannot drift apart.

// Thresholds as named constants, not in the QML: the node suite and the
// display share them. Temp is the one live threshold (danger only); the
// runs table reuses it for its Temp column. 87: three degrees under the
// 90 C spec limit of current NVIDIA cards; a lower line goes red in
// ordinary full load.
var TEMP_DANGER_C = 87
// The journal logs a queue time for every request, and a request that
// did not wait still carries one (12–27 ms of scheduling noise). Below
// this floor the Wait column renders a muted dash: not queued at all.
// The collector's QUEUE_WAIT_FLOOR_MS is the same 100 ms: it filters
// these out of the run's wait avg, so a legitimate avg can never read
// below the floor.
var QUEUE_WAIT_MIN_MS = 100

// Tone vocabulary for value text: "ok" (default foreground), "danger"
// (theme red/urgent). Tones color the value text, never the tile
// background.
function tempTone(celsius) {
  if (!has(celsius)) return "ok"
  return Number(celsius) >= TEMP_DANGER_C ? "danger" : "ok"
}

function fmtTemp(celsius) {
  if (!has(celsius)) return DASH
  var n = Number(celsius)
  return isFinite(n) ? Math.round(n) + "\u00B0" : DASH
}

function fmtWatts(watts) {
  if (!has(watts)) return DASH
  var n = Number(watts)
  return isFinite(n) ? Math.round(n) + " W" : DASH
}

// The runs table Wait column, both modes: the max is the run's longest
// per-request queue wait; the avg is the mean of the requests that
// crossed the noise floor (the collector filters it, so a legitimate avg
// never falls under it). Muted dash when nothing queued -- or while the
// avg simply does not exist yet.
function runWaitText(queueWaitMs) {
  if (!has(queueWaitMs) || Number(queueWaitMs) < QUEUE_WAIT_MIN_MS) return DASH
  return fmtDur(queueWaitMs)
}

// The four live tiles in grid order (tok/s, acceptance, temp, watts), each
// { text, tone }. Dashes are the idle story: Tok/s and Acceptance read
// "—" when nothing is generating, and Temp/Watts stay real -- the GPU is
// still there. Acceptance is pooled (the last run's value), so it shows
// only while generating: a pooled 35.3% is a run's story, not the idle
// story. No live indicator anywhere: the values themselves are the state,
// and status already lives in the toolbar.
function liveTiles(snapshot) {
  if (!snapshot || !snapshot.server) return null
  var server = snapshot.server
  var gpu = snapshot.gpu || {}
  var requests = snapshot.requests || {}
  var rate = Number(server.decode_tok_s || 0)
  var generating = server.state === "busy" || server.state === "queued"
  return {
    tokS:   { text: rate > 0 ? fmtTokS(rate) : DASH, tone: "ok" },
    accept: { text: generating ? fmtPct(requests.accept_pct, 1) : DASH, tone: "ok" },
    temp:   { text: fmtTemp(gpu.temp_c), tone: tempTone(gpu.temp_c) },
    watts:  { text: fmtWatts(gpu.watts), tone: "ok" }
  }
}

// One previous-runs row, projected the way the Avg/Max toggle projects
// it: every stat column switches simultaneously between the run's
// average and its max (peaks sampled across the run's duration). Duration
// is the run's own span, not a projection, so it does not switch. Temp
// carries the same red threshold as the live tile.
function runRow(run, mode) {
  if (!run) return null
  var max = mode === "max"
  var tokS = max ? run.tok_s_max : run.tok_s_avg
  var accept = max ? run.accept_pct_max : run.accept_pct_avg
  var temp = max ? run.temp_max_c : run.temp_avg_c
  return {
    when: has(run.when_label) ? String(run.when_label) : DASH,
    duration: fmtDur(run.duration_ms),
    tokS: fmtTokS(tokS),
    accept: fmtPct(accept, 1),
    temp: fmtTemp(temp),
    tempTone: tempTone(temp),
    wait: runWaitText(max ? run.queue_wait_ms_max : run.queue_wait_ms_avg)
  }
}

// The committed-at-boot tiles: the engine's own boot-time readings, the
// Settings tab's top section, rendered with the Telemetry tab's own tile UI.
// Static facts, not health -- every tone is ok.

// One GiB reading as the engine logs it: 21.4, 7.31, 32 (not 32.0).
function fmtGib(value) {
  return has(value) ? String(value) + " GiB" : DASH
}

function committedTiles(config) {
  var c = config || {}
  return {
    weights:   { text: fmtGib(c.weights_gib),    tone: "ok" },
    kvPool:    { text: fmtGib(c.kv_gib),         tone: "ok" },
    hostKV:    { text: fmtGib(c.host_kv_gib),    tone: "ok" },
    hostState: { text: fmtGib(c.host_state_gib), tone: "ok" }
  }
}

// The Settings tab's lower section: the engine's arguments in launch order,
// read-only. The endpoint row ends the table -- provenance, not a setting,
// but it renders like every other row.
function settingsRows(config) {
  var c = config || {}
  return [
    { label: "Model",            value: has(c.model) ? String(c.model) : DASH },
    { label: "Weight profile",   value: has(c.weight_profile) ? String(c.weight_profile) : DASH },
    { label: "Max context",      value: has(c.max_context_tokens) ? fmtInt(c.max_context_tokens) + " tokens" : DASH },
    { label: "KV dtype",         value: has(c.kv_dtype) ? String(c.kv_dtype) : DASH },
    { label: "KV capacity",      value: has(c.kv_capacity_tokens) ? fmtInt(c.kv_capacity_tokens) + " tokens" : DASH },
    { label: "Max concurrency",  value: has(c.max_concurrency) ? fmtInt(c.max_concurrency) : DASH },
    { label: "MTP draft tokens", value: has(c.mtp_draft_tokens) ? fmtInt(c.mtp_draft_tokens) : DASH },
    { label: "LM head draft",    value: c.lm_head_draft === true ? "on" : c.lm_head_draft === false ? "off" : DASH },
    { label: "GPU",              value: has(c.gpu) ? String(c.gpu) : DASH },
    { label: "Endpoint",         value: has(c.endpoint) ? String(c.endpoint) : DASH }
  ]
}

// The error-state banner replaces the tile grid: one line, the endpoint
// it could not reach. The runs table below it stays: cached history, not
// a live reading.
function errorBannerText(config) {
  var endpoint = config && has(config.endpoint) ? String(config.endpoint) : DASH
  return "Can't reach ninfer at " + endpoint
}




