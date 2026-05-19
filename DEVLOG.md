# Devlog

## 2026-05-19

### macOS Cross-Platform Portability

Made the player executable on macOS without modifying the original Linux
code paths. All platform-specific logic is centralised in a new
`player/platform.py` abstraction module.

**New files:**
- `player/platform.py` — Constants and helpers for platform detection,
  audio/video sink selection, log directory, and tool availability.
- `tests/test_platform.py` — Tests for platform detection and constants.
- `tests/test_cross_platform.py` — 26 tests validating GStreamer elements,
  pipeline building, device discovery, and video sinks on the current OS.
- `tests/test_video_widget.py` — Tests for platform-conditional VideoWidget
  behaviour (Gtk.Box + gtksink on macOS vs DrawingArea + X11 overlay on Linux).

**Modified files:**
- `player/playback/pipeline_builder.py` — Uses `AUDIO_SINK_ELEMENT`
  (`osxaudiosink` on macOS, `pulsesink` on Linux) and
  `AUDIO_SINK_DEVICE_PROPERTY` (`unique-id` on macOS, `device` on Linux).
  Skips PulseAudio-specific buffer tuning on macOS.
- `player/devices.py` — Uses `unique-id` for sink identification on macOS
  (CoreAudio) instead of `node.name` (PulseAudio). Skips `pactl` fallback and
  `pw-cli`/`pw-dump` Bluetooth codec queries on macOS.
- `ui/video_widget.py` — Rewritten to support both Linux (DrawingArea + X11
  XID overlay) and macOS (Gtk.Box + gtksink widget embedding). GdkX11 import
  is now guarded.
- `app.py` — Uses `platform.get_log_directory()` for logs
  (`~/Library/Application Support/` on macOS).
- `tests/test_queue.py`, `tests/test_queue_controller.py` — Fixed path
  comparisons for macOS `/tmp` → `/private/tmp` symlink resolution.

**Environment setup:**
- Requires `brew install gtk+3 gstreamer pygobject3` on macOS.
- Uses `/opt/homebrew/bin/python3` (Homebrew Python 3.14) with `gi` bindings.
- Virtual environment at `.venv` with `--system-site-packages` for gi access.

**Test results:** 175 passed, 2 skipped, 0 failures.

## 2026-05-15

### Add Flatpak manifest

Added `io.github.halfcat.AnimeDualplay.yml` for Flatpak packaging:
- Uses `org.freedesktop.Platform//24.08` runtime with Freedesktop SDK (GStreamer 1.26.11)
- Builds GTK3 and GStreamer (core, base, good, libav, bad, ugly) from source as modules
- Builds PyGObject for GObject-introspection Python bindings
- Patches `app.py` at build time to store config in `$XDG_CONFIG_HOME` instead of the read-only `/app` tree
- Generates a launcher script, desktop entry, and placeholder SVG icon
- Grants Wayland/X11 display sockets, PulseAudio socket, host filesystem access, and DRI device access

### Refactor: Playlist from Bottom-Docked to Right-Side Vertical Sidebar

Transitioned the playlist/queue panel from a bottom-aligned layout to a right-side vertical sidebar using `Gtk.Paned` (horizontal split) to resolve screen overflow issues when the queue grows large.

#### Layout Architecture
- Replaced root `Gtk.Box` (VERTICAL) with `Gtk.Paned` (HORIZONTAL) — left pane holds all player chrome + video, right pane holds the QueuePanel as a vertical sidebar.
- Statusbar moved from root window bottom to left pane bottom (`pack_end`), keeping status messages visible when sidebar is collapsed.
- Sidebar defaults to 280px width with a draggable divider handle between panes.

#### Sidebar Toggle & Responsive Behavior
- Added "◫" toggle button to `PlaybackControls` bar (far right of transport controls).
- Added F9 keyboard shortcut for sidebar toggle.
- Added "Show Playlist Sidebar" checkbox item in the View menu.
- Auto-collapse: sidebar hides when window width < 900px, re-shows when width >= 900px (unless user manually collapsed it).
- Sidebar width clamped between 200-500px via `size-allocate` handler on the Paned widget.

