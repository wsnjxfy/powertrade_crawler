from pathlib import Path

import pytest

from powertrade_crawler.storage import export_table_to_csv


def test_export_table_rejects_invalid_table_name(tmp_path: Path):
    with pytest.raises(ValueError):
        export_table_to_csv("gridstatus_records;drop", tmp_path / "out.csv")
