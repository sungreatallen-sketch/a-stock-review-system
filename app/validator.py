"""交叉验证：双源比对 + 容差，输出可信值、来源与验证状态"""
import logging

log = logging.getLogger("validator")


def validate_number(primary, secondary, name, rel_tol=0.005, abs_tol=0.05):
    """
    数值交叉验证。
    primary: (value, source)  首选源
    secondary: (value, source) 验证源
    返回: {value, sources, validated, note}
    """
    pv, ps = primary
    sv, ss = secondary
    if pv is None and sv is None:
        return {"value": None, "sources": [], "validated": False, "note": "数据不可获取"}
    if pv is None:
        return {"value": sv, "sources": [ss], "validated": False, "note": f"首选源缺失，采用{ss}"}
    if sv is None:
        return {"value": pv, "sources": [ps], "validated": False, "note": f"验证源缺失，仅{ps}单源"}
    diff = abs(pv - sv)
    tol = max(abs_tol, abs(pv) * rel_tol)
    if diff <= tol:
        return {"value": pv, "sources": [ps, ss], "validated": True,
                "note": f"{ps}与{ss}一致（差异{diff:.4f}）"}
    return {"value": pv, "sources": [ps, ss], "validated": True,
            "note": f"{ps}与{ss}存在差异（{pv} vs {sv}），采用{ps}为准"}


def validate_text(primary, secondary, name):
    pv, ps = primary
    sv, ss = secondary
    if pv == sv:
        return {"value": pv, "sources": [ps, ss], "validated": True, "note": f"{ps}与{ss}一致"}
    if pv is None and sv is None:
        return {"value": None, "sources": [], "validated": False, "note": "数据不可获取"}
    if pv is None:
        return {"value": sv, "sources": [ss], "validated": False, "note": f"采用{ss}"}
    return {"value": pv, "sources": [ps, ss], "validated": True,
            "note": f"{ps}与{ss}存在差异，采用{ps}"}
