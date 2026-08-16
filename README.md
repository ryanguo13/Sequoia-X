# Sequoia-X: 王者回归 | The King Returns

> A 股量化选股系统 V2 | A-Share Quantitative Stock Selection System V2

---

## 简介 | Introduction

Sequoia-X V2 是面向 A 股市场的量化选股系统，基于现代 Python 工程化标准从零重构。
系统以 OOP 架构、向量化计算和增量数据更新为核心设计原则，每日收盘后自动选股并
**双通道推送**：📱 飞书 Webhook + 🌐 GitHub Pages 静态站。

数据层使用 [baostock](http://baostock.com)（免费、无需注册、无限流）拉取历史及增量日 K 数据（后复权），
存储于本地 SQLite，彻底规避东方财富反爬问题。

---

## 两种运行模式

```bash
python main.py               # 日常模式：8进程增量补数据 + 跑策略 + 飞书推送（2~3分钟）
python main.py --backfill     # 回填模式：全市场历史K线一次性灌入（约12分钟）
```

---

## 内置策略 | Strategies

| 策略 | 说明 |
|---|---|
| **TurtleTrade** | 海龟突破：20日新高 + 成交额过亿 + 阳线防诱多，按涨幅排序 |
| **MaVolume** | 均线+放量突破 |
| **HighTightFlag** | 高而窄的旗形整理突破 |
| **LimitUpShakeout** | 涨停洗盘回踩确认 |
| **UptrendLimitDown** | 上升趋势中的跌停反包 |
| **RpsBreakout** | 欧奈尔 RPS 相对强度突破 |

---

## 快速开始 | Quick Start

### 环境要求

- Python >= 3.10

### 1. 安装依赖

```bash
# 推荐使用 uv（快速包管理器）
uv sync

# 或者 pip
pip install .
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，至少填写 FEISHU_WEBHOOK_URL
# 可选：填写 GITHUB_TOKEN + GITHUB_REPO 启用 GitHub Pages 推送
```

### 3. 首次回填历史数据

```bash
python main.py --backfill
```

约 12 分钟完成 ~5200 只 A 股历史后复权日 K 数据回填。

### 4. 日常运行

```bash
python main.py
```

建议配合 crontab 每个交易日收盘后自动执行：

```cron
15 19 * * 1-5 cd /root/Sequoia-X && .venv/bin/python main.py >> log.txt 2>&1
```

---

## 推送通道 | Notification Channels

每日收盘后，结果会同时推送到两个通道：

| 通道 | 触发条件 | 内容 |
|---|---|---|
| 📱 **飞书 Webhook** | `FEISHU_WEBHOOK_URL` 已配置 | 8 个机器人（每策略一个）+ 1 张超级总结卡片 |
| 🌐 **GitHub Pages** | `GITHUB_TOKEN` + `GITHUB_REPO` 已配置 | 单页面 SPA：KPI + 共振榜 + 策略详情 + 历史折线 |

两个通道**互相独立**，可单独启用/禁用：
- 飞书失败不影响 Pages，反之亦然
- 整体开关：`GITHUB_PAGES_ENABLED=false` 可单独关闭 Pages 推送

### 🌐 GitHub Pages 推送说明

- 通过 [PyGitHub](https://github.com/PyGithub/PyGithub) Contents API 直接 commit 文件到 `docs/` 目录
- **无需本地 git 操作**，跨平台、无 SSH 依赖
- 首次部署步骤：
  1. GitHub repo → **Settings** → **Pages**
  2. **Build and deployment** → Source: `Deploy from a branch`
  3. Branch: `master` / Folder: `/docs`
  4. 推送一次后访问 `https://<owner>.github.io/<repo>/`

页面包含：
- **KPI 卡片网格**：全市场去重数 / 总选股数 / 共振数 / 最强信号
- **多策略共振榜**：被 ≥2 个策略同时选中的最强信号（紫色高亮）
- **策略详情**：每个策略今日选股列表（点击代码跳转雪球）
- **历史折线图**：近 60 个交易日各策略命中数趋势（Chart.js）

---

## 🤖 GitHub Actions 自动化部署（推荐）

Sequoia-X 可**完全在云端运行**——不消耗本机资源，每日定时跑批：

- 触发：北京时间工作日 15:30（`cron: '30 7 * * 1-5'` UTC）
- 也可手动触发：Actions 页面 → Daily Stock Selection → Run workflow
- 首次部署会自动 backfill（~12 分钟），后续只增量同步（~3 分钟）
- 数据缓存：`actions/cache` 保留 SQLite 数据库，避免每次重新拉历史
- 认证：使用 GitHub Actions 内置 `secrets.GITHUB_TOKEN`，**无需任何 PAT**

### 启用步骤

#### 1. Fork repo（如果你还没有）

访问 https://github.com/ryanguo13/Sequoia-X → Fork 到你自己的账号。

#### 2. 配置 secrets（可选，用于飞书推送）

进入 fork 的 repo → Settings → Secrets and variables → Actions → New repository secret：

| Secret 名称 | 是否必填 | 说明 |
|---|---|---|
| `FEISHU_WEBHOOK_URL` | 可选 | 飞书机器人默认 webhook；不填则只推 GitHub Pages |
| `STRATEGY_WEBHOOK_*` | 可选 | 各策略专属飞书机器人（覆盖默认） |

> ⚠️ **不需要**配置 `GITHUB_TOKEN` secret——GitHub Actions 自动注入 `secrets.GITHUB_TOKEN`，自带 `contents:write` 权限。

#### 3. 启用 Pages

进入 fork 的 repo → Settings → Pages：
- Source: **Deploy from a branch**
- Branch: **master** / Folder: **/docs**

#### 4. 手动触发首次部署

进入 fork 的 repo → Actions → Daily Stock Selection → Run workflow：
- ✅ 勾选 **Force backfill**（首次必须 backfill 初始化数据库）
- 点击 Run workflow

等待约 12-15 分钟完成首次 backfill + Pages 部署。

#### 5. 验证

访问 `https://<your-username>.github.io/Sequoia-X/`（约 1 分钟后 Pages CDN 生效）。

#### 6. 日常自动化

workflow 会按 cron 每天 15:30 自动跑。`actions/cache` 保留历史数据,后续每次 run 只需 ~3 分钟。

### CI 模式本地测试

```bash
# 本地模拟 GH Actions 环境
CI=true GITHUB_ACTIONS=true GITHUB_TOKEN=dummy_token \
FEISHU_WEBHOOK_URL=https://example.com/hook \
GITHUB_REPO=ryanguo13/Sequoia-X \
python main.py --ci

# 强制 backfill（首次部署场景）
python main.py --ci --backfill
```

CI 模式特性：
- 跳过 `load_dotenv()`（假定环境变量已就绪）
- 不写本地 `.github_pages_history.json`（每次 run 是新 container）
- commit message 加 `[actions]` 标记
- 飞书未配置时优雅跳过（不报错）

### CI 配额

GitHub Actions 公共仓库**无限**分钟；私有仓库每月 2000 分钟。
Sequoia-X 单次 run 约 3-5 分钟（首次 12 分钟），月用量 < 200 分钟。

---

## 目录结构 | Project Structure

```
Sequoia-X/
├── main.py                      # 入口：argparse 分发日常/回填模式
├── pyproject.toml               # 依赖声明 + ruff/pytest 配置
├── .env.example                 # 环境变量模板
├── data/                        # SQLite 数据库（运行时生成，不入 git）
├── sequoia_x/
│   ├── core/
│   │   ├── config.py            # Pydantic-settings 配置管理
│   │   └── logger.py            # rich 结构化日志
│   ├── data/
│   │   └── engine.py            # 数据引擎（baostock 回填 + 增量同步 + SQLite）
│   ├── strategy/
│   │   ├── base.py              # 策略抽象基类
│   │   ├── turtle_trade.py      # 海龟交易策略
│   │   ├── ma_volume.py         # 均线放量策略
│   │   ├── high_tight_flag.py   # 高窄旗形策略
│   │   ├── limit_up_shakeout.py # 涨停洗盘策略
│   │   ├── uptrend_limit_down.py # 上升跌停策略
│   │   └── rps_breakout.py      # RPS 突破策略
│   └── notify/
│       ├── feishu.py            # 飞书 Webhook 推送
│       ├── github_pages.py      # GitHub Pages 推送（PyGitHub Contents API）
│       ├── site_renderer.py     # 静态站渲染（Polymarket 风格）
│       └── site/                # 静态资源（CSS/JS）
└── tests/                       # 属性测试（hypothesis）
```

---

## 数据说明

- **数据源**：[baostock](http://baostock.com)（免费、无需注册、无限流）
- **复权方式**：后复权（hfq）— 历史价格不变，适合增量存储，避免除权导致数据错乱
- **存储**：本地 SQLite（`data/sequoia_v2.db`），可直接拷贝到其他机器使用
- **日常增量**：8 进程并行通过 baostock 拉取，2~3 分钟完成全市场更新

---

## 许可证 | License

MIT

---

## 配套子项目

### [`realtime_dashboard/`](realtime_dashboard/) · A 股大盘实时量化监控仪表盘

独立子项目，独立的 venv/SQLite/数据采集调度，与主项目（baostock 日频 + 飞书推送）互不干涉：

- **盘中实时**：新浪指数 tick（3-5 秒刷新）
- **日终全量**：雷富活跃度、涨跌停池、龙虎榜、东财板块涨跌榜、股指期货主力、北向资金、融资融券、同花顺连板/持续放量
- **社交媒体情绪**：股吧综合得分（仅副驾驶提示）+ 百度热搜（仅展示）
- **本地 Web**：单页 SPA，6 tab，Chart.js 折线/柱状图，5 秒轮询
- **免费数据源**：100% akshare，无任何付费/爬虫依赖

详见 [`realtime_dashboard/README.md`](realtime_dashboard/README.md)。
