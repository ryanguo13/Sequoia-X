"""GitHub Pages 静态站渲染器。

设计原则：
- 纯 Python str.format 渲染，无 Jinja2 依赖
- Polymarket 风：sticky topbar + KPI 大字 + 共振榜突出 + 策略 section + 历史折线
- 所有 HTML 输出都走 html.escape，绝不注入原始 f-string
"""

from __future__ import annotations

import html
import json
from collections import defaultdict
from collections.abc import Iterable

from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)


# ── 中英策略名映射（与 feishu.py 保持一致，可独立维护） ──
_STRATEGY_CN = {
    "MaVolumeStrategy": "均线放量",
    "TurtleTradeStrategy": "海龟突破",
    "HighTightFlagStrategy": "高窄旗形",
    "LimitUpShakeoutStrategy": "涨停洗盘",
    "UptrendLimitDownStrategy": "上升跌停",
    "RpsBreakoutStrategy": "RPS突破",
    "PrivatePlacementStrategy": "定增公告",
    "TrendResonanceStrategy": "趋势共振",
}


def _cn(strategy_name: str) -> str:
    return _STRATEGY_CN.get(strategy_name, strategy_name)


def _escape(s) -> str:
    """递归 escape 任意输入（None 友好）。"""
    if s is None:
        return ""
    return html.escape(str(s))


def _escape_url(url: str) -> str:
    """URL 字段专用 escape + scheme 白名单（防 javascript: XSS）。

    只允许 http:// 和 https://，其他 scheme 一律替换为 #。
    """
    if not url:
        return ""
    url = str(url).strip()
    # scheme 白名单
    if not (url.startswith("http://") or url.startswith("https://")):
        return "#"
    return html.escape(url)


def _to_xueqiu_code(code: str) -> str:
    if code.startswith("6"):
        return f"SH{code}"
    if code.startswith(("4", "8")):
        return f"BJ{code}"
    return f"SZ{code}"


def _load_style_css() -> str:
    """读本地 style.css（打包在仓库内）。"""
    from pathlib import Path

    css_path = Path(__file__).parent / "site" / "style.css"
    return css_path.read_text(encoding="utf-8")


def _load_app_js() -> str:
    from pathlib import Path

    js_path = Path(__file__).parent / "site" / "app.js"
    return js_path.read_text(encoding="utf-8")


# ── 复用资产：股票名映射 ──
def get_stock_names(symbols: Iterable[str]) -> dict[str, str]:
    """通过 baostock 批量查股票名，复用 feishu 模块的逻辑。"""
    import baostock as bs

    mapping: dict[str, str] = {}
    symbols = list(symbols)
    if not symbols:
        return mapping
    bs.login()
    try:
        for code in symbols:
            prefix = "sh" if code.startswith(("6", "9")) else "sz"
            rs = bs.query_stock_basic(code=f"{prefix}.{code}")
            while rs.next():
                row = rs.get_row_data()
                mapping[code] = row[1]
    except Exception as exc:
        logger.warning(f"get_stock_names 失败，使用代码回退：{exc}")
    finally:
        bs.logout()
    return mapping


# ═══════════════════════════════════════════════════════════════
# 主页面渲染
# ═══════════════════════════════════════════════════════════════


