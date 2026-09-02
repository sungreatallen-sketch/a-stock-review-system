"""发送飞书告警消息"""
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import load_config, _load_env
_load_env()  # 加载 .env

import requests

def send_alert(text: str):
    cfg = load_config()
    feishu = cfg.get('feishu', {})
    app_id = feishu.get('app_id') or os.environ.get('FEISHU_APP_ID')
    app_secret = feishu.get('app_secret') or os.environ.get('FEISHU_APP_SECRET')
    send_to = feishu.get('send_to') or os.environ.get('FEISHU_SEND_TO')
    
    if not all([app_id, app_secret, send_to]):
        print(f"飞书配置缺失: app_id={bool(app_id)}, secret={bool(app_secret)}, send_to={bool(send_to)}")
        return False
    
    # 获取 token
    r = requests.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                      json={"app_id": app_id, "app_secret": app_secret})
    token = r.json().get("tenant_access_token")
    if not token:
        print("获取token失败")
        return False
    
    # 发送。content 必须是完整 JSON 字符串；直接拼接 text 会把换行符
    # 写进非法 JSON，导致飞书接口解析失败。
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    r = requests.post("https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
                      headers=headers,
                      json={"receive_id": send_to, "msg_type": "text",
                            "content": json.dumps({"text": text}, ensure_ascii=False)})
    result = r.json()
    return result.get("code") == 0

if __name__ == "__main__":
    msg = sys.argv[1] if len(sys.argv) > 1 else "测试消息"
    ok = send_alert(msg)
    print(f"发送{'成功' if ok else '失败'}")
