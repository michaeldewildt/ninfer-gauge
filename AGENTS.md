# ninfer-gauge

The Omarchy bar plugin (`michaeldewildt.ninfer-gauge`): health and live throughput of the local NInfer inference server, at a glance. The `README.md` documents the bar states, the collector/state-file contract, and the fixture-driven test suite — read it before changing the display or the collector.

The engine itself is not in this repo — the plugin reads the unit's journal, the server's `/health` endpoint, and `nvidia-smi`, and asks nothing else of the machine.

## Dev loop

- The installed plugin is a **real directory** at `~/.config/omarchy/plugins/michaeldewildt.ninfer-gauge/`, rsync'd from this checkout — rsync it after a change; the shell reloads the plugin on its own, so there is nothing to restart. Keep it a real directory, not a symlink: the shell's watcher doesn't follow symlinks.
- Bump `buildTag` in `Panel.qml` with every QML change: a hot reload can serve the previously compiled component, and the tag — the `bN` prefix in the `omarchy-shell michaeldewildt.ninfer-gauge probe` output — is the only way to tell which build is running. If an edit appears not to land, check the tag first; `omarchy restart shell` gets a fresh process.

## Tests

```
python3 tests/test_parsers.py     # line shapes + state derivation + the golden
node    tests/test_format.mjs     # bar strings and popup formatting
python3 tests/make_fixtures.py    # re-freeze the golden after a contract change
```

The fixtures under `tests/fixtures/` are the contract: `test_parsers.py` replays the journal at a pinned clock and diffs against `stats-golden.json`, so any change to what the widget can read shows up as a reviewable diff.

## Git

Work directly on `master` — commits land on the default branch, no branches or pull requests unless asked.

## Commits

Plain English, matching the `omacom/omarchy` history. No conventional-commit prefixes (`feat:`/`fix:`/…), no trailers (`Co-Authored-By`), no `(#NNNN)` issue refs (solo repo, no issues) — a `[Security]` tag is used only when the change is security-related.

- **Subject:** one plain-English line, imperative or declarative, the change stated directly — `Bump buildTag 1 -> 2 after the QML change`. A `:` clause carries the why when it is load-bearing: `Temp danger line 80 -> 87 C: the 5090's 90 C spec limit minus 3`.
- **Body:** the rationale in prose, with the actual numbers. For a multi-part change, one bullet per component (`collector: …`, `format.js: …`, `tests: …`, `README: …`). Wrap at ~72 columns.
