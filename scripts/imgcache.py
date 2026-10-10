#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
券图管线
========

把麦当劳 MCP 返回的券图 / 餐品图，下载 → 缓存 → 转成可直接内联进 HTML 的
base64 data URI。

为什么要有这层？
  纯文字说"你白扔了一个麦旋风"，不如直接把那个麦旋风摆出来。图片有食欲，
  有食欲的东西才会让人真的想吃、真的去用券。

设计原则：
  - **零第三方依赖**：只用标准库（urllib / base64 / hashlib / threading）
  - **失败不阻塞**：任何一张图下载失败都返回空串，由模板兜底为 SVG 占位。
    绝不让"图挂了"变成"报告生不出来" —— 比赛演示不能翻车。
  - **本地缓存**：同一个 URL 只下一次，落盘到 assets/img/，重复生成秒出
  - **自动瘦身**：腾讯云 COS 的图走数据万象缩略图参数，79KB → 44KB
  - **并发下载**：14 张图串行太慢，用线程池

用法:
    from imgcache import data_uri, coupon_image, warm
    warm([c["image"] for c in coupons])      # 先并发预热，后面取就是内存级
    uri = coupon_image(coupon)               # "data:image/jpeg;base64,..."

CLI:
    python imgcache.py clear                 # 清空缓存
    python imgcache.py stat                  # 看缓存情况
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
CACHE_DIR = HERE.parent / "assets" / "img"
INDEX_FILE = CACHE_DIR / "index.json"

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# 腾讯云 COS 数据万象：缩略到 200px，体积减半，肉眼看不出差别
_COS_THUMB = "imageMogr2/thumbnail/200x"

_MIME_BY_EXT = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}

_LOCK = threading.Lock()

# 离线图片引用前缀。`local:coupons/xxx.png` 表示读 assets/img/coupons/xxx.png，
# 不走网络。DEMO 数据集用它保证断网演示时也有真实产品图。
_LOCAL_PREFIX = "local:"


def _local_path(ref: str) -> Path | None:
    """把 `local:xxx` 解析成本地路径；不是 local 引用则返回 None。"""
    if not ref.startswith(_LOCAL_PREFIX):
        return None
    rel = ref[len(_LOCAL_PREFIX):].strip().lstrip("/\\")
    if not rel or ".." in rel:
        return None
    return CACHE_DIR / rel


# ----------------------------------------------------------------------------
# URL 处理
# ----------------------------------------------------------------------------

def _slim(url: str) -> str:
    """给腾讯云 COS 的图加缩略图参数；其它 CDN 原样返回。"""
    if not url:
        return ""
    if "myqcloud.com" in url or "cos." in url:
        sep = "&" if "?" in url else "?"
        return url + sep + _COS_THUMB
    return url


def _ext_from(url: str, content_type: str = "") -> str:
    ct = (content_type or "").lower()
    if "jpeg" in ct or "jpg" in ct:
        return "jpg"
    if "webp" in ct:
        return "webp"
    if "gif" in ct:
        return "gif"
    if "png" in ct:
        return "png"
    # 回退：从 URL 尾部的扩展名猜
    tail = url.split("?")[0].rsplit(".", 1)
    if len(tail) == 2 and tail[1].lower() in _MIME_BY_EXT:
        return tail[1].lower()
    return "png"


def _key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


# ----------------------------------------------------------------------------
# 缓存索引
# ----------------------------------------------------------------------------

