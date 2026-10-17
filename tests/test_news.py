"""헤드라인 뉴스 수집: 응답 형식 정규화·빈 응답 대체 경로 검증."""
from pipeline import data as dmod

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>Stocks rally on earnings - Reuters</title><link>https://ex.com/a</link>
<pubDate>Fri, 16 Oct 2026 20:00:00 GMT</pubDate><source url="https://reuters.com">Reuters</source></item>
<item><title>코스피 상승 마감</title><link>https://ex.com/b</link></item>
<item><title></title><link>https://ex.com/c</link></item>
</channel></rss>"""


def test_parse_yf_news_new_and_old_formats():
    items = [
        {"id": "1", "content": {"title": "A", "canonicalUrl": {"url": "https://a"},
                                "provider": {"displayName": "Yahoo"}, "pubDate": "2026-10-16T20:00:00Z"}},
        {"id": "2", "content": {"title": "B", "canonicalUrl": None, "clickThroughUrl": {"url": "https://b"}}},
        {"title": "C", "link": "https://c", "publisher": "Old", "providerPublishTime": 0},
        {"content": {"title": "링크 없음"}},
        "garbage",
    ]
    out = dmod.parse_yf_news(items)
    assert [n["title"] for n in out] == ["A", "B", "C"]
    assert out[0]["publisher"] == "Yahoo" and out[1]["link"] == "https://b"
    assert out[2]["time"].startswith("1970-01-01")


def test_parse_rss_strips_publisher_suffix():
    out = dmod.parse_rss(RSS)
    assert len(out) == 2
    assert out[0]["title"] == "Stocks rally on earnings" and out[0]["publisher"] == "Reuters"
    assert out[0]["time"] and out[1]["publisher"] is None


class _FakeTicker:
    def __init__(self, latest, all_):
        self.news, self._all = latest, all_

    def get_news(self, count=10, tab="news"):
        return self._all


class _FakeYF:
    def __init__(self, latest=None, all_=None, search=None, boom=False):
        self.latest, self.all_, self.search, self.boom = latest or [], all_ or [], search or [], boom

    def Ticker(self, t):
        if self.boom:
            raise RuntimeError("down")
        return _FakeTicker(self.latest, self.all_)

    def Search(self, q, max_results=8, news_count=8):
        return type("S", (), {"news": self.search})()


def _src(fake):
    s = object.__new__(dmod.LiveSource)
    s.yf = fake
    return s


def test_live_news_falls_back_when_latest_empty():
    hit = [{"title": "S", "link": "https://s", "publisher": "P", "providerPublishTime": 1}]
    assert _src(_FakeYF(search=hit)).news("SPY")[0]["title"] == "S"
    assert _src(_FakeYF(all_=[{"content": {"title": "X", "canonicalUrl": {"url": "https://x"}}}])).news("SPY")[0]["title"] == "X"
    assert _src(_FakeYF()).news("SPY") == []
    # Ticker 자체가 실패해도 검색 경로로 이어짐
    assert _src(_FakeYF(search=hit, boom=True)).news("SPY")[0]["link"] == "https://s"