#### Component Changes
- `ui/controls.py`: Added `_sidebar_toggle_button`, `set_sidebar_toggle_callback()`, click handler.
- `ui/queue_panel.py`: Added `sidebar_mode` parameter to `__init__` (default `False` for backward compatibility). In sidebar mode: frame label "Playlist" instead of "Queue", no `min_content_height` constraint on ScrolledWindow, Duration column `min_width` reduced from 65 to 50.
- `ui/main_window.py`: Full `_build_ui()` reconstruction, sidebar show/hide/toggle methods, `size-allocate` and `configure-event` handlers, fullscreen exit respects sidebar visibility state.

#### Files Changed
- `ui/controls.py` — sidebar toggle button + callback plumbing (~25 lines)
- `ui/queue_panel.py` — `sidebar_mode` parameter with conditional layout (~10 lines)
- `ui/main_window.py` — layout reconstruction, sidebar management, fullscreen fix (~120 lines)
- `DEVLOG.md`

#### Tests Run
- `python3 -m compileall .` passed.
- `python3 -m pytest` passed: 94 tests.
- No database changes.
- No changes to `player/` engine, queue controller, config, or data models.

### Post-Review Fixes (2026-05-15)
- Removed duplicate `# Profile actions` section header artifact.
- Added retry cap (20 attempts) to `_init_sidebar_position` to prevent infinite GLib idle loop if window never allocates.
- Added reentrancy guard (`_clamping_paned` flag) to `_on_paned_size_allocate` to prevent cascading reallocation cycles on some GTK versions.

## 2026-05-14

### Fix: Video 1fps Frame Drop from `leaky=1` Queue

Removed `leaky=1` from the `video_delay_queue` in `player/engine.py:_ensure_video_branch()` and updated both the builder and `set_video_delay()` runtime method for consistent queue configuration:

- Removed `leaky=1` property (was causing aggressive frame drops via downstream leaky mode)
- Set `max-size-buffers=120` (~2s at 60fps) instead of unlimited
- When `video_delay_ms > 0`: `max-size-time = delay_ns * 8` (was `delay_ns * 4`), `min-threshold-time = delay_ns`
- When `video_delay_ms == 0`: `max-size-time = 2s`, `min-threshold-time = 0` (explicit pass-through)
- Synchronized `set_video_delay()` multiplier and zero-delay handling with builder logic

### Bluetooth Streaming Architecture Remediation — Completion (Phases 4–5)

Completed all remaining work from the comprehensive code review remediation plan.

#### S6: Logger Migration
- Replaced all 21 `print()` and `print(..., file=sys.stderr)` calls in `player/engine.py` with appropriate `_logger` calls (`info`, `warning`, `error`, `debug` levels).
- Removed unused `import sys` from `player/engine.py`.

#### Phase 5: Test Coverage Expansion (13 new tests)
- **5A — Config drift tests** (`tests/test_engine_config_sync.py`): 5 new tests verifying `set_video_delay()` and `set_listener_delay()` update `self.config` before pipeline modification (M4 fix verification).
- **5B — BT error classifier tests** (in `tests/test_validation.py::TestBluetooth`): 4 new tests covering dual-BT classification, no-BT-listeners guard, non-BT error with BT listener, and possible-keyword matching (M3 fix verification).
- **5C — Codec-aware delay tests** (in `tests/test_validation.py::TestBluetooth`): 3 new tests with mocked `get_bluetooth_codec` verifying SBC→200ms, aptx→100ms, unknown→150ms fallback (m2 fix verification).
- **5D — SinkInfo.is_bluetooth_sink_name test** (in `tests/test_validation.py::TestBluetooth`): 1 new test verifying the classmethod delegation from S5.

#### Files Changed
- `player/engine.py` — S6: print() → _logger, removed import sys
- `tests/test_engine_config_sync.py` — new file, 5 tests
- `tests/test_validation.py` — 8 new tests (BT classifier, codec delay, SinkInfo.is_bluetooth_sink_name)
- `DEVLOG.md`

#### Tests Run
- `python3 -m compileall .` passed.
- `python3 -m pytest` passed: 94 tests (81 existing + 13 new).
- No database changes.

#### Final Status
- All Critical fixes (C1, C2, C4): ✅
- All Major fixes (M1–M6): ✅
- All Minor fixes (m1–m6): ✅
- All Suggestions (S1–S6): ✅
- Plan target: 90+ tests — achieved: 94 tests.

### Bluetooth Audio Streaming Integration (Phases 1–3)

Implemented comprehensive Bluetooth audio streaming support across three phases.

