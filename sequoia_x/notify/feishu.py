"""飞书通知模块：将选股结果通过 Webhook 推送至飞书群。"""

import json
from datetime import date

import requests

from sequoia_x.core.config import Settings
from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)


# 策略类名 -> 中文简称（用于超级总结卡片，未登记的回退到类名）
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


class FeishuNotifier:
    """飞书 Webhook 推送器。

    根据策略的 webhook_key 路由到对应的飞书机器人。
    若 webhook_key 未在 Settings.strategy_webhooks 中配置，
    则 fallback 到 Settings.feishu_webhook_url。
    """

    def __init__(self, settings: Settings) -> None:
        """
        初始化 FeishuNotifier。

        Args:
            settings: Settings 实例，提供 Webhook URL 配置。
        """
        self.settings = settings

    @staticmethod
    def _to_xueqiu_code(code: str) -> str:
        """将纯数字代码转为雪球格式：6开头→SH，4/8开头→BJ，其余→SZ。"""
        if code.startswith("6"):
            return f"SH{code}"
        elif code.startswith(("4", "8")):
            return f"BJ{code}"
        return f"SZ{code}"

    @staticmethod
    def _get_stock_names(symbols: list[str]) -> dict[str, str]:
        """通过 baostock 批量查询股票名称，返回 {code: name} 映射。"""
        import baostock as bs
        bs.login()
        mapping = {}
        for code in symbols:
            prefix = "sh" if code.startswith(("6", "9")) else "sz"
            rs = bs.query_stock_basic(code=f"{prefix}.{code}")
            while rs.next():
                row = rs.get_row_data()
                mapping[code] = row[1]  # 第2个字段是股票名称
        bs.logout()
        return mapping

    def _build_card(self, symbols: list[str], strategy_name: str) -> dict:
        today = date.today().strftime("%Y-%m-%d")
        names = self._get_stock_names(symbols)

        links: list[str] = []
        for code in symbols:
            xq_code = self._to_xueqiu_code(code)
            name = names.get(code, xq_code)
            links.append(f"[{name}](https://xueqiu.com/S/{xq_code})")

        symbol_text = " ".join(links) if links else "（无选股结果）"

        return {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": f"📈 Sequoia-X 选股播报 | {strategy_name}",
                    },
                    "template": "blue",
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": f"**日期：** {today}\n**策略：** {strategy_name}\n**选股数量：** {len(symbols)}",
                        },
                    },
                    {"tag": "hr"},
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": f"**选股列表：**\n{symbol_text}",
                        },
                    },
                ],
            },
        }

    def send(
        self,
        symbols: list[str],
        strategy_name: str,
        webhook_key: str = "default",
    ) -> None:
        """
        将选股结果格式化为飞书卡片消息并 POST 至对应 Webhook。

        根据 webhook_key 从 Settings 中查找专属 URL；
        若未配置，则 fallback 到 feishu_webhook_url。

        Args:
            symbols: 选股结果代码列表。
            strategy_name: 策略名称，用于卡片标题。
            webhook_key: 策略标识，用于路由到对应飞书机器人。

        Raises:
            不抛出异常，HTTP 失败时记录 ERROR 日志。
        """
        url = self.settings.get_webhook_url(webhook_key)
        payload = self._build_card(symbols, strategy_name)

        try:
            resp = requests.post(
                url,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            # 解析飞书真正的返回体
            resp_json = resp.json()

            # 飞书真正的成功标志是内部的 code == 0
            if resp.status_code != 200 or resp_json.get("code") != 0:
                logger.error(
                    f"飞书推送失败 [{webhook_key}] "
                    f"HTTP状态={resp.status_code} 飞书响应={resp.text}"
                )
            else:
                logger.info(f"飞书推送成功 [{webhook_key}]，共 {len(symbols)} 只股票")

        except requests.RequestException as exc:
            logger.error(f"飞书推送请求异常 [{webhook_key}]：{exc}")

    # ── 超级总结套餐 ──

    def _build_summary_card(self, results: dict[str, list[str]]) -> dict:
        """汇总全部策略结果为一张总结卡片。

        三段式：各策略数量一览 + 全市场去重总数 + 多策略共振榜。
        共振榜 = 被 ≥2 个策略同时选中的个股，按命中策略数降序，最强信号置顶。
        """
        from collections import defaultdict

        today = date.today().strftime("%Y-%m-%d")

        # 1. 各策略数量（保持传入顺序）
        count_parts = [
            f"{_STRATEGY_CN.get(name, name)} **{len(syms)}**"
            for name, syms in results.items()
        ]
        counts_text = " ｜ ".join(count_parts) if count_parts else "（无策略）"

        # 2. 全市场去重
        union: set[str] = set()
        for syms in results.values():
            union.update(syms)

        # 3. 共振统计：symbol -> [命中的策略中文名, ...]
        hit: dict[str, list[str]] = defaultdict(list)
        for name, syms in results.items():
            cn = _STRATEGY_CN.get(name, name)
            for code in syms:
                hit[code].append(cn)

        resonance = [(code, strats) for code, strats in hit.items() if len(strats) >= 2]
        # 命中策略数多的优先，其次代码稳定排序
        resonance.sort(key=lambda x: (-len(x[1]), x[0]))
        top = resonance[:20]  # 只对共振榜查名字，控制 baostock 调用量

        if top:
            names = self._get_stock_names([code for code, _ in top])
            lines = []
            for rank, (code, strats) in enumerate(top, 1):
                xq = self._to_xueqiu_code(code)
                nm = names.get(code, xq)
                lines.append(
                    f"{rank}. [{nm}](https://xueqiu.com/S/{xq}) "
                    f"`×{len(strats)}` {' / '.join(strats)}"
                )
            resonance_text = "\n".join(lines)
        else:
            resonance_text = "今日无多策略共振个股"

        return {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": "📊 Sequoia-X 收盘总结 · 超级套餐",
                    },
                    "template": "purple",
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": (
                                f"**日期：** {today}\n"
                                f"**全市场去重：** 共 **{len(union)}** 只入选"
                            ),
                        },
                    },
                    {"tag": "hr"},
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": f"**📋 各策略选股数量**\n{counts_text}",
                        },
                    },
                    {"tag": "hr"},
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": (
                                "**⭐ 多策略共振榜**（被 ≥2 个策略同时选中，命中越多信号越强）\n"
                                f"{resonance_text}"
                            ),
                        },
                    },
                ],
            },
        }

    def send_summary(
        self,
        results: dict[str, list[str]],
        webhook_key: str = "summary",
    ) -> None:
        """全部策略跑完后，推送一张超级总结卡片。

        Args:
            results: {策略类名: 选中代码列表}，含空结果策略。
            webhook_key: 总结专属机器人标识，未配置则 fallback 到默认。
        """
        url = self.settings.get_webhook_url(webhook_key)
        payload = self._build_summary_card(results)

        try:
            resp = requests.post(
                url,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            resp_json = resp.json()
            if resp.status_code != 200 or resp_json.get("code") != 0:
                logger.error(
                    f"飞书总结推送失败 HTTP状态={resp.status_code} 飞书响应={resp.text}"
                )
            else:
                logger.info("飞书总结推送成功")
        except requests.RequestException as exc:
            logger.error(f"飞书总结推送请求异常：{exc}")
