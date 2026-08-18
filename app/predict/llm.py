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
    """解析模型返回的 JSON（容错：去掉围栏/推理文本/前后噪声）"""
    if not text:
        return {}
    t = text.strip()

    # 1. 去掉 markdown 围栏
    if t.startswith("```"):
        t = t.strip("`")
        if t.startswith("json"):
            t = t[4:]
        t = t.strip()

    # 2. 直接解析
    try:
        return json.loads(t)
    except Exception:
        pass

    # 3. 截取第一个 { 到最后一个 }
    try:
        i, j = t.find("{"), t.rfind("}")
        if i >= 0 and j > i:
            return json.loads(t[i:j + 1])
    except Exception:
        pass

    # 4. 修复常见 JSON 问题：尾部逗号
    try:
        import re
        cleaned = re.sub(r',\s*([}\]])', r'\1', t)
        i, j = cleaned.find("{"), cleaned.rfind("}")
        if i >= 0 and j > i:
            return json.loads(cleaned[i:j + 1])
    except Exception:
        pass

    # 5. 尝试提取最大的完整 JSON 对象（跳过推理文本中的碎片）
    try:
        import re
        # 找所有 {...} 块，取最大的
        depth = 0
        start = -1
        candidates = []
        for idx, ch in enumerate(t):
            if ch == '{':
                if depth == 0:
                    start = idx
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0 and start >= 0:
                    candidates.append(t[start:idx + 1])
                    start = -1
        # 按长度排序，尝试最长的
        candidates.sort(key=len, reverse=True)
        for c in candidates:
            try:
                result = json.loads(c)
                if isinstance(result, dict) and len(result) >= 1:
                    return result
            except Exception:
                continue
    except Exception:
        pass

    log.error("模型返回非 JSON: %s", text[:200])
    return {}
