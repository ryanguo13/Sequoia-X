"""短线趋势共振策略：均线多头排列 + MACD 动量 + 量能配合三维共振。"""

import pandas as pd

from sequoia_x.core.logger import get_logger
from sequoia_x.strategy.base import BaseStrategy

logger = get_logger(__name__)


class TrendResonanceStrategy(BaseStrategy):
    """短线趋势共振策略（参数取中庸值，贴合 A 股）。

    「共振」= 三个不同信号家族同时确认，避免单一指标的假信号：

    选股条件（向量化，严禁 iterrows，全部在最新交易日成立）：
    1. 趋势·均线多头排列：MA5 > MA10 > MA20 且 收盘价 > MA5（价在均线之上）
    2. 趋势·斜率向上：MA20 较 N 日前抬升（中期趋势确向上，过滤短线反弹）
    3. 动量·MACD 多头：DIF > DEA 且 DIF > 0（标准 12/26/9，零轴上方金叉状态）
    4. 量能·温和放量：当日成交量 >= 20 日均量 ×比例（不缩量）
    5. 流动性：当日成交额 > 阈值（过滤垃圾股）
    6. 防追高：收盘价 <= MA20 ×(1 + 乖离上限)（乖离过大不追，贴合大 A 均值回归）

    结果按近 5 日涨幅从高到低排序（短线强度优先）。

    Attributes:
        webhook_key: 路由到 'trend_resonance' 专属飞书机器人（未配置则用默认）。
    """

    webhook_key: str = "trend_resonance"

    # ── 可调参数（中庸值，适合大 A 短线）──
    ma_short: int = 5
    ma_mid: int = 10
    ma_long: int = 20
    slope_lookback: int = 5          # MA20 与几日前比较判断上行
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    vol_ratio: float = 1.0           # 当日量需 >= 20日均量的倍数（温和放量）
    min_turnover: float = 50_000_000  # 成交额下限：5000 万
    max_bias: float = 0.15           # 收盘价相对 MA20 的乖离上限（防追高）
    _MIN_BARS: int = 60              # MACD 需足够预热，至少 60 根 K 线

    def run(self) -> list[str]:
        """
        遍历全市场，返回满足趋势共振条件的股票代码列表（按短线强度排序）。

        Returns:
            满足条件的股票代码列表。
        """
        symbols = self.engine.get_local_symbols()
        candidates: list[str] = []
        ret5_map: dict[str, float] = {}

        for symbol in symbols:
            try:
                df = self.engine.get_ohlcv(symbol)
                if len(df) < self._MIN_BARS:
                    continue

                close = df["close"]

                # 1. 均线
                ma_s = close.rolling(self.ma_short).mean()
                ma_m = close.rolling(self.ma_mid).mean()
                ma_l = close.rolling(self.ma_long).mean()

                # 2. MACD（EMA，adjust=False 与通达信/同花顺口径一致）
                ema_fast = close.ewm(span=self.macd_fast, adjust=False).mean()
                ema_slow = close.ewm(span=self.macd_slow, adjust=False).mean()
                dif = ema_fast - ema_slow
                dea = dif.ewm(span=self.macd_signal, adjust=False).mean()

                # 3. 20 日均量
                vol_ma_l = df["volume"].rolling(self.ma_long).mean()

                last = df.iloc[-1]
                i = -1  # 最新交易日索引

                # 关键值缺失（预热不足）则跳过
                if pd.isna(ma_l.iloc[i]) or pd.isna(vol_ma_l.iloc[i]):
                    continue

                # 条件 1：均线多头排列 + 价在均线上方
                bullish_stack = (
                    ma_s.iloc[i] > ma_m.iloc[i] > ma_l.iloc[i]
                    and last["close"] > ma_s.iloc[i]
                )
                # 条件 2：MA20 上行
                ma_long_rising = ma_l.iloc[i] > ma_l.iloc[i - self.slope_lookback]
                # 条件 3：MACD 零轴上方多头
                macd_bull = dif.iloc[i] > dea.iloc[i] and dif.iloc[i] > 0
                # 条件 4：温和放量
                volume_ok = last["volume"] >= vol_ma_l.iloc[i] * self.vol_ratio
                # 条件 5：流动性
                liquid = last["turnover"] > self.min_turnover
                # 条件 6：防追高（乖离不过大）
                not_overheated = last["close"] <= ma_l.iloc[i] * (1 + self.max_bias)

                if (
                    bullish_stack
                    and ma_long_rising
                    and macd_bull
                    and volume_ok
                    and liquid
                    and not_overheated
                ):
                    candidates.append(symbol)
                    prev5 = close.iloc[i - self.ma_short]
                    ret5_map[symbol] = (
                        last["close"] / prev5 - 1 if prev5 else 0.0
                    )

            except Exception as exc:
                logger.warning(f"[{symbol}] TrendResonanceStrategy 计算失败：{exc}")
                continue

        # 按近 5 日涨幅从高到低排序（短线强度优先）
        candidates.sort(key=lambda s: ret5_map.get(s, 0.0), reverse=True)

        logger.info(f"TrendResonanceStrategy 选出 {len(candidates)} 只股票")
        return candidates
