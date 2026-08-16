"""GitHub Pages 通知器测试。

覆盖：
- is_configured 状态机（三种组合：未配置 / 已配置 / 已禁用）
- send() graceful skip（无 commit）
- send_summary() 完整 commit 流（mock PyGithub）
- 失败注入：GitHub API 异常时不抛出
- HTML/JSON 渲染内容正确性
- 历史合并（本地 + 远端 + 当日 去重）
"""

import json
import logging
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given
from hypothesis import settings as h_settings
from hypothesis import strategies as st

from sequoia_x.core.config import Settings
from sequoia_x.notify.github_pages import GithubPagesNotifier
from sequoia_x.notify.site_renderer import (
    append_to_history,
    render_history_data,
    render_index,
    render_readme,
)


def make_settings(
    github_token: str = "",
    github_repo: str = "",
    github_pages_enabled: bool = True,
) -> Settings:
    return Settings(
        db_path="data/test.db",
        start_date="2024-01-01",
        feishu_webhook_url="https://example.com/webhook",
        github_token=github_token,
        github_repo=github_repo,
        github_pages_enabled=github_pages_enabled,
    )


# ─────────────────────────────────────────────────────────
# 1. is_configured 状态机
# ─────────────────────────────────────────────────────────


def test_not_configured_when_token_missing() -> None:
    """无 GITHUB_TOKEN → is_configured = False"""
    s = make_settings(github_token="", github_repo="ryanguo13/Sequoia-X")
    assert GithubPagesNotifier(s).is_configured is False


def test_not_configured_when_repo_missing() -> None:
    """无 GITHUB_REPO → is_configured = False"""
    s = make_settings(github_token="ghp_xxx", github_repo="")
    assert GithubPagesNotifier(s).is_configured is False


def test_not_configured_when_disabled() -> None:
    """GITHUB_PAGES_ENABLED=false → is_configured = False"""
    s = make_settings(
        github_token="ghp_xxx",
        github_repo="ryanguo13/Sequoia-X",
        github_pages_enabled=False,
    )
    assert GithubPagesNotifier(s).is_configured is False


def test_configured_when_all_set() -> None:
    s = make_settings(
        github_token="ghp_xxx",
        github_repo="ryanguo13/Sequoia-X",
        github_pages_enabled=True,
    )
    assert GithubPagesNotifier(s).is_configured is True


# ─────────────────────────────────────────────────────────
# 2. graceful skip（未配置时不应抛出）
# ─────────────────────────────────────────────────────────


def test_send_skips_when_not_configured(caplog: pytest.LogCaptureFixture) -> None:
    s = make_settings()  # 无 token
    n = GithubPagesNotifier(s)

    with caplog.at_level(logging.DEBUG, logger="sequoia_x.notify.github_pages"):
        n.send(["000001"], "MaVolumeStrategy", "ma_volume")
        n.send_summary({"MaVolumeStrategy": ["000001"]})

    assert any("未配置" in r.message for r in caplog.records)
    # 不应有任何 commit
    # （因为连 _ensure_client 都不会被调用）


# ─────────────────────────────────────────────────────────
# 3. send_summary 完整流程（mock PyGithub）
# ─────────────────────────────────────────────────────────


def _build_mock_repo() -> MagicMock:
    """构造一个 mock GitHub repo：首次部署，所有文件不存在。"""
    repo = MagicMock()
    repo.get_contents.side_effect = Exception("not found")
    repo.get_git_ref.return_value.object.sha = "base_sha_123"
    repo.get_git_tree.return_value.tree = []
    repo.create_file = MagicMock(return_value=MagicMock())
    repo.update_file = MagicMock(return_value=MagicMock())
    return repo


