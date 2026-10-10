#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
麦麦浪费账本 · 本地累积记账
==========================

每次券包体检时，把快照落到本地，用于周/月/年的"你白扔了多少钱"统计。

设计要点：
  - 一天只记一条（同一天多次体检取最新覆盖，避免刷量虚高）
  - 记录券包明细（名字/券值/到期/阶段），便于回溯
  - 单独跟踪"已化灰积分"增量，这是最扎心的数字
  - 存储位置：~/.workbuddy/skills/mcd-life/data/ledger.json

用法:
    python ledger.py snapshot          # 记一次快照
    python ledger.py dump              # 打印全部历史
    python ledger.py show --period week  # 查看某周期
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcd_client import MCDClient            # noqa: E402
from coupon_life import CouponLifeEngine    # noqa: E402
import roast                                # noqa: E402

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
LEDGER_FILE = DATA_DIR / "ledger.json"

# 积分 -> 人民币展示折算率（1 分 ≈ ¥0.1）。仅用于"白扔了多少"的估算展示，
# 不作为任何交易依据。
POINT_RATE = 0.1


def use_ledger(path: str | Path) -> Path:
    """切换到指定的账本文件。

    用于演示场景：`assets/ledger_sample.json` 是一份跨多天的示例账本，
    能让评委一眼看到周期统计和"券消失了"的完整效果，而不用真的等几天。
    """
    global LEDGER_FILE
    LEDGER_FILE = Path(path).expanduser().resolve()
    return LEDGER_FILE


# ----------------------------------------------------------------------------
# 存取
# ----------------------------------------------------------------------------

def load_ledger() -> dict[str, Any]:
    if not LEDGER_FILE.is_file():
        return {"version": 1, "entries": []}
    try:
        data = json.loads(LEDGER_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"version": 1, "entries": []}
        data.setdefault("version", 1)
        data.setdefault("entries", [])
        return data
    except (json.JSONDecodeError, OSError):
        return {"version": 1, "entries": []}