#### Phase 1 — Latency Correction
- Added `video_delay_ms` field to `PlayerConfig` (`player/models.py:28`) — configurable video delay to compensate for Bluetooth latency.
- Extended `player/validation.py` with `clamp_video_delay_ms()`, `is_bluetooth_sink()`, `suggest_delay_for_sink()`, and `_validate_bluetooth_sinks()` that emits warnings when Bluetooth sinks are selected.
- Added video delay serialization to `player/config.py` (`video.delayMs` in JSON).
- Added `video_delay_queue` (`GstQueue` with `min-threshold-time`) to the video pipeline in `player/engine.py:_ensure_video_branch()` — inserted right before the video sink to buffer and delay video frames.
- Added `set_video_delay()` live adjustment method to `PlaybackEngine` (`player/engine.py`) — no pipeline rebuild required.
- Added video delay `Gtk.SpinButton` (0–2000ms, step 10ms) to `MainWindow` UI with auto-save integration.

#### Phase 2 — Connection Resilience
- Added `DeviceMonitor` class to `player/devices.py` — persistent sink monitor with 3s polling and `Gst.DeviceMonitor` signal-based change detection. Exposes `subscribe()` / `unsubscribe()` for hotplug callbacks.
- Added Bluetooth error classification in `player/engine.py:_on_bus_message()` — detects `bluez`, `bluetooth`, `pa_context`, `connection terminated` keywords and emits BT-specific error messages.
- Added `_last_bt_disconnect_sink` tracking on `PlaybackEngine` for reconnection awareness.
- Integrated `DeviceMonitor` into `MainWindow.__init__()` — subscribes to hotplug changes, auto-refreshes sink combos on device add/remove.
- Added `_on_hotplug_device_change()` and `_apply_hotplug_sinks()` — updates UI sink lists on device changes via `GLib.idle_add()`.
- Updated `_on_engine_error()` to display BT-specific recovery messages and keep controls partially responsive.

#### Phase 3 — UX Polish
- Added `SinkInfo.sink_type` field (`player/models.py:67`) with `is_bluetooth` property — values: `wired`, `bluetooth`, `usb`, `hdmi`.
- Added `_classify_sink()` to `player/devices.py` — classifies by name patterns (`bluez_`, `usb`, `hdmi`, `displayport`).
- Added `get_bluetooth_codec()` to `player/devices.py` — queries `pw-dump` for `api.bluez5.codec` property.
- Added Bluetooth icon (`BT_ICON`) and "(BT)" suffix to sink display labels in `MainWindow` sink combos.
- Updated `get_audio_sinks()` to populate `sink_type` on all discovered sinks.
- Added `BT_DELAY_SUGGESTION_MS = 150` constant and `suggest_delay_for_sink()` helper.

#### Tests
- Added 6 new validation tests: `TestBluetooth` class covering `is_bluetooth_sink`, `suggest_delay_for_sink`, Bluetooth sink validation warnings, `clamp_video_delay`, `normalize_config` with video delay, and video delay in config building.

### Files Changed

- `player/models.py` — `video_delay_ms` on `PlayerConfig`, `sink_type` + `is_bluetooth` on `SinkInfo`
- `player/validation.py` — `clamp_video_delay_ms`, `is_bluetooth_sink`, `suggest_delay_for_sink`, `_validate_bluetooth_sinks`, BT warnings
- `player/config.py` — serialize/deserialize `video.delayMs`
- `player/engine.py` — `video_delay_queue` in pipeline, `set_video_delay()`, BT error classification, `_last_bt_disconnect_sink`
- `player/devices.py` — `DeviceMonitor` class, `_classify_sink()`, `get_bluetooth_codec()`, `BT_ICON`
- `ui/main_window.py` — video delay spin button, DeviceMonitor integration, hotplug handler, BT error recovery, BT icons in sink display
- `tests/test_validation.py` — 6 new BT + video delay tests
- `DEVLOG.md`

### Tests Run

- `python3 -m compileall .` passed.
- `python3 -m pytest` passed: 81 tests (75 existing + 6 new).
- No database changes.

### Bluetooth Audio Research Report

