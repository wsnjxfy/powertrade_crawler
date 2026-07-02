from abc import ABC, abstractmethod
from collections.abc import Iterable

from powertrade_crawler.http import HttpClient
from powertrade_crawler.models import (
    ElecheckClearPriceRecord,
    ElecheckMechanismElectricityPriceRecord,
    ElecheckPurchasingRecord,
    ElexonRecord,
    EntsoeRecord,
    GridStatusDatasetMetadataRecord,
    GridStatusRecord,
    GzpecNewsRecord,
    MarketRecord,
)


SpiderRecord = (
    MarketRecord
    | GzpecNewsRecord
    | GridStatusRecord
    | GridStatusDatasetMetadataRecord
    | EntsoeRecord
    | ElexonRecord
    | ElecheckClearPriceRecord
    | ElecheckPurchasingRecord
    | ElecheckMechanismElectricityPriceRecord
)


class BaseSpider(ABC):
    name: str
    source: str
    source_url: str

    def __init__(self, http: HttpClient | None = None) -> None:
        self.http = http or HttpClient()

    @abstractmethod
    def crawl(self) -> Iterable[SpiderRecord]:
        raise NotImplementedError

    def close(self) -> None:
        self.http.close()
