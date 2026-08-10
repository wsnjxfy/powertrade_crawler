from __future__ import annotations

import shutil
import sys
from pathlib import Path
from tkinter import messagebox


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def prepare_runtime_files(base_dir: Path) -> None:
    import os

    os.chdir(base_dir)
    bundled_dir = Path(getattr(sys, "_MEIPASS", base_dir))
    bundled_configs = bundled_dir / "configs"
    runtime_configs = base_dir / "configs"
    if not runtime_configs.exists() and bundled_configs.exists():
        shutil.copytree(bundled_configs, runtime_configs)
    else:
        bundled_gridstatus_seed = bundled_configs / "gridstatus" / "datasets.initial.json"
        runtime_gridstatus_seed = runtime_configs / "gridstatus" / "datasets.initial.json"
        if bundled_gridstatus_seed.exists() and not runtime_gridstatus_seed.exists():
            runtime_gridstatus_seed.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(bundled_gridstatus_seed, runtime_gridstatus_seed)

    env_path = base_dir / ".env"
    env_example_path = base_dir / ".env.example"
    bundled_env_example_path = bundled_dir / ".env.example"
    if not env_example_path.exists() and bundled_env_example_path.exists():
        shutil.copyfile(bundled_env_example_path, env_example_path)
    if not env_path.exists() and env_example_path.exists():
        shutil.copyfile(env_example_path, env_path)
    runtime_data_dir = base_dir / "data"
    runtime_data_dir.mkdir(exist_ok=True)
    bundled_initial_database = bundled_dir / "initial_data" / "powertrade.initial.db"
    runtime_database = runtime_data_dir / "powertrade.db"
    if bundled_initial_database.exists() and not runtime_database.exists():
        shutil.copyfile(bundled_initial_database, runtime_database)


def main() -> int:
    base_dir = app_dir()
    if len(sys.argv) > 1:
        try:
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
