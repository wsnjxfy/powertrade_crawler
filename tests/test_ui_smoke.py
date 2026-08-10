from __future__ import annotations

import time
from pathlib import Path
from tkinter import Canvas, TclError, Tk, ttk

import pytest

from powertrade_crawler.config import get_settings
from powertrade_crawler.gui import build_gui
from powertrade_crawler.storage import init_db


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


INTERACTIVE_WIDGETS = (
    ttk.Button,
    ttk.Checkbutton,
    ttk.Combobox,
    ttk.Entry,
    ttk.Radiobutton,
)
VISIBLE_WIDGETS = (*INTERACTIVE_WIDGETS, ttk.Label, ttk.Treeview)


def has_canvas_ancestor(widget, boundary) -> bool:
    current = widget
    while current is not boundary:
        current = current.nametowidget(current.winfo_parent())
        if isinstance(current, Canvas):
            return True
    return False


def assert_visible_controls_fit(boundary, *, context: str) -> None:
    boundary_right = boundary.winfo_rootx() + boundary.winfo_width()
    boundary_bottom = boundary.winfo_rooty() + boundary.winfo_height()
    for widget in descendants(boundary):
        if not isinstance(widget, VISIBLE_WIDGETS) or not widget.winfo_manager():
            continue
        parent = widget.nametowidget(widget.winfo_parent())
        if not parent.winfo_viewable() or has_canvas_ancestor(widget, boundary):
            continue
        label = widget.cget("text") if "text" in widget.keys() else widget.winfo_name()
        assert widget.winfo_ismapped(), f"{context}: control is hidden: {label}"
        assert widget.winfo_width() > 1, f"{context}: control has no width: {label}"
        assert (
            widget.winfo_rootx() + widget.winfo_width() <= boundary_right + 2
        ), f"{context}: control is clipped horizontally: {label}"
        assert (
            widget.winfo_rooty() + widget.winfo_height() <= boundary_bottom + 2
        ), f"{context}: control is clipped vertically: {label}"
        if isinstance(widget, ttk.Treeview):
            total_column_width = sum(
                int(widget.column(column, "width")) for column in widget["columns"]
            )
            if total_column_width > widget.winfo_width() + 2:
                assert str(widget.cget("xscrollcommand")).strip(), (
                    f"{context}: horizontally overflowing table has no scrollbar: {widget}"
                )


def assert_every_notebook_tab_is_usable(root, page, *, context: str, visited=None) -> None:
    visited = set() if visited is None else visited
    root.update_idletasks()
    root.update()
    assert_visible_controls_fit(page, context=context)

    notebooks = [
        widget
        for widget in descendants(page)
        if isinstance(widget, ttk.Notebook)
        and widget.winfo_viewable()
        and str(widget) not in visited
    ]
    if not notebooks:
        return

    notebook = notebooks[0]
    original_tab = notebook.select()
    next_visited = visited | {str(notebook)}
    for tab_id in notebook.tabs():
        tab_label = str(notebook.tab(tab_id, "text"))
        notebook.select(tab_id)
        assert_every_notebook_tab_is_usable(
            root,
            page,
            context=f"{context} > {tab_label}",
            visited=next_visited,
        )
    if original_tab:
        notebook.select(original_tab)


@pytest.mark.parametrize(
    ("window_mode", "tk_scaling"),
    [
        ("minimum", None),
        ("minimum", 1.75),
        ("maximized", None),
        ("maximized", 1.75),
    ],
    ids=["minimum", "minimum-high-dpi", "maximized", "maximized-high-dpi"],
)
def test_every_page_renders_without_clipped_visible_controls(
    tmp_path: Path,
    monkeypatch,
    window_mode,
    tk_scaling,
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
        if tk_scaling is not None:
            root.tk.call("tk", "scaling", tk_scaling)
        root.geometry("1060x680")
        root.minsize(1060, 680)
        if window_mode == "maximized":
            try:
                root.state("zoomed")
            except TclError:
                root.geometry(
                    f"{root.winfo_screenwidth()}x{root.winfo_screenheight()}+0+0"
                )
        shell = build_gui(root, db_path)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            root.update()
            time.sleep(0.02)

        if window_mode == "minimum":
            assert shell.sidebar_collapsed is True
            assert shell.collapse_button.instate(["disabled"])
            shell.toggle_sidebar()
            assert shell.sidebar_collapsed is True
        else:
            assert shell.collapse_button.instate(["!disabled"])
            shell.toggle_sidebar()
            assert shell.sidebar_collapsed is True
            shell.toggle_sidebar()
            assert shell.sidebar_collapsed is False
        for page_key, page in shell.pages.items():
            shell.show_page(page_key)
            root.update()
            root.update_idletasks()
            assert page.winfo_ismapped()
            assert_every_notebook_tab_is_usable(root, page, context=page_key)

        elecheck = shell.controllers["elecheck"]
        assert elecheck.clear_full_crawl_button.cget("text") == "全地区采集/更新"
        assert elecheck.clear_full_crawl_button.cget("style") == "Primary.TButton"
        assert [
            elecheck.notebook.tab(tab_id, "text") for tab_id in elecheck.notebook.tabs()
        ] == [
            "现货价格分析",
            "现货价格数据",
            "代理购电分析",
            "代理购电数据",
            "增量机制分析",
            "增量机制数据",
            "智能 Agent",
        ]

        schedule = shell.controllers["schedule"]
        assert schedule.enable_job_button.instate(["disabled"])
        assert schedule.disable_job_button.instate(["disabled"])
        assert schedule.run_job_button.instate(["disabled"])
        assert schedule.more_job_button.instate(["disabled"])
        test_item = schedule.jobs_tree.insert("", "end", values=(9999, "交互状态测试"))
        schedule.jobs_tree.selection_set(test_item)
        schedule.jobs_tree.event_generate("<<TreeviewSelect>>")
        root.update()
        assert schedule.enable_job_button.instate(["!disabled"])
        assert schedule.disable_job_button.instate(["!disabled"])
        assert schedule.run_job_button.instate(["!disabled"])
        assert schedule.more_job_button.instate(["!disabled"])

        gridstatus = shell.controllers["gridstatus"]
        api_key_buttons = [
            widget
            for widget in descendants(gridstatus.root)
            if isinstance(widget, ttk.Button)
            and widget.cget("text") == "更换 API key"
        ]
        assert len(api_key_buttons) == 1
    finally:
        root.destroy()
