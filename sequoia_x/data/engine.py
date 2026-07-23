"""数据引擎模块：负责 SQLite 行情数据存储与 baostock 增量同步。"""

import sqlite3
from pathlib import Path

import pandas as pd

from sequoia_x.core.config import Settings
from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)


_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS stock_daily (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol   TEXT    NOT NULL,
    date     TEXT    NOT NULL,
    open     REAL,
    high     REAL,
    low      REAL,
    close    REAL,
    volume   REAL,
    turnover REAL,
    UNIQUE (symbol, date)
);
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_symbol_date ON stock_daily (symbol, date);
"""


def _bs_fetch_batch(tasks: list) -> list:
    """多进程 worker：独立 login，批量拉取 baostock 数据。"""
    import baostock as bs
    bs.login()
    results = []
    for symbol, bs_code, start, end in tasks:
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,open,high,low,close,volume,amount",
            start_date=start,
            end_date=end,
            frequency="d",
            adjustflag="1",  # 后复权
        )
        if rs.error_code != "0":
            continue
        while rs.next():
            results.append([symbol] + rs.get_row_data())
    bs.logout()
    return results


def _bs_backfill_batch(tasks: list) -> list:
    """回填专用多进程 worker：独立 login，带单只重试，批量拉取历史 K 线。

    每个 chunk 一次 login/logout，天然规避长连接超时；单只查询失败重试 3 次
    （每次重试前重连），仍失败则跳过该只，不影响本批其余股票。

    Args:
        tasks: [(symbol, bs_code, start, end), ...]

    Returns:
        [[symbol, date, open, high, low, close, volume, amount], ...]
    """
    import time

    import baostock as bs

    max_retries = 3

    def _login() -> None:
        bs.login()

    _login()
    results: list = []
    try:
        for symbol, bs_code, start, end in tasks:
            symbol_rows: list = []
            for attempt in range(max_retries):
                try:
                    rs = bs.query_history_k_data_plus(
                        bs_code,
                        "date,open,high,low,close,volume,amount",
                        start_date=start,
                        end_date=end,
                        frequency="d",
                        adjustflag="1",  # 后复权
                    )
                    if rs.error_code != "0":
                        raise RuntimeError(rs.error_msg)
                    symbol_rows = []
                    while rs.next():
                        symbol_rows.append([symbol] + rs.get_row_data())
                    # 完整取回后才并入结果，避免重试产生半截重复
                    results.extend(symbol_rows)
                    break
                except Exception:
                    if attempt < max_retries - 1:
                        time.sleep(2 ** (attempt + 1))
                        bs.logout()
                        time.sleep(1)
                        _login()
                    # 末次仍失败：跳过该只
    finally:
        bs.logout()
    return results


class DataEngine:
    """行情数据引擎，负责 SQLite 存储和 baostock 数据同步。"""

    def __init__(self, settings: Settings) -> None:
        self.db_path: str = settings.db_path
        self.start_date: str = settings.start_date
        self._init_db()

    def _init_db(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(_CREATE_TABLE_SQL)
            conn.execute(_CREATE_INDEX_SQL)
            conn.commit()
        logger.info(f"数据库初始化完成：{self.db_path}")

    def _get_last_date(self, symbol: str) -> str | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT MAX(date) FROM stock_daily WHERE symbol = ?",
                (symbol,),
            ).fetchone()
        return row[0] if row and row[0] else None

    def get_ohlcv(self, symbol: str) -> pd.DataFrame:
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql(
                "SELECT * FROM stock_daily WHERE symbol = ? ORDER BY date",
                conn,
                params=(symbol,),
            )
        return df

    @staticmethod
    def _to_baostock_code(symbol: str) -> str:
        """将纯数字代码转为 baostock 格式：6/9开头 -> sh，其余 -> sz。"""
        prefix = "sh" if symbol.startswith(("6", "9")) else "sz"
        return f"{prefix}.{symbol}"

    # ── 数据同步 ──

    def sync_today_bulk(self) -> int:
        """多进程并行通过 baostock 拉取增量数据（后复权），写入 SQLite。"""
        from datetime import date, timedelta
        from multiprocessing import Pool

        today_str = date.today().strftime("%Y-%m-%d")

        tasks = []
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT symbol, MAX(date) FROM stock_daily GROUP BY symbol"
            ).fetchall()

        if not rows:
            logger.warning("本地无股票数据，请先执行 --backfill")
            return 0

        for symbol, last_date in rows:
            if last_date and last_date >= today_str:
                continue
            start = today_str
            if last_date:
                start = (date.fromisoformat(last_date) + timedelta(days=1)).strftime("%Y-%m-%d")
            tasks.append((symbol, self._to_baostock_code(symbol), start, today_str))

        if not tasks:
            logger.info("所有股票已是最新，无需更新")
            return 0

        logger.info(f"需要更新 {len(tasks)} 只股票，启动多进程并行拉取...")

        n_workers = min(8, len(tasks))
        chunks = [tasks[i::n_workers] for i in range(n_workers)]

        with Pool(n_workers) as pool:
            batch_results = pool.map(_bs_fetch_batch, chunks)

        all_rows = []
        for batch in batch_results:
            all_rows.extend(batch)

        if not all_rows:
            logger.info("无新数据（可能非交易日）")
            return 0

        df = pd.DataFrame(all_rows, columns=["symbol", "date", "open", "high", "low", "close", "volume", "turnover"])
        for col in ["open", "high", "low", "close", "volume", "turnover"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["close"])
        df = df[df["volume"] > 0]

        count = len(df)
        with sqlite3.connect(self.db_path) as conn:
            for d in df["date"].unique().tolist():
                conn.execute("DELETE FROM stock_daily WHERE date = ?", (d,))
            df.to_sql("stock_daily", conn, if_exists="append", index=False, method="multi", chunksize=500)
            conn.commit()

        logger.info(f"sync_today_bulk: 写入 {count} 条数据")
        return count

    def backfill(self, symbols: list[str]) -> None:
        """多进程并行回填历史日 K 线数据（后复权）。

        容错机制：
        - 8 进程并行拉取，每进程独立 baostock 会话（chunk 边界自动重连）
        - 单只股票失败自动重试 3 次，间隔递增（2s/4s/8s）
        - 已入库的自动 skip（按 symbol 增量续传），中断后可重跑
        - 主进程串行写库（INSERT OR IGNORE），无跨进程写锁竞争
        """
        from datetime import date, timedelta
        from multiprocessing import Pool

        today_str = date.today().strftime("%Y-%m-%d")

        # 主进程构建任务：跳过已最新，按 symbol 已有数据计算增量起始日
        tasks: list[tuple[str, str, str, str]] = []
        for symbol in symbols:
            last_date = self._get_last_date(symbol)
            if last_date and last_date >= today_str:
                continue
            if last_date:
                start = (date.fromisoformat(last_date) + timedelta(days=1)).strftime("%Y-%m-%d")
            else:
                start = self.start_date
            tasks.append((symbol, self._to_baostock_code(symbol), start, today_str))

        total = len(symbols)
        already = total - len(tasks)
        if not tasks:
            logger.info(f"所有股票已是最新（{total} 只），无需回填")
            return

        # 每个子任务 40 只股票：控制进度粒度与内存，chunk 边界即 baostock 重连点
        chunk_size = 40
        chunks = [tasks[i:i + chunk_size] for i in range(0, len(tasks), chunk_size)]
        n_workers = min(8, len(chunks))
        logger.info(
            f"共 {total} 只，需回填 {len(tasks)} 只（已最新跳过 {already} 只），"
            f"{n_workers} 进程并行、拆为 {len(chunks)} 批..."
        )

        done = 0
        written = 0
        with Pool(n_workers) as pool:
            for rows in pool.imap_unordered(_bs_backfill_batch, chunks):
                done += 1
                if rows:
                    written += self._write_rows(rows)
                if done % 10 == 0 or done == len(chunks):
                    logger.info(f"进度 {done}/{len(chunks)} 批，已写入 {written} 条 K 线")

        logger.info(f"回填完成 — 写入 {written} 条 K 线，覆盖 {len(tasks)} 只目标股票")

    def _write_rows(self, rows: list) -> int:
        """清洗并批量写入 K 线数据，返回实际新增行数。

        使用 INSERT OR IGNORE 幂等写入，重复的 (symbol, date) 自动忽略，
        因此中断重跑或增量重叠都不会抛 IntegrityError。
        """
        df = pd.DataFrame(
            rows,
            columns=["symbol", "date", "open", "high", "low", "close", "volume", "turnover"],
        )
        for col in ["open", "high", "low", "close", "volume", "turnover"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["close"])
        df = df[df["volume"] > 0]
        if df.empty:
            return 0

        # object dtype -> tolist() 得到原生 Python 类型，避免 numpy 类型绑定问题
        data = df[
            ["symbol", "date", "open", "high", "low", "close", "volume", "turnover"]
        ].astype(object).values.tolist()

        with sqlite3.connect(self.db_path) as conn:
            before = conn.total_changes
            conn.executemany(
                "INSERT OR IGNORE INTO stock_daily "
                "(symbol, date, open, high, low, close, volume, turnover) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                data,
            )
            conn.commit()
            return conn.total_changes - before

    # ── 股票列表 ──

    def get_all_symbols(self) -> list[str]:
        """通过 baostock 获取全市场 A 股代码列表。"""
        import baostock as bs

        lg = bs.login()
        if lg.error_code != "0":
            logger.error(f"baostock 登录失败: {lg.error_msg}")
            return []

        try:
            rs = bs.query_stock_basic(code_name="", code="")
            symbols = []
            while rs.next():
                row = rs.get_row_data()
                code = row[0]           # "sh.600000" or "sz.000001"
                status = row[4]         # "1" = 上市
                stock_type = row[5]     # "1" = 股票
                if status == "1" and stock_type == "1":
                    symbols.append(code.split(".")[1])  # 提取纯数字代码
            logger.info(f"获取股票列表完成，共 {len(symbols)} 只")
            return symbols
        except Exception as e:
            logger.error(f"获取股票列表失败: {e}")
            return []
        finally:
            bs.logout()

    def get_local_symbols(self) -> list[str]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM stock_daily"
            ).fetchall()
        return [row[0] for row in rows]
