from __future__ import annotations

import sys
from pathlib import Path

from desktop_launcher import prepare_runtime_files


def test_prepare_runtime_files_adds_missing_gridstatus_seed_without_overwrite(
    tmp_path: Path,
    monkeypatch,
) -> None:
    bundled_dir = tmp_path / "bundled"
    bundled_seed = bundled_dir / "configs" / "gridstatus" / "datasets.initial.json"
    bundled_seed.parent.mkdir(parents=True)
    bundled_seed.write_text('{"dataset_count": 535}', encoding="utf-8")
    (bundled_dir / ".env.example").write_text("APP_ENV=prod\n", encoding="utf-8")
    bundled_database = bundled_dir / "initial_data" / "powertrade.initial.db"
    bundled_database.parent.mkdir(parents=True)
    bundled_database.write_bytes(b"initial demo database")

    runtime_dir = tmp_path / "runtime"
    runtime_configs = runtime_dir / "configs"
    runtime_configs.mkdir(parents=True)
    user_config = runtime_configs / "user-kept.json"
    user_config.write_text('{"keep": true}', encoding="utf-8")
    monkeypatch.setattr(sys, "_MEIPASS", str(bundled_dir), raising=False)

    prepare_runtime_files(runtime_dir)

    assert (runtime_configs / "gridstatus" / "datasets.initial.json").read_text(
        encoding="utf-8"
    ) == '{"dataset_count": 535}'
    assert user_config.read_text(encoding="utf-8") == '{"keep": true}'
    assert (runtime_dir / ".env").exists()
    assert (runtime_dir / "data").is_dir()
    runtime_database = runtime_dir / "data" / "powertrade.db"
    assert runtime_database.read_bytes() == b"initial demo database"

    runtime_database.write_bytes(b"user database")
    prepare_runtime_files(runtime_dir)
    assert runtime_database.read_bytes() == b"user database"
