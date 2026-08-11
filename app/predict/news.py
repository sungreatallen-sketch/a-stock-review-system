"""消息面扫描：候选股近 N 日新闻/公告/事件（同舟 doc_search），关键词情绪初筛"""
import logging

from .llm import classify_news_sentiment

log = logging.getLogger("news")

DAYS = 5
LIMIT = 5


class NewsScanner:
    def __init__(self, mcp):
        self.mcp = mcp

    def _search(self, tool: str, args: dict):
        try:
            return self.mcp.call(tool, args, timeout=60)
        except Exception as e:
            log.warning("%s 查询失败: %s", tool, str(e)[:120])
            return {}

    def _docs(self, resp: dict):
        return ((resp or {}).get("data") or {}).get("documents") or []

    def scan(self, ticker: str, name: str) -> dict:
        """返回 {ticker, name, items:[{title,date,source,url,sentiment}], summary}"""
        items = []
        # 新闻
        r = self._search("tongzhou-fin-research_doc_search__search_company_news",
                         {"company": name, "ticker": ticker, "days": DAYS, "limit": LIMIT})
        for d in self._docs(r):
            title = d.get("title") or ""
            snippet = d.get("snippet") or ""
            text = f"{title}。{snippet}"
            items.append({
                "type": "新闻", "title": title, "date": (d.get("publish_date") or "")[:10],
                "source": d.get("source"), "url": d.get("source_url"),
                "sentiment": classify_news_sentiment(text), "text": text[:300],
            })
        # 公告
        r = self._search("tongzhou-fin-research_doc_search__search_announcements",
                         {"company": name, "ticker": ticker, "days": DAYS, "limit": LIMIT})
        for d in self._docs(r):
            title = d.get("title") or ""
            items.append({
                "type": "公告", "title": title, "date": (d.get("publish_date") or "")[:10],
                "source": d.get("source"), "url": d.get("source_url"),
                "sentiment": classify_news_sentiment(title), "text": (title + "。" + (d.get("summary") or ""))[:300],
            })
        # 事件
        r = self._search("tongzhou-fin-research_doc_search__search_events",
                         {"company": name, "ticker": ticker, "days": DAYS, "limit": LIMIT})
        for d in self._docs(r):
            title = d.get("title") or ""
            items.append({
                "type": "事件", "title": title, "date": (d.get("publish_date") or "")[:10],
                "source": d.get("source"), "url": d.get("source_url"),
                "sentiment": classify_news_sentiment(title), "text": (title + "。" + (d.get("snippet") or ""))[:300],
            })

        pos = sum(1 for x in items if x["sentiment"] == "positive")
        neg = sum(1 for x in items if x["sentiment"] == "negative")
        return {
            "ticker": ticker, "name": name,
            "items": items,
            "summary": f"近{DAYS}日 {len(items)} 条消息：利好 {pos} / 利空 {neg} / 中性 {len(items)-pos-neg}",
        }
