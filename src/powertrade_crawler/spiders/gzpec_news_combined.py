import re
from collections.abc import Iterable
from datetime import date
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag

from powertrade_crawler.models import GzpecNewsRecord, NewsContentBlock
from powertrade_crawler.spiders.base import BaseSpider


class GzpecNewsCombinedSpider(BaseSpider):
    name = "gzpec-news-combined"
    source = "广州电力交易中心"
    category = "市场研究"
    source_url = "http://www.gzpec.cn/news/scyj/index.html"
    base_url = "http://www.gzpec.cn/news/scyj/"
    page_count = 20

    def crawl(self) -> Iterable[GzpecNewsRecord]:
        # 1. 访问索引页
        # 2. 提取详情页链接
        # 3. 进入详情页
        # 4. 整理成统一数据结构
        for page_no in range(self.page_count):
            index_url = self.build_index_url(page_no)
            index_html = self.http.get_text(index_url)
            for news_url, index_title, index_publish_date in self.parse_index_page(index_html, index_url):
                detail_html = self.http.get_text(news_url)
                yield self.parse_detail_page(
                    detail_html,
                    news_url,
                    index_url,
                    index_title,
                    index_publish_date,
                )

    def build_index_url(self, page_no: int) -> str:
        if page_no == 0:
            return urljoin(self.base_url, "index.html")
        return urljoin(self.base_url, f"index_{page_no}.html")

    def parse_index_page(self, html: str, index_url: str) -> Iterable[tuple[str, str, date]]:
        soup = BeautifulSoup(html, "html.parser")

        for item in soup.select("li.MarkList"):
            link = item.select_one(".Matitle a")
            year_month = item.select_one(".date .year")
            day = item.select_one(".date .day")
            if link is None or year_month is None or day is None:
                continue

            href = link.get("href")
            title = link.get_text(strip=True)
            if not href or not title:
                continue

            yield (
                urljoin(index_url, href),
                title,
                self.parse_index_publish_date(
                    year_month.get_text(strip=True),
                    day.get_text(strip=True),
                ),
            )

    def parse_index_publish_date(self, year_month: str, day: str) -> date:
        year, month = year_month.split("/")
        return date(int(year), int(month), int(day))

    def parse_detail_page(
        self,
        html: str,
        url: str,
        index_url: str,
        index_title: str,
        index_publish_date: date,
    ) -> GzpecNewsRecord:
        soup = BeautifulSoup(html, "html.parser")
        content = soup.select_one(".rightMain .content") or soup.select_one(".content") or soup

        title = self.extract_title(content) or index_title
        publish_date = self.extract_publish_date(content) or index_publish_date
        body = content.select_one(".txt .TRS_Editor") or content.select_one(".txt")

        return GzpecNewsRecord(
            source=self.source,
            category=self.category,
            title=title,
            url=url,
            publish_date=publish_date,
            index_url=index_url,
            news_type=self.classify_news_type(title),
            content_blocks=self.extract_content_blocks(body, url) if body is not None else [],
        )

    def classify_news_type(self, title: str) -> str:
        if title.startswith("绿证交易每周行情一览"):
            return "green_certificate"
        if title.startswith("南方区域电力现货市场每周行情一览"):
            return "spot_market"
        return "ordinary"

    def extract_title(self, content: Tag) -> str:
        title_node = content.select_one("h3.title")
        if title_node is not None:
            return title_node.get_text(strip=True)
        h1_node = content.select_one("h1")
        if h1_node is not None:
            return h1_node.get_text(strip=True)
        return ""

    def extract_publish_date(self, content: Tag) -> date | None:
        subtitle_node = content.select_one(".subTitle")
        if subtitle_node is None:
            return None

        subtitle = " ".join(subtitle_node.get_text(" ", strip=True).split())
        date_match = re.search(r"发布时间：\s*(\d{4})-(\d{2})-(\d{2})", subtitle)
        if date_match is None:
            return None
        return date(
            int(date_match.group(1)),
            int(date_match.group(2)),
            int(date_match.group(3)),
        )

    def extract_content_blocks(self, body: Tag, page_url: str) -> list[NewsContentBlock]:
        blocks: list[NewsContentBlock] = []
        self._walk_content(body, page_url, blocks)

        for sequence, block in enumerate(blocks, start=1):
            block.sequence = sequence
        return blocks

    def _walk_content(
        self,
        node: Tag | NavigableString,
        page_url: str,
        blocks: list[NewsContentBlock],
    ) -> None:
        if isinstance(node, NavigableString):
            self._append_text(blocks, self._normalize_text(str(node)))
            return

        if node.name in {"script", "style"}:
            return

        if node.name == "img":
            src = node.get("src")
            if src:
                blocks.append(
                    NewsContentBlock(
                        sequence=0,
                        block_type="image",
                        url=urljoin(page_url, src),
                        alt=node.get("alt") or None,
                    )
                )
            return

        for child in node.children:
            self._walk_content(child, page_url, blocks)

    def _append_text(self, blocks: list[NewsContentBlock], text: str) -> None:
        if not text:
            return
        if blocks and blocks[-1].block_type == "text":
            blocks[-1].text = f"{blocks[-1].text}{text}"
            return
        blocks.append(NewsContentBlock(sequence=0, block_type="text", text=text))

    def _normalize_text(self, text: str) -> str:
        text = text.replace("\xa0", " ")
        text = re.sub(r"\s+", " ", text)
        return text.strip()
