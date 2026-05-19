from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import threading
from typing import Optional

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Gdk

from player.engine import PlaybackEngine, PlaybackObserver
from player.config import (
    load_config_file,
    save_config_file,
    get_profile,
    get_active_profile_name,
    set_active_profile_name,
    list_profile_names,
    set_profile,
    delete_profile,
    get_last_directory,
    set_last_directory,
    get_queue,
    set_queue,
    get_queue_index,
    set_queue_index,
    get_sink_delay_map,
    set_sink_delay,
    get_default_audio_language,
    set_default_audio_language,
    get_subtitle_style,
    get_theme_preference,
    set_theme_preference,
)
from player.platform import is_system_dark_mode
from player.models import PlayerConfig, MediaInfo, SinkInfo
from player.inspector import discover_media
from player.devices import get_audio_sinks, DeviceMonitor, BT_ICON
from player.validation import normalize_config, validate_playback_config, is_bluetooth_sink
from player.queue_controller import QueueController
from ui.video_widget import VideoWidget
from ui.controls import PlaybackControls
from ui.routing_panel import RoutingPanel
from ui.subtitle_panel import SubtitlePanel
from ui.queue_panel import QueuePanel


class MainWindow(Gtk.Window):

    def __init__(self, config_path: Path):
        super().__init__(title="Dual Audio Player")
        self.set_default_size(1280, 800)

        self._config_path = config_path
        self._config_data = load_config_file(config_path)
        self._config: PlayerConfig | None = None
        self._media_path: Path | None = None
        self._media_info: Optional[MediaInfo] = None
        self._engine: Optional[PlaybackEngine] = None
        self._populating = False
        self._sinks: list[SinkInfo] = []
        self._start_retry_id: Optional[int] = None
        self._queue_controller = QueueController()
        self._queue_in_transition = False
        self._pipeline_generation = 0
        self._queue_save_timer_id: Optional[int] = None
        self._device_monitor: Optional[DeviceMonitor] = None
        self._bt_reconnect_visible = False
        self._last_bt_disconnected_sinks: Optional[list[str]] = None

        # Widget references
        self._video_widget: Optional[VideoWidget] = None
        self._controls: Optional[PlaybackControls] = None
        self._panel_a: Optional[RoutingPanel] = None
        self._panel_b: Optional[RoutingPanel] = None
        self._queue_panel: Optional[QueuePanel] = None
        self._subtitle_panel: Optional[SubtitlePanel] = None
        self._video_delay_spin: Optional[Gtk.SpinButton] = None
        self._video_delay_row: Optional[Gtk.Box] = None
        self._statusbar: Optional[Gtk.Statusbar] = None
        self._statusbar_context_id: int = 0
        self._profile_menu: Optional[Gtk.Menu] = None
        self._sidebar_menu_item: Optional[Gtk.CheckMenuItem] = None
        self._sidebar_pane: Optional[Gtk.Box] = None

        self._fullscreen = False
        self._chrome_widgets: list[Gtk.Widget] = []
        self._video_context_menu: Optional[Gtk.Menu] = None

        self._sidebar_visible = True
        self._sidebar_collapsed_by_user = False
        self._saved_paned_position: Optional[int] = None
        self._paned: Optional[Gtk.Paned] = None
        self._clamping_paned = False
        self._sidebar_position_retries = 20
        self._auto_save_timer_id: Optional[int] = None

        self._build_ui()

        self.connect("destroy", self._on_destroy)
        self.connect("key-press-event", self._on_key_press)
        self.connect("window-state-event", self._on_window_state_event)

        GLib.idle_add(self._init_sidebar_position)

        # Wire up QueueController callbacks
        self._queue_controller.set_transition_callback(self._on_queue_transition)
        self._queue_controller.set_stop_callback(self._stop_playback)
        self._queue_controller.set_status_callback(self._set_status)
        self._queue_controller.set_queue_changed_callback(self._on_queue_model_changed)

        # Restore saved queue
        saved = get_queue(self._config_data)
        saved_index = get_queue_index(self._config_data)
        if saved:
            self._queue_controller.load_from_data(saved, saved_index)
            if self._queue_panel:
                self._queue_panel.refresh_from_model(self._queue_controller.queue)
            paths = [e.media_path for e in self._queue_controller.queue.entries]
            if paths and self._queue_panel:
                self._queue_panel.start_discovery(paths)

        # Load initial sinks
        GLib.idle_add(self._refresh_all_sinks_async)

        # Start persistent device monitor for hotplug detection
        self._device_monitor = DeviceMonitor(poll_interval_ms=3000)
        self._device_monitor.subscribe(self._on_hotplug_device_change)
        self._device_monitor.start()
        
        self._apply_theme()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self._paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self._paned.set_wide_handle(True)
        self.add(self._paned)

        # --- Left pane: all chrome + video ---
        left_pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Menu bar
        menubar = self._create_menu_bar()
        left_pane.pack_start(menubar, False, False, 0)

        # File selection row
        file_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        file_row.set_margin_start(8)
        file_row.set_margin_end(8)
        file_row.set_margin_top(4)
        file_row.set_margin_bottom(4)

        open_button = Gtk.Button(label="Choose File...")
        open_button.connect("clicked", self._on_open_clicked)
        file_row.pack_start(open_button, False, False, 0)

        self._file_label = Gtk.Label(label="No file selected")
        self._file_label.set_halign(Gtk.Align.START)
        self._file_label.set_ellipsize(3)
        file_row.pack_start(self._file_label, True, True, 0)

        left_pane.pack_start(file_row, False, False, 0)

        # Video area
        self._video_widget = VideoWidget()
        self._video_widget.connect("button-press-event", self._on_video_button_press)
        video_frame = Gtk.Frame()
        video_frame.add(self._video_widget)
        left_pane.pack_start(video_frame, True, True, 0)

        # Playback controls
        self._controls = PlaybackControls()
        self._controls.set_play_callback(self._on_play)
        self._controls.set_pause_callback(self._on_pause)
        self._controls.set_stop_callback(self._on_stop)
        self._controls.set_seek_callback(self._on_seek)
        self._controls.set_previous_callback(self._on_skip_backward)
        self._controls.set_next_callback(self._on_skip_forward)
        self._controls.set_sidebar_toggle_callback(self._on_toggle_sidebar)
        self._controls.set_controls_sensitive(False)
        left_pane.pack_start(self._controls, False, False, 0)

        # Listener A panel
        self._panel_a = RoutingPanel("listener_a", "Listener A")
        self._panel_a.set_track_callback(self._on_listener_track_changed)
        self._panel_a.set_sink_callback(self._on_listener_sink_changed)
        self._panel_a.set_volume_callback(self._on_listener_volume_changed)
        self._panel_a.set_delay_callback(self._on_listener_delay_changed)
        self._panel_a.set_refresh_callback(self._on_refresh_sinks_for)
        left_pane.pack_start(self._panel_a, False, False, 0)

        # Listener B panel
        self._panel_b = RoutingPanel("listener_b", "Listener B")
        self._panel_b.set_track_callback(self._on_listener_track_changed)
        self._panel_b.set_sink_callback(self._on_listener_sink_changed)
        self._panel_b.set_volume_callback(self._on_listener_volume_changed)
        self._panel_b.set_delay_callback(self._on_listener_delay_changed)
        self._panel_b.set_refresh_callback(self._on_refresh_sinks_for)
        left_pane.pack_start(self._panel_b, False, False, 0)

        # Video delay control
        video_delay_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        video_delay_row.set_margin_start(8)
        video_delay_row.set_margin_end(8)
        video_delay_row.set_margin_top(4)
        video_delay_row.set_margin_bottom(4)

        video_delay_label = Gtk.Label(label="Video Delay:")
        video_delay_row.pack_start(video_delay_label, False, False, 0)

        self._video_delay_spin = Gtk.SpinButton.new_with_range(0, 2000, 10)
        self._video_delay_spin.set_value(0)
        self._video_delay_spin.set_tooltip_text("Delays video to compensate for Bluetooth audio latency")
        self._video_delay_spin.connect("value-changed", self._on_video_delay_changed)
        video_delay_row.pack_start(self._video_delay_spin, False, False, 0)
        video_delay_row.pack_start(Gtk.Label(label="ms"), False, False, 0)

        left_pane.pack_start(video_delay_row, False, False, 0)
        self._video_delay_row = video_delay_row

        # Subtitle panel
        self._subtitle_panel = SubtitlePanel()
        self._subtitle_panel.set_enable_callback(self._on_subtitle_enable_changed)
        self._subtitle_panel.set_track_callback(self._on_subtitle_track_changed)
        left_pane.pack_start(self._subtitle_panel, False, False, 0)

        # Status bar (moved to bottom of left pane)
        self._statusbar = Gtk.Statusbar()
        self._statusbar_context_id = self._statusbar.get_context_id("main")
        left_pane.pack_end(self._statusbar, False, False, 0)

        self._paned.pack1(left_pane, resize=True, shrink=True)

        # --- Right pane: playlist sidebar ---
        right_pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._queue_panel = QueuePanel(sidebar_mode=True)
        self._queue_panel.set_add_callback(self._on_queue_add)
        self._queue_panel.set_remove_callback(self._on_queue_remove)
        self._queue_panel.set_clear_callback(self._on_queue_clear)
        self._queue_panel.set_play_callback(self._on_queue_play_entry)
        self._queue_panel.set_move_callback(self._on_queue_move)
        right_pane.pack_start(self._queue_panel, True, True, 0)
        self._sidebar_pane = right_pane

        self._paned.pack2(right_pane, resize=False, shrink=True)

        # Track chrome widgets for fullscreen hide/show
        self._chrome_widgets = [
            menubar, file_row, self._controls,
            self._panel_a, self._panel_b, self._video_delay_row,
            self._subtitle_panel,
            self._queue_panel, self._statusbar,
        ]

        # Responsive sidebar management
        self._paned.connect("size-allocate", self._on_paned_size_allocate)
        self.connect("configure-event", self._on_window_configure)

    def _create_menu_bar(self) -> Gtk.MenuBar:
        menubar = Gtk.MenuBar()

        # -- File menu --
        file_menu = Gtk.Menu()
        open_item = Gtk.MenuItem(label="Open...")
        open_item.connect("activate", self._on_open_clicked)
        file_menu.append(open_item)
        file_menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda w: self.destroy())
        file_menu.append(quit_item)

        file_menu_item = Gtk.MenuItem(label="File")
        file_menu_item.set_submenu(file_menu)
        menubar.append(file_menu_item)

        # -- View menu --
        view_menu = Gtk.Menu()
        self._fullscreen_menu_item = Gtk.MenuItem(label="Fullscreen")
        self._fullscreen_menu_item.connect("activate", self._on_fullscreen_menu)
        view_menu.append(self._fullscreen_menu_item)
        view_menu.append(Gtk.SeparatorMenuItem())
        self._sidebar_menu_item = Gtk.CheckMenuItem(label="Show Playlist Sidebar")
        self._sidebar_menu_item.set_active(self._sidebar_visible)
        self._sidebar_menu_item.connect("activate", self._on_sidebar_menu_toggle)
        view_menu.append(self._sidebar_menu_item)
        view_menu.append(Gtk.SeparatorMenuItem())

        theme_menu_item = Gtk.MenuItem(label="Theme")
        theme_menu = Gtk.Menu()
        theme_menu_item.set_submenu(theme_menu)
        
        current_theme = get_theme_preference(self._config_data)
        self._theme_radios = []
        for theme_val, label in [("system", "System"), ("light", "Light"), ("dark", "Dark")]:
            if not self._theme_radios:
                item = Gtk.RadioMenuItem(label=label)
            else:
                item = Gtk.RadioMenuItem.new_with_label_from_widget(self._theme_radios[0], label)
            item.set_name(theme_val)
            if theme_val == current_theme:
                item.set_active(True)
            item.connect("toggled", self._on_theme_toggled)
            theme_menu.append(item)
            self._theme_radios.append(item)
            
        view_menu.append(theme_menu_item)

        view_menu_item = Gtk.MenuItem(label="View")
        view_menu_item.set_submenu(view_menu)
        menubar.append(view_menu_item)

        # -- Profiles menu --
        self._profile_menu = Gtk.Menu()
        profiles_menu_item = Gtk.MenuItem(label="Profiles")
        profiles_menu_item.set_submenu(self._profile_menu)
        menubar.append(profiles_menu_item)

        # -- Settings menu --
        settings_menu = Gtk.Menu()
        lang_item = Gtk.MenuItem(label="Default Audio Language…")
        lang_item.connect("activate", self._on_set_default_language)
        settings_menu.append(lang_item)
        subtitle_style_item = Gtk.MenuItem(label="Subtitle Style…")
        subtitle_style_item.connect("activate", self._on_subtitle_style)
        settings_menu.append(subtitle_style_item)
        settings_menu_item = Gtk.MenuItem(label="Settings")
        settings_menu_item.set_submenu(settings_menu)
        menubar.append(settings_menu_item)

        return menubar

    def _on_set_default_language(self, widget) -> None:
        current = get_default_audio_language(self._config_data)
        dialog = Gtk.Dialog(
            title="Default Audio Language",
            parent=self,
            flags=Gtk.DialogFlags.MODAL,
        )
        dialog.add_button("_Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("_Save", Gtk.ResponseType.OK)
        box = dialog.get_content_area()
        box.set_spacing(6)
        box.pack_start(
            Gtk.Label(label="ISO language code (e.g. eng, jpn). Leave blank to disable."),
            False, False, 4,
        )
        entry = Gtk.Entry()
        entry.set_text(current)
        entry.set_placeholder_text("eng")
        entry.set_activates_default(True)
        box.pack_start(entry, False, False, 4)
        dialog.show_all()
        response = dialog.run()
        text = entry.get_text().strip().lower()
        dialog.destroy()
        if response == Gtk.ResponseType.OK:
            set_default_audio_language(self._config_data, text)
            save_config_file(self._config_path, self._config_data)
            self._set_status(
                f"Default audio language set to {text or '(none)'}",
                is_error=False,
            )

    def _on_subtitle_style(self, widget) -> None:
        from ui.subtitle_style_dialog import run_subtitle_style_dialog
        changed = run_subtitle_style_dialog(self, self._config_data)
        if changed:
            save_config_file(self._config_path, self._config_data)
            if self._engine:
                self._engine.apply_subtitle_style(
                    get_subtitle_style(self._config_data),
                )

    def _apply_theme(self) -> None:
        theme = get_theme_preference(self._config_data)
        if theme == "system":
            is_dark = is_system_dark_mode()
        else:
            is_dark = (theme == "dark")
        settings = Gtk.Settings.get_default()
        if settings:
            settings.set_property("gtk-application-prefer-dark-theme", is_dark)

    def _on_theme_toggled(self, item: Gtk.RadioMenuItem) -> None:
        if self._populating or not item.get_active():
            return
        theme_val = item.get_name()
        set_theme_preference(self._config_data, theme_val)
        self._save_config()
        self._apply_theme()

    # ------------------------------------------------------------------
    # Sidebar management
    # ------------------------------------------------------------------

    def _on_toggle_sidebar(self) -> None:
        if self._sidebar_visible:
            self._sidebar_collapsed_by_user = True
            self._hide_sidebar()
        else:
            self._sidebar_collapsed_by_user = False
            self._show_sidebar()

    def _on_sidebar_menu_toggle(self, item: Gtk.CheckMenuItem) -> None:
        if item.get_active():
            self._sidebar_collapsed_by_user = False
            self._show_sidebar()
        else:
            self._sidebar_collapsed_by_user = True
            self._hide_sidebar()

    def _show_sidebar(self) -> None:
        if self._sidebar_visible:
            return
        self._sidebar_visible = True
        self._sidebar_pane.show()
        if self._saved_paned_position is not None:
            self._paned.set_position(self._saved_paned_position)
            self._saved_paned_position = None
        self._update_sidebar_menu_check()

    def _hide_sidebar(self) -> None:
        if not self._sidebar_visible:
            return
        self._sidebar_visible = False
        self._saved_paned_position = self._paned.get_position()
        self._sidebar_pane.hide()
        self._update_sidebar_menu_check()

    def _update_sidebar_menu_check(self) -> None:
        if self._sidebar_menu_item is not None:
            self._sidebar_menu_item.handler_block_by_func(self._on_sidebar_menu_toggle)
            self._sidebar_menu_item.set_active(self._sidebar_visible)
            self._sidebar_menu_item.handler_unblock_by_func(self._on_sidebar_menu_toggle)

    def _init_sidebar_position(self) -> bool:
        win_w = self.get_allocated_width()
        if win_w <= 1:
            self._sidebar_position_retries -= 1
            return self._sidebar_position_retries > 0
        sidebar_w = 280
        self._paned.set_position(max(0, win_w - sidebar_w))
        return False

    def _on_paned_size_allocate(self, paned: Gtk.Paned, allocation: Gdk.Rectangle) -> None:
        if self._clamping_paned or not self._sidebar_visible:
            return
        self._clamping_paned = True
        try:
            handle_width = paned.get_handle_window().get_width() if paned.get_handle_window() else 8
            total = allocation.width
            sidebar_width = total - paned.get_position() - handle_width
            if sidebar_width < 200:
                paned.set_position(max(0, total - 200 - handle_width))
            elif sidebar_width > 500:
                paned.set_position(max(0, total - 500 - handle_width))
        finally:
            self._clamping_paned = False

    def _on_window_configure(self, widget, event) -> bool:
        width = event.width if hasattr(event, 'width') else getattr(event, 'width', 0)
        if width <= 0:
            return False
        if width < 900 and self._sidebar_visible:
            self._hide_sidebar()
        elif width >= 900 and not self._sidebar_visible and not self._sidebar_collapsed_by_user:
            self._show_sidebar()
        return False

    # ------------------------------------------------------------------
    # Profile actions
    # ------------------------------------------------------------------

    def _rebuild_profile_menu(self) -> None:
        if self._profile_menu is None:
            return
        for child in self._profile_menu.get_children():
            self._profile_menu.remove(child)

        active_name = get_active_profile_name(self._config_data)

        for name in list_profile_names(self._config_data):
            item = Gtk.MenuItem(label=name)
            if name == active_name:
                item.set_sensitive(False)
            item.connect("activate", self._on_profile_switch, name)
            self._profile_menu.append(item)

        self._profile_menu.append(Gtk.SeparatorMenuItem())

        save_item = Gtk.MenuItem(label="Save As...")
        save_item.connect("activate", self._on_profile_save_as)
        self._profile_menu.append(save_item)

        delete_item = Gtk.MenuItem(label="Delete...")
        delete_item.connect("activate", self._on_profile_delete)
        self._profile_menu.append(delete_item)

        self._profile_menu.append(Gtk.SeparatorMenuItem())

        import_item = Gtk.MenuItem(label="Import Profile...")
        import_item.connect("activate", self._on_profile_import)
        self._profile_menu.append(import_item)

        export_item = Gtk.MenuItem(label="Export Active Profile...")
        export_item.connect("activate", self._on_profile_export)
        self._profile_menu.append(export_item)

        self._profile_menu.show_all()

    def _on_profile_import(self, widget) -> None:
        dialog = Gtk.FileChooserDialog(
            title="Import Profile",
            parent=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_button("_Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("_Open", Gtk.ResponseType.OK)
        json_filter = Gtk.FileFilter()
        json_filter.set_name("Profile JSON")
        json_filter.add_pattern("*.json")
        dialog.add_filter(json_filter)

        response = dialog.run()
        path = dialog.get_filename() if response == Gtk.ResponseType.OK else None
        dialog.destroy()
        if not path:
            return

        import json
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, ValueError) as exc:
            self._set_status(f"Import failed: {exc}", is_error=True)
            return

        # Accept either a wrapped {"name": ..., "profile": {...}} or a bare
        # profile dict (matches what export writes).
        if isinstance(payload, dict) and "profile" in payload:
            name = str(payload.get("name") or Path(path).stem)
            profile_data = payload["profile"]
        else:
            name = Path(path).stem
            profile_data = payload

        if not isinstance(profile_data, dict):
            self._set_status("Import failed: profile must be a JSON object", is_error=True)
            return

        unique_name = self._unique_profile_name(name)
        if "profiles" not in self._config_data:
            self._config_data["profiles"] = {}
        self._config_data["profiles"][unique_name] = profile_data
        save_config_file(self._config_path, self._config_data)
        self._rebuild_profile_menu()
        self._set_status(f"Imported profile '{unique_name}'", is_error=False)

    def _on_profile_export(self, widget) -> None:
        active_name = get_active_profile_name(self._config_data)
        profiles = self._config_data.get("profiles", {})
        if active_name not in profiles:
            self._set_status("No active profile to export", is_error=True)
            return

        dialog = Gtk.FileChooserDialog(
            title="Export Active Profile",
            parent=self,
            action=Gtk.FileChooserAction.SAVE,
        )
        dialog.set_do_overwrite_confirmation(True)
        dialog.set_current_name(f"{active_name}.json")
        dialog.add_button("_Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("_Save", Gtk.ResponseType.OK)

        response = dialog.run()
        path = dialog.get_filename() if response == Gtk.ResponseType.OK else None
        dialog.destroy()
        if not path:
            return

        import json
        payload = {"name": active_name, "profile": profiles[active_name]}
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
                f.write("\n")
        except OSError as exc:
            self._set_status(f"Export failed: {exc}", is_error=True)
            return
        self._set_status(f"Exported profile '{active_name}' to {path}", is_error=False)

    def _unique_profile_name(self, base: str) -> str:
        existing = set(self._config_data.get("profiles", {}).keys())
        if base not in existing:
            return base
        i = 2
        while f"{base} ({i})" in existing:
            i += 1
        return f"{base} ({i})"

    def _on_profile_switch(self, widget, name: str) -> None:
        self._config = normalize_config(get_profile(self._config_data, name))
        set_active_profile_name(self._config_data, name)
        save_config_file(self._config_path, self._config_data)
        self._apply_config_to_ui()
        if self._media_path:
            GLib.idle_add(self._start_playback)
        self._rebuild_profile_menu()

    def _on_profile_save_as(self, widget) -> None:
        dialog = Gtk.Dialog(
            title="Save Profile As...",
            parent=self,
            flags=Gtk.DialogFlags.MODAL,
        )
        dialog.add_button("_Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("_Save", Gtk.ResponseType.OK)

        entry = Gtk.Entry()
        entry.set_placeholder_text("Profile name")
        dialog.get_content_area().pack_start(entry, False, False, 8)
        dialog.show_all()

        response = dialog.run()
        name = entry.get_text().strip()
        dialog.destroy()

        if response == Gtk.ResponseType.OK and name:
            config = self._build_current_config()
            set_profile(self._config_data, name, config)
            self._config = config
            save_config_file(self._config_path, self._config_data)
            self._rebuild_profile_menu()
            self._set_status(f"Profile '{name}' saved")

    def _on_profile_delete(self, widget) -> None:
        names = list_profile_names(self._config_data)
        if len(names) <= 1:
            self._set_status("Cannot delete the last profile", is_error=True)
            return

        dialog = Gtk.Dialog(
            title="Delete Profile",
            parent=self,
            flags=Gtk.DialogFlags.MODAL,
        )
        dialog.add_button("_Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("_Delete", Gtk.ResponseType.OK)

        combo = Gtk.ComboBoxText()
        for n in names:
            combo.append(n, n)
        combo.set_active(0)
        dialog.get_content_area().pack_start(combo, False, False, 8)
        dialog.show_all()

        response = dialog.run()
        model = combo.get_model()
        tree_iter = combo.get_active_iter()
        selected = model[tree_iter][0] if tree_iter else ""
        dialog.destroy()

        if response == Gtk.ResponseType.OK and selected:
            active_name = get_active_profile_name(self._config_data)
            delete_profile(self._config_data, selected)
            save_config_file(self._config_path, self._config_data)
            if selected == active_name:
                new_active = get_active_profile_name(self._config_data)
                self._config = get_profile(self._config_data, new_active)
                self._apply_config_to_ui()
            self._rebuild_profile_menu()
            self._set_status(f"Profile '{selected}' deleted")

    # ------------------------------------------------------------------
    # Auto-save current state to active profile
    # ------------------------------------------------------------------

    def _auto_save(self) -> None:
        """Debounced — coalesces rapid UI changes (slider drags etc.) into
        a single disk write."""
        self._cancel_auto_save_timer()
        self._auto_save_timer_id = GLib.timeout_add(500, self._do_auto_save)

    def _do_auto_save(self) -> bool:
        self._auto_save_timer_id = None
        config = self._build_current_config()
        name = get_active_profile_name(self._config_data)
        set_profile(self._config_data, name, config)
        self._config = config
        save_config_file(self._config_path, self._config_data)
        return False

    def _cancel_auto_save_timer(self) -> None:
        if self._auto_save_timer_id is not None:
            try:
                GLib.source_remove(self._auto_save_timer_id)
            except Exception:
                pass
            self._auto_save_timer_id = None

    def _flush_auto_save(self) -> None:
        if self._auto_save_timer_id is not None:
            self._cancel_auto_save_timer()
            self._do_auto_save()

    def _build_current_config(self) -> PlayerConfig:
        """Build a PlayerConfig from current UI state."""
        from player.models import ListenerConfig, SubtitleConfig

        a_track = self._panel_a.get_selected_track() if self._panel_a else 0
        a_sink = self._panel_a.get_selected_sink() if self._panel_a else ""
        a_vol = self._panel_a.get_volume() if self._panel_a else 1.0
        a_delay = self._panel_a.get_delay() if self._panel_a else 0

        b_track = self._panel_b.get_selected_track() if self._panel_b else 1
        b_sink = self._panel_b.get_selected_sink() if self._panel_b else ""
        b_vol = self._panel_b.get_volume() if self._panel_b else 1.0
        b_delay = self._panel_b.get_delay() if self._panel_b else 0

        sub_enabled = self._subtitle_panel.get_enabled() if self._subtitle_panel else False
        sub_track = self._subtitle_panel.get_selected_track() if self._subtitle_panel else 0

        video_delay = int(self._video_delay_spin.get_value()) if self._video_delay_spin else 0

        return normalize_config(PlayerConfig(
            listener_a=ListenerConfig(
                label=self._config.listener_a.label if self._config else "Listener A",
                sink=a_sink, audio_track=a_track, delay_ms=a_delay, volume=a_vol,
            ),
            listener_b=ListenerConfig(
                label=self._config.listener_b.label if self._config else "Listener B",
                sink=b_sink, audio_track=b_track, delay_ms=b_delay, volume=b_vol,
            ),
            video_enabled=True,
            video_sink=self._config.video_sink if self._config else "autovideosink",
            video_delay_ms=video_delay,
            subtitles=SubtitleConfig(enabled=sub_enabled, subtitle_track=sub_track),
        ))

    # ------------------------------------------------------------------
    # File open
    # ------------------------------------------------------------------

    def _on_open_clicked(self, widget) -> None:
        last_dir = get_last_directory(self._config_data)

        dialog = Gtk.FileChooserDialog(
            title="Open Media File",
            parent=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        if last_dir:
            dialog.set_current_folder(last_dir)
        dialog.add_button("_Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("_Open", Gtk.ResponseType.OK)

        filter_media = Gtk.FileFilter()
        filter_media.set_name("Media files")
        filter_media.add_pattern("*.mkv")
        filter_media.add_pattern("*.mp4")
        filter_media.add_pattern("*.webm")
        filter_media.add_pattern("*.avi")
        dialog.add_filter(filter_media)

        filter_all = Gtk.FileFilter()
        filter_all.set_name("All files")
        filter_all.add_pattern("*")
        dialog.add_filter(filter_all)

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            path = Path(dialog.get_filename())
            set_last_directory(self._config_data, str(path.parent))
            save_config_file(self._config_path, self._config_data)
            self._open_media(path)
        dialog.destroy()

    def _open_media(self, path: Path) -> None:
        self._load_and_play_file(path, is_queue_transition=False)

    def _on_queue_transition(self, path: Path, index: int) -> None:
        if not path.exists():
            self._queue_controller.on_error(f"File not found: {path}")
            return
        self._load_and_play_file(path, is_queue_transition=True)

    def _load_and_play_file(self, path: Path, is_queue_transition: bool = False) -> None:
        if not path.exists():
            self._set_status(f"File not found: {path}", is_error=True)
            return

        self._stop_playback()

        self._media_path = path
        self._file_label.set_text(str(path))
        self.set_title(f"Dual Audio Player — {path.name}")

        self._config = get_profile(
            self._config_data,
            get_active_profile_name(self._config_data),
        )
        self._config = normalize_config(self._config)

        try:
            self._media_info = discover_media(path)
        except Exception as exc:
            self._set_status(f"Media discovery failed: {exc}", is_error=True)
            self._media_info = MediaInfo(
                uri=str(path), duration=0, audio_tracks=[], video_tracks=[], subtitle_tracks=[],
            )

        self._populate_ui_from_media()

        if is_queue_transition:
            self._queue_in_transition = True

        self._start_playback()
    # ------------------------------------------------------------------

    def _populate_ui_from_media(self) -> None:
        if self._media_info is None:
            return

        self._populating = True

        self._rebuild_profile_menu()

        for panel in (self._panel_a, self._panel_b):
            if panel is None:
                continue
            panel.clear_tracks()
            for t in self._media_info.audio_tracks:
                panel.add_track(t.display_label, t.index)

        if self._subtitle_panel:
            self._subtitle_panel.clear_tracks()
            for t in self._media_info.subtitle_tracks:
                self._subtitle_panel.add_track(t.display_label, t.index)

        # Apply audio-language preference: when the saved track is unavailable
        # in this file, fall back to the first track matching the user's
        # default language. Only mutates the in-memory config.
        self._apply_default_language_preference()

        self._apply_config_to_ui()

        self._populating = False

    def _apply_default_language_preference(self) -> None:
        if self._media_info is None or self._config is None:
            return
        lang = get_default_audio_language(self._config_data).strip().lower()
        if not lang:
            return
        track_count = len(self._media_info.audio_tracks)
        if track_count == 0:
            return

        def matching_index() -> Optional[int]:
            for t in self._media_info.audio_tracks:
                if (t.language or "").strip().lower() == lang:
                    return t.index
            return None

        for listener in (self._config.listener_a, self._config.listener_b):
            if listener.audio_track >= track_count:
                fallback = matching_index()
                if fallback is not None:
                    listener.audio_track = fallback

    def _apply_config_to_ui(self) -> None:
        if self._config is None:
            return

        # Block widget signals while we restore values so this doesn't fan
        # out into engine.set_listener_*() rebuilds during profile switches
        # or sink-refresh applies (where _populating is False).
        panels = [p for p in (self._panel_a, self._panel_b, self._subtitle_panel) if p]
        for panel in panels:
            panel.block_signals(True)
        if self._video_delay_spin:
            try:
                self._video_delay_spin.handler_block_by_func(self._on_video_delay_changed)
            except TypeError:
                pass

        try:
            if self._panel_a:
                self._panel_a.set_selected_track(self._config.listener_a.audio_track)
                self._panel_a.set_selected_sink(self._config.listener_a.sink)
                self._panel_a.set_volume(self._config.listener_a.volume)
                self._panel_a.set_delay(self._config.listener_a.delay_ms)

            if self._panel_b:
                self._panel_b.set_selected_track(self._config.listener_b.audio_track)
                self._panel_b.set_selected_sink(self._config.listener_b.sink)
                self._panel_b.set_volume(self._config.listener_b.volume)
                self._panel_b.set_delay(self._config.listener_b.delay_ms)

            if self._subtitle_panel:
                self._subtitle_panel.set_enabled(self._config.subtitles.enabled)
                self._subtitle_panel.set_selected_track(self._config.subtitles.subtitle_track)

            if self._video_delay_spin:
                self._video_delay_spin.set_value(self._config.video_delay_ms)
        finally:
            for panel in panels:
                panel.block_signals(False)
            if self._video_delay_spin:
                try:
                    self._video_delay_spin.handler_unblock_by_func(self._on_video_delay_changed)
                except TypeError:
                    pass

    # ------------------------------------------------------------------
    # Sink refresh
    # ------------------------------------------------------------------

    def _refresh_all_sinks_async(self) -> bool:
        threading.Thread(target=self._load_sinks_worker, daemon=True).start()
        return False

    def _load_sinks_worker(self) -> None:
        sinks = get_audio_sinks()
        GLib.idle_add(self._apply_all_sinks, sinks)

    def _populate_sink_panel(self, panel: RoutingPanel, sinks: list[SinkInfo], fallback_if_missing: bool = False) -> None:
        current = panel.get_selected_sink()
        panel.clear_sinks()
        for s in sinks:
            display = s.description or s.name
            if s.is_bluetooth:
                display = f"{BT_ICON} {display} (BT)"
            panel.add_sink(s.name, display)
        if current and any(s.name == current for s in sinks):
            panel.set_selected_sink(current)
        elif fallback_if_missing and sinks:
            panel.set_selected_sink(sinks[0].name)
            if current:
                self._set_status(
                    f"Sink '{current}' is no longer available; switched to {sinks[0].description or sinks[0].name}.",
                    is_error=False,
                )

    def _apply_all_sinks(self, sinks: list[SinkInfo]) -> bool:
        if not sinks:
            self._set_status("No audio sinks found", is_error=True)
            return False

        self._sinks = sinks

        for panel in (self._panel_a, self._panel_b):
            if panel is None:
                continue
            self._populate_sink_panel(panel, sinks, fallback_if_missing=True)

        self._apply_config_to_ui()
        return False

    def _on_refresh_sinks_for(self, listener_id: str) -> None:
        threading.Thread(target=self._load_sinks_for_worker, args=(listener_id,), daemon=True).start()

    def _load_sinks_for_worker(self, listener_id: str) -> None:
        sinks = get_audio_sinks()
        GLib.idle_add(self._apply_sinks_for, listener_id, sinks)

    def _apply_sinks_for(self, listener_id: str, sinks: list[SinkInfo]) -> bool:
        if not sinks:
            self._set_status("No audio sinks found", is_error=True)
            return False

        self._sinks = sinks

        panel = self._panel_a if listener_id == "listener_a" else self._panel_b
        if panel is None:
            return False

        self._populate_sink_panel(panel, sinks, fallback_if_missing=True)
        return False

    def _on_hotplug_device_change(self, sinks: list[SinkInfo]) -> None:
        sink_names = {s.name for s in sinks}
        if self._last_bt_disconnected_sinks:
            reconnected = [s for s in self._last_bt_disconnected_sinks if s in sink_names]
            for bt_sink in reconnected:
                self._set_status(
                    f"Bluetooth device reconnected: {bt_sink}",
                    is_error=False,
                )
            remaining = [s for s in self._last_bt_disconnected_sinks if s not in sink_names]
            self._last_bt_disconnected_sinks = remaining if remaining else None
        GLib.idle_add(self._apply_hotplug_sinks, sinks)

    def _apply_hotplug_sinks(self, sinks: list[SinkInfo]) -> bool:
        if not sinks:
            return False
        self._sinks = sinks
        for panel in (self._panel_a, self._panel_b):
            if panel is None:
                continue
            self._populate_sink_panel(panel, sinks, fallback_if_missing=True)
        return False

    # ------------------------------------------------------------------
    # Playback engine
    # ------------------------------------------------------------------

    def _start_playback(self) -> None:
        if not self._config or not self._media_path:
            return

        if not self._video_widget or not self._video_widget.is_realized_for_video:
            self._cancel_start_retry()
            self._start_retry_id = GLib.timeout_add(100, self._start_playback_retry)
            return

        self._do_start_playback()

    def _start_playback_retry(self) -> bool:
        if self._video_widget and self._video_widget.is_realized_for_video:
            self._start_retry_id = None
            self._do_start_playback()
            return False
        return True

    def _do_start_playback(self) -> None:
        assert self._config and self._media_path

        self._cancel_start_retry()
        validation = validate_playback_config(
            self._config,
            self._media_path,
            self._media_info,
            self._sinks,
            clamp_on_error=self._queue_in_transition,
        )

        working_config = self._config
        if validation.auto_clamped:
            working_config = deepcopy(self._config)
            for key, actions in validation.auto_clamped.items():
                if key == "listener_a":
                    if "audio_track" in actions:
                        working_config.listener_a.audio_track = actions["audio_track"]
                elif key == "listener_b":
                    if "audio_track" in actions:
                        working_config.listener_b.audio_track = actions["audio_track"]
                elif key == "subtitles":
                    if "enabled" in actions:
                        working_config.subtitles.enabled = actions["enabled"]
                    if "subtitle_track" in actions:
                        working_config.subtitles.subtitle_track = actions["subtitle_track"]

        if not validation.ok:
            msg = "; ".join(validation.errors)
            if self._queue_in_transition:
                self._queue_in_transition = False
                self._queue_controller.on_error(msg)
                return
            self._set_status(msg, is_error=True)
            return
        if validation.warnings:
            if not self._queue_in_transition:
                self._set_status("; ".join(validation.warnings), is_error=False)

        if self._engine:
            self._engine.destroy_pipeline()
            self._engine = None

        video_sink = self._video_widget.create_video_sink()
        if not video_sink:
            msg = "Could not create video sink element"
            if self._queue_in_transition:
                self._queue_in_transition = False
                self._queue_controller.on_error(msg)
                return
            self._set_status(msg, is_error=True)
            return

        try:
            self._engine = PlaybackEngine(
                config=working_config,
                media_path=self._media_path,
                video_sink_element=video_sink,
                headless=False,
                media_info=self._media_info,
            )
            self._pipeline_generation += 1
            captured_gen = self._pipeline_generation
            self._engine.set_observer(PlaybackObserver(
                on_position=self._on_engine_position,
                on_status=self._on_engine_status,
                on_eos=lambda: self._on_engine_eos(captured_gen),
                on_pads=self._on_engine_pads,
                on_error=lambda msg: self._on_engine_error(msg, captured_gen),
            ))
            self._engine.set_video_sink_factory(self._make_video_sink)
            self._engine.apply_subtitle_style(
                get_subtitle_style(self._config_data),
            )

            self._engine.build_pipeline()
            self._engine.play()

            self._controls.set_playing(True)
            self._controls.set_controls_sensitive(True)
            self._set_status("Playing", is_error=False)

            if self._queue_in_transition:
                self._queue_in_transition = False
                self._queue_controller.on_transition_complete()
                self._refresh_queue_nav()
        except Exception as exc:
            msg = f"Playback error: {exc}"
            self._set_status(msg, is_error=True)
            if self._engine:
                self._engine.destroy_pipeline()
            self._engine = None
            if self._queue_in_transition:
                self._queue_in_transition = False
                self._queue_controller.on_error(msg)

    def _stop_playback(self) -> None:
        """Full tear-down. Used for shutdown, queue clearing, file change."""
        self._cancel_start_retry()
        if self._engine:
            self._engine.destroy_pipeline()
            self._engine = None
        if self._controls:
            self._controls.set_playing(False)
            self._controls.set_controls_sensitive(False)
            self._controls.set_position(0, 0)

    def _stop_for_user(self) -> None:
        """Stop button: tear down the engine but keep the file loaded so Play
        can restart it from the beginning."""
        self._cancel_start_retry()
        if self._engine:
            self._engine.destroy_pipeline()
            self._engine = None
        if self._controls:
            self._controls.set_playing(False)
            self._controls.set_position(0, 0)
            # Leave Play sensitive so the user can restart.
            if self._media_path is None:
                self._controls.set_controls_sensitive(False)

    def _cancel_start_retry(self) -> None:
        if self._start_retry_id is not None:
            try:
                GLib.source_remove(self._start_retry_id)
            except Exception:
                pass
            self._start_retry_id = None

    # ------------------------------------------------------------------
    # Control callbacks
    # ------------------------------------------------------------------

    def _on_play(self) -> None:
        if self._engine:
            self._engine.resume()
            self._controls.set_playing(True)
        elif self._media_path is not None:
            # User pressed Stop earlier; rebuild and play from the start.
            self._start_playback()

    def _on_pause(self) -> None:
        if self._engine:
            self._engine.pause()
            self._controls.set_playing(False)

    def _on_stop(self) -> None:
        self._stop_for_user()

    def _on_seek(self, seconds: float) -> None:
        if self._engine:
            self._engine.seek(seconds)

    # ------------------------------------------------------------------
    # Queue callbacks
    # ------------------------------------------------------------------

    def _on_skip_forward(self) -> None:
        self._queue_controller.skip_forward()

    def _on_skip_backward(self) -> None:
        self._queue_controller.skip_backward()

    def _on_queue_add(self, paths: list[Path]) -> None:
        self._queue_controller.add_files(paths)
        set_last_directory(self._config_data, str(paths[0].parent))
        self._save_queue()

    def _on_queue_remove(self, index: int) -> None:
        self._queue_controller.remove_entry(index)
        self._save_queue()

    def _on_queue_clear(self) -> None:
        self._queue_controller.clear_queue()
        self._save_queue()

    def _on_queue_play_entry(self, index: int) -> None:
        if self._queue_controller.play_entry(index):
            self._queue_in_transition = True
            self._save_queue()

    def _on_queue_move(self, from_idx: int, to_idx: int) -> None:
        self._queue_controller.move_entry(from_idx, to_idx)
        if self._queue_panel:
            self._queue_panel.refresh_from_model(self._queue_controller.queue)
        self._save_queue()

    def _on_queue_model_changed(self) -> None:
        if self._queue_panel:
            self._queue_panel.refresh_from_model(self._queue_controller.queue)
        self._refresh_queue_nav()

    def _refresh_queue_nav(self) -> None:
        if self._controls:
            q = self._queue_controller.queue
            self._controls.set_previous_sensitive(
                not q.empty and not q.is_at_beginning
            )
            self._controls.set_next_sensitive(
                not q.empty and not q.is_at_end
            )

    def _save_queue(self) -> None:
        self._cancel_queue_save_timer()
        self._queue_save_timer_id = GLib.timeout_add(500, self._do_persist_queue)

    def _do_persist_queue(self) -> bool:
        self._queue_save_timer_id = None
        queue_data, queue_index = self._queue_controller.to_data()
        set_queue(self._config_data, queue_data)
        set_queue_index(self._config_data, queue_index)
        save_config_file(self._config_path, self._config_data)
        return False

    def _cancel_queue_save_timer(self) -> None:
        if self._queue_save_timer_id is not None:
            GLib.source_remove(self._queue_save_timer_id)
            self._queue_save_timer_id = None

    # ------------------------------------------------------------------
    # Listener callbacks
    # ------------------------------------------------------------------

    def _on_listener_track_changed(self, listener_id: str, track_index: int) -> None:
        if self._populating:
            return
        if self._engine:
            self._engine.set_listener_track(listener_id, track_index)
        self._auto_save()

    def _on_listener_sink_changed(self, listener_id: str, sink_name: str) -> None:
        if self._populating:
            return
        # Prefill the listener's delay from the saved per-sink map when the
        # current value is 0 (treat 0 as "unset"). Touch the engine before
        # persisting so playback hears the new delay too.
        delay_map = get_sink_delay_map(self._config_data)
        saved_delay = delay_map.get(sink_name, 0)
        panel = self._panel_a if listener_id == "listener_a" else self._panel_b
        if panel is not None and panel.get_delay() == 0 and saved_delay > 0:
            panel.set_delay(saved_delay)
            if self._engine:
                self._engine.set_listener_delay(listener_id, saved_delay)
        if self._engine:
            self._engine.set_listener_sink(listener_id, sink_name)
        self._auto_save()
        if is_bluetooth_sink(sink_name) and self._config and self._config.video_delay_ms == 0:
            from player.validation import suggest_delay_for_sink
            suggested = suggest_delay_for_sink(sink_name)
            if suggested > 0 and self._video_delay_spin:
                self._video_delay_spin.set_value(suggested)
                if self._engine:
                    self._engine.set_video_delay(suggested)
                self._set_status(
                    f"Bluetooth sink detected \u2014 video delay set to {suggested}ms",
                    is_error=False,
                )

    def _on_listener_volume_changed(self, listener_id: str, volume: float) -> None:
        if self._populating:
            return
        if self._engine:
            self._engine.set_listener_volume(listener_id, volume)
        self._auto_save()

    def _on_listener_delay_changed(self, listener_id: str, delay_ms: int) -> None:
        if self._populating:
            return
        if self._engine:
            self._engine.set_listener_delay(listener_id, delay_ms)
        # Remember this delay against the sink so it is restored next time
        # the user picks the same physical output.
        panel = self._panel_a if listener_id == "listener_a" else self._panel_b
        if panel is not None:
            sink = panel.get_selected_sink()
            if sink:
                set_sink_delay(self._config_data, sink, delay_ms)
        self._auto_save()

    def _on_video_delay_changed(self, spin: Gtk.SpinButton) -> None:
        if self._populating:
            return
        delay_ms = int(spin.get_value())
        if self._engine:
            self._engine.set_video_delay(delay_ms)
        self._auto_save()

    # ------------------------------------------------------------------
    # Subtitle callbacks
    # ------------------------------------------------------------------

    def _on_subtitle_enable_changed(self, enabled: bool) -> None:
        if self._populating:
            return
        if self._engine:
            self._engine.set_subtitle_enabled(enabled)
        self._auto_save()

    def _on_subtitle_track_changed(self, track_index: int) -> None:
        if self._populating:
            return
        if self._engine:
            self._engine.set_subtitle_track(track_index)
        self._auto_save()

    # ------------------------------------------------------------------
    # Rebuild helper
    # ------------------------------------------------------------------

    def _make_video_sink(self):
        """Factory used by the engine to obtain a fresh video sink on rebuild."""
        if self._video_widget is None:
            return None
        return self._video_widget.create_video_sink()

    # ------------------------------------------------------------------
    # Fullscreen
    # ------------------------------------------------------------------

    def _toggle_fullscreen(self) -> None:
        if self._fullscreen:
            self._exit_fullscreen()
        else:
            self._enter_fullscreen()

    def _enter_fullscreen(self) -> None:
        if self._fullscreen:
            return
        self._fullscreen = True
        self._set_fullscreen_chrome(True)
        self.fullscreen()

    def _exit_fullscreen(self) -> None:
        if not self._fullscreen:
            return
        self._fullscreen = False
        self.unfullscreen()
        self._set_fullscreen_chrome(False)
        self.present()

    def _set_fullscreen_chrome(self, fullscreen: bool) -> None:
        for widget in self._chrome_widgets:
            if fullscreen:
                widget.hide()
            else:
                if widget is self._queue_panel and not self._sidebar_visible:
                    continue
                widget.show()

        if not fullscreen and not self._sidebar_visible:
            self._sidebar_pane.hide()
            self._update_sidebar_menu_check()

    def _on_fullscreen_menu(self, widget) -> None:
        self._toggle_fullscreen()

    def _on_video_button_press(self, widget, event) -> bool:
        button = getattr(event, "button", 0)
        if button == 3:
            self._show_video_context_menu(event)
            return True
        if button == 1 and getattr(event, "type", None) == Gdk.EventType._2BUTTON_PRESS:
            self._toggle_fullscreen()
            return True
        return False

    def _show_video_context_menu(self, event) -> None:
        menu = Gtk.Menu()
        self._video_context_menu = menu
        label = "Exit Fullscreen" if self._fullscreen else "Enter Fullscreen"
        item = Gtk.MenuItem(label=label)
        item.connect("activate", lambda *_: self._toggle_fullscreen())
        menu.append(item)
        menu.show_all()
        if hasattr(menu, "popup_at_pointer"):
            menu.popup_at_pointer(event)
        else:
            menu.popup(None, None, None, None, getattr(event, "button", 0), getattr(event, "time", 0))

    def _on_key_press(self, widget, event) -> bool:
        ok, keyval = event.get_keyval()
        if not ok:
            return False

        # Skip when an entry/spinbutton has focus so typing isn't hijacked.
        focus = self.get_focus()
        if isinstance(focus, (Gtk.Entry, Gtk.SpinButton)):
            return False

        state = event.state & Gtk.accelerator_get_default_mod_mask()
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)

        if keyval == Gdk.KEY_F11:
            self._toggle_fullscreen()
            return True
        if keyval == Gdk.KEY_F9:
            self._on_toggle_sidebar()
            return True
        if keyval == Gdk.KEY_Escape and self._fullscreen:
            self._exit_fullscreen()
            return True
        if keyval == Gdk.KEY_AudioNext:
            self._on_skip_forward()
            return True
        if keyval == Gdk.KEY_AudioPrev:
            self._on_skip_backward()
            return True
        if keyval in (Gdk.KEY_space, Gdk.KEY_p, Gdk.KEY_P):
            if self._engine:
                if self._engine.is_paused:
                    self._on_play()
                else:
                    self._on_pause()
            elif self._media_path is not None:
                self._on_play()
            return True
        if keyval in (Gdk.KEY_Right, Gdk.KEY_l, Gdk.KEY_L):
            if self._engine:
                self._engine.seek_relative(10)
            return True
        if keyval in (Gdk.KEY_Left, Gdk.KEY_h, Gdk.KEY_H):
            if self._engine:
                self._engine.seek_relative(-10)
            return True
        if keyval in (Gdk.KEY_Up, Gdk.KEY_Down):
            self._adjust_volume_via_keys(keyval, shift)
            return True
        if keyval in (Gdk.KEY_m, Gdk.KEY_M):
            self._toggle_mute_both()
            return True
        return False

    def _adjust_volume_via_keys(self, keyval: int, shift: bool) -> None:
        panel = self._panel_b if shift else self._panel_a
        if panel is None:
            return
        delta = 0.05 if keyval == Gdk.KEY_Up else -0.05
        new_vol = max(0.0, min(2.0, panel.get_volume() + delta))
        panel.set_volume(new_vol)

    def _toggle_mute_both(self) -> None:
        for panel, listener_id in (
            (self._panel_a, "listener_a"),
            (self._panel_b, "listener_b"),
        ):
            if panel is None:
                continue
            current = panel.get_volume()
            attr = f"_pre_mute_volume_{listener_id}"
            saved = getattr(self, attr, None)
            if current > 0:
                setattr(self, attr, current)
                panel.set_volume(0.0)
            elif saved is not None:
                panel.set_volume(saved)
                setattr(self, attr, None)

    def _on_window_state_event(self, widget, event) -> bool:
        fullscreen = bool(event.new_window_state & Gdk.WindowState.FULLSCREEN)
        if fullscreen != self._fullscreen:
            self._fullscreen = fullscreen
            self._set_fullscreen_chrome(fullscreen)
            if not fullscreen:
                self.present()
        return False

    # ------------------------------------------------------------------
    # Engine callbacks
    # ------------------------------------------------------------------

    def _on_engine_position(self, position: float, duration: float) -> None:
        if self._controls:
            self._controls.set_position(position, duration)

    def _on_engine_eos(self, generation: int = 0) -> None:
        if generation != self._pipeline_generation:
            return
        if self._queue_controller.queue.current is not None:
            self._queue_controller.on_eos()
        else:
            self._set_status("End of stream")
            if self._controls:
                self._controls.set_playing(False)
                self._controls.set_controls_sensitive(False)

    def _on_engine_pads(self, a_ok: bool, b_ok: bool, video_ok: bool, sub_ok: bool) -> None:
        issues = []
        if not a_ok:
            issues.append("Listener A audio not linked")
        if not b_ok:
            issues.append("Listener B audio not linked")
        if not video_ok and self._config and self._config.video_enabled:
            issues.append("Video not linked")
        if not sub_ok and self._config and self._config.subtitles.enabled:
            issues.append("Subtitles not linked")
        if issues:
            self._set_status("; ".join(issues), is_error=True)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _on_destroy(self, widget) -> None:
        self._stop_playback()
        if self._device_monitor:
            self._device_monitor.stop()
            self._device_monitor = None
        try:
            self._flush_auto_save()
        except Exception:
            pass
        self._cancel_queue_save_timer()
        self._do_persist_queue()
        Gtk.main_quit()

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def _set_status(self, message: str, is_error: bool = False) -> None:
        prefix = "ERROR: " if is_error else ""
        if self._statusbar:
            self._statusbar.push(self._statusbar_context_id, prefix + message)

    def _on_engine_status(self, message: str, is_error: bool) -> None:
        self._set_status(message, is_error=is_error)

    def _on_engine_error(self, message: str, generation: int = 0) -> None:
        if generation != self._pipeline_generation:
            return
        is_bt = "bluetooth" in message.lower()
        if is_bt:
            if self._engine and self._engine.last_bt_disconnect_sinks:
                self._last_bt_disconnected_sinks = self._engine.last_bt_disconnect_sinks
            self._set_status(
                f"Bluetooth device disconnected. Check connection and refresh sinks.",
                is_error=True,
            )
        else:
            self._set_status(f"{message}; choose an available output sink and restart playback.", is_error=True)
        if self._controls:
            self._controls.set_playing(False)
            self._controls.set_controls_sensitive(False)
        self._refresh_all_sinks_async()
        if self._queue_controller.queue.current is not None:
            self._queue_controller.on_error(message)
            if is_bt:
                self._set_status("Waiting for Bluetooth device to reconnect...", is_error=False)