- Created `docs/BLUETOOTH_AUDIO_RESEARCH.md`: comprehensive technical report on Linux Bluetooth audio streaming APIs and frameworks relevant to this project.
- Covers: BlueZ D-Bus, GStreamer bluez plugin, PulseAudio module-bluetooth-discover, PipeWire libspa-bluez5/WirePlumber, oFono, A2DP sink/source roles, codec negotiation (SBC/AAC/aptX/LDAC/LC3), latency pitfalls, and a comparison table with pros/cons.
- Key finding: PipeWire-Pulse (`pulsesink` + `device=<sink>`) already supports Bluetooth sinks transparently — no code changes needed. Raw BlueZ D-Bus and GStreamer `bluez` plugin are not recommended as they conflict with the audio server.
- Identified Bluetooth latency as the primary gap for dual-audio sync (200ms+), recommending a future video delay or negative audio delay feature.

### Files Changed

- `docs/BLUETOOTH_AUDIO_RESEARCH.md` (new)
- `DEVLOG.md`

### Tests Run

- `python3 -m compileall .` passed.
- `python3 -m pytest` passed: 75 tests.
- No code or database changes.

## 2026-05-14

### Playlist Code Review Remediation

- Fixed `clamp_on_error` mutation of shared `PlayerConfig` [C2]: validation now records clamp actions in `auto_clamped` dict instead of mutating config; `_do_start_playback` applies clamps to a `deepcopy`; original profile config is never mutated.
- Fixed `_queue_in_transition` flag leak [C4]: `QueueController.play_entry()` returns `bool`; `_on_queue_play_entry` only sets the flag on success.
- Fixed discovery index matching fragility [C3]: `_discovery_worker` now keys results by absolute path string; `QueuePanel` stores `COL_FULL_PATH` (hidden) for lookup; no longer relies on stale list indices.
- Fixed EOS auto-advance after user-initiated stop [C5]: added `_pipeline_generation` counter; EOS and error callbacks now capture generation via lambda closure and ignore stale events.
- Added `QueueController` unit tests (34 tests): skip forward/backward, debouncing, EOS, error-chain guard, add/remove/clear, transition state machine, serialization roundtrip.
- Fixed `QueueModel.move()` index tracking [C1]: rewritten to adjust `current_index` against post-pop state; added 8 comprehensive move test cases (cross-region, same-region, current-entry, edge positions).
- Deduplicated `_open_media` and `_on_queue_transition` [Q1]: extracted shared `_load_and_play_file(path, is_queue_transition)` method.
- Debounced `_save_queue()` [P1]: 500ms `GLib.timeout_add` timer with cancel-on-mutation; force-saves immediately on `_on_destroy`.
- Queued pending discovery requests [P4]: `start_discovery` now appends to `_pending_paths` when a thread is running; new thread started automatically when current discovery completes.
- Added `clamp_on_error` validation tests (7 tests): missing sinks, unavailable audio tracks (with/without tracks), subtitle clamping (track/subtitles disabled), immutable config verification.
- Replaced string labels `"Listener A"/"Listener B"` in `auto_clamped` with structured keys `listener_a`/`listener_b` [Q4].
- Removed unused `set_skip_forward_callback`/`set_skip_backward_callback` from `QueuePanel` [Q7].
- Added `saved_index` validation in `QueueModel.from_dict_list()` [C7]: out-of-range index clamped to -1.
- Fixed `QueueController.on_error` to always mark entry status and increment error counter, even during transition [C6]; pipeline generation guard also applied to `_on_engine_error`.
- Parallel media discovery [P3]: `_discovery_worker` now uses `ThreadPoolExecutor(max_workers=4)`.
- Added `QueueObserver` dataclass and `set_observer()` method for structured callback wiring [Q3].
- Added deferred-optimization comment in `refresh_from_model()` for incremental updates [P2].
- See `.kilo/plans/1778751999815-quick-sailor.md` for the full remediation plan.

### Files Changed

- `player/validation.py`
- `player/queue.py`
- `player/queue_controller.py`
- `ui/queue_panel.py`
- `ui/main_window.py`
- `tests/test_queue.py`
- `tests/test_queue_controller.py` (new)
- `tests/test_validation.py`
- `DEVLOG.md`

### Tests Run

- `python3 -m pytest tests/` — 75 passed (25 queue + 34 controller + 4 subtitle + 12 validation)
- `python3 -m compileall .` passed.
- `git diff --check` passed.
- No database changes.

### Playlist & Queue Management System

