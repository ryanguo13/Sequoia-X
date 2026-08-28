# Sequoia-X · A 股量化选股每日播报

> 数据源：baostock 日 K（免费、无需注册、无限流）+ akshare 实时数据。
> 自动化跑批：每日收盘后跑 8 个策略选股，结果推送到本页。

**📅 最近一次播报：** 2026-08-28

**🌐 在线浏览：** <https://ryanguo13.github.io/Sequoia-X>

**📂 源码仓库：** <https://github.com/ryanguo13/Sequoia-X>

## 数据文件

- `index.html` —— 当日播报主页（KPI + 共振榜 + 策略详情）
- `history.json` —— 最近 60 个交易日结构化历史（用于前端 Chart.js 折线）
- `strategy_<key>.json` —— 各策略完整选股明细

## 推送机制

每日收盘后自动：

1. 拉取 baostock 增量 K 线数据
2. 运行 8 个量化策略
3. 同时推送到 **飞书 Webhook**（机器人）和 **GitHub Pages**（本仓库 `docs/` 目录）

GitHub Pages 通过 PyGitHub Contents API 直接 commit 文件到 `https://ryanguo13.github.io/Sequoia-X，
无需本地 git 操作，CI/CD 友好。

---

*本仪表盘不构成投资建议，所有数据仅供参考。*