def test_send_summary_commits_three_files(tmp_path: Path) -> None:
    """send_summary 必须 commit 3 个文件：index.html / README.md / history.json。"""
    s = make_settings(github_token="ghp_test", github_repo="ryanguo13/Sequoia-X")
    n = GithubPagesNotifier(s)

    mock_repo = _build_mock_repo()
    mock_gh = MagicMock()
    mock_gh.get_repo.return_value = mock_repo

    with patch("github.Github", return_value=mock_gh), \
         patch("sequoia_x.notify.github_pages.get_stock_names", return_value={
             "000001": "平安银行", "600519": "贵州茅台", "300750": "宁德时代",
         }), \
         patch.object(Path, "cwd", return_value=tmp_path):
        results = {
            "MaVolumeStrategy": ["000001", "600519"],
            "TurtleTradeStrategy": ["600519"],
            "RpsBreakoutStrategy": ["000001", "300750"],
        }
        n.send_summary(results)

    # 验证 commit 次数和内容
    assert mock_repo.create_file.call_count == 3
    committed = {c.kwargs["path"] for c in mock_repo.create_file.call_args_list}
    assert "docs/index.html" in committed
    assert "docs/README.md" in committed
    assert "docs/history.json" in committed

    # 验证 commit message 含日期 + 数量
    today = date.today().strftime("%Y-%m-%d")
    for c in mock_repo.create_file.call_args_list:
        msg = c.kwargs["message"]
        assert today in msg
        assert "docs(pages): auto update" in msg


def test_send_summary_html_contains_results(tmp_path: Path) -> None:
    """HTML 内容必须包含：股票名、策略中文名、共振榜、KPI。"""
    s = make_settings(github_token="ghp_test", github_repo="ryanguo13/Sequoia-X")
    n = GithubPagesNotifier(s)

    mock_repo = _build_mock_repo()

    with (
        patch(
            "github.Github",
            return_value=MagicMock(get_repo=MagicMock(return_value=mock_repo)),
        ),
        patch("sequoia_x.notify.github_pages.get_stock_names", return_value={
            "000001": "平安银行", "600519": "贵州茅台",
        }),
        patch.object(Path, "cwd", return_value=tmp_path),
    ):
        results = {
            "MaVolumeStrategy": ["000001"],
            "TurtleTradeStrategy": ["000001", "600519"],  # 000001 共振
        }
        n.send_summary(results)

    # 抓 index.html 的内容
    html_call = next(c for c in mock_repo.create_file.call_args_list
                     if c.kwargs["path"] == "docs/index.html")
    html_doc = html_call.kwargs["content"]

    assert "Sequoia-X" in html_doc
    assert "平安银行" in html_doc
    assert "贵州茅台" in html_doc
    assert "均线放量" in html_doc
    assert "海龟突破" in html_doc
    assert "多策略共振榜" in html_doc
    assert "data-history" in html_doc  # Chart.js 历史数据
    assert "ryanguo13.github.io" in html_call.kwargs["content"]


def test_send_summary_handles_api_failure_gracefully(tmp_path: Path) -> None:
    """GitHub API 异常时，send_summary 应记录 ERROR 但不抛出。"""
    s = make_settings(github_token="ghp_test", github_repo="ryanguo13/Sequoia-X")
    n = GithubPagesNotifier(s)

    mock_gh = MagicMock()
    mock_gh.get_repo.side_effect = RuntimeError("network error")

    with patch("github.Github", return_value=mock_gh), \
         patch("sequoia_x.notify.github_pages.get_stock_names", return_value={}), \
         patch.object(Path, "cwd", return_value=tmp_path):
        # 不应抛出
        n.send_summary({"TestStrategy": ["000001"]})


# ─────────────────────────────────────────────────────────
# 4. send() 单策略暂存（无 commit）
# ─────────────────────────────────────────────────────────


def test_send_does_not_trigger_commit(tmp_path: Path) -> None:
    """单策略 send() 只暂存，不触发任何 commit。"""
    s = make_settings(github_token="ghp_test", github_repo="ryanguo13/Sequoia-X")
    n = GithubPagesNotifier(s)
    mock_repo = _build_mock_repo()

    with patch("github.Github", return_value=MagicMock(get_repo=MagicMock(return_value=mock_repo))):
        n.send(["000001"], "MaVolumeStrategy", "ma_volume")

    assert mock_repo.create_file.call_count == 0
    assert mock_repo.update_file.call_count == 0


