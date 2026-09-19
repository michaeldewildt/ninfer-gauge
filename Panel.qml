import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "util/format.js" as Format

// The display side. Bar button plus a two-tab popout (Telemetry: four live tiles
// and the previous-runs table; Settings: the committed-at-boot budget as
// tiles, then the engine's arguments, read-only). No I/O node lives here
// -- every number on screen comes from Main's `snapshot`, and every string
// that renders one goes through util/format.js so the bar and the popout
// can never disagree.
Panel {
  id: root
  moduleName: "michaeldewildt.ninfer-gauge"
  ipcTarget: "michaeldewildt.ninfer-gauge"
  manageIpc: false   // this panel owns the target: probe() lives below

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  readonly property var snapshot: stats.snapshot
  readonly property var server: snapshot && snapshot.server ? snapshot.server : null
  readonly property var gpu: snapshot && snapshot.gpu ? snapshot.gpu : null
  readonly property var requests: snapshot && snapshot.requests ? snapshot.requests : null
  readonly property var config: snapshot && snapshot.config ? snapshot.config : null
  readonly property var generatedAtMs: snapshot ? snapshot.generated_at_ms : null

  readonly property bool showGpu: setting("showGpu", true) !== false
  readonly property bool showAccept: setting("showAccept", true) !== false
  readonly property bool stateDown: !!server && server.state === "down"

  // Discriminates the running component build in the probe output -- the QML
  // disk cache can serve a stale compiled component after a hot reload, and
  // the symptom is a fix that appears not to have landed.
  readonly property int buildTag: 1

  // Popout state. Telemetry is the default tab and Avg the default projection,
  // reset on every open so the popout lands on the live view.
  property int activeTab: 0
  property int runMode: 0
  onOpenedChanged: {
    if (opened) {
      activeTab = 0
      runMode = 0
      refreshConfigRows()
      refreshRuns()
    }
  }

  // The Settings rows and the runs table are cached: the data behind them
  // changes rarely (a boot line, a completed run), and rebuilding those
  // delegates at 1 Hz would be exactly the churn the Loader exists to
  // avoid. The runs "When" column still ticks: each row re-reads
  // generatedAtMs through fmtAge, which is a text binding, not a rebuild.
  property var configRows: []
  readonly property string configKey: config ? JSON.stringify(config) : ""
  function refreshConfigRows() { configRows = Format.settingsRows(config) }
  onConfigKeyChanged: refreshConfigRows()

  property var runsModel: []
  readonly property string runsKey: (snapshot && snapshot.runs)
      ? snapshot.runs.map(function(r) { return r.completed_at_ms }).join(",")
      : ""
  function refreshRuns() {
    runsModel = (snapshot && snapshot.runs) ? snapshot.runs : []
  }
  onRunsKeyChanged: refreshRuns()

  // Runs-table column geometry: six equal cells. The font is monospace,
  // so right-alignment inside equal widths is the whole alignment story --
  // the header row and every RunRow compute the cell from the same row
  // width, so the grid holds even when the popout is capped narrow.
  readonly property int colCount: 6

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  // `id: stats`, not `data` -- Item already has a `data` property (its default
  // children list), and shadowing it makes bindings resolve to the wrong thing.
  Main {
    id: stats
    refreshMs: Math.max(500, Number(root.setting("refreshMs", 1000)))
    gpuIndex: Math.max(0, Number(root.setting("gpuIndex", 0)))
    showGpu: root.showGpu
    fixturePath: String(root.setting("statePath", ""))
  }

  IpcHandler {
    target: root.ipcTarget
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): string { stats.refresh(); return "ok" }

    // The no-screenshot observability channel: one line of live widget state.
    //   omarchy-shell michaeldewildt.ninfer-gauge probe
    function probe(): string {
      var age = stats.snapshotAgeMs
      // The no-scroll acceptance test: does the content in front of the user
      // fit the flick? A Column ignores invisible children, so
      // item.implicitHeight is exactly the content being shown.
      var item = contentLoader.item
      var content = item ? item.implicitHeight : -1
      return ["b" + root.buildTag,
              "bar=" + button.text,
              "state=" + Format.serverState(root.snapshot),
              "urgent=" + button.active,
              "dim=" + button.dimmed,
              "stale=" + stats.snapshotStale,
              "decode=" + (root.server ? root.server.decode_tok_s : "-"),
              "gpu=" + (root.gpu && !root.gpu.stale ? root.gpu.util_pct : "-"),
              "reqs=" + (root.requests ? root.requests.total_this_invocation : "-"),
              "age=" + (age < 0 ? "-" : Math.round(age) + "ms"),
              // The effective widget settings (the `omarchy bar set`
              // surface), plus fixture mode.
              "cfg=" + ["refresh:" + root.setting("refreshMs", 1000),
                        "gpu:" + root.setting("gpuIndex", 0),
                        "showGpu:" + (root.showGpu ? 1 : 0),
                        "showAccept:" + (root.showAccept ? 1 : 0)].join(",")
                    + (stats.fixtureMode ? ",fixture:1" : ""),
              // The popout position: which tab, which projection.
              "tab=" + (root.activeTab === 0 ? "telemetry" : "settings")
                    + "|mode=" + (root.runMode === 0 ? "avg" : "max"),
              // The acceptance test made mechanical: the popup must not
              // scroll, and the probe says whether it does.
              "content=" + (content < 0 ? "-" : Math.round(content) + "px"),
              "scroll=" + (content < 0 ? "-"
                : (content > panelFlick.height ? "yes" : "no")),
              "open=" + root.opened].join("|")
    }

  }

  // ---------------------------------------------------------- bar button

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: Format.barText(root.snapshot, stats.snapshotStale)
    // `active` is the theme's urgent colour; dimming and tint are orthogonal
    // to the words, exactly as the state precedence is orthogonal to health.
    active: Format.barUrgent(root.snapshot, root.showAccept)
    dimmed: Format.barDimmed(root.snapshot, stats.snapshotStale)
    onPressed: function(buttonCode) {
      if (buttonCode === Qt.RightButton) stats.refresh()
      else root.toggle()
    }
  }

  // --------------------------------------------------------------- popout

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher   // Esc closes, Tab flips tabs, r refreshes
    contentWidth: panel.fittedContentWidth(Style.space(380))
    contentHeight: panel.fittedContentHeight(
        contentLoader.item ? contentLoader.item.implicitHeight : 0, Style.space(620))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function() { root.activeTab = 1 - root.activeTab }
      onTextKey: function(text) { if (text === "r" || text === "R") stats.refresh() }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: contentLoader.item ? contentLoader.item.implicitHeight : 0
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        // Every one of the bindings below re-evaluates on every state-file
        // write, once a second, whether or not anyone had the popout open.
        // Building them on open costs one layout pass.
        Loader {
          id: contentLoader
          width: panelFlick.width
          active: root.opened || panel.visible
          sourceComponent: Component {
            Column {
              width: contentLoader.width
              spacing: Style.space(10)

              // --------------------------------------------------- tab bar
              //
              // Telemetry (default) / Settings. The active tab is bold with an
              // underline; the inactive one is muted. No other chrome above
              // it: status already lives in the toolbar.
              Item {
                width: parent.width
                implicitHeight: Style.space(28)

                Tab {
                  id: dataTab
                  label: "Telemetry"
                  index: 0
                  active: root.activeTab === 0
                }
                Tab {
                  x: dataTab.width + Style.space(16)
                  label: "Settings"
                  index: 1
                  active: root.activeTab === 1
                }
              }

              // -------------------------------------------------- telemetry tab
              Column {
                width: parent.width
                spacing: Style.space(10)
                visible: root.activeTab === 0

                Text {
                  visible: !root.snapshot
                  width: parent.width
                  textFormat: Text.PlainText
                  text: "No state file yet at\n" + stats.statePath
                    + "\n\nThe collector runs every " + stats.refreshMs + " ms; if this "
                    + "persists, run it by hand:\npython3 " + stats.collectorPath
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  wrapMode: Text.WordWrap
                }

                // The error state: the banner replaces the tile grid
                // entirely. The runs table below stays -- cached history,
                // not a live reading.
                Rectangle {
                  width: parent.width
                  visible: !!root.snapshot && root.stateDown
                  radius: Style.cornerRadius
                  color: root.urgent
                  opacity: 0.12
                  implicitHeight: bannerInner.implicitHeight + 2 * Style.space(8)
                  Row {
                    id: bannerInner
                    anchors.fill: parent
                    anchors.margins: Style.space(8)
                    spacing: Style.space(6)
                    Text {
                      anchors.verticalCenter: parent.verticalCenter
                      textFormat: Text.PlainText
                      text: Format.WARN
                      color: root.urgent
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.body
                    }
                    Text {
                      anchors.verticalCenter: parent.verticalCenter
                      textFormat: Text.PlainText
                      text: Format.errorBannerText(root.config)
                      color: root.urgent
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.body
                    }
                  }
                }

                // The live tiles: 2 columns, 2 rows. Dashes are the idle
                // story -- the values themselves are the state, so there is
                // no live indicator here. (Grid, not GridLayout: the shell's
                // QtQuick carries the classic layout items, and Tile reads
                // `parent.columnSpacing`, which only resolves when the grid
                // is the parent.)
                Grid {
                  id: tileGrid
                  width: parent.width
                  visible: !!root.snapshot && !root.stateDown
                  columns: 2
                  columnSpacing: Style.space(8)
                  rowSpacing: Style.space(8)
                  property var tiles: root.snapshot ? Format.liveTiles(root.snapshot) : null

                  Tile { label: "Tok/s";      reading: tileGrid.tiles ? tileGrid.tiles.tokS : null }
                  Tile { label: "Acceptance"; reading: tileGrid.tiles ? tileGrid.tiles.accept : null }
                  Tile { label: "Temp";       reading: tileGrid.tiles ? tileGrid.tiles.temp : null }
                  Tile { label: "Watts";      reading: tileGrid.tiles ? tileGrid.tiles.watts : null }
                }

                // --------------------------------------------- previous runs
                PanelSeparator { visible: !!root.snapshot; foreground: root.foreground }

                Column {
                  width: parent.width
                  visible: !!root.snapshot
                  spacing: Style.spacing.labelGap

                  // Section header: the label left, a bare Avg/Max switch
                  // right -- off reads the run's average, on its max, and
                  // every stat column switches at once. The tooltip names
                  // the projection a click will switch to.
                  Item {
                    width: parent.width
                    implicitHeight: Style.space(26)
                    Text {
                      anchors.left: parent.left
                      anchors.verticalCenter: parent.verticalCenter
                      textFormat: Text.PlainText
                      text: root.runMode === 1 ? "Previous run maximums"
                        : "Previous run averages"
                      color: Qt.darker(root.foreground, 1.4)
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                      font.bold: true
                    }
                    ToggleSwitch {
                      id: modeSwitch
                      anchors.right: parent.right
                      anchors.verticalCenter: parent.verticalCenter
                      checked: root.runMode === 1
                      trackHeight: 14   // it rides a section header
                      foreground: root.foreground
                      // Toggle runMode itself: at this point `checked` still
                      // holds the pre-toggle value (its binding has not
                      // re-evaluated), so reading it would write the mode
                      // back to what it already was.
                      onToggled: root.runMode = root.runMode === 1 ? 0 : 1

                      PanelToolTip {
                        visible: modeSwitch.containsMouse
                        text: root.runMode === 1 ? "show averages" : "show maximums"
                      }
                    }
                  }

                  // Column headers, 11px muted: When | Duration | Tok/s | Accept | Temp | Wait.
                  // Six equal cells: the last absorbs the rounding so the
                  // row always fills its width exactly.
                  Row {
                    id: colHeader
                    width: parent.width
                    spacing: 0
                    property int cellW: Math.floor(parent.width / root.colCount)
                    Text {
                      width: colHeader.cellW
                      textFormat: Text.PlainText
                      text: "When"
                      color: root.dim
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.bodySmall
                    }
                    Text {
                      width: colHeader.cellW
                      textFormat: Text.PlainText
                      text: "Duration"
                      horizontalAlignment: Text.AlignRight
                      color: root.dim
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.bodySmall
                    }
                    Text {
                      width: colHeader.cellW
                      textFormat: Text.PlainText
                      text: "Tok/s"
                      horizontalAlignment: Text.AlignRight
                      color: root.dim
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.bodySmall
                    }
                    Text {
                      width: colHeader.cellW
                      textFormat: Text.PlainText
                      text: "Accept"
                      horizontalAlignment: Text.AlignRight
                      color: root.dim
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.bodySmall
                    }
                    Text {
                      width: colHeader.cellW
                      textFormat: Text.PlainText
                      text: "Temp"
                      horizontalAlignment: Text.AlignRight
                      color: root.dim
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.bodySmall
                    }
                    Text {
                      width: parent.width - 5 * colHeader.cellW
                      textFormat: Text.PlainText
                      text: "Wait"
                      horizontalAlignment: Text.AlignRight
                      color: root.dim
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.bodySmall
                    }
                  }

                  // One row per run, most recent first, up to five: the
                  // collector caps the ring.
                  Repeater {
                    model: root.runsModel
                    RunRow { run: modelData }
                  }

                  Metric {
                    visible: root.runsModel.length === 0
                    label: "Runs"
                    value: "\u2014 (no completed runs since the last boot)"
                  }
                }
              }

              // ------------------------------------------------ settings tab
              //
              // The committed-at-boot budget in the Telemetry tab's own tile UI --
              // the readings first, like the Telemetry tab -- then the engine's
              // arguments under a section header, in the previous-runs
              // table's own style. Read-only: a different launch is a
              // different serve invocation. The tiles hold no special state:
              // config is what the last boot committed, read the same up or
              // down.
              Column {
                width: parent.width
                visible: root.activeTab === 1
                // The Telemetry tab's own rhythm: 10 px around the separator.
                spacing: Style.space(10)

                // (Grid, not GridLayout: the shell's QtQuick carries the
                // classic layout items, and Tile reads `parent.columnSpacing`,
                // which only resolves when the grid is the parent.)
                Grid {
                  id: commitGrid
                  width: parent.width
                  columns: 2
                  columnSpacing: Style.space(8)
                  rowSpacing: Style.space(8)
                  property var tiles: Format.committedTiles(root.config)

                  // The four committed readings, in the engine's own journal
                  // vocabulary: weights, KV, host KV, host state.
                  Tile { label: "Weights";    reading: commitGrid.tiles.weights }
                  Tile { label: "KV";         reading: commitGrid.tiles.kvPool }
                  Tile { label: "Host KV";    reading: commitGrid.tiles.hostKV }
                  Tile { label: "Host state"; reading: commitGrid.tiles.hostState }
                }

                PanelSeparator { foreground: root.foreground }

                // The arguments in the previous-runs table's own style --
                // the header and the rows inside one labelGap column, the
                // way the runs table nests its header and rows, so the
                // header sits labelGap under the separator on both tabs.
                Column {
                  width: parent.width
                  spacing: Style.spacing.labelGap

                  // Section header: the Telemetry tab's own header style, minus
                  // the toggle -- there is nothing to switch on the arguments.
                  Item {
                    width: parent.width
                    implicitHeight: Style.space(26)
                    Text {
                      anchors.left: parent.left
                      anchors.verticalCenter: parent.verticalCenter
                      textFormat: Text.PlainText
                      text: "Launched with"
                      color: Qt.darker(root.foreground, 1.4)
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                      font.bold: true
                    }
                  }

                  // RunRow's exact geometry: 20 px rows, no hairlines -- the
                  // labelGap rhythm does the separating.
                  Column {
                    width: parent.width
                    spacing: Style.spacing.labelGap
                    Repeater {
                      model: root.configRows
                      ArgRow {
                        label: modelData.label
                        value: modelData.value
                      }
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
  }

  // ------------------------------------------------------ small components

  // One label/value line. Values right-align into their own column so the
  // rows read as a table without any of them being one.
  component Metric: Item {
    id: metric
    property string label: ""
    property string value: ""
    property bool mutedValue: false
    property int vPad: Style.space(4)

    width: parent ? parent.width : 0
    implicitHeight: Math.max(labelText.implicitHeight, valueText.implicitHeight)
                      + 2 * metric.vPad

    Text {
      id: labelText
      textFormat: Text.PlainText
      anchors.left: parent.left
      anchors.top: parent.top
      anchors.bottom: parent.bottom
      anchors.topMargin: metric.vPad
      anchors.bottomMargin: metric.vPad
      verticalAlignment: Text.AlignVCenter
      text: metric.label
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    Text {
      id: valueText
      textFormat: Text.PlainText
      anchors.right: parent.right
      anchors.left: labelText.right
      anchors.leftMargin: Style.space(8)
      anchors.top: parent.top
      anchors.bottom: parent.bottom
      anchors.topMargin: metric.vPad
      anchors.bottomMargin: metric.vPad
      verticalAlignment: Text.AlignVCenter
      horizontalAlignment: Text.AlignRight
      text: metric.value
      color: metric.mutedValue ? root.dim : root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      elide: Text.ElideLeft
    }
  }

  // One tab of the tab bar: bold + underlined when active, muted otherwise.
  component Tab: Item {
    id: tab
    property string label: ""
    property int index: 0
    property bool active: false

    width: tabText.implicitWidth + 2 * Style.space(6)
    implicitHeight: Style.space(28)

    Text {
      id: tabText
      anchors.left: parent.left
      anchors.leftMargin: Style.space(6)
      anchors.verticalCenter: parent.verticalCenter
      textFormat: Text.PlainText
      text: tab.label
      color: tab.active ? root.foreground : root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.body
      font.bold: tab.active
    }

    Rectangle {
      anchors.left: parent.left
      anchors.leftMargin: Style.space(6)
      anchors.bottom: parent.bottom
      width: tabText.implicitWidth
      height: Math.max(2, Style.spacing.hairline)
      visible: tab.active
      color: root.foreground
    }

    MouseArea {
      anchors.fill: parent
      cursorShape: Qt.PointingHandCursor
      onClicked: root.activeTab = tab.index
    }
  }

  // One live tile: a muted label over a bold value, on a raised surface.
  // The value's colour is its tone: ok/danger.
  component Tile: Item {
    id: tile
    property string label: ""
    property var reading: null   // { text, tone } from Format.liveTiles

    width: parent ? (parent.width - parent.columnSpacing) / 2 : 0
    implicitHeight: tileContent.implicitHeight + 2 * Style.space(10)

    Rectangle {
      anchors.fill: parent
      radius: Style.cornerRadius
      color: Style.normalFill
    }

    Column {
      id: tileContent
      anchors.horizontalCenter: parent.horizontalCenter
      anchors.verticalCenter: parent.verticalCenter
      spacing: Style.spacing.labelGap
      Text {
        anchors.horizontalCenter: parent.horizontalCenter
        textFormat: Text.PlainText
        text: tile.label
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
      }
      Text {
        id: tileValue
        anchors.horizontalCenter: parent.horizontalCenter
        textFormat: Text.PlainText
        text: tile.reading ? tile.reading.text : Format.DASH
        color: {
          var t = tile.reading ? tile.reading.tone : "ok"
          if (t === "danger") return root.urgent
          return root.foreground
        }
        font.family: root.fontFamily
        font.pixelSize: Style.font.heading
        font.bold: true
      }
    }
  }

  // One row of the settings arguments table: RunRow's exact geometry -- a
  // fixed 20 px, a dim label left and the value right, no hairline.
  component ArgRow: Item {
    id: argRow
    property string label: ""
    property string value: ""

    width: parent ? parent.width : 0
    implicitHeight: Style.space(20)

    Text {
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      textFormat: Text.PlainText
      text: argRow.label
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
    }
    Text {
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      textFormat: Text.PlainText
      text: argRow.value
      elide: Text.ElideLeft
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
    }
  }

  // One row of the previous-runs table. The cells are projected by the
  // Avg/Max toggle (Duration is the run's span, not a projection); the
  // When column re-reads generatedAtMs so it ticks.
  component RunRow: Item {
    id: runRow
    property var run: null

    width: parent ? parent.width : 0
    implicitHeight: Style.space(20)
    property var cells: run
      ? Format.runRow(run, root.runMode === 1 ? "max" : "avg") : null
    // Six equal cells, the same split the header row uses. The last cell
    // absorbs the rounding so the row fills its width exactly.
    property int cellW: parent ? Math.floor(parent.width / root.colCount) : 0

    Text {
      x: 0
      width: cellW
      anchors.verticalCenter: parent.verticalCenter
      textFormat: Text.PlainText
      text: runRow.cells ? Format.fmtAge(
              root.generatedAtMs - runRow.run.completed_at_ms) : Format.DASH
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
    }
    Text {
      x: cellW
      width: cellW
      anchors.verticalCenter: parent.verticalCenter
      textFormat: Text.PlainText
      text: runRow.cells ? runRow.cells.duration : Format.DASH
      horizontalAlignment: Text.AlignRight
      elide: Text.ElideLeft
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
    }
    Text {
      x: 2 * cellW
      width: cellW
      anchors.verticalCenter: parent.verticalCenter
      textFormat: Text.PlainText
      text: runRow.cells ? runRow.cells.tokS : Format.DASH
      horizontalAlignment: Text.AlignRight
      elide: Text.ElideLeft
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
    }
    Text {
      x: 3 * cellW
      width: cellW
      anchors.verticalCenter: parent.verticalCenter
      textFormat: Text.PlainText
      text: runRow.cells ? runRow.cells.accept : Format.DASH
      horizontalAlignment: Text.AlignRight
      elide: Text.ElideLeft
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
    }
    Text {
      x: 4 * cellW
      width: cellW
      anchors.verticalCenter: parent.verticalCenter
      textFormat: Text.PlainText
      text: runRow.cells ? runRow.cells.temp : Format.DASH
      horizontalAlignment: Text.AlignRight
      elide: Text.ElideLeft
      color: runRow.cells && runRow.cells.tempTone === "danger"
        ? root.urgent : root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
    }
    Text {
      x: 5 * cellW
      width: parent ? parent.width - 5 * cellW : 0
      anchors.verticalCenter: parent.verticalCenter
      textFormat: Text.PlainText
      text: runRow.cells ? runRow.cells.wait : Format.DASH
      horizontalAlignment: Text.AlignRight
      elide: Text.ElideLeft
      // A muted dash: the run was never queued.
      color: runRow.cells && runRow.cells.wait === Format.DASH
        ? root.dim : root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
    }
  }

}
