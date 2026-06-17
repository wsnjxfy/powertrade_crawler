from datetime import date

from powertrade_crawler.spiders.gzpec_news_combined import GzpecNewsCombinedSpider


def test_gzpec_news_combined_spider_builds_index_urls():
    spider = GzpecNewsCombinedSpider()

    assert spider.build_index_url(0) == "http://www.gzpec.cn/news/scyj/index.html"
    assert spider.build_index_url(19) == "http://www.gzpec.cn/news/scyj/index_19.html"


def test_gzpec_news_combined_spider_parses_index_page():
    html = """
    <ul class="Marke">
      <li class="MarkList">
        <div class="date">
          <p class="day">14</p>
          <p class="year">2026/05</p>
        </div>
        <div class="Marktext">
          <p class="Matitle">
            <a href="./202605/t20260515_36372.html" target="_blank">
              绿证交易每周行情一览（2026年第18周）
            </a>
          </p>
        </div>
      </li>
    </ul>
    """
    spider = GzpecNewsCombinedSpider()

    records = list(spider.parse_index_page(html, "http://www.gzpec.cn/news/scyj/index.html"))

    assert records == [
        (
            "http://www.gzpec.cn/news/scyj/202605/t20260515_36372.html",
            "绿证交易每周行情一览（2026年第18周）",
            date(2026, 5, 14),
        )
    ]


def test_gzpec_news_combined_spider_parses_detail_page():
    html = """
    <div class="content">
      <h3 class="title">绿证交易每周行情一览（2026年第18周）</h3>
      <p class="subTitle">信息来源：广州电力交易中心    发布时间：2026-05-14</p>
      <div class="txt">
        <div class="TRS_Editor">
          <p><span>第一段文字</span></p>
          <p style="text-align: center;"><img src="./W020260515637122614859.png" alt="" /></p>
          <p><b><span>一、分省区情况</span></b></p>
          <p><img src="./W020260515637122615466.png" alt="省区图" /></p>
        </div>
      </div>
    </div>
    """
    spider = GzpecNewsCombinedSpider()

    record = spider.parse_detail_page(
        html,
        "http://www.gzpec.cn/news/scyj/202605/t20260515_36372.html",
        "http://www.gzpec.cn/news/scyj/index.html",
        "索引标题",
        date(2026, 5, 14),
    )

    assert record.title == "绿证交易每周行情一览（2026年第18周）"
    assert record.news_type == "green_certificate"
    assert record.publish_date == date(2026, 5, 14)
    assert not hasattr(record, "info_source")
    assert [block.block_type for block in record.content_blocks] == ["text", "image", "text", "image"]
    assert record.content_blocks[1].url == (
        "http://www.gzpec.cn/news/scyj/202605/W020260515637122614859.png"
    )