def _load_index() -> dict[str, str]:
    if INDEX_FILE.is_file():
        try:
            data = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_index(idx: dict[str, str]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_FILE.write_text(json.dumps(idx, ensure_ascii=False, indent=2),
                          encoding="utf-8")


# ----------------------------------------------------------------------------
# 下载
# ----------------------------------------------------------------------------

def _download(url: str, timeout: float = 12.0) -> tuple[bytes, str] | None:
    """下载单张图，返回 (数据, content_type)。任何异常都返回 None。"""
    if not url:
        return None
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(), resp.headers.get("Content-Type", "") or ""
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return None


def cache_bytes(url: str) -> bytes | None:
    """先查缓存，未命中再下载并落盘。失败返回 None。"""
    if not url:
        return None

    # local: 引用直接读本地文件，不走网络 —— 离线演示也能出图
    lp = _local_path(url)
    if lp is not None:
        try:
            return lp.read_bytes() if lp.is_file() else None
        except OSError:
            return None

    slim = _slim(url)
    with _LOCK:
        idx = _load_index()
        for cand in (url, slim):
            name = idx.get(cand)
            if name:
                p = CACHE_DIR / name
                if p.is_file():
                    return p.read_bytes()

    got = _download(slim) or (_download(url) if slim != url else None)
    if got is None:
        return None
    data, ctype = got
    if not data:
        return None

    ext = _ext_from(slim, ctype)
    name = f"{_key(url)}.{ext}"
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (CACHE_DIR / name).write_bytes(data)
    except OSError:
        return data      # 写不进缓存也要把图给出去

    with _LOCK:
        idx = _load_index()
        idx[url] = name
        idx[slim] = name
        _save_index(idx)
    return data


def warm(urls: list[str], workers: int = 6) -> int:
    """并发预热一批图。返回成功张数。"""
    uniq = [u for u in dict.fromkeys(urls) if u]
    if not uniq:
        return 0
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(uniq)))) as ex:
        return sum(1 for d in ex.map(cache_bytes, uniq) if d)


# ----------------------------------------------------------------------------
# 对外的取图接口
# ----------------------------------------------------------------------------

def data_uri(url: str) -> str:
    """把图转成可直接写进 HTML 的 data URI。失败返回空串。"""
    data = cache_bytes(url)
    if not data:
        return ""
    ext = _ext_from(_slim(url))
    mime = _MIME_BY_EXT.get(ext, "image/png")
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def coupon_image(coupon: dict[str, Any]) -> str:
    """从券对象里取图并转 data URI。优先 image，回退 bigImage。"""
    for key in ("image", "bigImage", "couponImage", "imgUrl"):
        u = coupon.get(key)
        if u:
            uri = data_uri(str(u))
            if uri:
                return uri
    return ""


def meal_image(meal: dict[str, Any]) -> str:
    """从餐品对象里取图并转 data URI。"""
    for key in ("image", "bigImage", "mealImage", "imgUrl"):
        u = meal.get(key)
        if u:
            uri = data_uri(str(u))
            if uri:
                return uri
    return ""


# ----------------------------------------------------------------------------
# 维护
# ----------------------------------------------------------------------------

def clear_cache() -> int:
    """清空图片缓存。返回删掉的文件数。"""
    n = 0
    if CACHE_DIR.is_dir():
        for p in CACHE_DIR.iterdir():
            if p.is_file() and p.name != INDEX_FILE.name:
                try:
                    p.unlink()
                    n += 1
                except OSError:
                    pass
    if INDEX_FILE.is_file():
        try:
            INDEX_FILE.unlink()
        except OSError:
            pass
    return n


def stat() -> dict[str, Any]:
    idx = _load_index()
    names = set(idx.values())
    total = 0
    if CACHE_DIR.is_dir():
        for p in CACHE_DIR.iterdir():
            if p.is_file() and p.name != INDEX_FILE.name:
                total += p.stat().st_size
    return {
        "dir": str(CACHE_DIR),
        "entries": len(idx),
        "files": len(names),
        "bytes": total,
        "mb": round(total / 1024 / 1024, 2),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="券图管线")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("clear", help="清空图片缓存")
    sub.add_parser("stat", help="查看缓存情况")
    args = p.parse_args()

    if args.cmd == "clear":
        print(f"已清空 {clear_cache()} 个缓存文件")
        return 0

    s = stat()
    print(f"缓存目录：{s['dir']}")
    print(f"索引条目：{s['entries']}    实际文件：{s['files']} 张    "
          f"占用：{s['mb']} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
