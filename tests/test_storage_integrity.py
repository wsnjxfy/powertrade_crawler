from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import text

from powertrade_crawler.config import get_settings
from powertrade_crawler.models import (
    ElecheckAreaRecord,
    ElecheckClearPriceRecord,
    ElecheckMechanismElectricityPriceRecord,
    ElecheckPurchasingProvinceRecord,
    ElecheckPurchasingRecord,
    ElexonRecord,
    EntsoeRecord,
    GridStatusDatasetMetadataRecord,
    GridStatusRecord,
    GzpecNewsRecord,
    MarketRecord,
)
from powertrade_crawler.storage import (
    add_null_safe_unique_indexes_if_missing,
    get_engine,
    get_session,
    init_db,
    upsert_elexon_records,
    upsert_entsoe_records,
    upsert_elecheck_area_records,
    upsert_elecheck_clear_price_records,
    upsert_elecheck_mechanism_electricity_price_records,
    upsert_elecheck_purchasing_province_records,
    upsert_elecheck_purchasing_records,
    upsert_gridstatus_dataset_metadata_records,
    upsert_gridstatus_records,
    upsert_gzpec_news_records,
    upsert_records,
)


def prepare_db(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'integrity.db'}")
    get_settings.cache_clear()
    init_db()


def row_count(table_name: str) -> int:
    with get_engine().connect() as connection:
        return int(connection.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar_one())


def test_every_business_upsert_is_idempotent(tmp_path: Path, monkeypatch):
    prepare_db(tmp_path, monkeypatch)
    collected_at = datetime(2026, 8, 10, 1, 2, 3)
    cases = [
        (
            "market_records",
            upsert_records,
            MarketRecord(
                source="demo",
                market="spot",
                region="江苏",
                trade_date=date(2026, 8, 9),
                metric="price",
                value=1,
                unit="CNY/MWh",
                collected_at=collected_at,
            ),
        ),
        (
            "entsoe_records",
            upsert_entsoe_records,
            EntsoeRecord(
                dataset="entsoe_test",
                category="price",
                title_en="Test",
                title_zh="测试",
                row_key="row-1",
                area="DE-LU",
                interval_start_utc="2026-08-09T00:00Z",
                collected_at=collected_at,
            ),
        ),
        (
            "elexon_records",
            upsert_elexon_records,
            ElexonRecord(
                dataset="elexon_test",
                category="price",
                title_en="Test",
                title_zh="测试",
                endpoint="/test",
                row_key="row-1",
                metric="price",
                value_field="price",
                collected_at=collected_at,
            ),
        ),
        (
            "gridstatus_records",
            upsert_gridstatus_records,
            GridStatusRecord(
                request_name="gridstatus_test",
                request_type="dataset_query",
                dataset="test",
                location="CAISO",
                row_key="row-1",
                collected_at=collected_at,
            ),
        ),
        (
            "gridstatus_dataset_metadata",
            upsert_gridstatus_dataset_metadata_records,
            GridStatusDatasetMetadataRecord(
                dataset_id="test_dataset",
                source="caiso",
                collected_at=collected_at,
            ),
        ),
        (
            "gzpec_news_records",
            upsert_gzpec_news_records,
            GzpecNewsRecord(
                source="GZPEC",
                category="公开信息",
                title="测试公告",
                url="https://example.test/news/1",
                index_url="https://example.test/news",
                news_type="ordinary",
                collected_at=collected_at,
            ),
        ),
        (
            "elecheck_clear_price_records",
            upsert_elecheck_clear_price_records,
            ElecheckClearPriceRecord(
                endpoint="statistics",
                area_code="test-area",
                start_date=date(2026, 8, 9),
                end_date=date(2026, 8, 9),
                time96=None,
                metric="average",
                unit="CNY/MWh",
                collected_at=collected_at,
            ),
        ),
        (
            "elecheck_area_records",
            upsert_elecheck_area_records,
            ElecheckAreaRecord(
                area_name="测试地区",
                area_code="test-area",
                collected_at=collected_at,
            ),
        ),
        (
            "elecheck_purchasing_records",
            upsert_elecheck_purchasing_records,
            ElecheckPurchasingRecord(
                endpoint="provinceMonthList",
                data_kind="national_table",
                data_month="2026-08",
                province_name=None,
                metric="price",
                statistic=None,
                related_province_name=None,
                collected_at=collected_at,
            ),
        ),
        (
            "elecheck_purchasing_area_records",
            upsert_elecheck_purchasing_province_records,
            ElecheckPurchasingProvinceRecord(
                province_name="测试省",
                collected_at=collected_at,
            ),
        ),
        (
            "elecheck_mechanism_electricity_price_records",
            upsert_elecheck_mechanism_electricity_price_records,
            ElecheckMechanismElectricityPriceRecord(
                region_name="测试地区",
                category="煤电",
                collected_at=collected_at,
            ),
        ),
    ]

    for table_name, upsert, record in cases:
        before = row_count(table_name)
        assert upsert([record]) == 1
        assert upsert([record]) == 1
        assert row_count(table_name) == before + 1


