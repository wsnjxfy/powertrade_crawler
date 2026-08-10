from powertrade_crawler.app_shell import PAGE_SPECS, resolve_quick_navigation
from powertrade_crawler.dashboard_gui import ScheduleDataApp
from powertrade_crawler.overview_gui import format_record_count


def test_ui_page_keys_are_unique_and_cover_existing_top_level_features():
    keys = [spec.key for spec in PAGE_SPECS]

    assert len(keys) == len(set(keys))
    assert keys == [
        "overview",
        "gridstatus",
        "elecheck",
        "entsoe",
        "elexon",
        "agent",
        "schedule",
        "setup",
    ]


def test_quick_navigation_resolves_user_facing_terms():
    assert resolve_quick_navigation("江苏现货价格") == "elecheck"
    assert resolve_quick_navigation("欧洲日前数据") == "entsoe"
    assert resolve_quick_navigation("英国 Elexon") == "elexon"
    assert resolve_quick_navigation("北美 GridStatus") == "gridstatus"
    assert resolve_quick_navigation("跨来源智能问答") == "agent"
    assert resolve_quick_navigation("数据库维护") == "schedule"
    assert resolve_quick_navigation("新用户 API key 配置") == "setup"
    assert resolve_quick_navigation("回到首页") == "overview"


def test_quick_navigation_rejects_blank_or_unknown_queries():
    assert resolve_quick_navigation("") is None
    assert resolve_quick_navigation("完全未知的功能") is None


def test_overview_record_count_uses_readable_thousands_separators():
    assert format_record_count(0) == "0"
    assert format_record_count(1_284_620) == "1,284,620"


def test_schedule_quick_update_covers_every_supported_source():
    assert set(ScheduleDataApp.source_options.values()) == {
        "elecheck",
        "entsoe",
        "elexon",
        "gridstatus",
        "gzpec",
    }
    assert ScheduleDataApp.source_credentials == {
        "elecheck": ("elecheck_authorization", "Elecheck 采集凭据"),
        "entsoe": ("entsoe_security_token", "ENTSO-E 访问凭据"),
        "gridstatus": ("gridstatus_api_key", "GridStatus API Key"),
    }
