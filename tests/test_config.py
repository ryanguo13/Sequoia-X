"""配置管理属性测试。"""

import os

from hypothesis import HealthCheck, given
from hypothesis import settings as h_settings
from hypothesis import strategies as st


# Feature: sequoia-x-v2, Property 1: 环境变量覆盖配置默认值
@given(db_path=st.text(min_size=1, max_size=100, alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="/_.-")))
@h_settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_env_overrides_default(db_path: str, monkeypatch) -> None:
    """属性 1：任意合法 db_path 通过环境变量设置后，Settings 实例应反映该值。"""
    import sequoia_x.core.config as cfg_module
    monkeypatch.setenv("DB_PATH", db_path)
    monkeypatch.setenv("FEISHU_WEBHOOK_URL", "https://example.com/hook")
    monkeypatch.setattr(cfg_module, "_settings", None)
    from sequoia_x.core.config import Settings
    s = Settings()
    assert s.db_path == db_path


# Feature: sequoia-x-v2, Property 2: 缺失可选字段使用默认值
def test_missing_optional_field_uses_default() -> None:
    """属性 2：feishu_webhook_url 是可选字段，缺失时使用空字符串默认值（不抛 ValidationError）。

    设计权衡：让 Settings 在缺省场景下仍能构造（如 CI 环境未配 webhook）。
    飞书推送会通过 FeishuNotifier.is_configured 自动跳过。
    """
    from sequoia_x.core.config import Settings
    # 确保环境变量和 .env 中都没有该字段
    env_backup = os.environ.pop("FEISHU_WEBHOOK_URL", None)
    try:
        s = Settings(_env_file=None)
        # 默认值应是空字符串
        assert s.feishu_webhook_url == ""
        # feishu notifier 应能正常构造 + is_configured 返回 False
        from sequoia_x.notify.feishu import FeishuNotifier
        n = FeishuNotifier(s)
        assert n.is_configured is False
    finally:
        if env_backup is not None:
            os.environ["FEISHU_WEBHOOK_URL"] = env_backup


# Feature: sequoia-x-v2, Property 2b: feishu 配置后 is_configured 返回 True
def test_feishu_configured_when_webhook_set() -> None:
    """属性 2b：配置 FEISHU_WEBHOOK_URL 后，FeishuNotifier.is_configured 应返回 True。"""
    from sequoia_x.core.config import Settings
    from sequoia_x.notify.feishu import FeishuNotifier
    env_backup = os.environ.pop("FEISHU_WEBHOOK_URL", None)
    try:
        s = Settings(_env_file=None, feishu_webhook_url="https://example.com/hook")
        n = FeishuNotifier(s)
        assert n.is_configured is True
    finally:
        if env_backup is not None:
            os.environ["FEISHU_WEBHOOK_URL"] = env_backup
