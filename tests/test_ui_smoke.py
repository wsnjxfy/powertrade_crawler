from __future__ import annotations

import time
from pathlib import Path
from tkinter import TclError, Tk, ttk

import pytest

from powertrade_crawler.config import get_settings
from powertrade_crawler.gui import build_gui
from powertrade_crawler.storage import init_db


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def test_every_page_renders_at_minimum_window_without_clipped_visible_buttons(
    tmp_path: Path,
    monkeypatch,
):
    db_path = tmp_path / "ui smoke 中文路径.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    init_db()
    try:
        root = Tk()
    except TclError as exc:
        pytest.skip(f"Tk display is unavailable: {exc}")

    try:
        root.geometry("1060x680")
        root.minsize(1060, 680)
        shell = build_gui(root, db_path)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            root.update()
            time.sleep(0.02)

        assert shell.sidebar_collapsed is True
        root_right = root.winfo_rootx() + root.winfo_width()
        root_bottom = root.winfo_rooty() + root.winfo_height()
        for page_key, page in shell.pages.items():
            shell.show_page(page_key)
            root.update()
            root.update_idletasks()
            assert page.winfo_ismapped()
            for widget in descendants(page):
                if not isinstance(widget, ttk.Button) or not widget.winfo_viewable():
                    continue
                assert widget.winfo_width() > 1
                assert widget.winfo_rootx() + widget.winfo_width() <= root_right + 2
                assert widget.winfo_rooty() + widget.winfo_height() <= root_bottom + 2
    finally:
        root.destroy()