# ─────────────────────────────────────────────────────────
# 5. 历史合并逻辑
# ─────────────────────────────────────────────────────────


def test_append_to_history_dedup_same_day() -> None:
    """同日多次调用应覆盖而非追加。"""
    history = [
        {"date": "2026-08-14", "strategies": {}, "total": 0, "unique": 0, "resonance_count": 0},
    ]
    h1 = append_to_history(history, "2026-08-15", {"A": ["000001"]})
    h2 = append_to_history(h1, "2026-08-15", {"A": ["000002"], "B": ["000003"]})

    assert len(h2) == 2  # 仍是 2 天
    # 当日条目应为最后一次调用的结果
    assert h2[-1]["date"] == "2026-08-15"
    assert "B" in h2[-1]["strategies"]
    assert h2[-1]["unique"] == 2


def test_append_to_history_computes_resonance() -> None:
    """append_to_history 应正确计算共振数。"""
    h = append_to_history(
        [],
        "2026-08-15",
        {
            "A": ["000001", "000002"],
            "B": ["000001", "000003"],  # 000001 被 2 策略选中
        },
    )
    assert h[0]["resonance_count"] == 1
    assert h[0]["unique"] == 3
    assert h[0]["total"] == 4


# ─────────────────────────────────────────────────────────
# 6. 渲染器单元测试
# ─────────────────────────────────────────────────────────


def test_render_index_is_valid_html() -> None:
    html_doc = render_index(
        today="2026-08-15",
        results={"MaVolumeStrategy": ["000001"]},
        history=[],
        stock_names={"000001": "平安银行"},
        repo_url="https://github.com/x/y",
        pages_url="https://x.github.io/y",
    )
    assert html_doc.startswith("<!DOCTYPE html>")
    assert "</html>" in html_doc
    assert "Sequoia-X" in html_doc
    assert "平安银行" in html_doc
    # 关键 CSS/JS 嵌入
    assert "--bg:" in html_doc  # Polymarket 风变量
    assert "chart.js" in html_doc.lower()


def test_render_index_handles_empty_results() -> None:
    """空 results 应正常渲染，不抛错。"""
    html_doc = render_index(
        today="2026-08-15",
        results={},
        history=[],
        stock_names={},
        repo_url="https://github.com/x/y",
        pages_url="https://x.github.io/y",
    )
    assert html_doc  # 非空


def test_render_index_hide_empty_strategies() -> None:
    """hide_empty_strategies=True 时，0 命中的策略不应出现在 detail 区。"""
    html_doc = render_index(
        today="2026-08-15",
        results={
            "ActiveStrategy": ["000001"],
            "EmptyStrategy": [],
        },
        history=[],
        stock_names={"000001": "平安银行"},
        repo_url="https://github.com/x/y",
        pages_url="https://x.github.io/y",
        hide_empty_strategies=True,
    )
    # 0 命中的策略不渲染 detail section
    assert "EmptyStrategy" not in html_doc
    # 有命中的策略正常渲染
    assert "ActiveStrategy" in html_doc
    assert "000001" in html_doc


def test_render_index_strong_resonance_class() -> None:
    """被 ≥3 策略选中的股票应有 resonance-strong 高亮 class。"""
    html_doc = render_index(
        today="2026-08-15",
        results={
            "A": ["000001", "000002"],
            "B": ["000001", "000003"],
            "C": ["000001"],  # 000001 被 3 策略选中
            "D": ["000002"],  # 000002 被 2 策略选中（普通）
        },
        history=[],
        stock_names={"000001": "AAA", "000002": "BBB", "000003": "CCC"},
        repo_url="https://github.com/x/y",
        pages_url="https://x.github.io/y",
    )
    # ×3 的 000001 应该有 strong 类
    assert "resonance-strong" in html_doc
    # 验证 000001 在 strong container 里
    import re
    strong_blocks = re.findall(
        r'<div class="resonance-item resonance-strong"[^>]*>(.*?)(?=<div class="resonance-item)',
        html_doc,
        re.DOTALL,
    )
    # 应该至少有一个 strong block 包含 000001
    assert any("000001" in block for block in strong_blocks)


