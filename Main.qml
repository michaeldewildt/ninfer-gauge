import QtQuick
import Quickshell
import Quickshell.Io

// The data side. All extraction lives in collect/omarchy-ninfer-stats-update,
// which we spawn once per refresh; this file only triggers it and watches the
// state file it writes. Same shape as the first-party Agent.qml: the FileView
// is the single binding source, the Process is nothing but a trigger.
//
// Nothing here draws. Panel.qml reads `snapshot` and never learns where the
// numbers came from.
Item {
  id: root
  visible: false

  // Resolved by Panel, which has the base class's setting() -- one reading
  // of each setting, not one per file.
  property int refreshMs: 1000
  property int gpuIndex: 0
  property bool showGpu: true
  property string fixturePath: ""

  readonly property string home: Quickshell.env("HOME") || ""
  readonly property string stateDir:
      (Quickshell.env("XDG_STATE_HOME") || home + "/.local/state") + "/omarchy/ninfer"

  // The repo is the installed plugin directory, so the collector ships beside
  // this file and is found relative to it -- no ~/.local/bin install.
  readonly property string pluginDir: {
    var url = String(Qt.resolvedUrl("."))
    var path = url.indexOf("file://") === 0 ? url.substring(7) : url
    return path.charAt(path.length - 1) === "/" ? path : path + "/"
  }
  readonly property string collectorPath: pluginDir + "collect/omarchy-ninfer-stats-update"

  // A `fixturePath` drives the widget from a hand-written state file
  // (tests/fixtures/states/*.json) with no collector in the loop -- that is
  // the display test suite.
  readonly property bool fixtureMode: fixturePath !== ""
  readonly property string statePath: fixtureMode ? fixturePath : stateDir + "/stats.json"

  property var snapshot: null

  // A clock the bindings can depend on: `snapshotAgeMs` has to change on its
  // own when the file stops changing, which is exactly the case that matters.
  property real nowMs: Date.now()
  readonly property real snapshotAgeMs:
      snapshot && snapshot.generated_at_ms ? Math.max(0, nowMs - snapshot.generated_at_ms) : -1

  // The collector exits 0 on any failure by design -- it must never look
  // like a dead server. The cost is that a collector which has stopped
  // running leaves a perfectly readable state file behind, and a frozen
  // `idle` is indistinguishable from a healthy one. Age is the only thing
  // that tells them apart. Five missed refreshes, so a slow run never trips it.
  readonly property bool snapshotStale:
      !fixtureMode && snapshotAgeMs > refreshMs * 5
  // ------------------------------------------------------------- collection

  Process {
    id: collector
    running: false
    // The FileView watch is the binding source, but it can only report
    // changes to a file that existed when it started watching. Reloading
    // after each run covers the first-ever write (and any notification the
    // watcher misses) instead of leaving the widget blank until a restart.
    onExited: stateFile.reload()
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var message = String(text || "").trim()
        // The state file carries its own errors in `debug`; this is for the
        // ones that stop it being written at all.
        if (message !== "") console.warn("michaeldewildt.ninfer-gauge", message)
      }
    }
  }

  function collectorCommand() {
    var command = ["python3", root.collectorPath, "--state-dir", root.stateDir,
                   "--gpu-index", String(root.gpuIndex)]
    if (!root.showGpu) command.push("--no-gpu")
    return command
  }

  function refresh() {
    root.nowMs = Date.now()
    if (root.fixtureMode || collector.running) return
    collector.command = root.collectorCommand()
    collector.running = true
  }

  Timer {
    interval: root.refreshMs
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  // ---------------------------------------------------------------- watcher

  FileView {
    id: stateFile
    path: root.statePath
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: root.parse(text())
    // No state file yet (first run, or the state dir was wiped). Not an
    // error worth shouting about -- the bar says so on its own.
    onLoadFailed: root.snapshot = null
  }

  function parse(content) {
    try {
      var parsed = JSON.parse(String(content || ""))
      root.snapshot = parsed && typeof parsed === "object" ? parsed : null
    } catch (error) {
      // A half-written file cannot happen (the collector writes via rename),
      // so a parse failure means a genuinely bad file: keep the last good
      // snapshot rather than blanking the widget.
      console.warn("michaeldewildt.ninfer-gauge", "Ignoring bad state file", root.statePath, error)
    }
    root.nowMs = Date.now()
  }
}
