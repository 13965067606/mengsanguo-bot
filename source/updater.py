"""Explicit, release-only updater. Never installs source archives or user data."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

REPOSITORY = "13965067606/mengsanguo-bot"
API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
ASSET_NAME = "MengSanGuoBot-update.zip"
EXE_NAME = "MengSanGuoWindowBot.exe"
MAX_DOWNLOAD = 400 * 1024 * 1024
MAX_EXPANDED = 900 * 1024 * 1024


def version_tuple(text: str) -> tuple[int, ...]:
    if not re.fullmatch(r"v?\d+(?:\.\d+){1,3}", text):
        raise ValueError("版本号格式不正确")
    numbers = tuple(map(int, text.lstrip("v").split(".")))
    return numbers + (0,) * (4 - len(numbers))


def release_info(data: dict, current: str) -> dict | None:
    if data.get("draft") or data.get("prerelease"):
        return None
    version = str(data.get("tag_name", ""))
    if version_tuple(version) <= version_tuple(current):
        return None
    assets = [a for a in data.get("assets", []) if a.get("name") == ASSET_NAME and a.get("state") == "uploaded"]
    if len(assets) != 1:
        raise ValueError("新版尚未提供一键更新包，请等待发布完成")
    asset = assets[0]
    digest = str(asset.get("digest", ""))
    if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
        raise ValueError("更新包缺少 SHA256 校验信息，已拒绝安装")
    prefix = f"https://github.com/{REPOSITORY}/releases/download/{version}/"
    url = str(asset.get("browser_download_url", ""))
    if url != prefix + ASSET_NAME:
        raise ValueError("更新来源不正确，已拒绝下载")
    size = int(asset.get("size", 0))
    if not 0 < size <= MAX_DOWNLOAD:
        raise ValueError("更新包大小异常")
    return {"version": version.lstrip("v"), "url": url, "sha256": digest[7:].lower(), "size": size,
            "notes": str(data.get("body") or "暂无说明")[:4000]}


def request(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "MengSanGuoBot-Updater", "Accept": "application/vnd.github+json" if url == API_URL else "application/octet-stream"})
    response = urllib.request.urlopen(req, timeout=25)
    final = urllib.parse.urlparse(response.geturl())
    allowed_hosts = {"api.github.com"} if url == API_URL else {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}
    if final.scheme != "https" or final.hostname not in allowed_hosts:
        response.close()
        raise ValueError("更新下载跳转到了不受信任的地址")
    return response


def check_latest(current: str) -> dict | None:
    try:
        with request(API_URL) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("版本信息过大")
        return release_info(json.loads(raw), current)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ValueError("GitHub 尚未发布正式更新版本") from exc
        if exc.code in (403, 429):
            raise ValueError("GitHub 暂时限制访问，请稍后重试") from exc
        raise


def allowed_file(name: str) -> bool:
    # Strict Windows-compatible paths, even when tests run on other platforms.
    if not name or "\\" in name or ":" in name or name.startswith("/"):
        return False
    parts = name.split("/")
    for part in parts:
        if part in ("", ".", "..") or part.endswith((".", " ")) or any(ord(c) < 32 for c in part):
            return False
        if re.search(r'[<>"|?*]', part) or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part):
            return False
    if name in (EXE_NAME, "config.example.json", "使用说明.md", "update.json"):
        return True
    return len(parts) > 1 and parts[0] in ("templates", "assets", "ocr")


def stage_archive(archive: Path, destination: Path, version: str) -> dict:
    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        if len(entries) > 6000 or sum(e.file_size for e in entries) > MAX_EXPANDED:
            raise ValueError("解压大小或文件数超限")
        names = set()
        for entry in entries:
            if entry.is_dir():
                raise ValueError("更新包应仅包含文件")
            if not allowed_file(entry.filename) or stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError("更新包存在不允许的路径")
            key = entry.filename.casefold()
            if key in names:
                raise ValueError("更新包有重复文件")
            names.add(key)
        meta = bundle.getinfo("update.json")
        if meta.file_size > 2 * 1024 * 1024:
            raise ValueError("更新清单过大")
        manifest = json.loads(bundle.read(meta))
        files = manifest.get("files", {})
        if manifest.get("version") != version or manifest.get("executable") != EXE_NAME:
            raise ValueError("更新版本与安装清单不一致")
        if not isinstance(files, dict) or EXE_NAME not in files or "config.example.json" not in files:
            raise ValueError("更新包缺少程序或默认配置")
        if set(files) | {"update.json"} != {e.filename for e in entries} or "update.json" in files:
            raise ValueError("更新清单与实际文件不一致")
        destination.mkdir(parents=True, exist_ok=False)
        for name, expected in files.items():
            if not re.fullmatch(r"[0-9a-f]{64}", str(expected)):
                raise ValueError("文件校验格式错误")
            target = destination.joinpath(*PurePosixPath(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            with bundle.open(name) as source, target.open("xb") as output:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    output.write(chunk)
            if digest.hexdigest() != expected:
                raise ValueError(f"文件校验失败：{name}")
        (destination / "update.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        return manifest


def prepare_update(info: dict, progress=lambda percent: None) -> Path:
    stage = Path(tempfile.mkdtemp(prefix="MengSanGuo-update-"))
    archive = stage / "download.zip"
    try:
        digest = hashlib.sha256()
        received = 0
        deadline = time.monotonic() + 600
        with request(info["url"]) as response, archive.open("xb") as output:
            while chunk := response.read(1024 * 256):
                if time.monotonic() > deadline:
                    raise ValueError("下载超过十分钟，请检查网络后重试")
                received += len(chunk)
                if received > info["size"] or received > MAX_DOWNLOAD:
                    raise ValueError("下载文件大小异常")
                digest.update(chunk)
                output.write(chunk)
                progress(min(99, int(received * 100 / info["size"])))
        if received != info["size"] or digest.hexdigest() != info["sha256"]:
            raise ValueError("下载不完整或 SHA256 不匹配，请重试")
        stage_archive(archive, stage / "payload", info["version"])
        helper = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "updater_install.ps1"
        # Windows PowerShell 5.1 requires a BOM for non-ASCII script text.
        (stage / "install.ps1").write_text(helper.read_text(encoding="utf-8-sig"), encoding="utf-8-sig")
        progress(100)
        return stage
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def launch_installer(stage: Path, target: Path) -> None:
    if not getattr(sys, "frozen", False):
        raise ValueError("源码运行请使用 Git 更新；一键替换仅适用于打包程序")
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    subprocess.Popen([str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(stage / "install.ps1"),
                      "-Stage", str(stage), "-Target", str(target), "-ProcessId", str(os.getpid())],
                     creationflags=subprocess.CREATE_NO_WINDOW, cwd=str(stage), close_fds=True)