def test_render_index_escapes_xss() -> None:
    """恶意输入必须被 HTML escape。"""
    html_doc = render_index(
        today="2026-08-15",
        results={"<script>alert(1)</script>": ["<img src=x onerror=alert(1)>"]},
        history=[],
        stock_names={"<img src=x onerror=alert(1)>": "<b>evil</b>"},
        repo_url="https://github.com/x/y",  # 用安全 URL，避免误判
        pages_url="https://x.github.io/y",
    )
    # 原始 <script> 不应作为可执行标签存在
    assert "<script>alert(1)</script>" not in html_doc
    # 应该出现转义形式
    assert "&lt;script&gt;" in html_doc
    # <img onerror=... 也应被转义
    assert "<img src=x onerror=" not in html_doc
    # 股票名里的 <b>evil</b> 也应被转义
    assert "<b>evil</b>" not in html_doc


def test_render_index_escapes_unsafe_url() -> None:
    """恶意 repo_url 应被严格 escape，防止 attribute injection XSS。"""
    html_doc = render_index(
        today="2026-08-15",
        results={},
        history=[],
        stock_names={},
        # 用带引号的 URL 试图突破 href 属性边界
        repo_url='https://evil.com/" onerror=alert(1) "',
        pages_url="https://x.github.io/y",
    )
    # _escape_url 必须把 " 转义为 &quot;，这样属性边界不会被突破
    assert '&quot;' in html_doc
    # 真实的攻击是 onerror=alert(1) 不被浏览器解析为新属性
    # 我们检查双引号被 escape 即可（说明 _escape_url 起作用）
    # 如果 _escape_url 没起作用，会是：href="..." onerror=alert(1) "..."（裸引号）
    assert 'href="https://evil.com/&quot; onerror=alert(1) &quot;"' in html_doc


def test_render_index_rejects_javascript_scheme() -> None:
    """非 http/https scheme 必须被拒绝为 #。"""
    html_doc = render_index(
        today="2026-08-15",
        results={},
        history=[],
        stock_names={},
        repo_url="javascript:alert(1)",
        pages_url="https://x.github.io/y",
    )
    # javascript: 不能作为 href 出现（除 "javascript:" 字面量外）
    # 我们确保它被替换为 #
    assert 'href="#"' in html_doc
    # 也不要以 javascript: 作为 href value
    assert 'href="javascript:' not in html_doc


def test_render_history_data_is_valid_json() -> None:
    raw = render_history_data(
        today="2026-08-15",
        results={"A": ["000001"]},
        history=[{
            "date": "2026-08-14", "strategies": {},
            "total": 0, "unique": 0, "resonance_count": 0,
        }],
        pages_url="https://x.github.io/y",
    )
    parsed = json.loads(raw)
    assert parsed["today_total"] == 1
    assert parsed["today_unique"] == 1
    assert len(parsed["history"]) == 1


def test_render_readme_contains_pages_url() -> None:
    md = render_readme("2026-08-15", "https://github.com/x/y", "https://x.github.io/y")
    assert "https://x.github.io/y" in md
    assert "2026-08-15" in md


# ─────────────────────────────────────────────────────────
# 7. 属性测试：共振数始终等于被 ≥2 策略选中的股票数
# ─────────────────────────────────────────────────────────


@given(
    results=st.dictionaries(
        keys=st.sampled_from(["A", "B", "C", "D", "E"]),
        values=st.lists(
            st.text(min_size=6, max_size=6, alphabet="0123456789"),
            min_size=0, max_size=5, unique=True,
        ),
        min_size=1, max_size=5,
    )
)
@h_settings(max_examples=50)
def test_resonance_count_is_correct(results: dict[str, list[str]]) -> None:
    """属性：_count_resonance 结果必须等于实际被 ≥2 策略选中的股票数。"""
    from collections import Counter

    cnt = GithubPagesNotifier._count_resonance(results)
    all_codes = [c for syms in results.values() for c in syms]
    actual = sum(1 for n in Counter(all_codes).values() if n >= 2)
    assert cnt == actual