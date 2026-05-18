from __future__ import annotations

from pathlib import Path
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Callable

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Gdk, GLib, Pango

from player.queue import QueueModel
from player.inspector import discover_media


class QueuePanel(Gtk.Frame):
    """Playlist panel with a TreeView showing queued files."""

    COL_INDEX = 0
    COL_STATUS = 1
    COL_FILENAME = 2
    COL_DURATION = 3
    COL_QUEUE_INDEX = 4
    COL_FULL_PATH = 5

    STATUS_ICONS = {
        "playing": "\u25b6",
        "played": "\u2713",
        "error": "\u2717",
        "pending": "",
    }

    def __init__(self, sidebar_mode: bool = False):
        frame_label = "Playlist" if sidebar_mode else "Queue"
        super().__init__(label=frame_label)

        self._sidebar_mode = sidebar_mode

        self._model: Optional[Gtk.ListStore] = None
        self._treeview: Optional[Gtk.TreeView] = None
        self._populating = False

        self._on_add_files: Optional[Callable[[list[Path]], None]] = None
        self._on_remove: Optional[Callable[[int], None]] = None
        self._on_clear: Optional[Callable[[], None]] = None
        self._on_play_entry: Optional[Callable[[int], None]] = None
        self._on_move: Optional[Callable[[int, int], None]] = None

        self._discovery_thread: Optional[threading.Thread] = None
        self._pending_paths: list[Path] = []

        self._build()

    def set_add_callback(self, cb: Optional[Callable[[list[Path]], None]]) -> None:
        self._on_add_files = cb

    def set_remove_callback(self, cb: Optional[Callable[[int], None]]) -> None:
        self._on_remove = cb

    def set_clear_callback(self, cb: Optional[Callable[[], None]]) -> None:
        self._on_clear = cb

    def set_play_callback(self, cb: Optional[Callable[[int], None]]) -> None:
        self._on_play_entry = cb

    def set_move_callback(self, cb: Optional[Callable[[int, int], None]]) -> None:
        self._on_move = cb

    def refresh_from_model(self, queue: QueueModel) -> None:
        if self._model is None:
            return

        # OPTIMIZE: full ListStore rebuild is O(n). For queues > 1000 entries,
        # incremental diff-based update would reduce TreeView redraw cost.
        self._populating = True
        self._model.clear()

        for i, entry in enumerate(queue.entries):
            duration_str = "--:--"
            if entry.media_info and entry.media_info.duration > 0:
                duration_str = _format_duration(entry.media_info.duration)

            self._model.append([
                i + 1,
                self.STATUS_ICONS.get(entry.status, ""),
                entry.media_path.name,
                duration_str,
                i,
                str(entry.media_path),
            ])

        self._populating = False

        if queue.current_index >= 0 and self._treeview:
            path = Gtk.TreePath.new_from_indices([queue.current_index])
            self._treeview.set_cursor(path, None, False)
            self._treeview.scroll_to_cell(path, None, True, 0.5, 0.0)

    def start_discovery(self, paths: list[Path]) -> None:
        if self._discovery_thread and self._discovery_thread.is_alive():
            self._pending_paths.extend(paths)
            return
        self._discovery_thread = threading.Thread(
            target=self._discovery_worker, args=(paths,), daemon=True,
        )
        self._discovery_thread.start()

    def _discovery_worker(self, paths: list[Path]) -> None:
        results: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=4) as executor:
            future_to_path = {executor.submit(self._discover_one, p): str(p) for p in paths if p.exists()}
            for future in as_completed(future_to_path):
                path_str = future_to_path[future]
                try:
                    duration_str = future.result()
                    results[path_str] = duration_str
                except Exception:
                    results[path_str] = "error"

        GLib.idle_add(self._apply_discovery_results, results)

    @staticmethod
    def _discover_one(path: Path) -> str:
        info = discover_media(path)
        return _format_duration(info.duration) if info.duration > 0 else "--:--"

    def _apply_discovery_results(self, results: dict[str, str]) -> bool:
        if self._model is None:
            return False

        self._populating = True
        for row in self._model:
            full_path = row[self.COL_FULL_PATH]
            if full_path in results:
                duration_str = results[full_path]
                if duration_str == "error":
                    row[self.COL_DURATION] = "--:--"
                    row[self.COL_STATUS] = self.STATUS_ICONS["error"]
                else:
                    row[self.COL_DURATION] = duration_str
        self._populating = False

        if self._pending_paths:
            next_paths = self._pending_paths
            self._pending_paths = []
            self._discovery_thread = threading.Thread(
                target=self._discovery_worker, args=(next_paths,), daemon=True,
            )
            self._discovery_thread.start()

        return False

    def _build(self) -> None:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        vbox.set_margin_start(8)
        vbox.set_margin_end(8)
        vbox.set_margin_top(4)
        vbox.set_margin_bottom(4)
        self.add(vbox)

        toolbar = self._build_toolbar()
        vbox.pack_start(toolbar, False, False, 0)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        if not self._sidebar_mode:
            scrolled.set_min_content_height(80)

        self._model = Gtk.ListStore(int, str, str, str, int, str)

        self._treeview = Gtk.TreeView(model=self._model)
        self._treeview.set_headers_visible(True)
        self._treeview.set_rules_hint(True)
        self._treeview.connect("row-activated", self._on_row_activated)
        self._treeview.connect("button-press-event", self._on_button_press)

        self._treeview.enable_model_drag_source(
            Gdk.ModifierType.BUTTON1_MASK, [], Gdk.DragAction.MOVE,
        )
        self._treeview.enable_model_drag_dest(
            [], Gdk.DragAction.MOVE,
        )
        self._treeview.connect("drag-data-get", self._on_drag_data_get)
        self._treeview.connect("drag-data-received", self._on_drag_data_received)

        self._add_columns()

        scrolled.add(self._treeview)
        vbox.pack_start(scrolled, True, True, 0)

    def _build_toolbar(self) -> Gtk.Box:
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)

        add_btn = Gtk.Button(label="+ Add Files")
        add_btn.set_tooltip_text("Add files to the queue")
        add_btn.connect("clicked", self._on_add_clicked)
        toolbar.pack_start(add_btn, False, False, 0)

        remove_btn = Gtk.Button(label="- Remove")
        remove_btn.set_tooltip_text("Remove selected files from the queue")
        remove_btn.connect("clicked", self._on_remove_clicked)
        toolbar.pack_start(remove_btn, False, False, 0)

        clear_btn = Gtk.Button(label="Clear")
        clear_btn.set_tooltip_text("Clear the entire queue")
        clear_btn.connect("clicked", self._on_clear_clicked)
        toolbar.pack_start(clear_btn, False, False, 0)

        return toolbar

    def _add_columns(self) -> None:
        renderer_index = Gtk.CellRendererText()
        renderer_index.set_property("xalign", 0.5)
        col_index = Gtk.TreeViewColumn("#", renderer_index, text=self.COL_INDEX)
        col_index.set_min_width(30)
        self._treeview.append_column(col_index)

        renderer_status = Gtk.CellRendererText()
        renderer_status.set_property("xalign", 0.5)
        col_status = Gtk.TreeViewColumn("", renderer_status, text=self.COL_STATUS)
        col_status.set_min_width(24)
        self._treeview.append_column(col_status)

        renderer_filename = Gtk.CellRendererText()
        renderer_filename.set_property("ellipsize", Pango.EllipsizeMode.END)
        col_filename = Gtk.TreeViewColumn("File", renderer_filename, text=self.COL_FILENAME)
        col_filename.set_expand(True)
        col_filename.set_min_width(150)
        self._treeview.append_column(col_filename)

        renderer_duration = Gtk.CellRendererText()
        renderer_duration.set_property("xalign", 1.0)
        col_duration = Gtk.TreeViewColumn("Duration", renderer_duration, text=self.COL_DURATION)
        col_duration.set_min_width(50 if self._sidebar_mode else 65)
        self._treeview.append_column(col_duration)

    def _on_add_clicked(self, button: Gtk.Button) -> None:
        dialog = Gtk.FileChooserDialog(
            title="Add Files to Queue",
            parent=self.get_toplevel(),
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Add", Gtk.ResponseType.OK)
        dialog.set_select_multiple(True)

        filter_video = Gtk.FileFilter()
        filter_video.set_name("Media files")
        for pattern in (
            "*.mkv", "*.mp4", "*.avi", "*.webm", "*.mov", "*.m4v",
            "*.mp3", "*.flac", "*.ogg", "*.opus", "*.wav", "*.m4a",
        ):
            filter_video.add_pattern(pattern)
        dialog.add_filter(filter_video)

        filter_all = Gtk.FileFilter()
        filter_all.set_name("All files")
        filter_all.add_pattern("*")
        dialog.add_filter(filter_all)

        if dialog.run() == Gtk.ResponseType.OK:
            paths = [Path(p) for p in dialog.get_filenames()]
            dialog.destroy()
            if paths and self._on_add_files:
                self._on_add_files(paths)
                self.start_discovery(paths)
        else:
            dialog.destroy()

    def _on_remove_clicked(self, button: Gtk.Button) -> None:
        if self._populating:
            return
        selection = self._treeview.get_selection()
        model, tree_iter = selection.get_selected()
        if tree_iter is not None:
            queue_idx = model[tree_iter][self.COL_QUEUE_INDEX]
            if self._on_remove:
                self._on_remove(queue_idx)

    def _on_clear_clicked(self, button: Gtk.Button) -> None:
        if not self._on_clear:
            return

        dialog = Gtk.MessageDialog(
            transient_for=self.get_toplevel(),
            flags=Gtk.DialogFlags.MODAL,
            type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            message_format="Clear the entire queue?",
        )
        dialog.format_secondary_text("This will stop playback and remove all queued files.")

        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.OK:
            self._on_clear()

    def _on_row_activated(self, treeview: Gtk.TreeView, path: Gtk.TreePath, column) -> None:
        if self._populating:
            return
        model = treeview.get_model()
        tree_iter = model.get_iter(path)
        queue_idx = model[tree_iter][self.COL_QUEUE_INDEX]
        if self._on_play_entry:
            self._on_play_entry(queue_idx)

    def _on_button_press(self, widget, event) -> bool:
        if event.type == Gdk.EventType.BUTTON_PRESS and event.button == 3:
            self._show_context_menu(event)
            return True
        return False

    def _get_selected_queue_index(self) -> int:
        selection = self._treeview.get_selection()
        model, tree_iter = selection.get_selected()
        if tree_iter is not None:
            return model[tree_iter][self.COL_QUEUE_INDEX]
        return -1

    def _show_context_menu(self, event) -> None:
        queue_idx = self._get_selected_queue_index()
        if queue_idx < 0:
            return

        menu = Gtk.Menu()

        play_item = Gtk.MenuItem(label="Play")
        play_item.connect("activate", lambda *_: self._on_play_entry and self._on_play_entry(queue_idx))
        menu.append(play_item)

        menu.append(Gtk.SeparatorMenuItem())

        top_item = Gtk.MenuItem(label="Move to Top")
        top_item.connect("activate", lambda *_: self._on_move and self._on_move(queue_idx, 0))
        menu.append(top_item)

        up_item = Gtk.MenuItem(label="Move Up")
        up_item.connect("activate", lambda *_: self._on_move and self._on_move(queue_idx, queue_idx - 1))
        menu.append(up_item)

        down_item = Gtk.MenuItem(label="Move Down")
        down_item.connect("activate", lambda *_: self._on_move and self._on_move(queue_idx, queue_idx + 1))
        menu.append(down_item)

        bottom_item = Gtk.MenuItem(label="Move to Bottom")
        bottom_item.connect("activate", lambda *_: self._on_move and self._on_move(queue_idx, len(self._model) - 1) if self._model else None)
        menu.append(bottom_item)

        menu.append(Gtk.SeparatorMenuItem())

        remove_item = Gtk.MenuItem(label="Remove")
        remove_item.connect("activate", lambda *_: self._on_remove and self._on_remove(queue_idx))
        menu.append(remove_item)

        menu.show_all()
        if hasattr(menu, "popup_at_pointer"):
            menu.popup_at_pointer(event)
        else:
            menu.popup(None, None, None, None, getattr(event, "button", 0), getattr(event, "time", 0))

    def _on_drag_data_get(self, treeview, context, selection_data, info, timestamp) -> None:
        selection = treeview.get_selection()
        model, tree_iter = selection.get_selected()
        if tree_iter is not None:
            queue_idx = model[tree_iter][self.COL_QUEUE_INDEX]
            selection_data.set_text(str(queue_idx), -1)

    def _on_drag_data_received(self, treeview, context, x, y, selection_data, info, timestamp) -> None:
        if self._populating:
            return
        try:
            from_idx = int(selection_data.get_text())
        except (ValueError, TypeError):
            return

        dest_path, drop_pos = treeview.get_dest_row_at_pos(x, y)
        if dest_path is None:
            return

        dest_model = treeview.get_model()
        dest_iter = dest_model.get_iter(dest_path)
        to_idx = dest_model[dest_iter][self.COL_QUEUE_INDEX]

        if from_idx != to_idx and self._on_move:
            self._on_move(from_idx, to_idx)

        context.finish(True, False, timestamp)


def _format_duration(seconds: float) -> str:
    if seconds <= 0:
        return "--:--"
    total = int(seconds)
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"
