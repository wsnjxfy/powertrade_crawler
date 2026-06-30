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

    env_path = base_dir / ".env"
    env_example_path = base_dir / ".env.example"
    bundled_env_example_path = bundled_dir / ".env.example"
    if not env_example_path.exists() and bundled_env_example_path.exists():
        shutil.copyfile(bundled_env_example_path, env_example_path)
    if not env_path.exists() and env_example_path.exists():
        shutil.copyfile(env_example_path, env_path)
    (base_dir / "data").mkdir(exist_ok=True)


def main() -> int:
    base_dir = app_dir()
    try:
        prepare_runtime_files(base_dir)

        from powertrade_crawler.storage import init_db

        init_db()

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