def render_index(
    today: str,
    results: dict[str, list[str]],
    history: list[dict],
    stock_names: dict[str, str],
    repo_url: str,
    pages_url: str,
    hide_empty_strategies: bool = False,
) -> str:
    """渲染主页面 (docs/index.html)。

    Args:
        today: 日期字符串 YYYY-MM-DD
        results: {策略类名: [代码, ...]}
        history: [{date, strategies, total, resonance_count}, ...] 倒序（最新在前）
        stock_names: {代码: 股票名}
        repo_url: 仓库地址
        pages_url: GitHub Pages URL
        hide_empty_strategies: 是否隐藏今日 0 命中的策略（节省空间）

    Returns:
        完整 HTML 字符串。
    """
    # ── 计算 KPI ──
    all_symbols: set[str] = set()
    for syms in results.values():
        all_symbols.update(syms)

    # 共振统计
    hit: dict[str, list[str]] = defaultdict(list)
    for name, syms in results.items():
        for code in syms:
            hit[code].append(_cn(name))
    resonance = [(code, strats) for code, strats in hit.items() if len(strats) >= 2]
    resonance.sort(key=lambda x: (-len(x[1]), x[0]))

    total_picks = sum(len(s) for s in results.values())
    strategy_count = len(results)
    non_empty = sum(1 for s in results.values() if s)

    # ── 策略 section HTML（detail 视图）──
    strategy_sections: list[str] = []
    for name, symbols in results.items():
        cn = _cn(name)
        if hide_empty_strategies and not symbols:
            continue
        section = _render_strategy_section(name, cn, symbols, stock_names)
        strategy_sections.append(section)

    # ── 共振榜 HTML ──
    if resonance:
        resonance_items: list[str] = []
        for rank, (code, strats) in enumerate(resonance[:20], 1):
            xq = _to_xueqiu_code(code)
            nm = _escape(stock_names.get(code, xq))
            badges = " ".join(f'<span class="badge">{_escape(s)}</span>' for s in strats)
            # 视觉分级：×3+ 用紫色高亮，×2 普通
            hit_count = len(strats)
            item_class = "resonance-item resonance-strong" if hit_count >= 3 else "resonance-item"
            resonance_items.append(f'''
            <div class="{item_class}" data-code="{_escape(code)}">
              <span class="resonance-rank">{rank}</span>
              <span>
                <div class="resonance-code"><a href="https://xueqiu.com/S/{_escape(xq)}" target="_blank">{_escape(code)}</a> <span class="resonance-name">{nm}</span></div>
              </span>
              <span class="resonance-strats">×{hit_count}</span>
              <span class="resonance-badges">{badges}</span>
            </div>''')
        resonance_html = "\n".join(resonance_items)
    else:
        resonance_html = '<div class="empty-state">今日无多策略共振个股</div>'

    # ── 历史数据 (Chart.js) ──
    # 把 history 倒序反转成时间正序
    chart_history = list(reversed(history[-60:]))
    history_json = json.dumps(chart_history, ensure_ascii=False)

    # 历史折线 section（有数据才渲染）
    if history:
        history_section = (
            '<div class="section-head">'
            '<div class="section-title">📈 近 60 个交易日趋势</div>'
            '<div class="section-meta">每日各策略命中数 + 全市场去重（白虚线）</div>'
            '</div>'
            '<div class="chart-card">'
            f'<canvas id="chart-history" data-history="{_escape(history_json)}" height="240"></canvas>'
            '</div>'
        )
    else:
        history_section = ""

    # ── 各策略列表表格 ──
    summary_rows: list[str] = []
    for name, symbols in results.items():
        cn = _cn(name)
        cnt = len(symbols)
        if hide_empty_strategies and cnt == 0:
            continue
        # 列出最多 5 只票
        sample_links = []
        for code in symbols[:5]:
            xq = _to_xueqiu_code(code)
            nm = _escape(stock_names.get(code, xq))
            sample_links.append(f'<a href="https://xueqiu.com/S/{_escape(xq)}" target="_blank" title="{nm}">{_escape(code)}</a>')
        sample = " · ".join(sample_links) if sample_links else '<span class="flat">—</span>'
        if len(symbols) > 5:
            sample += f' <span class="flat">+{len(symbols)-5}</span>'
        summary_rows.append(f'''
          <tr>
            <td><span class="mono">{_escape(cn)}</span><br><span style="color:var(--text-dim);font-size:11px">{_escape(name)}</span></td>
            <td class="num"><strong>{cnt}</strong></td>
            <td>{sample}</td>
          </tr>''')

    css = _load_style_css()
    js = _load_app_js()

    # ── 主 HTML ──
    html_doc = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sequoia-X · 选股播报 {today}</title>