def test_batch_upsert_rolls_back_when_later_record_cannot_be_serialized(
    tmp_path: Path,
    monkeypatch,
):
    prepare_db(tmp_path, monkeypatch)
    good = MarketRecord(
        source="rollback",
        market="spot",
        region="A",
        trade_date=date(2026, 8, 9),
        metric="price",
        value=1,
        unit="CNY/MWh",
    )
    bad = MarketRecord(
        source="rollback",
        market="spot",
        region="B",
        trade_date=date(2026, 8, 9),
        metric="price",
        value=2,
        unit="CNY/MWh",
        raw={"not_json": {1, 2}},
    )

    with pytest.raises(TypeError):
        upsert_records([good, bad])

    with get_session() as session:
        count = session.execute(
            text("SELECT COUNT(*) FROM market_records WHERE source = 'rollback'")
        ).scalar_one()
    assert count == 0


def test_null_safe_index_deduplicates_old_rows_before_enforcing_constraint(
    tmp_path: Path,
    monkeypatch,
):
    prepare_db(tmp_path, monkeypatch)
    record = ElecheckPurchasingRecord(
        endpoint="provinceMonthList",
        data_kind="national_table",
        data_month="2026-08",
        province_name=None,
        metric="migration-test",
        statistic=None,
        related_province_name=None,
    )
    upsert_elecheck_purchasing_records([record])
    engine = get_engine()
    columns = (
        "source, endpoint, data_kind, data_month, province_name, metric, value, unit, "
        "diff_value, statistic, related_province_name, raw_json, collected_at"
    )
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX uq_elecheck_purchasing_natural_key_null_safe"))
        connection.execute(
            text(
                "INSERT INTO elecheck_purchasing_records "
                f"({columns}) SELECT {columns} FROM elecheck_purchasing_records "
                "WHERE metric = 'migration-test' LIMIT 1"
            )
        )
    assert row_count("elecheck_purchasing_records") == 2

    add_null_safe_unique_indexes_if_missing(engine)

    assert row_count("elecheck_purchasing_records") == 1
    with engine.connect() as connection:
        index_exists = connection.execute(
            text(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='index' "
                "AND name='uq_elecheck_purchasing_natural_key_null_safe'"
            )
        ).scalar_one()
    assert index_exists == 1


def test_delivery_composite_indexes_are_created(tmp_path: Path, monkeypatch):
    prepare_db(tmp_path, monkeypatch)
    expected = {
        "ix_market_source_region_trade_date",
        "ix_entsoe_dataset_area_interval_start",
        "ix_elexon_dataset_area_settlement_date",
        "ix_elexon_dataset_effective_time",
        "ix_gridstatus_dataset_location_record_time",
        "ix_gzpec_news_type_publish_date",
        "ix_elecheck_purchasing_province_month_metric",
        "ix_elecheck_clear_price_browse",
        "ix_elecheck_mechanism_region_category",
    }
    with get_engine().connect() as connection:
        names = {
            str(row[0])
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='index'")
            )
        }
    assert expected <= names
