from powertrade_crawler.spiders.base import BaseSpider
from powertrade_crawler.spiders.demo import DemoMarketSpider
from powertrade_crawler.spiders.elecheck import (
    ElecheckClearPriceSpider,
    ElecheckMechanismElectricityPriceSpider,
    ElecheckPurchasingNationalMonthSpider,
    ElecheckPurchasingNationalRangeSpider,
    ElecheckPurchasingProvinceListSpider,
    ElecheckPurchasingProvinceMonthSpider,
)
from powertrade_crawler.spiders.entsoe import (
    EntsoeDayAheadPricesSpider,
    build_entsoe_spider_classes,
)
from powertrade_crawler.spiders.elexon import build_elexon_spider_classes
from powertrade_crawler.spiders.gridstatus import build_gridstatus_spider_classes
from powertrade_crawler.spiders.gzpec_news_combined import GzpecNewsCombinedSpider


SPIDERS: dict[str, type[BaseSpider]] = {
    DemoMarketSpider.name: DemoMarketSpider,
    ElecheckClearPriceSpider.name: ElecheckClearPriceSpider,
    ElecheckMechanismElectricityPriceSpider.name: ElecheckMechanismElectricityPriceSpider,
    ElecheckPurchasingNationalMonthSpider.name: ElecheckPurchasingNationalMonthSpider,
    ElecheckPurchasingNationalRangeSpider.name: ElecheckPurchasingNationalRangeSpider,
    ElecheckPurchasingProvinceListSpider.name: ElecheckPurchasingProvinceListSpider,
    ElecheckPurchasingProvinceMonthSpider.name: ElecheckPurchasingProvinceMonthSpider,
    EntsoeDayAheadPricesSpider.name: EntsoeDayAheadPricesSpider,
    GzpecNewsCombinedSpider.name: GzpecNewsCombinedSpider,
}
SPIDERS.update(build_gridstatus_spider_classes())
SPIDERS.update(build_entsoe_spider_classes())
SPIDERS.update(build_elexon_spider_classes())


def list_spiders() -> list[str]:
    return sorted(SPIDERS)


def get_spider(name: str, **kwargs) -> BaseSpider:
    spider_cls = SPIDERS.get(name)
    if spider_cls is None:
        available = ", ".join(list_spiders())
        raise ValueError(f"Unknown spider: {name}. Available spiders: {available}")
    return spider_cls(**kwargs)
