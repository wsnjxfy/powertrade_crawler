from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from typing import Any


_DISPATCHER_ATTRIBUTE = "_powertrade_ui_dispatcher"


class TkEventDispatcher:
    """Deliver background-thread results on Tk's owning thread.

    Tk itself is never called by :meth:`post`.  A single timer, installed while
    the UI is being built on the main thread, drains a regular thread-safe
    queue.  This avoids the tempting but unsafe ``widget.after(...)`` call from
    worker threads.
    """

    def __init__(self, root: Any, *, poll_interval_ms: int = 25) -> None:
        self.root = root
        self.poll_interval_ms = poll_interval_ms
        self._events: queue.Queue[Callable[[], None]] = queue.Queue()
        self._closed = threading.Event()
        self._after_id: str | None = None
        self.root.bind("<Destroy>", self._on_root_destroy, add="+")
        self._schedule_drain()

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def post(self, callback: Callable[[], None]) -> bool:
        """Queue a callback without touching Tk; safe from any thread."""

        if self.closed:
            return False
        self._events.put(callback)
        return True

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        after_id, self._after_id = self._after_id, None
        if after_id is not None:
            try:
                self.root.after_cancel(after_id)
            except Exception:
                pass
        self._discard_pending()

    def _on_root_destroy(self, event: Any) -> None:
        if event.widget is self.root:
            self.close()

    def _schedule_drain(self) -> None:
        if self.closed:
            return
        try:
            self._after_id = self.root.after(self.poll_interval_ms, self._drain)
        except Exception:
            self._closed.set()
            self._after_id = None
            self._discard_pending()

    def _drain(self) -> None:
        self._after_id = None
        if self.closed:
            self._discard_pending()
            return
        for _ in range(256):
            try:
                callback = self._events.get_nowait()
            except queue.Empty:
                break
            try:
                callback()
            except Exception as exc:
                self._report_callback_exception(exc)
        self._schedule_drain()

    def _report_callback_exception(self, exc: Exception) -> None:
        reporter = getattr(self.root, "report_callback_exception", None)
        if callable(reporter):
            reporter(type(exc), exc, exc.__traceback__)
            return
        raise exc

    def _discard_pending(self) -> None:
        while True:
            try:
                self._events.get_nowait()
            except queue.Empty:
                return


class UiLifecycle:
    """A cancellable delivery token tied to one page or child window."""

    def __init__(self, owner: Any, dispatcher: TkEventDispatcher | None = None) -> None:
        self.owner = owner
        self.dispatcher = dispatcher or get_ui_dispatcher(owner)
        self.cancel_event = threading.Event()
        self.owner.bind("<Destroy>", self._on_owner_destroy, add="+")

    @property
    def active(self) -> bool:
        return not self.cancel_event.is_set() and not self.dispatcher.closed

    def post(self, callback: Callable[..., None], *args: Any, **kwargs: Any) -> bool:
        if not self.active:
            return False

        def deliver() -> None:
            if self.active:
                callback(*args, **kwargs)

        return self.dispatcher.post(deliver)

    def close(self) -> None:
        self.cancel_event.set()

    def _on_owner_destroy(self, event: Any) -> None:
        if event.widget is self.owner:
            self.close()


def get_ui_dispatcher(widget: Any) -> TkEventDispatcher:
    """Return the one dispatcher shared by every page in a Tk window."""

    root_getter = getattr(widget, "_root", None)
    root = root_getter() if callable(root_getter) else widget.winfo_toplevel()
    dispatcher = getattr(root, _DISPATCHER_ATTRIBUTE, None)
    if dispatcher is None or dispatcher.closed:
        dispatcher = TkEventDispatcher(root)
        setattr(root, _DISPATCHER_ATTRIBUTE, dispatcher)
    return dispatcher


def install_ui_dispatcher(root: Any) -> TkEventDispatcher:
    """Install the dispatcher explicitly during application startup."""

    dispatcher = getattr(root, _DISPATCHER_ATTRIBUTE, None)
    if dispatcher is None or dispatcher.closed:
        dispatcher = TkEventDispatcher(root)
        setattr(root, _DISPATCHER_ATTRIBUTE, dispatcher)
    return dispatcher
