"""生成手机自适应的 HTML 可视化复盘报告（ECharts 本地加载）"""
import html
import json
from datetime import datetime

import base64
from pathlib import Path

_STATIC = Path(__file__).resolve().parent.parent / "static" / "echarts.min.js"
_ECHARTS_JS = _STATIC.read_text(encoding="utf-8") if _STATIC.exists() else ""


TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>A股收盘复盘 {{date}}</title>
<style>
:root{--red:#e0342c;--green:#0aa869;--bg:#0f1115;--card:#1a1d24;--text:#e8eaed;--sub:#9aa3af;--line:#2a2f3a}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Helvetica Neue",sans-serif;padding:16px;padding-bottom:40px;line-height:1.5}
h1{font-size:20px;font-weight:700;margin-bottom:4px}
.sub{color:var(--sub);font-size:12px;margin-bottom:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;margin-bottom:14px}
.card h2{font-size:15px;margin-bottom:10px;display:flex;align-items:center;gap:6px}
.card h2 .tag{font-size:10px;color:var(--sub);font-weight:400;border:1px solid var(--line);padding:1px 6px;border-radius:8px}
.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}
.idx{padding:12px;border-radius:10px;background:#14171e;border:1px solid var(--line)}
.idx .n{font-size:13px;color:var(--sub)}
.idx .c{font-size:20px;font-weight:700;margin:2px 0}
.idx .p{font-size:13px;font-weight:600}
.up{color:var(--red)} .down{color:var(--green)}
.emo{display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px}
.emo .b{flex:1;min-width:72px;text-align:center;padding:10px 4px;border-radius:10px;background:#14171e;border:1px solid var(--line)}
.emo .b .v{font-size:22px;font-weight:800}
.emo .b .l{font-size:11px;color:var(--sub);margin-top:2px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{color:var(--sub);font-weight:500;text-align:left;padding:6px 4px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:7px 4px;border-bottom:1px solid #22262f;white-space:nowrap}
tr:last-child td{border-bottom:none}
.tbl-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
.src{font-size:11px;color:var(--sub);line-height:1.7;word-break:break-all}
.src li{margin-left:16px}
.note{font-size:11px;color:var(--sub);margin-top:6px;line-height:1.6}
.chart{width:100%;height:220px}
.foot{text-align:center;color:var(--sub);font-size:11px;margin-top:20px;line-height:1.8}
.warn{color:#d9a441;font-size:12px;margin-top:6px}
.badge{display:inline-block;font-size:10px;padding:1px 8px;border-radius:8px;margin-left:6px;vertical-align:middle}
.badge.ok{background:rgba(10,168,105,.15);color:var(--green)}
.badge.single{background:rgba(217,164,65,.15);color:#d9a441}
a{color:#5aa7ff;text-decoration:none}
</style>
</head>
<body>
<h1>A股收盘复盘 · {{date}}</h1>
<div class="sub">生成时间 {{gen_time}} · 数据均经真实来源采集与交叉验证</div>

<!-- 指数 -->
<div class="card"><h2>市场指数</h2>
<div class="grid" id="idxGrid"></div>
<div class="chart" id="idxChart"></div>
</div>

<!-- 情绪 -->
<div class="card"><h2>市场情绪</h2>
<div class="emo" id="emoBox"></div>
<div class="chart" id="emoChart"></div>
</div>

<!-- 板块 -->
<div class="card"><h2>板块排行（行业 Top10）</h2>
<div class="tbl-wrap"><table id="indTbl"></table></div>
</div>
<div class="card"><h2>板块排行（概念 Top10）</h2>
<div class="tbl-wrap"><table id="conTbl"></table></div>
</div>

<!-- 资金 -->
<div class="card"><h2>资金数据</h2>
<div id="capBox"></div>
</div>

<!-- 龙虎榜 -->
<div class="card"><h2>龙虎榜（{{lhb_date}}）</h2>
<div class="tbl-wrap"><table id="lhbTbl"></table></div>
</div>

<!-- 预测（M1 占位） -->
<div class="card"><h2>次日标的预测</h2>
<div id="predBox" class="sub"></div>
</div>

<!-- 推荐跟踪 -->
<div class="card"><h2>推荐跟踪（模拟盘）</h2>
<div id="trackBox"></div>
</div>

<!-- 来源 -->
<div class="card"><h2>数据来源</h2>
<ul class="src" id="srcList"></ul>
<div class="note" id="metaNote"></div>
</div>

<div class="foot">仅供研究参考，不构成任何投资建议<br>A股收盘复盘系统 · Powered by Codex</div>

<script>
__ECHARTS_JS__
</script>
<script>
const DATA = __DATA__;
const red='#e0342c', green='#0aa869', gray='#9aa3af';
const fmtB = v => (v==null?'数据不可获取':Number(v).toFixed(2));
const cls = v => (v>0?'up':v<0?'down':'');

// 指数卡片
(function(){
  const g = document.getElementById('idxGrid');
  const rows = Object.entries(DATA.market_index||{});
  g.innerHTML = rows.map(([n,v])=>`<div class="idx"><div class="n">${n}</div><div class="c">${fmtB(v['收盘价'])}</div><div class="p ${cls(v['涨跌幅%'])}">${fmtB(v['涨跌幅%'])}%</div></div>`).join('');
  // 图表
  const el = document.getElementById('idxChart');
  if (typeof echarts!=='undefined') {
    const chart = echarts.init(el);
    chart.setOption({grid:{left:40,right:20,top:20,bottom:24},
      xAxis:{type:'category',data:rows.map(r=>r[0]),axisLabel:{color:gray}},
      yAxis:{type:'value',axisLabel:{color:gray,formatter:v=>v+'%'},splitLine:{lineStyle:{color:'#22262f'}}},
      series:[{type:'bar',data:rows.map(r=>r[1]['涨跌幅%']),barWidth:'40%',
        itemStyle:{color:p=>p.value>=0?red:green,borderRadius:[4,4,0,0]},
        label:{show:true,position:'top',formatter:p=>p.value+'%',color:gray}}]});
  } else { el.style.display='none'; }
})();

// 情绪
(function(){
  const e = DATA.emotion||{};
  const items=[['涨停',e['涨停数量']],['跌停',e['跌停数量']],['炸板',e['炸板数量']],['最高连板',e['最高连板']]];
  document.getElementById('emoBox').innerHTML = items.map(([l,v])=>`<div class="b"><div class="v">${v==null?'-':v}</div><div class="l">${l}</div></div>`).join('');
  const dist = e['连板分布']||{};
  const keys = Object.keys(dist).map(Number).sort((a,b)=>a-b);
  const el = document.getElementById('emoChart');
  if (typeof echarts!=='undefined' && keys.length) {
    const chart = echarts.init(el);
    chart.setOption({grid:{left:40,right:20,top:20,bottom:24},
      xAxis:{type:'category',data:keys.map(k=>k+'板'),axisLabel:{color:gray}},
      yAxis:{type:'value',axisLabel:{color:gray},splitLine:{lineStyle:{color:'#22262f'}}},
      series:[{type:'bar',data:keys.map(k=>dist[k]),barWidth:'45%',itemStyle:{color:red,borderRadius:[4,4,0,0]},
        label:{show:true,position:'top',color:gray}}]});
  } else { el.style.display='none'; }
})();

// 板块表格
function boardTable(rows){
  if(!rows||!rows.length) return '<tr><td>数据不可获取</td></tr>';
  return `<tr><th>#</th><th>板块</th><th>涨跌幅</th><th>主力净流入(亿)</th><th>龙头</th></tr>`+
    rows.map(r=>`<tr><td>${r['排名']}</td><td>${r['板块']}</td><td class="${cls(r['涨跌幅%'])}">${fmtB(r['涨跌幅%'])}%</td><td class="${cls(r['主力净流入(亿元)'])}">${fmtB(r['主力净流入(亿元)'])}</td><td>${r['龙头股']||'-'}</td></tr>`).join('');
}
(function(){
  const ind = (DATA.sector_rank||[]).filter(r=>r['类型']==='行业');
  const con = (DATA.sector_rank||[]).filter(r=>r['类型']==='概念');
  document.getElementById('indTbl').innerHTML = boardTable(ind);
  document.getElementById('conTbl').innerHTML = boardTable(con);
})();

// 资金
(function(){
  const cf = DATA.capital_flow||{};
  const mf = cf['主力资金']||{}, nb = cf['北向资金']||{};
  let h = `<div class="grid">`;
  h += `<div class="idx"><div class="n">上证主力净流入(亿)</div><div class="p ${cls(mf['上证主力净流入(亿元)'])}">${fmtB(mf['上证主力净流入(亿元)'])}</div></div>`;
  h += `<div class="idx"><div class="n">上证超大单净流入(亿)</div><div class="p ${cls(mf['上证超大单净流入(亿元)'])}">${fmtB(mf['上证超大单净流入(亿元)'])}</div></div>`;
  h += `<div class="idx"><div class="n">北向·港>沪成交(亿)</div><div class="p">${fmtB(nb['港>沪成交额(亿元)'])}</div></div>`;
  h += `<div class="idx"><div class="n">北向·港>深成交(亿)</div><div class="p">${fmtB(nb['港>深成交额(亿元)'])}</div></div>`;
  h += `</div>`;
  if (nb['净买入额']==='数据不可获取') h += `<div class="warn">北向净买入额：数据不可获取（2024年8月起监管停止实时披露，仅披露成交总额）</div>`;
  document.getElementById('capBox').innerHTML = h;
})();

// 龙虎榜
(function(){
  const d = (DATA.capital_flow||{})['龙虎榜']||{};
  const rows = d['明细']||[];
  document.getElementById('lhbTbl').innerHTML = rows.length ? 
    `<tr><th>代码</th><th>名称</th><th>涨跌幅</th><th>净买入(万)</th><th>上榜原因</th></tr>`+
    rows.map(r=>`<tr><td>${r['代码']}</td><td>${r['名称']}</td><td class="${cls(r['涨跌幅%'])}">${fmtB(r['涨跌幅%'])}%</td><td class="${cls(r['龙虎榜净买入(万元)'])}">${fmtB(r['龙虎榜净买入(万元)'])}</td><td>${r['上榜原因']||'-'}</td></tr>`).join('')
    : '<tr><td>数据不可获取</td></tr>';
})();

// 预测
(function(){
  const p = DATA.prediction||{};
  const t = p.targets||[];
  const box = document.getElementById('predBox');
  if (t.length) {
    const heads = t.map((x,i)=>`<div class="idx">
      <div class="n">${i+1}. ${x['name']||x['名称']||''}（${x['code']||x['代码']||''}）· 置信${x['confidence']||'中'}</div>
      <div class="c">${x['参考买入价(收盘)'] ?? '-'}</div>
      <div class="p">${x['板块']||x['行业']||''}</div>
      <div class="note" style="margin-top:6px"><b>逻辑</b>：${x['reason']||x['逻辑']||''}</div>
      <div class="warn" style="margin-top:4px"><b>风险</b>：${x['risk']||'无'}</div>
    </div>`).join('');
    box.innerHTML = `${p.market_view?`<div class="warn" style="margin-bottom:10px">📌 市场判断：${p.market_view}</div>`:''}<div class="grid">${heads}</div><div class="note">${p.strategy||''}｜参考买入价 = 收盘价，次日开盘后卖出</div>`;
  } else {
    box.innerHTML = p.status || '预测引擎开发中（M2/M3 上线）';
  }
})();

// 推荐跟踪
(function(){
  const t = DATA.tracking||{};
  const stats = t.stats||{};
  const settle = t.settle||{};
  const box = document.getElementById('trackBox');
  let html = '';
  if (settle && settle.settled) {
    html = `<div class="warn" style="margin-bottom:8px">昨日推荐已结算：${settle.settled} 只（${settle.sell_date||''} 开盘价）</div>`;
  }
  if (stats && stats.count) {
    html += `<div class="emo">
      <div class="b"><div class="v">${stats.win_rate??'-'}%</div><div class="l">开盘卖出命中率</div></div>
      <div class="b"><div class="v">${stats.avg_ret??'-'}</div><div class="l">开盘平均%</div></div>
      <div class="b"><div class="v">${stats.avg_ret_close??'-'}</div><div class="l">收盘平均%</div></div>
      <div class="b"><div class="v">${stats.count??'-'}</div><div class="l">已结算笔数</div></div></div>`;
    const rows = stats.recent||[];
    if (rows.length) {
      html += `<div class="tbl-wrap" style="margin-top:10px"><table>
        <tr><th>日期</th><th>标的</th><th>买入</th><th>开卖</th><th>开收益</th><th>收价</th><th>收收益</th></tr>` +
        rows.map(r=>`<tr><td>${r.date}</td><td>${r.name}</td><td>${r.buy}</td><td>${r.sell}</td>
          <td class="${cls(r.ret)}">${r.ret>=0?'+':''}${r.ret}%</td><td>${r.sell_close??'-'}</td>
          <td class="${cls(r.ret_close)}">${r.ret_close>=0?'+':''}${r.ret_close}%</td></tr>`).join('') + `</table></div>`;
    }
  } else {
    html += `<div class="note">暂无已结算推荐记录（每天复盘/预测后，次日自动结算）</div>`;
  }
  box.innerHTML = html;
})();

// 来源
(function(){
  document.getElementById('srcList').innerHTML = (DATA.source||[]).map(s=>`<li>${s}</li>`).join('');
  const m = DATA.meta||{};
  document.getElementById('metaNote').innerHTML = (m['说明']||'') + '<br>' + (m.generated_at?('生成时间 '+m.generated_at):'');
})();
</script>
</body>
</html>
"""


def render_html(report: dict) -> str:
    data = json.dumps(report, ensure_ascii=False)
    lhb_date = "数据不可获取"
    cf = report.get("capital_flow") or {}
    lhb = cf.get("龙虎榜") or {}
    if lhb.get("数据日期"):
        lhb_date = lhb["数据日期"]
    out = TEMPLATE
    out = out.replace("{{date}}", html.escape(report.get("date", "")))
    out = out.replace("{{gen_time}}", html.escape(str(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))))
    out = out.replace("{{lhb_date}}", html.escape(str(lhb_date)))
    out = out.replace("__DATA__", data)
    out = out.replace("__ECHARTS_JS__", _ECHARTS_JS)
    return out
