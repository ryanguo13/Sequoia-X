#!/usr/bin/env python3
"""把本地 SQLite db 压缩上传到 GitHub Release，作为 GH Actions 的初始数据源。

使用场景：
    1. 你在本地跑完 `python main.py --backfill`，db 是最新的
    2. 执行本脚本 → db 压缩后上传到 GitHub Release（asset 名 sequoia_v2.db.tar.gz）
    3. workflow 修改为从 release 下载 db（取代 backfill），后续只增量

用法：
    python scripts/upload_db_to_release.py                  # 上传到最新 release
    python scripts/upload_db_to_release.py --release v1.0   # 上传到指定 release
    python scripts/upload_db_to_release.py --create-release # 创建新 release 后上传

依赖：
    pip install requests
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tarfile
from datetime import datetime
from pathlib import Path

import requests

DB_PATH = Path("data/sequoia_v2.db")
ARCHIVE_NAME = "sequoia_v2.db.tar.gz"
ARCHIVE_PATH = Path("data") / ARCHIVE_NAME
GITHUB_API = "https://api.github.com"


def get_token_and_repo() -> tuple[str, str]:
    """从环境变量读 GH_TOKEN 和 GITHUB_REPO。"""
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPO")
    if not token:
        print("❌ 缺少 GH_TOKEN 环境变量", file=sys.stderr)
        print("   生成: https://github.com/settings/tokens (需要 repo 权限)", file=sys.stderr)
        sys.exit(1)
    if not repo:
        print("❌ 缺少 GITHUB_REPO 环境变量 (格式: owner/name)", file=sys.stderr)
        sys.exit(1)
    return token, repo


def compress_db(db_path: Path, archive_path: Path) -> int:
    """压缩 db，返回压缩后大小。"""
    if not db_path.exists():
        print(f"❌ db 文件不存在: {db_path}", file=sys.stderr)
        sys.exit(1)

    print(f"📦 压缩 {db_path} → {archive_path}")
    original_size = db_path.stat().st_size

    # 重要：SQLite 压缩前先 VACUUM，能减小 20-30%
    print("🧹 VACUUM db（优化空间）...")
    subprocess.run(
        ["sqlite3", str(db_path), "VACUUM;"],
        check=False,  # 即使 sqlite3 CLI 不存在也不报错
    )

    with tarfile.open(archive_path, "w:gz", compresslevel=6) as tar:
        tar.add(db_path, arcname=db_path.name)

    archive_size = archive_path.stat().st_size
    ratio = (1 - archive_size / original_size) * 100 if original_size else 0
    print(
        f"   原始: {original_size / 1e6:.1f} MB → 压缩: {archive_size / 1e6:.1f} MB "
        f"(节省 {ratio:.1f}%)"
    )
    return archive_size


def find_release(token: str, repo: str, tag: str | None) -> dict:
    """查找或创建 release。"""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    if tag:
        # 找指定 release
        url = f"{GITHUB_API}/repos/{repo}/releases/tags/{tag}"
        r = requests.get(url, headers=headers)
        if r.status_code != 200:
            print(f"❌ Release {tag} 不存在: {r.status_code}", file=sys.stderr)
            sys.exit(1)
        return r.json()

    # 找最新 release
    url = f"{GITHUB_API}/repos/{repo}/releases/latest"
    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        return r.json()
    if r.status_code == 404:
        print("⚠️  仓库还没有 release，将创建 v1.0")
        return create_release(token, repo, "v1.0")
    print(f"❌ 查找 release 失败: {r.status_code} {r.text}", file=sys.stderr)
    sys.exit(1)


def create_release(token: str, repo: str, tag: str) -> dict:
    """创建新 release。"""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    url = f"{GITHUB_API}/repos/{repo}/releases"
    payload = {
        "tag_name": tag,
        "name": f"Sequoia-X Initial Data {tag}",
        "body": (
            "Initial SQLite database snapshot for Sequoia-X.\n\n"
            "**This asset is auto-uploaded by `scripts/upload_db_to_release.py`.**\n\n"
            f"包含 ~5200 只 A 股的历史日 K 数据，由 baostock 拉取。\n"
            "workflow 通过此 asset 获取初始数据，避免每次跑 12+ 分钟 backfill。"
        ),
        "draft": False,
        "prerelease": False,
    }
    r = requests.post(url, headers=headers, json=payload)
    if r.status_code not in (200, 201):
        print(f"❌ 创建 release 失败: {r.status_code} {r.text}", file=sys.stderr)
        sys.exit(1)
    print(f"✅ 创建 release {tag} 成功")
    return r.json()


def upload_asset(token: str, repo: str, release_id: int, file_path: Path) -> None:
    """上传 asset 到指定 release。"""
    upload_url = (
        f"{GITHUB_API}/repos/{repo}/releases/{release_id}/assets"
        f"?name={file_path.name}"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/gzip",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    print(f"⬆️  上传 {file_path.name} ({file_path.stat().st_size / 1e6:.1f} MB)...")
    with open(file_path, "rb") as f:
        r = requests.post(upload_url, headers=headers, data=f)
    if r.status_code not in (200, 201):
        print(f"❌ 上传失败: {r.status_code} {r.text}", file=sys.stderr)
        sys.exit(1)
    asset_info = r.json()
    print(f"✅ 上传成功: {asset_info['browser_download_url']}")


def delete_existing_asset(token: str, repo: str, release_id: int, asset_name: str) -> None:
    """如果 release 里已有同名 asset，先删掉（避免 duplicate）。"""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    url = f"{GITHUB_API}/repos/{repo}/releases/{release_id}/assets"
    r = requests.get(url, headers=headers)
    if r.status_code != 200:
        return
    for asset in r.json():
        if asset["name"] == asset_name:
            print(f"🗑️  删除旧 asset: {asset_name}")
            requests.delete(
                f"{GITHUB_API}/repos/{repo}/releases/assets/{asset['id']}",
                headers=headers,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="上传 db 到 GitHub Release")
    parser.add_argument(
        "--release", help="指定 release tag（默认 latest）", default=None
    )
    parser.add_argument(
        "--create-release", action="store_true", help="如无 release 则创建"
    )
    parser.add_argument(
        "--db", default=str(DB_PATH), help=f"db 路径（默认 {DB_PATH}）"
    )
    parser.add_argument(
        "--keep-archive",
        action="store_true",
        help="保留压缩包（默认上传成功后删除）",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"❌ db 不存在: {db_path}", file=sys.stderr)
        sys.exit(1)

    # 1. 压缩
    compress_db(db_path, ARCHIVE_PATH)

    # 2. 上传
    token, repo = get_token_and_repo()
    release = find_release(token, repo, args.release)
    delete_existing_asset(token, repo, release["id"], ARCHIVE_NAME)
    upload_asset(token, repo, release["id"], ARCHIVE_PATH)

    # 3. 清理
    if not args.keep_archive:
        ARCHIVE_PATH.unlink()
        print(f"🧹 删除临时压缩包 {ARCHIVE_PATH}")

    # 4. 提示下一步
    download_url = (
        f"https://github.com/{repo}/releases/download/"
        f"{release['tag_name']}/{ARCHIVE_NAME}"
    )
    print()
    print("🎉 完成！下一步：")
    print(f"   1. workflow 里加入下载步骤（curl {download_url}）")
    print(f"   2. 或直接 run Actions 看效果（如果 workflow 已支持 release）")
    print()
    print(f"   Asset URL: {download_url}")


if __name__ == "__main__":
    main()