- Added `player/queue.py` with `QueueEntry` and `QueueModel` dataclasses supporting add, remove, move, advance, retreat, jump_to, clear operations with proper index tracking.
- Added `player/queue_controller.py` with `QueueController` orchestrating transitions between queue entries, skip forward/backward with debouncing, consecutive error skip-chain guard, and EOS/error event handling.
- Added `ui/queue_panel.py` with `Gtk.TreeView`-based playlist panel featuring columns for index, status icon, file name, duration; drag-and-drop reorder; context menu (Play, Move, Remove); lazy media discovery on background thread.
- Extended `player/validation.py` to support `clamp_on_error` mode: auto-clamps unavailable audio/subtitle tracks to track 0 and downgrades missing-sink errors to warnings for queue transitions, returning clamped track information via `auto_clamped` dict.
- Extended `player/config.py` with `get_queue()`, `set_queue()`, `get_queue_index()`, `set_queue_index()` for JSON persistence of queue state in `config.json`.
- Added Prev/Next buttons to `PlaybackControls` with sensitivity management for queue boundaries.
- Integrated `QueueController` and `QueuePanel` into `MainWindow`: queue-aware open file flow, EOS/error delegation, keyboard media-key shortcuts, queue save on exit, queue restore on startup.
- See `.kilo/plans/1778746966272-stellar-engine.md` for the full implementation plan.

### Files Changed

- `player/queue.py` (new)
- `player/queue_controller.py` (new)
- `ui/queue_panel.py` (new)
- `tests/test_queue.py` (new)
- `player/validation.py`
- `player/config.py`
- `ui/controls.py`
- `ui/main_window.py`
- `DEVLOG.md`

### Tests Run

- `python3 -m pytest tests/` — 27 passed (19 queue + 4 subtitle + 4 validation)
- `python3 -m compileall .` passed.
- No database migrations needed.

## 2026-05-13

### Fullscreen Exit Keyboard Focus Fix

- Root cause: after `unfullscreen()`, the X11 video overlay window created by `xvimagesink` via `set_window_handle()` retains keyboard focus at the X11 protocol level, preventing key events from reaching the GTK `key-press-event` handler on `MainWindow`. Additionally, `Gtk.Window.unfullscreen()` does not guarantee the window manager restores focus.
- Fix: added `self.present()` call in `_exit_fullscreen()` after restoring chrome to explicitly request window focus from the window manager.
- Fix: added `self.present()` call in `_on_window_state_event()` when exiting fullscreen via external means (window manager hotkeys).
- Fix: added `KEY_PRESS_MASK` to `VideoWidget` event mask to ensure keyboard events are properly routed through the GTK widget hierarchy when the video area has implicit focus during fullscreen transitions.

### Files Changed

- `ui/main_window.py`
- `ui/video_widget.py`
- `DEVLOG.md`

### Tests Run

- `python3 -m compileall .` passed.
- `python3 -m pytest` passed: 8 tests.
- `git diff --check` passed.

### Migration Note

- No database changes. No migration files required.

### Subtitle Overlay And Async Switching Fix

- Replaced the subtitle display branch with explicit `textoverlay` rendering for decoded `text/x-raw` subtitle streams in `pango-markup` or `utf8` format.
- Added a subtitle raw-text capsfilter between the `input-selector` and overlay so subtitle caps negotiation is visible and constrained before rendering.
- Disabled textoverlay `wait-text` behavior to prevent sparse subtitle streams from stalling video while waiting for the next cue.
- Changed subtitle track switching to schedule `input-selector` active-pad updates asynchronously, with stale-selector guards for rebuilt pipelines.
- Updated subtitle selector unit coverage and refreshed subtitle architecture/troubleshooting docs.

### Files Changed

- `player/engine.py`
- `tests/test_subtitle_selector.py`
- `docs/ARCHITECTURE.md`
- `docs/TROUBLESHOOTING.md`
- `README.md`
- `DEVLOG.md`

### Tests Run

- `python3 -m pytest tests/test_subtitle_selector.py -q` passed: 4 tests.
- `python3 -m pytest` passed: 8 tests.
- `python3 -m compileall .` passed.
- `git diff --check` passed.

### Migration Note

- No database schema changes. No migration files required.

### Subtitle Selector Stabilization

- Pre-linked the subtitle `input-selector` when the subtitle overlay video branch is created, instead of waiting for the first subtitle pad.
- Removed the refresh seek during subtitle track switching to avoid hangs on sparse text streams.
- Kept selector sink pads `always-ok` so inactive subtitle streams do not report not-linked while selected tracks change.
- Stopped preparing a replacement GTK video sink for subtitle track changes because track switching no longer rebuilds the pipeline.
- Updated subtitle testing and troubleshooting notes to clarify that switched tracks may show text at the next cue.