<meta name="description" content="Sequoia-X A 股量化选股每日播报，多策略共振榜、详细选股列表、历史趋势。">
<style>{css}</style>
</head>
<body>

<header class="topbar">
  <div class="brand">
    <div class="brand-logo">S</div>
    <div>
      <div class="brand-name">Sequoia-X<span class="brand-sub">· A 股量化选股每日播报</span></div>
    </div>
  </div>
  <div class="meta">
    <a class="meta-pill" href="{_escape_url(pages_url)}" target="_blank">🌐 Pages 在线版</a>
    <span class="meta-pill"><span class="dot"></span> 自动更新</span>
    <span class="meta-pill">📅 {today}</span>
  </div>
</header>

<main>

  <!-- ───── KPI ───── -->
  <section class="kpi-grid">
    <div class="kpi kpi-blue">
      <div class="kpi-label">全市场去重</div>
      <div class="kpi-value">{len(all_symbols)}</div>
      <div class="kpi-sub">共 {strategy_count} 个策略</div>
    </div>
    <div class="kpi kpi-green">
      <div class="kpi-label">总选股数</div>
      <div class="kpi-value">{total_picks}</div>
      <div class="kpi-sub">{non_empty}/{strategy_count} 策略有命中</div>
    </div>
    <div class="kpi kpi-purple">
      <div class="kpi-label">多策略共振</div>
      <div class="kpi-value">{len(resonance)}</div>
      <div class="kpi-sub">被 ≥2 个策略同时选中</div>
    </div>
    <div class="kpi kpi-amber">
      <div class="kpi-label">最强信号</div>
      <div class="kpi-value">×{max((len(s) for _, s in resonance), default=0)}</div>
      <div class="kpi-sub">{'—' if not resonance else f'{resonance[0][0]} {_escape(stock_names.get(resonance[0][0], ""))}'}</div>
    </div>
  </section>

  <!-- ───── 共振榜 ───── -->
  <section class="resonance">
    <div class="resonance-head">
      <div class="resonance-title">⭐ 多策略共振榜</div>
      <div class="resonance-tag">STRONG SIGNAL</div>
    </div>
    <div class="resonance-list">
      {resonance_html}
    </div>
  </section>

  <!-- ───── 历史折线 ───── -->
  {history_section}

  <!-- ───── 今日策略摘要表 ───── -->
  <div class="section-head">
    <div class="section-title">📋 今日各策略选股摘要</div>
    <div class="section-meta">按策略分组，点击代码跳转雪球</div>
  </div>
  <table class="strategy-table">
    <thead>
      <tr>
        <th>策略</th>
        <th style="width:100px">命中数</th>
        <th>前 5 只（点击跳转雪球）</th>
      </tr>
    </thead>
    <tbody>
      {''.join(summary_rows)}
    </tbody>
  </table>

  <!-- ───── 各策略详情 ───── -->
  <div class="section-head">
    <div class="section-title">🔍 各策略详细选股</div>
    <div class="section-meta">悬浮代码可点击跳转雪球</div>
  </div>

  {''.join(strategy_sections)}

</main>

<footer>
  <div>
    数据源 · Sequoia-X V2 · baostock 日 K + akshare 实时数据
    · 量化为主，社交情绪仅副驾驶提示
  </div>
  <div>
    <a href="{_escape_url(pages_url)}" target="_blank">🌐 在线浏览</a>
    · <a href="{_escape_url(repo_url)}" target="_blank">📂 源码</a>
    · 本仪表盘不构成投资建议
  </div>