def save_ledger(ledger: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LEDGER_FILE.write_text(json.dumps(ledger, ensure_ascii=False, indent=2),
                           encoding="utf-8")


# ----------------------------------------------------------------------------
# 快照
# ----------------------------------------------------------------------------

def build_entry(rep: dict[str, Any]) -> dict[str, Any]:
    """从体检结果构造一条账本记录。"""
    s = rep["survival"]
    acc = rep["account"]
    now = datetime.now()

    coupons = []
    for c in rep.get("coupons", []):
        coupons.append({
            "name": c["name"],
            "stage": c["stage"],
            "value": c.get("value", 0),
            "expire": c["expire"].strftime("%Y-%m-%d %H:%M:%S") if c.get("expire") else None,
            "duplicate_count": c.get("duplicate_count", 1),
            # 存图 URL：券消失后，只有这里还留着它长什么样 —— 浪费博物馆要用
            "image": c.get("image") or c.get("big_image") or "",
        })

    return {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "weekday": now.weekday(),          # 0=周一
        "mode": rep.get("mode", "DEMO"),
        "coupon_total": s["total"],
        "coupon_value": round(s.get("live_value", 0), 2),
        "at_risk_value": round(s.get("at_risk_value", 0), 2),
        "dying_count": s.get("dying", 0),
        "dead_count": s.get("dead", 0),
        "grab_rate": round(s.get("grab_rate", 0), 1),
        "available_points": round(acc.get("available", 0), 2),
        "accumulated_points": round(acc.get("accumulative", 0), 2),
        "expired_points": round(acc.get("expired", 0), 2),
        "monthly_expiring_points": round(acc.get("monthly_expiring", 0), 2),
        "coupons": coupons,
    }


def record_snapshot(rep: dict[str, Any] | None = None,
                    client: MCDClient | None = None) -> dict[str, Any]:
    """记一次快照。同一天只保留最新一条。"""
    if rep is None:
        engine = CouponLifeEngine(client=client or MCDClient())
        rep = engine.full_exam()

    entry = build_entry(rep)
    ledger = load_ledger()
    entries = ledger["entries"]

    # 同一天覆盖
    entries = [e for e in entries if e.get("date") != entry["date"]]
    entries.append(entry)
    entries.sort(key=lambda e: (e.get("date", ""), e.get("time", "")))
    ledger["entries"] = entries
    save_ledger(ledger)
    return entry


# ----------------------------------------------------------------------------
# 化灰检测：不是"现在有 0 张已化灰"，而是"曾经有过的券，后来找不到了"
#
# 这是整个账本最扎心的数字来源。麦当劳 MCP 的券包接口只给"当前还在的券"，
# 已过期的券会被静默移除 —— 于是"上一次记的 id，这一次不见了"就是最真实的
# 白扔信号。
# ----------------------------------------------------------------------------

def _coupon_key(c: dict[str, Any]) -> str:
    return f'{c.get("name", "")}|{c.get("expire") or "?"}'


def _coupon_ids(entry: dict[str, Any]) -> set[str]:
    return {_coupon_key(c) for c in entry.get("coupons", [])}


def _detect_gone(entries: list[dict[str, Any]], until: datetime | None = None) -> list[dict[str, Any]]:
    """按日期顺序扫描，找出"上一轮还在、这一轮消失了"的券。

    until 之后的记录不参与（只看周期内的消失）。
    """
    gone: list[dict[str, Any]] = []
    gone_keys: set[str] = set()
    for prev, cur in zip(entries, entries[1:]):
        if until is not None:
            try:
                if datetime.strptime(cur["date"], "%Y-%m-%d") > until:
                    break
            except (KeyError, ValueError):
                pass
        prev_map = {_coupon_key(c): c for c in prev.get("coupons", [])}
        cur_ids = _coupon_ids(cur)
        for k, c in prev_map.items():
            if k in cur_ids or k in gone_keys:
                continue
            gone_keys.add(k)
            gone.append({
                "name": c.get("name"),
                "value": c.get("value", 0),
                "last_seen": prev.get("date"),
                "expire": c.get("expire"),
                "image": c.get("image") or "",
            })
    return gone


# ----------------------------------------------------------------------------
# 周期聚合
# ----------------------------------------------------------------------------

def _period_start(period: str, now: datetime | None = None) -> datetime:
    now = now or datetime.now()
    if period == "week":
        # 本周一 00:00
        return (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if period == "year":
        return now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    raise ValueError(f"未知周期: {period}")


def entries_in_period(period: str, ledger: dict[str, Any] | None = None,
                      now: datetime | None = None) -> list[dict[str, Any]]:
    ledger = ledger or load_ledger()
    start = _period_start(period, now)
    out = []
    for e in ledger["entries"]:
        try:
            d = datetime.strptime(e["date"], "%Y-%m-%d")
        except (KeyError, ValueError):
            continue
        if d >= start:
            out.append(e)
    return out


def period_stats(period: str, ledger: dict[str, Any] | None = None,
                 now: datetime | None = None) -> dict[str, Any]:
    """计算某周期的"浪费账单"。"""
    ledger = ledger or load_ledger()
    now = now or datetime.now()
    entries = entries_in_period(period, ledger, now)
    label = {"week": "本周", "month": "本月", "year": "今年"}.get(period, period)

    if not entries:
        return {
            "period": period, "label": label, "has_data": False, "checks": 0,
            "coupon_total": 0, "coupon_value": 0, "at_risk_value": 0,
            "dying_count": 0, "dead_count": 0, "gone_coupons": [],
            "expired_points": 0, "monthly_expiring_points": 0,
            "points_value": 0.0, "gone_value": 0.0,
            "wasted_total": 0.0, "grabbed_rate": 0.0,
        }

    # 券包快照：以周期内最后一次体检为准（存量口径，不重复累加同一批券）
    last = entries[-1]
    coupon_total = last.get("coupon_total", 0)
    coupon_value = last.get("coupon_value", 0)
    at_risk = last.get("at_risk_value", 0)
    dying_count = last.get("dying_count", 0)
    monthly_expiring = last.get("monthly_expiring_points", 0)

    # 已化灰：靠"上一轮有、这一轮没了"推断，而不是读接口的 0
    # 扫描要跨越整个账本，才能看出周期内的消失
    all_entries = ledger["entries"]
    gone = _detect_gone(all_entries, until=now)
    period_start = _period_start(period, now)
    gone_in_period = [g for g in gone if _entry_date_ge(g.get("last_seen"), period_start)]

    dead_count = len(gone_in_period)
    gone_value = round(sum(g.get("value", 0) for g in gone_in_period), 2)

    # 积分净亏：周期内"已过期积分"的最大值（存量口径，避免重复累加）
    expired_points = max((e.get("expired_points", 0) for e in entries), default=0)
    points_value = round(expired_points * POINT_RATE, 2)

    # 白扔总额 = 化灰券面值 + 化灰积分折算
    wasted_total = round(gone_value + points_value, 2)

    # 薅到率：优先用快照里存好的（券+积分综合口径），无则回退到券的粗算
    if last.get("grab_rate") is not None:
        grabbed_rate = last["grab_rate"]
    else:
        seen_names = set()
        for e in entries:
            for c in e.get("coupons", []):
                seen_names.add(f'{c.get("name")}|{c.get("expire") or "?"}')
        denom = max(len(seen_names), coupon_total)
        grabbed_rate = ((denom - dead_count) / denom * 100) if denom else 0.0

    return {
        "period": period,
        "label": label,
        "has_data": True,
        "checks": len(entries),
        "since": entries[0].get("date"),
        "until": last.get("date"),
        "coupon_total": coupon_total,
        "coupon_value": round(coupon_value, 2),
        "at_risk_value": round(at_risk, 2),
        "dying_count": dying_count,
        "dead_count": dead_count,
        "gone_coupons": gone_in_period,
        "expired_points": expired_points,
        "monthly_expiring_points": monthly_expiring,
        "points_value": points_value,
        "gone_value": gone_value,
        "wasted_total": wasted_total,
        "grabbed_rate": round(grabbed_rate, 1),
    }


def _entry_date_ge(date_str: str | None, start: datetime) -> bool:
    if not date_str:
        return False
    try:
        return datetime.strptime(date_str, "%Y-%m-%d") >= start
    except ValueError:
        return False


def all_period_stats(ledger: dict[str, Any] | None = None,
                     now: datetime | None = None) -> dict[str, dict[str, Any]]:
    ledger = ledger or load_ledger()
    return {p: period_stats(p, ledger, now) for p in ("week", "month", "year")}


# ----------------------------------------------------------------------------
# 渲染
# ----------------------------------------------------------------------------

def render_stats(stats: dict[str, dict[str, Any]]) -> str:
    L = []
    L.append("=" * 56)
    L.append("  麦麦浪费账本 · 周期统计")
    L.append("=" * 56)
    L.append("")

    # 顶部：今年累计（最扎心的数字）
    year = stats.get("year", {})
    if year.get("has_data") and year.get("wasted_total", 0) > 0:
        L.append(f"💸 今年累计白扔：¥{year['wasted_total']:g}")
        food = roast.money_to_food(year["wasted_total"])
        lunch = roast.money_to_lunch(year["wasted_total"])
        if lunch:
            L.append(f"   {lunch}。")
        elif food:
            L.append(f"   折合{food}。")
        L.append("")

    for p in ("week", "month", "year"):
        s = stats[p]
        L.append(f"【{s['label']}】")
        L.append("-" * 56)
        if not s["has_data"]:
            L.append("  还没有记账数据。跑一次「券包体检」就会开始记账。")
            L.append("")
            continue
        L.append(f"  {roast.period_punch(s['label'], s['wasted_total'], s['dead_count'], s['grabbed_rate'])}")
        L.append(f"  体检 {s['checks']} 次 ｜ 券包存量 {s['coupon_total']} 张（面值 ¥{s['coupon_value']:g}）")
        if s["expired_points"]:
            L.append(f"  已作废积分 {s['expired_points']:g} 分（≈ ¥{s['points_value']:g}）")
        for g in s.get("gone_coupons", []):
            L.append(f"     🗑 {g['name']}（{g.get('last_seen')} 之后就没再见过）")
        if s["dying_count"]:
            L.append(f"  ⏰ 当下正在倒计时：{s['dying_count']} 张，约 ¥{s['at_risk_value']:g}")
        L.append("")
    return "\n".join(L)


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="麦麦浪费账本")
    p.add_argument("--mode", choices=["LIVE", "DEMO"])
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("snapshot", help="记一次快照")
    sub.add_parser("dump", help="打印全部历史")
    sub.add_parser("stats", help="查看周/月/年统计")
    sp = sub.add_parser("show", help="查看某周期")
    sp.add_argument("--period", choices=["week", "month", "year"], default="week")

    args = p.parse_args(argv)
    cmd = args.cmd or "stats"

    if cmd == "snapshot":
        client = MCDClient(force_mode=args.mode)
        entry = record_snapshot(client=client)
        print(f"✅ 已记账：{entry['date']}  券 {entry['coupon_total']} 张 ｜ "
              f"今夜清零 {entry['dying_count']} 张 ｜ 已作废积分 {entry['expired_points']:g}")
        print(f"   账本位置：{LEDGER_FILE}")
        return 0

    if cmd == "dump":
        ledger = load_ledger()
        print(json.dumps(ledger, ensure_ascii=False, indent=2))
        return 0

    if cmd == "show":
        s = period_stats(args.period)
        print(render_stats({args.period: s}))
        return 0

    print(render_stats(all_period_stats()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
