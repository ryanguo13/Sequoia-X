"""Sequoia-X V2 主程序入口。

三种运行模式：
  python main.py               # 日常模式：8进程增量补数据 + 跑策略 + 飞书推送（2~3分钟）
  python main.py --backfill    # 回填模式：baostock 拉全市场历史K线（首次/补数据用，约12分钟）
  python main.py --ci          # CI 模式：用于 GitHub Actions（跳过 .env，假定 secrets 已注入环境变量）
"""

import argparse
import os
import sys

# CI 模式下不加载 .env（GH Actions 会通过 secrets 注入环境变量）
if not os.environ.get("CI"):
    from dotenv import load_dotenv
    load_dotenv()


import socket

socket.setdefaulttimeout(10.0)

from sequoia_x.core.config import get_settings
from sequoia_x.core.logger import get_logger
from sequoia_x.data.engine import DataEngine
from sequoia_x.notify.feishu import FeishuNotifier
from sequoia_x.notify.github_pages import GithubPagesNotifier
from sequoia_x.strategy.base import BaseStrategy
from sequoia_x.strategy.high_tight_flag import HighTightFlagStrategy
from sequoia_x.strategy.limit_up_shakeout import LimitUpShakeoutStrategy
from sequoia_x.strategy.ma_volume import MaVolumeStrategy
from sequoia_x.strategy.private_placement import PrivatePlacementStrategy
from sequoia_x.strategy.rps_breakout import RpsBreakoutStrategy
from sequoia_x.strategy.trend_resonance import TrendResonanceStrategy
from sequoia_x.strategy.turtle_trade import TurtleTradeStrategy
from sequoia_x.strategy.uptrend_limit_down import UptrendLimitDownStrategy


def main() -> None:
    parser = argparse.ArgumentParser(description="Sequoia-X V2 选股系统")
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="回填模式：通过 baostock 拉取全市场历史 K 线（约12分钟）",
    )
    parser.add_argument(
        "--ci",
        action="store_true",
        help="CI 模式：用于 GitHub Actions，跳过 .env 加载，假定环境变量已就绪",
    )
    args = parser.parse_args()

    is_ci = args.ci or bool(os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"))
    if is_ci:
        # GitHub Actions 友好日志
        print("::group::Sequoia-X V2 启动（CI 模式）")

    try:
        # 1. 初始化配置
        settings = get_settings()

        # 2. 初始化日志
        logger = get_logger(__name__)
        logger.info("Sequoia-X V2 启动")

        # 3. 初始化数据引擎
        engine = DataEngine(settings)

        if args.backfill:
            # ── 回填模式：8 进程并行拉历史 K 线，中断后可重跑续传 ──
            logger.info("进入回填模式...")
            all_symbols = engine.get_all_symbols()
            engine.backfill(all_symbols)
            logger.info("Sequoia-X V2 回填模式运行完成")
            return

        # ── 日常模式：单次 API 补今天 + 策略 + 推送 ──
        logger.info("开始拉取最新快照...")
        count = engine.sync_today_bulk()
        logger.info(f"快照同步完成，写入 {count} 只股票")

        # 4. 策略列表（新增策略在此追加即可）
        strategies: list[BaseStrategy] = [
            MaVolumeStrategy(engine=engine, settings=settings),
            TurtleTradeStrategy(engine=engine, settings=settings),
            HighTightFlagStrategy(engine=engine, settings=settings),
            LimitUpShakeoutStrategy(engine=engine, settings=settings),
            UptrendLimitDownStrategy(engine=engine, settings=settings),
            RpsBreakoutStrategy(engine=engine, settings=settings),
            PrivatePlacementStrategy(engine=engine, settings=settings),
            TrendResonanceStrategy(engine=engine, settings=settings),
        ]

        feishu_notifier = FeishuNotifier(settings)
        pages_notifier = GithubPagesNotifier(settings)

        # 飞书推送：仅在配置了 webhook 时启用（CI 模式下 user 可不配）
        feishu_enabled = feishu_notifier.is_configured

        # 5. 遍历策略，有结果则推送至对应机器人；同时收集结果供总结
        all_results: dict[str, list[str]] = {}
        for strategy in strategies:
            strategy_name = type(strategy).__name__
            logger.info(f"执行策略：{strategy_name}")

            selected: list[str] = strategy.run()
            logger.info(f"{strategy_name} 选出 {len(selected)} 只股票")
            all_results[strategy_name] = selected

            if selected:
                # 飞书：按策略路由到对应 webhook（仅当配置了 webhook 时）
                if feishu_enabled:
                    feishu_notifier.send(
                        symbols=selected,
                        strategy_name=strategy_name,
                        webhook_key=strategy.webhook_key,
                    )
                # GitHub Pages：暂存当日结果（send_summary() 统一 commit）
                pages_notifier.send(
                    symbols=selected,
                    strategy_name=strategy_name,
                    webhook_key=strategy.webhook_key,
                )
            else:
                logger.info(f"{strategy_name} 无选股结果，跳过推送")

        # 6. 全部策略跑完，推送超级总结套餐（含多策略共振榜）
        #    飞书：发一张汇总卡片（仅当配置了 webhook 时）
        if feishu_enabled:
            feishu_notifier.send_summary(all_results)
        else:
            logger.info("飞书未配置，跳过飞书总结推送")
        #    GitHub Pages：渲染整站 + commit docs/ 目录
        pages_notifier.send_summary(all_results)
        logger.info("超级总结已推送（飞书 + GitHub Pages）")

    except Exception:
        try:
            _logger = get_logger(__name__)
            _logger.exception("主流程发生未捕获异常，程序终止")
        except Exception:
            import traceback
            traceback.print_exc()
        sys.exit(1)

    logger.info("Sequoia-X V2 运行完成")
    if is_ci:
        print("::endgroup::")


if __name__ == "__main__":
    main()