</footer>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script>{js}</script>
</body>
</html>
'''
    return html_doc


def _render_strategy_section(
    strategy_name: str,
    cn_name: str,
    symbols: list[str],
    stock_names: dict[str, str],
) -> str:
    """渲染单个策略的详情 section。"""
    if not symbols:
        return f'''
    <div class="strategy-section">
      <div class="strategy-head">
        <div>
          <span class="strategy-name">{_escape(cn_name)}</span>
          <span class="strategy-key">{_escape(strategy_name)}</span>
        </div>
        <span class="strategy-count empty">0</span>
      </div>
      <div class="empty-state">今日无选股结果</div>
    </div>
    '''
    pills: list[str] = []
    for code in symbols:
        xq = _to_xueqiu_code(code)
        nm = _escape(stock_names.get(code, xq))
        pills.append(
            f'<a class="symbol-pill" data-strategy-for="{_escape(code)}" '
            f'href="https://xueqiu.com/S/{_escape(xq)}" target="_blank">'
            f'<span class="code">{_escape(code)}</span>'
            f'<span class="name">{nm}</span>'
            f'</a>'
        )
    return f'''
    <div class="strategy-section">
      <div class="strategy-head">
        <div>
          <span class="strategy-name">{_escape(cn_name)}</span>
          <span class="strategy-key">{_escape(strategy_name)}</span>
        </div>
        <span class="strategy-count">{len(symbols)}</span>
      </div>
      <div class="symbol-list">
        {''.join(pills)}
      </div>
    </div>
    '''


def render_history_data(
    today: str,
    results: dict[str, list[str]],
    history: list[dict],
    pages_url: str,
) -> str:
    """渲染 history.json，供前端 Chart.js 拉取。

    把结构化历史数据序列化为 JSON 字符串。
    """
    return json.dumps(
        {
            "generated_at": today,
            "today_total": sum(len(s) for s in results.values()),
            "today_unique": len({c for syms in results.values() for c in syms}),
            "history": list(reversed(history[-60:])),
            "pages_url": pages_url,
        },
        ensure_ascii=False,
        indent=2,
    )


def render_readme(today: str, repo_url: str, pages_url: str) -> str:
    """docs/README.md —— GitHub Pages 在仓库根路径展示。"""
    return f"""# Sequoia-X · A 股量化选股每日播报

> 数据源：baostock 日 K（免费、无需注册、无限流）+ akshare 实时数据。
> 自动化跑批：每日收盘后跑 8 个策略选股，结果推送到本页。

**📅 最近一次播报：** {today}

**🌐 在线浏览：** <{pages_url}>

**📂 源码仓库：** <{repo_url}>

## 数据文件

- `index.html` —— 当日播报主页（KPI + 共振榜 + 策略详情）
- `history.json` —— 最近 60 个交易日结构化历史（用于前端 Chart.js 折线）
- `strategy_<key>.json` —— 各策略完整选股明细

## 推送机制

每日收盘后自动：

1. 拉取 baostock 增量 K 线数据
2. 运行 8 个量化策略
3. 同时推送到 **飞书 Webhook**（机器人）和 **GitHub Pages**（本仓库 `docs/` 目录）

GitHub Pages 通过 PyGitHub Contents API 直接 commit 文件到 `{pages_url}，
无需本地 git 操作，CI/CD 友好。

---

*本仪表盘不构成投资建议，所有数据仅供参考。*
"""


# ── 增量更新：合并当日数据进历史 ──
def append_to_history(
    history: list[dict],
    today: str,
    results: dict[str, list[str]],
) -> list[dict]:
    """把当日结果写入历史（去重：同日覆盖）。"""
    # 共振数
    hit_count: dict[str, int] = defaultdict(int)
    for syms in results.values():
        for c in syms:
            hit_count[c] += 1
    resonance_count = sum(1 for c, n in hit_count.items() if n >= 2)
    total_unique = len(hit_count)

    new_entry = {
        "date": today,
        "strategies": {k: list(v) for k, v in results.items()},
        "total": sum(len(v) for v in results.values()),
        "unique": total_unique,
        "resonance_count": resonance_count,
    }

    # 去重：同日覆盖
    out = [h for h in history if h.get("date") != today]
    out.append(new_entry)
    # 按日期排序（最新在后 → push 进 list 时 append）
    out.sort(key=lambda h: h.get("date", ""))
    return out