### Files Changed

- `player/engine.py`
- `ui/main_window.py`
- `tests/test_subtitle_selector.py`
- `docs/TESTING.md`
- `docs/TROUBLESHOOTING.md`
- `DEVLOG.md`

### Tests Run

- `python3 -m pytest tests/test_subtitle_selector.py -q` passed: 3 tests.
- `python3 -m py_compile player/engine.py ui/main_window.py tests/test_subtitle_selector.py` passed.
- GStreamer selector prelink smoke check passed.
- `python3 -m compileall .` passed.
- `python3 -m pytest` passed: 7 tests.
- `git diff --check` passed.

### Migration Note

- No database changes. No migration files required.

### Subtitle Switching And Seek Stability

- Routed enabled subtitle streams through a GStreamer `input-selector` so track changes can switch active subtitle pads in place.
- Preserved playback position for subtitle track switching and kept rebuilds for subtitle enable/disable or selector fallback cases.
- Changed user seeks to use accurate seek flags and report rejected seeks through status/log output.
- Added focused unit coverage for subtitle selector activation, pending track selection, and no-rebuild switching.
- Updated testing and troubleshooting documentation for subtitle switching, seek behavior, and source subtitle gaps.

### Files Changed

- `player/engine.py`
- `tests/test_subtitle_selector.py`
- `docs/TESTING.md`
- `docs/TROUBLESHOOTING.md`
- `DEVLOG.md`

### Tests Run

- `python3 -m pytest tests/test_subtitle_selector.py -q` passed: 3 tests.
- `python3 -m py_compile player/engine.py tests/test_subtitle_selector.py` passed.
- GStreamer subtitle selector smoke check passed.
- `python3 -m compileall .` passed.
- `python3 -m pytest` passed: 7 tests.
- `git diff --check` passed.

### Migration Note

- No database changes. No migration files required.

### Fullscreen Fix

- Fixed GTK fullscreen keyboard handling by unpacking `event.get_keyval()` correctly.
- Enabled button events on the video drawing area so double-click and right-click handlers can fire.
- Added a right-click video context menu for entering and exiting fullscreen.
- Synchronized internal fullscreen state with window-manager fullscreen state changes and centralized chrome hide/show behavior.
- Updated README and troubleshooting fullscreen controls.

### Files Changed

- `ui/main_window.py`
- `ui/video_widget.py`
- `README.md`
- `docs/TROUBLESHOOTING.md`
- `DEVLOG.md`

### Tests Run

- `python3 -m compileall .` passed.
- `python3 -m pytest` passed: 4 tests.
- `git diff --check` passed.
- GTK fullscreen import check passed.
- Manual fullscreen staging was not run in this terminal session.

### Migration Note

- No database changes. No migration files required.

### Summary

- Added repository standards, steering guidelines, remediation documentation, and test documentation.
- Hardened playback lifecycle cleanup, bus watch cleanup, startup timer cleanup, GUI start retry cancellation, and GUI rebuild handling.
- Added media stream IDs, playback config validation, duplicate-track fan-out support, latency recalculation, stereo PCM listener output policy, and async sink refresh in GTK.
- Added focused validation tests.

### Files Changed

- `AGENTS.md`, `CLAUDE.md`, `DEVLOG.md`, `README.md`
- `docs/ARCHITECTURE.md`, `docs/CONFIGURATION.md`, `docs/PLAYBACK_SYNC.md`, `docs/DEVICE_HANDLING.md`, `docs/TESTING.md`, `docs/TROUBLESHOOTING.md`, `docs/REMEDIATION_AUDIT.md`
- `player/models.py`, `player/config.py`, `player/validation.py`, `player/inspector.py`, `player/devices.py`, `player/engine.py`
- `ui/main_window.py`
- `dual_audio_player.py`
- `tests/test_validation.py`

### Tests Run

- `python -m compileall .` attempted but unavailable because `python` is not on PATH.
- `python -m pytest` attempted but unavailable because `python` is not on PATH.
- `python3 -m compileall .` passed.
- `python3 -m pytest` passed: 4 tests.
- GStreamer import check passed with GStreamer 1.24.2.
- `player.engine`, `player.validation`, and `ui.main_window` import checks passed.

### Migration Note

- No database changes. No migration files required.
