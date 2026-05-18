#!/usr/bin/env python3
"""
app.py — GTK application entry point for Dual Audio Player.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gst", "1.0")
from gi.repository import Gtk, Gst  # noqa: E402

from ui.main_window import MainWindow


def _setup_logging() -> None:
    log_dir = Path.home() / ".local" / "share" / "dual-audio-player"
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("dual_audio_player")
    logger.setLevel(logging.DEBUG)

    fh = logging.FileHandler(str(log_dir / "debug.log"), encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    logger.info("Dual Audio Player starting")


def main() -> int:
    _setup_logging()

    Gst.init(None)

    config_path = Path(__file__).resolve().parent / "config.json"
    if not config_path.exists():
        print(f"Warning: config.json not found at {config_path}", file=sys.stderr)

    window = MainWindow(config_path)
    window.show_all()

    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
