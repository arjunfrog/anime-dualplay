"""Subtitle style preferences dialog.

Reads/writes `subtitle_style` from the global config dict via
player.config.{get,set}_subtitle_style. The dialog returns True from
run_subtitle_style_dialog when the style was changed and saved into the
in-memory dict (the caller persists to disk).
"""

from __future__ import annotations

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk

from player.config import get_subtitle_style, set_subtitle_style
from player.models import SubtitleStyle


_VALIGN_LABELS = [("Top", 0), ("Baseline", 1), ("Bottom", 2), ("Center", 3)]
_HALIGN_LABELS = [("Left", 0), ("Center", 1), ("Right", 2)]


def run_subtitle_style_dialog(parent: Gtk.Window, config_data: dict) -> bool:
    style = get_subtitle_style(config_data)

    dialog = Gtk.Dialog(
        title="Subtitle Style",
        parent=parent,
        flags=Gtk.DialogFlags.MODAL,
    )
    dialog.add_button("_Cancel", Gtk.ResponseType.CANCEL)
    dialog.add_button("_Save", Gtk.ResponseType.OK)
    dialog.set_default_size(360, -1)

    grid = Gtk.Grid(column_spacing=8, row_spacing=6)
    grid.set_margin_start(10)
    grid.set_margin_end(10)
    grid.set_margin_top(8)
    grid.set_margin_bottom(8)
    dialog.get_content_area().pack_start(grid, True, True, 0)

    row = 0
    grid.attach(Gtk.Label(label="Font:", xalign=1.0), 0, row, 1, 1)
    font_button = Gtk.FontButton.new_with_font(style.font_desc)
    font_button.set_hexpand(True)
    grid.attach(font_button, 1, row, 1, 1)
    row += 1

    grid.attach(Gtk.Label(label="Position:", xalign=1.0), 0, row, 1, 1)
    valign_combo = Gtk.ComboBoxText()
    for label, value in _VALIGN_LABELS:
        valign_combo.append(str(value), label)
    valign_combo.set_active_id(str(style.valignment))
    grid.attach(valign_combo, 1, row, 1, 1)
    row += 1

    grid.attach(Gtk.Label(label="Alignment:", xalign=1.0), 0, row, 1, 1)
    halign_combo = Gtk.ComboBoxText()
    for label, value in _HALIGN_LABELS:
        halign_combo.append(str(value), label)
    halign_combo.set_active_id(str(style.halignment))
    grid.attach(halign_combo, 1, row, 1, 1)
    row += 1

    grid.attach(Gtk.Label(label="Text color:", xalign=1.0), 0, row, 1, 1)
    color_button = Gtk.ColorButton()
    color_button.set_use_alpha(True)
    color_button.set_rgba(_argb_to_rgba(style.color_argb))
    grid.attach(color_button, 1, row, 1, 1)
    row += 1

    grid.attach(Gtk.Label(label="Outline color:", xalign=1.0), 0, row, 1, 1)
    outline_button = Gtk.ColorButton()
    outline_button.set_use_alpha(True)
    outline_button.set_rgba(_argb_to_rgba(style.outline_color_argb))
    grid.attach(outline_button, 1, row, 1, 1)
    row += 1

    outline_check = Gtk.CheckButton(label="Draw outline")
    outline_check.set_active(style.draw_outline)
    grid.attach(outline_check, 1, row, 1, 1)
    row += 1

    shadow_check = Gtk.CheckButton(label="Draw shadow")
    shadow_check.set_active(style.draw_shadow)
    grid.attach(shadow_check, 1, row, 1, 1)

    dialog.show_all()
    response = dialog.run()

    if response != Gtk.ResponseType.OK:
        dialog.destroy()
        return False

    new_style = SubtitleStyle(
        font_desc=font_button.get_font() or style.font_desc,
        halignment=int(halign_combo.get_active_id() or style.halignment),
        valignment=int(valign_combo.get_active_id() or style.valignment),
        draw_outline=outline_check.get_active(),
        draw_shadow=shadow_check.get_active(),
        color_argb=_rgba_to_argb(color_button.get_rgba()),
        outline_color_argb=_rgba_to_argb(outline_button.get_rgba()),
    )
    dialog.destroy()

    if _styles_equal(new_style, style):
        return False
    set_subtitle_style(config_data, new_style)
    return True


def _argb_to_rgba(argb: int) -> Gdk.RGBA:
    a = ((argb >> 24) & 0xFF) / 255.0
    r = ((argb >> 16) & 0xFF) / 255.0
    g = ((argb >> 8) & 0xFF) / 255.0
    b = (argb & 0xFF) / 255.0
    return Gdk.RGBA(r, g, b, a)


def _rgba_to_argb(rgba: Gdk.RGBA) -> int:
    a = int(round(rgba.alpha * 255)) & 0xFF
    r = int(round(rgba.red * 255)) & 0xFF
    g = int(round(rgba.green * 255)) & 0xFF
    b = int(round(rgba.blue * 255)) & 0xFF
    return (a << 24) | (r << 16) | (g << 8) | b


def _styles_equal(a: SubtitleStyle, b: SubtitleStyle) -> bool:
    return (
        a.font_desc == b.font_desc
        and a.halignment == b.halignment
        and a.valignment == b.valignment
        and a.draw_outline == b.draw_outline
        and a.draw_shadow == b.draw_shadow
        and a.color_argb == b.color_argb
        and a.outline_color_argb == b.outline_color_argb
    )
