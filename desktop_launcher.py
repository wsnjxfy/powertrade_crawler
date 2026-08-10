from __future__ import annotations

import shutil
import sys
import locale
import os
from pathlib import Path
from tkinter import messagebox
from uuid import uuid4


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def ensure_headless_streams() -> None:
    """Attach a frozen windowed process to its parent console when possible."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    output_path = os.environ.get("POWERTRADE_HEADLESS_OUTPUT_FILE", "").strip()
    error_path = os.environ.get("POWERTRADE_HEADLESS_ERROR_FILE", "").strip()
    if output_path:
        output = Path(output_path).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        sys.stdout = open(  # noqa: SIM115
            output, "w", encoding="utf-8", errors="backslashreplace"
        )
        error = Path(error_path).resolve() if error_path else output.with_suffix(".err.txt")
        error.parent.mkdir(parents=True, exist_ok=True)
        sys.stderr = open(  # noqa: SIM115
            error, "w", encoding="utf-8", errors="backslashreplace"
        )
        if sys.stdin is None:
            sys.stdin = open(os.devnull, "r", encoding="utf-8")  # noqa: SIM115
        return
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.AttachConsole(-1)  # type: ignore[attr-defined]
            encoding = locale.getpreferredencoding(False) or "utf-8"
            if sys.stdout is None:
                sys.stdout = open(  # noqa: SIM115
                    "CONOUT$", "w", encoding=encoding, errors="backslashreplace"
                )
            if sys.stderr is None:
                sys.stderr = open(  # noqa: SIM115
                    "CONOUT$", "w", encoding=encoding, errors="backslashreplace"
                )
            if sys.stdin is None:
                sys.stdin = open(  # noqa: SIM115
                    "CONIN$", "r", encoding=encoding, errors="replace"
                )
        except OSError:
            pass
    if sys.stdout is None:
        sys.stdout = open(  # noqa: SIM115
            os.devnull, "w", encoding="utf-8", errors="backslashreplace"
        )
    if sys.stderr is None:
        sys.stderr = open(  # noqa: SIM115
            os.devnull, "w", encoding="utf-8", errors="backslashreplace"
        )
    if sys.stdin is None:
        sys.stdin = open(os.devnull, "r", encoding="utf-8")  # noqa: SIM115


def copy_file_if_missing(source: Path, destination: Path) -> None:
    """Atomically install a bundled default while preserving an existing file."""
    if not source.is_file() or destination.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{uuid4().hex}.powertrade-copy"
    )
    try:
        shutil.copyfile(source, temporary)
        try:
            temporary.rename(destination)
        except FileExistsError:
            # Another concurrently launched instance installed the same default first.
            pass
    finally:
        temporary.unlink(missing_ok=True)


def copy_missing_tree(source: Path, destination: Path) -> None:
    """Copy bundled defaults without overwriting user-managed runtime files."""
    if not source.is_dir():
        return
    for source_path in source.rglob("*"):
        relative = source_path.relative_to(source)
        destination_path = destination / relative
        if source_path.is_dir():
            destination_path.mkdir(parents=True, exist_ok=True)
        else:
            copy_file_if_missing(source_path, destination_path)


def prepare_runtime_files(base_dir: Path) -> None:
    os.chdir(base_dir)
    bundled_dir = Path(getattr(sys, "_MEIPASS", base_dir))
    bundled_configs = bundled_dir / "configs"
    runtime_configs = base_dir / "configs"
    copy_missing_tree(bundled_configs, runtime_configs)

    env_path = base_dir / ".env"
    env_example_path = base_dir / ".env.example"
    bundled_env_example_path = bundled_dir / ".env.example"
    copy_file_if_missing(bundled_env_example_path, env_example_path)
    copy_file_if_missing(env_example_path, env_path)
    runtime_data_dir = base_dir / "data"
    runtime_data_dir.mkdir(exist_ok=True)
    bundled_initial_database = bundled_dir / "initial_data" / "powertrade.initial.db"
    runtime_database = runtime_data_dir / "powertrade.db"
    copy_file_if_missing(bundled_initial_database, runtime_database)


def run_acceptance_gui_smoke(base_dir: Path) -> int:
    """Exercise frozen Tk imports and page construction without entering mainloop."""

    ensure_headless_streams()
    prepare_runtime_files(base_dir)
    from powertrade_crawler.storage import init_db

    init_db()
    from powertrade_crawler.gridstatus_seed import (
        GRIDSTATUS_SEED_RELATIVE_PATH,
        seed_gridstatus_dataset_catalog_if_empty,
    )

    seed_gridstatus_dataset_catalog_if_empty(base_dir / GRIDSTATUS_SEED_RELATIVE_PATH)
    from tkinter import Tk

    from powertrade_crawler.gui import build_gui, resolve_sqlite_path

    root = Tk()
    try:
        root.withdraw()
        shell = build_gui(root, resolve_sqlite_path())
        root.update_idletasks()
        root.update()
        for page_key in shell.pages:
            shell.show_page(page_key)
            root.update_idletasks()
            root.update()
    finally:
        root.destroy()
    print('{"gui_smoke":"passed","pages":8}')
    return 0


def main() -> int:
    base_dir = app_dir()
    if len(sys.argv) > 1:
        ensure_headless_streams()
        try:
            if sys.argv[1] == "--acceptance-gui-smoke":
                return run_acceptance_gui_smoke(base_dir)
            if sys.argv[1] == "--headless":
                del sys.argv[1]
            prepare_runtime_files(base_dir)
            from powertrade_crawler.storage import init_db

            init_db()
            from powertrade_crawler.gridstatus_seed import (
                GRIDSTATUS_SEED_RELATIVE_PATH,
                seed_gridstatus_dataset_catalog_if_empty,
            )

            seed_gridstatus_dataset_catalog_if_empty(
                base_dir / GRIDSTATUS_SEED_RELATIVE_PATH
            )
            from powertrade_crawler.cli import app

            app()
        except Exception as exc:
            print(f"Powertrade Crawler headless command failed: {exc}", file=sys.stderr)
            return 1
        return 0

    try:
        prepare_runtime_files(base_dir)

        from powertrade_crawler.storage import init_db

        init_db()
        from powertrade_crawler.gridstatus_seed import (
            GRIDSTATUS_SEED_RELATIVE_PATH,
            seed_gridstatus_dataset_catalog_if_empty,
        )

        seed_gridstatus_dataset_catalog_if_empty(base_dir / GRIDSTATUS_SEED_RELATIVE_PATH)

        from powertrade_crawler.gui import launch_gui

        launch_gui()
    except Exception as exc:
        messagebox.showerror(
            "Powertrade Crawler 启动失败",
            (
                "软件启动时遇到问题。\n\n"
                f"{exc}\n\n"
                "请确认软件目录可写，并且不要直接在压缩包内运行。\n"
                "如需采集需要鉴权的数据，请使用 powertrade set-credential "
                "或让管理员更新同目录 .auth\\credentials.json。"
            ),
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
