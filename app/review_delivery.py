"""报告终版校验、发送记录与进程/线程互斥；不调用外部服务。"""
from contextlib import contextmanager
from datetime import datetime, time
from pathlib import Path
import fcntl
import json
import math
import os
import tempfile
import threading
from .utils import MARKET_TZ, is_trading_day, market_now

_thread_lock = threading.RLock()
_local = threading.local()

def automatic_target(now=None):
    now = now or market_now()
    if now.tzinfo is not None: now = now.astimezone(MARKET_TZ)
    if now.time() < time(16): return None
    return now.date().isoformat() if is_trading_day(now.date()) else None

def final_report(report, day):
    if report.get('date') != day: return False
    pred = report.get('prediction') or {}
    targets = pred.get('targets') or []
    if pred.get('date') != day or pred.get('status') != 'M3完整版' or len(targets) != 3: return False
    codes = [str(t.get('code') or '').split('.')[0] for t in targets]
    if any(not c for c in codes) or len(set(codes)) != len(codes): return False
    try:
        if any(not math.isfinite(float(t['参考买入价(收盘)'])) or float(t['参考买入价(收盘)']) <= 0 for t in targets): return False
        stamp = datetime.fromisoformat(report['meta']['generated_at'])
        if stamp.tzinfo is not None: stamp = stamp.astimezone(MARKET_TZ)
        return stamp.date().isoformat() == day and stamp.time() >= time(15, 30)
    except (KeyError, TypeError, ValueError):
        return False

def report_delivered(data_dir, day):
    """一次成功终版发送按报告交易日记账，重新生成不会清除成功记录。"""
    path = Path(data_dir) / f'review_delivery_{day}.json'
    if path.exists():
        info = json.loads(path.read_text(encoding='utf-8'))
        if info.get('date') != day or info.get('status') != 'sent':
            raise ValueError(f'发送记录异常: {path.name}')
        return True
    flag = Path(data_dir) / f'last_review_sent_{day}.flag'
    if not flag.exists(): return False
    text = flag.read_text(encoding='utf-8').strip()
    if not text: return False
    try:
        stamp = datetime.fromisoformat(text)
        if stamp.tzinfo is not None: stamp = stamp.astimezone(MARKET_TZ)
        # 兼容旧格式；盘中标记不算终版，终版生成时间变动不再导致重发。
        return stamp.date().isoformat() == day and stamp.time() >= time(15,30)
    except ValueError:
        raise ValueError(f'发送标记无法解析: {flag.name}')

def mark_report_delivered(data_dir, report, source):
    day = report.get('date')
    if not final_report(report, day): return False
    path = Path(data_dir); path.mkdir(parents=True, exist_ok=True)
    receipt = path / f'review_delivery_{day}.json'
    # 后续手动查看不得覆盖首次成功发送证据。
    if not receipt.exists():
        info={'date':day,'status':'sent','sent_at':market_now().isoformat(timespec='seconds'),
              'generated_at':report['meta']['generated_at'],'source':source}
        fd, temp = tempfile.mkstemp(prefix='.review-delivery-', dir=path)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as out:
                json.dump(info,out,ensure_ascii=False,indent=2);out.flush();os.fsync(out.fileno())
            os.replace(temp,receipt)
        finally:
            if os.path.exists(temp): os.unlink(temp)
    # 保留旧脚本兼容字段；新的日级记录为准。
    flag=path/f'last_review_sent_{day}.flag'
    flag.write_text(report['meta']['generated_at'], encoding='utf-8')
    return True

@contextmanager
def delivery_lock(data_dir, blocking=True):
    """同一数据目录串行处理自动与手动发送；同线程支持嵌套。"""
    acquired=_thread_lock.acquire(blocking=blocking)
    if not acquired:
        yield False
        return
    file=None
    try:
        if getattr(_local,'depth',0):
            _local.depth+=1
            try: yield True
            finally: _local.depth-=1
            return
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        file=open(Path(data_dir)/'review_delivery.lock','a')
        try: fcntl.flock(file, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        _local.depth=1
        try: yield True
        finally: _local.depth=0
    finally:
        if file is not None: file.close()
        _thread_lock.release()
