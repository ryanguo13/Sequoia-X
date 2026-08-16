# Sequoia-X · A 股量化选股每日播报

> 🌐 **在线浏览：** https://ryanguo13.github.io/Sequoia-X/

本目录由 GitHub Actions 自动生成并推送 — 每日 15:30（北京时间，工作日）后约 5 分钟内更新。

## 文件说明

- `index.html` — 当日播报主页（KPI + 共振榜 + 策略详情 + 历史折线）
- `README.md` — 本文件
- `history.json` — 最近 60 个交易日结构化历史（Chart.js 折线图数据源）

## 自动化机制

详见仓库根目录的 `README.md` → "GitHub Actions 自动化部署" 章节。

触发方式：
- **定时**：北京时间工作日 15:30（`cron: '30 7 * * 1-5'` UTC）
- **手动**：GitHub Actions 页面 → Daily Stock Selection → Run workflow

---

*本仪表盘不构成投资建议，所有数据仅供参考。*
