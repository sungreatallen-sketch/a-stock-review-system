"""DeepSeek 大模型封装（复用办公项目已验证的调用方式）"""
import json
import logging

from openai import OpenAI

from ..config import load_config

log = logging.getLogger("llm")

# 利好/利空关键词（初筛用，最终由模型结合上下文判断）
POSITIVE_KW = ["中标", "预增", "增长", "回购", "增持", "合作", "签约", "突破", "获批", "涨价",
               "创新高", "订单", "扩产", "投产", "扭亏", "战略", "布局", "龙头", "利好", "获奖"]
NEGATIVE_KW = ["减持", "质押", "问询", "立案", "亏损", "下滑", "处罚", "解禁", "终止", "下调",
               "诉讼", "违规", "退市", "风险", "利空", "警示", "低于预期"]


def _client():
    cfg = load_config()["model"]
    return OpenAI(api_key=cfg.get("api_key"),
                  base_url=cfg.get("base_url") or "https://api.deepseek.com")


def deepseek_chat(system: str, user: str, json_mode: bool = False,
                  max_tokens: int = 4000, effort: str = "medium") -> str:
    cfg = load_config()["model"]
    client = _client()
    kwargs = dict(
        model=cfg.get("model", "deepseek-v4-flash"),
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        max_tokens=max_tokens,
        reasoning_effort=effort,
    )
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content or ""


def classify_news_sentiment(text: str) -> str:
    """关键词初筛：positive / negative / neutral"""
    if not text:
        return "neutral"
    pos = sum(1 for k in POSITIVE_KW if k in text)
    neg = sum(1 for k in NEGATIVE_KW if k in text)
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


def safe_json(text: str) -> dict:
    """解析模型返回的 JSON（容错：去掉围栏与前后噪声）"""
    if not text:
        return {}
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.startswith("json"):
            t = t[4:]
    try:
        return json.loads(t)
    except Exception:
        # 截取第一个 { 到最后一个 }
        try:
            i, j = t.find("{"), t.rfind("}")
            return json.loads(t[i:j + 1])
        except Exception:
            log.error("模型返回非 JSON: %s", text[:200])
            return {}
