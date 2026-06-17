from collections.abc import Iterable
from pathlib import Path

from bs4 import BeautifulSoup

from powertrade_crawler.models import MarketRecord
from powertrade_crawler.spiders.base import BaseSpider


class DemoMarketSpider(BaseSpider):
    name = "demo-market"
    source = "Demo Electricity Market"
    source_url = "fixtures/demo_market.html"

    def crawl(self) -> Iterable[MarketRecord]:
        fixture_path = Path(__file__).resolve().parents[3] / "fixtures" / "demo_market.html"
        html = fixture_path.read_text(encoding="utf-8")
        soup = BeautifulSoup(html, "html.parser")

        table = soup.select_one("table#market-data")
        if table is None:
            return

        for row in table.select("tbody tr"):
            cells = [cell.get_text(strip=True) for cell in row.select("td")]
            if len(cells) != 6:
                continue
            trade_date, region, market, metric, value, unit = cells
            yield MarketRecord(
                source=self.source,
                market=market,
                region=region,
                trade_date=trade_date,
                metric=metric,
                value=float(value),
                unit=unit,
                currency="CNY" if "CNY" in unit else None,
                raw={"cells": cells, "source_url": self.source_url},
            )
