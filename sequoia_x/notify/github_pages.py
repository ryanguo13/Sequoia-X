"""GitHub Pages 通知器：将选股结果渲染为静态站并通过 PyGitHub 推送到 GitHub Pages。

架构：
    main.py ──> GithubPagesNotifier ──┬─> render_index()   → docs/index.html
                                       ├─> render_readme()  → docs/README.md
                                       └─> render_history()  → docs/history.json

    通过 GitHub Contents API 直接 commit 文件到 docs/ 目录，
    无需本地 git 操作，跨平台、无 SSH 依赖、可 mock 测试。

依赖：
    PyGithub (>=2.1)
    GitHub PAT (Classic 或 Fine-grained，需 `contents:write` 权限)
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from sequoia_x.core.config import Settings
from sequoia_x.core.logger import get_logger
from sequoia_x.notify.site_renderer import (
    get_stock_names,
    render_history_data,
    render_index,
    render_readme,
)

logger = get_logger(__name__)


class GithubPagesNotifier:
    """GitHub Pages 推送器：将每日选股结果渲染为静态站并 push 到仓库 docs/ 目录。

    Attributes:
        settings: 全局 Settings，提供 github_token/github_repo/github_pages_dir 等。
        history_cache_path: 本地历史缓存文件（用于跨日合并）。
        stock_names_cache_path: 股票名映射缓存。
    """

    history_cache_path = "data/.github_pages_history.json"
    stock_names_cache_path = "data/.github_pages_names.json"

    def __init__(self, settings: Settings) -> None:
        """初始化 GithubPagesNotifier。

        Args:
            settings: 全局 Settings 实例。
        """
        self.settings = settings
        self._gh: Any | None = None  # lazy-init 的 Github client
        self._repo: Any | None = None
        # CI 模式（GH Actions）：不写本地 cache（每次 run 都是新 container）
        self._is_ci: bool = bool(
            os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS")
        )

    # ── 启用判断 ──

    @property
    def is_configured(self) -> bool:
        """是否已正确配置（用于 main.py 的条件判断）。"""
        return bool(
            self.settings.github_pages_enabled
            and self.settings.github_token
            and self.settings.github_repo
        )

    def _ensure_client(self) -> tuple[Any, Any]:
        """lazy 创建 PyGithub client + repo handle。"""
        if self._gh is None:
            try:
                from github import Github
            except ImportError as exc:
                raise RuntimeError(
                    "PyGithub 未安装，请运行 `uv pip install PyGithub`"
                ) from exc
            self._gh = Github(self.settings.github_token, per_page=5)
        if self._repo is None:
            self._repo = self._gh.get_repo(self.settings.github_repo)
            logger.info(f"GitHub repo 已连接: {self.settings.github_repo}")
        return self._gh, self._repo

    # ── 历史持久化（本地缓存 + 远端合并） ──

    def _load_history_local(self) -> list[dict]:
        """读本地历史缓存文件。"""
        if self._is_ci:
            # CI 模式：每次 run 是新 container，本地 cache 无意义
            return []
        p = Path(self.history_cache_path)
        if not p.exists():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception as exc:
            logger.warning(f"本地历史缓存损坏，将从空开始：{exc}")
            return []

    def _save_history_local(self, history: list[dict]) -> None:
        """写本地历史缓存。"""
        if self._is_ci:
            # CI 模式：cache 写到 actions/cache 管理的路径，下次 run 会恢复
            return
        p = Path(self.history_cache_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(history, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _fetch_remote_history(self, repo: Any) -> list[dict]:
        """从 GitHub 拉取远端 history.json（如果存在）。"""
        path = f"{self.settings.github_pages_dir}/history.json"
        try:
            content = repo.get_contents(path, ref=self.settings.github_pages_branch)
            if isinstance(content, list):
                content = content[0]
            data = json.loads(content.decoded_content.decode("utf-8"))
            # history.json 包含 {generated_at, today_total, today_unique, history: [...]}
            if isinstance(data, dict) and "history" in data:
                return data["history"]
            return data if isinstance(data, list) else []
        except Exception as exc:
            logger.info(f"远端 history.json 不存在或读取失败（首次推送？）：{exc}")
            return []

    # ── 核心：单策略推送 ──

    def send(
        self,
        symbols: list[str],
        strategy_name: str,
        webhook_key: str = "default",
    ) -> None:
        """单个策略推送入口。

        GitHub Pages 与飞书不同：单策略推送暂不触发整站重建（避免 8 次 commit 抖动）。
        真正写盘在 send_summary() 里完成。这里只把数据暂存。

        Args:
            symbols: 选股代码列表。
            strategy_name: 策略类名。
            webhook_key: 策略路由标识（GitHub Pages 不区分，仅记录日志）。
        """
        if not self.is_configured:
            logger.debug(f"[GitHub Pages] 未配置或已禁用，跳过 [{webhook_key}] {strategy_name}")
            return
        logger.info(
            f"[GitHub Pages] 暂存 {strategy_name} ({webhook_key}): {len(symbols)} 只"
        )
        # 数据汇总由 send_summary() 完成

    # ── 核心：总结推送（真正的 commit 入口） ──

    def send_summary(
        self,
        results: dict[str, list[str]],
        webhook_key: str = "summary",
    ) -> None:
        """全部策略跑完后，渲染整站并 commit 到 GitHub Pages。

        流程：
            1. 校验配置 → 未配置则跳过
            2. 查股票名（带缓存）
            3. 合并历史（本地 + 远端 + 当日）
            4. 渲染 index.html / README.md / history.json
            5. 通过 Contents API 一次性 commit 3 个文件

        Args:
            results: {策略类名: 代码列表}。
            webhook_key: 日志标识，GitHub Pages 不区分。
        """
        if not self.is_configured:
            logger.info("[GitHub Pages] 未配置或已禁用，跳过站点更新")
            return

        today = date.today().strftime("%Y-%m-%d")
        logger.info(f"[GitHub Pages] 开始推送 {today} 总结...")

        try:
            # 1. 拿股票名
            all_codes = list({c for syms in results.values() for c in syms})
            stock_names = self._get_stock_names_cached(all_codes)

            # 2. 合并历史
            gh, repo = self._ensure_client()
            local_history = self._load_history_local()
            remote_history = self._fetch_remote_history(repo)
            # 合并策略：本地 + 远端去重，再 append 当日
            history_by_date: dict[str, dict] = {}
            for h in local_history + remote_history:
                d = h.get("date")
                if d:
                    history_by_date[d] = h
            history = sorted(
                [
                    *history_by_date.values(),
                    # 当日条目
                    {
                        "date": today,
                        "strategies": {k: list(v) for k, v in results.items()},
                        "total": sum(len(v) for v in results.values()),
                        "unique": len(all_codes),
                        "resonance_count": self._count_resonance(results),
                    },
                ],
                key=lambda h: h.get("date", ""),
            )

            # 3. 渲染
            repo_url = f"https://github.com/{self.settings.github_repo}"
            # Pages URL：取 owner 名 → ryanguo13.github.io/<repo>
            owner = self.settings.github_repo.split("/")[0]
            pages_url = f"https://{owner}.github.io/{self.settings.github_repo.split('/')[-1]}"

            index_html = render_index(
                today=today,
                results=results,
                history=history,
                stock_names=stock_names,
                repo_url=repo_url,
                pages_url=pages_url,
                hide_empty_strategies=True,  # 节省空间
            )
            readme_md = render_readme(today, repo_url, pages_url)
            history_json = render_history_data(today, results, history, pages_url)

            # 4. Commit 3 个文件
            files = {
                f"{self.settings.github_pages_dir}/index.html": index_html,
                f"{self.settings.github_pages_dir}/README.md": readme_md,
                f"{self.settings.github_pages_dir}/history.json": history_json,
            }

            commit_message = (
                f"docs(pages): auto update {today} "
                f"({sum(len(v) for v in results.values())} picks)"
                + (" [actions]" if self._is_ci else "")
            )
            self._commit_files(repo, files, commit_message)

            # 5. 本地历史缓存持久化
            self._save_history_local(history)

            total_picks = sum(len(v) for v in results.values())
            logger.info(
                "[GitHub Pages] ✅ 推送成功 → "
                f"{pages_url}（共 {total_picks} 只，历史 {len(history)} 天）"
            )

        except Exception as exc:
            logger.error(f"[GitHub Pages] ❌ 推送失败：{exc}")
            import traceback
            logger.debug(traceback.format_exc())

    # ── 内部工具 ──

    @staticmethod
    def _count_resonance(results: dict[str, list[str]]) -> int:
        """计算共振数（被 ≥2 策略同时选中）。"""
        hit: dict[str, int] = defaultdict(int)
        for syms in results.values():
            for c in syms:
                hit[c] += 1
        return sum(1 for n in hit.values() if n >= 2)

    def _get_stock_names_cached(self, codes: list[str]) -> dict[str, str]:
        """带本地缓存的股票名查询。"""
        cache_path = Path(self.stock_names_cache_path)
        cache: dict[str, str] = {}
        if cache_path.exists():
            try:
                cache = json.loads(cache_path.read_text(encoding="utf-8"))
            except Exception:
                cache = {}

        missing = [c for c in codes if c not in cache]
        if missing:
            logger.info(f"[GitHub Pages] 查询 {len(missing)} 只股票名（baostock）...")
            fresh = get_stock_names(missing)
            cache.update(fresh)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(cache, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        return {c: cache.get(c, c) for c in codes}

    def _commit_files(
        self,
        repo: Any,
        files: dict[str, str],
        commit_message: str,
    ) -> None:
        """通过 GitHub Contents API 一次性 commit 多个文件。

        对每个文件：
            - 存在则 update（用 sha）
            - 不存在则 create
        """
        branch = self.settings.github_pages_branch
        base_path = self.settings.github_pages_dir

        # 1. 拿到 base 目录的 tree sha（用于嵌套创建）
        try:
            base_ref = repo.get_git_ref(f"heads/{branch}")
            base_sha = base_ref.object.sha
            base_tree = repo.get_git_tree(base_sha, recursive=True)
            existing_paths = {e.path for e in base_tree.tree}
        except Exception:
            existing_paths = set()

        # 2. 逐文件处理：拿 sha（如果要 update）
        file_payloads: list[dict] = []
        for path, content in files.items():
            try:
                existing = repo.get_contents(path, ref=branch)
                sha = existing.sha if not isinstance(existing, list) else existing[0].sha
                file_payloads.append({
                    "path": path,
                    "message": commit_message,
                    "content": content,
                    "sha": sha,
                })
            except Exception:
                # 不存在 → create
                file_payloads.append({
                    "path": path,
                    "message": commit_message,
                    "content": content,
                })

        # 3. 批量 commit（PyGithub 一次只能 commit 一个文件，所以这里循环）
        # 注意：这里不是用 create_file/update_file 的 bulk 模式，是因为 PyGithub 没有原生 batch
        for fp in file_payloads:
            try:
                if "sha" in fp:
                    repo.update_file(
                        path=fp["path"],
                        message=fp["message"],
                        content=fp["content"],
                        sha=fp["sha"],
                        branch=branch,
                    )
                    logger.debug(f"  ↻ update {fp['path']}")
                else:
                    repo.create_file(
                        path=fp["path"],
                        message=fp["message"],
                        content=fp["content"],
                        branch=branch,
                    )
                    logger.debug(f"  + create {fp['path']}")
            except Exception as exc:
                logger.error(f"  ✗ commit {fp['path']} 失败：{exc}")
                raise

        # 4. 如果 base_path 不存在（首次部署），确保 Pages 能 serve
        if base_path not in existing_paths:
            logger.warning(
                f"⚠️  {base_path}/ 目录首次创建。请到 GitHub repo Settings → Pages "
                f"将 Source 设为 'Deploy from a branch' → branch={branch} → folder=/{base_path}"
            )