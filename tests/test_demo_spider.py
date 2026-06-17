from powertrade_crawler.spiders.demo import DemoMarketSpider


def test_demo_spider_parses_fixture():
    records = list(DemoMarketSpider().crawl())

    assert len(records) == 2
    assert records[0].source == "Demo Electricity Market"
    assert records[0].region == "广东"
    assert records[0].metric == "clearing_price"
    assert records[0].value == 412.35
