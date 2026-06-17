from powertrade_crawler.spiders.gridstatus import GridStatusSpider, load_gridstatus_request_configs


def test_gridstatus_request_configs_are_named_for_cli():
    configs = load_gridstatus_request_configs()

    names = {config["name"] for config in configs}

    assert "gridstatus_datasets" in names
    assert "gridstatus_caiso_fuel_mix" in names
    assert "gridstatus_ercot_load" in names
    assert "gridstatus_pjm_lmp_day_ahead_hourly_pseg" in names
    assert "gridstatus_nyiso_fuel_mix_updates" in names


def test_gridstatus_spider_maps_query_row_to_record_without_network():
    spider = object.__new__(GridStatusSpider)
    spider.request_config = {
        "name": "gridstatus_pjm_lmp_day_ahead_hourly_pseg",
        "type": "dataset_location_query",
        "dataset": "pjm_lmp_day_ahead_hourly",
        "location": "PSEG",
    }

    record = spider.to_record(
        {
            "interval_start_utc": "2010-01-01T05:00:00+00:00",
            "interval_end_utc": "2010-01-01T06:00:00+00:00",
            "market": "DAY_AHEAD_HOURLY",
            "location": "PSEG",
            "lmp": 41.760992,
        }
    )

    assert record.request_name == "gridstatus_pjm_lmp_day_ahead_hourly_pseg"
    assert record.dataset == "pjm_lmp_day_ahead_hourly"
    assert record.location == "PSEG"
    assert record.interval_start_utc == "2010-01-01T05:00:00+00:00"
    assert record.raw["lmp"] == 41.760992


def test_gridstatus_spider_maps_dataset_row_to_metadata_record_without_network():
    spider = object.__new__(GridStatusSpider)
    spider.request_config = {
        "name": "gridstatus_datasets",
        "type": "datasets",
    }

    record = spider.to_dataset_metadata_record(
        {
            "id": "spp_load_forecast_by_baa",
            "name": "SPP Load Forecast By BAA",
            "source": "spp",
            "status": "active",
            "primary_key_columns": ["interval_start_utc", "publish_time_utc", "baa"],
            "all_columns": [{"name": "load_forecast", "type": "DOUBLE PRECISION"}],
            "number_of_rows_approximate": 31314480,
            "data_frequency": "5_MINUTES",
        }
    )

    assert record.dataset_id == "spp_load_forecast_by_baa"
    assert record.source == "spp"
    assert record.status == "active"
    assert record.primary_key_columns == ["interval_start_utc", "publish_time_utc", "baa"]
    assert record.all_columns[0]["name"] == "load_forecast"
    assert record.number_of_rows_approximate == 31314480
