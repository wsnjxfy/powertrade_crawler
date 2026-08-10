from __future__ import annotations

import ast
import threading
from pathlib import Path
from types import SimpleNamespace

from powertrade_crawler.ui_dispatch import (
    TkEventDispatcher,
    UiLifecycle,
    get_ui_dispatcher,
)


class FakeRoot:
    def __init__(self) -> None:
        self.callbacks: dict[str, object] = {}
        self.bindings: dict[str, list[object]] = {}
        self.errors: list[Exception] = []
        self._next_id = 0

    def _root(self):
        return self

    def winfo_toplevel(self):
        return self

    def bind(self, event: str, callback, add: str | None = None) -> None:
        self.bindings.setdefault(event, []).append(callback)

    def after(self, _delay: int, callback) -> str:
        self._next_id += 1
        after_id = f"after-{self._next_id}"
        self.callbacks[after_id] = callback
        return after_id

    def after_cancel(self, after_id: str) -> None:
        self.callbacks.pop(after_id, None)

    def report_callback_exception(self, _kind, exc: Exception, _traceback) -> None:
        self.errors.append(exc)

    def run_next_timer(self) -> None:
        after_id = next(iter(self.callbacks))
        callback = self.callbacks.pop(after_id)
        callback()


class FakeOwner:
    def __init__(self, root: FakeRoot) -> None:
        self.root = root
        self.destroy_callbacks: list[object] = []

    def _root(self):
        return self.root

    def winfo_toplevel(self):
        return self.root

    def bind(self, event: str, callback, add: str | None = None) -> None:
        if event == "<Destroy>":
            self.destroy_callbacks.append(callback)

    def destroy(self) -> None:
        event = SimpleNamespace(widget=self)
        for callback in self.destroy_callbacks:
            callback(event)


def test_worker_posts_are_delivered_only_by_main_thread_drain() -> None:
    root = FakeRoot()
    dispatcher = TkEventDispatcher(root, poll_interval_ms=1)
    owner = FakeOwner(root)
    lifecycle = UiLifecycle(owner, dispatcher)
    main_thread_id = threading.get_ident()
    delivered_on: list[int] = []

    worker = threading.Thread(
        target=lambda: lifecycle.post(delivered_on.append, threading.get_ident())
    )
    worker.start()
    worker.join()

    assert delivered_on == []
    root.run_next_timer()
    assert delivered_on and delivered_on[0] != main_thread_id

    callback_thread: list[int] = []
    worker = threading.Thread(
        target=lambda: lifecycle.post(lambda: callback_thread.append(threading.get_ident()))
    )
    worker.start()
    worker.join()
    root.run_next_timer()
    assert callback_thread == [main_thread_id]
    assert root.errors == []


def test_destroyed_lifecycle_drops_queued_and_future_callbacks() -> None:
    root = FakeRoot()
    lifecycle = UiLifecycle(FakeOwner(root))
    delivered: list[str] = []

    assert lifecycle.post(delivered.append, "queued") is True
    lifecycle.owner.destroy()
    assert lifecycle.cancel_event.is_set()
    assert lifecycle.post(delivered.append, "late") is False
    root.run_next_timer()

    assert delivered == []


def test_pages_and_child_windows_share_one_root_dispatcher() -> None:
    root = FakeRoot()
    first = get_ui_dispatcher(FakeOwner(root))
    second = get_ui_dispatcher(FakeOwner(root))

    assert first is second


def test_background_workers_never_schedule_tk_after_directly() -> None:
    project_root = Path(__file__).resolve().parents[1]
    violations: list[str] = []
    for relative_path in (
        "src/powertrade_crawler/gui.py",
        "src/powertrade_crawler/dashboard_gui.py",
        "src/powertrade_crawler/overview_gui.py",
        "src/powertrade_crawler/credential_setup_gui.py",
        "src/powertrade_crawler/agent/gui.py",
        "src/powertrade_crawler/market_agent/gui.py",
    ):
        path = project_root / relative_path
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if "worker" not in node.name.lower():
                continue
            for child in ast.walk(node):
                if not isinstance(child, ast.Call) or not isinstance(child.func, ast.Attribute):
                    continue
                if child.func.attr in {"after", "after_idle", "after_cancel"}:
                    violations.append(f"{relative_path}:{child.lineno}:{node.name}")

    assert violations == []
