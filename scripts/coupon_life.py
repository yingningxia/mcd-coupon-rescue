#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
麦券生命周期引擎
================

「抢救麦券大作战」的核心大脑。

它把麦当劳优惠券和积分当成有寿命的东西：
  - 算剩余寿命、划分阶段（充裕 / 悠着点 / 余额告急 / 今夜清零 / 已化灰）
  - 识别券包的结构性浪费（重复券 / 僵尸券 / 积分作废）
  - 为告急券生成"薅它"的具体行动
  - 把已经白扔掉的券和积分摆进「浪费博物馆」
  - 统计薅到率与白扔金额

用法:
    python coupon_life.py exam                 # 券包体检（真实数据）
    python coupon_life.py exam --demo          # 用离线数据体检
    python coupon_life.py waste                # 只看浪费博物馆

作为模块:
    from coupon_life import CouponLifeEngine
    e = CouponLifeEngine()
    report = e.full_exam()
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcd_client import MCDClient  # noqa: E402
import roast  # noqa: E402

# ----------------------------------------------------------------------------
# 生命阶段定义
#
# 用词原则（重要）：
#   - 不用"死/临终/病危/墓志铭"等忌讳词，麦当劳是快乐场景，不该带阴间气
#   - 用"余额告急/今夜清零/已化灰"这类游戏化、可玩味、不犯忌的说法
#   - 核心叙事不是"我帮你抢救"，而是"你看，这东西要白白没了"
# ----------------------------------------------------------------------------

STAGE_SAFE = "safe"            # 💚 剩余 > 7 天    —— 充裕
STAGE_WATCH = "watch"          # 💛 3–7 天         —— 悠着点
STAGE_URGENT = "urgent"        # 🧡 24h – 3 天     —— 余额告急
STAGE_TONIGHT = "tonight"      # ❤️ < 24 小时      —— 今夜清零
STAGE_GONE = "gone"            # 🗑 已过期          —— 已化灰
STAGE_UNKNOWN = "unknown"      # ⚪ 无过期信息      —— 待查明

STAGE_META = {
    STAGE_SAFE:    {"label": "充裕",     "emoji": "💚", "order": 5, "actionable": False},
    STAGE_WATCH:   {"label": "悠着点",   "emoji": "💛", "order": 3, "actionable": True},
    STAGE_URGENT:  {"label": "余额告急", "emoji": "🧡", "order": 2, "actionable": True},
    STAGE_TONIGHT: {"label": "今夜清零", "emoji": "❤️", "order": 1, "actionable": True},
    STAGE_GONE:    {"label": "已化灰",   "emoji": "🗑", "order": 0, "actionable": False},
    STAGE_UNKNOWN: {"label": "待查明",   "emoji": "⚪", "order": 4, "actionable": True},
}

# 中文阶段名 -> 内部 key（兼容旧称呼与用户口语）
STAGE_ALIAS = {
    "健康": STAGE_SAFE, "充裕": STAGE_SAFE,
    "观察": STAGE_WATCH, "观察期": STAGE_WATCH, "悠着点": STAGE_WATCH,
    "病危": STAGE_URGENT, "余额告急": STAGE_URGENT, "告急": STAGE_URGENT,
    "临终": STAGE_TONIGHT, "今夜清零": STAGE_TONIGHT, "清零": STAGE_TONIGHT,
    "已死": STAGE_GONE, "已死亡": STAGE_GONE, "已化灰": STAGE_GONE, "化灰": STAGE_GONE,
    "未知": STAGE_UNKNOWN, "待查明": STAGE_UNKNOWN,
}

# 积分 -> 人民币的展示折算率（1 分 ≈ ¥0.1）。仅用于"你白扔了多少钱"的估算展示，
# 不作为任何交易依据。
POINT_RATE = 0.1


# ----------------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------------

def _parse_date_loose(value: Any) -> datetime | None:
    """尽力解析各种日期格式。失败返回 None，绝不猜测。

    额外支持 DEMO 数据集的相对日期占位符：
      @today            -> 今天 23:59:59
      @today 10:29:00   -> 今天 10:29:00
      @today+3          -> 3 天后 23:59:59
      @today+3 10:29:00 -> 3 天后 10:29:00
      @today-1          -> 1 天前
    """
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None

    # 相对日期占位符
    if s.startswith("@today"):
        m = re.match(r"@today([+-]\d+)?(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$", s)
        if m:
            base = datetime.now()
            if m.group(1):
                base = base + timedelta(days=int(m.group(1)))
            if m.group(2):
                return base.replace(hour=int(m.group(2)), minute=int(m.group(3)),
                                    second=int(m.group(4) or 0), microsecond=0)
            return base.replace(hour=23, minute=59, second=59, microsecond=0)

    fmts = [
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d",
        "%Y%m%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f",
    ]
    for f in fmts:
        try:
            return datetime.strptime(s, f)
        except ValueError:
            continue
    # 尝试从字符串里抠出 YYYY-MM-DD 或 YYYY/MM/DD
    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def _fmt_delta(td: timedelta) -> str:
    """把剩余时间格式化成人类可读。"""
    total = int(td.total_seconds())
    if total < 0:
        td = -td
        total = int(td.total_seconds())
        prefix = "已过期 "
    else:
        prefix = "剩余 "

    d, rem = divmod(total, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)

    if d > 0:
        body = f"{d} 天 {h} 小时" if h else f"{d} 天"
    elif h > 0:
        body = f"{h} 小时 {m} 分" if m else f"{h} 小时"
    else:
        body = f"{m} 分钟"
    return prefix + body


def _extract_expiry(coupon: dict[str, Any]) -> datetime | None:
    """从券对象里尽力提取过期时间。

    真实数据中，麦当劳券的关键字段是 `tradeEndDateTime`（如 "2026-10-09 23:59:59"），
    其次是 `tradeEndDate`。按可靠性从高到低尝试。
    """
    # 1) 精确到秒的结束时间（最可靠）
    dt = _parse_date_loose(coupon.get("tradeEndDateTime"))
    if dt:
        # 若只有日期没有时间，补齐到当天 23:59:59
        if dt.hour == 0 and dt.minute == 0 and dt.second == 0:
            dt = dt.replace(hour=23, minute=59, second=59)
        return dt
    # 2) 只有日期
    dt = _parse_date_loose(coupon.get("tradeEndDate"))
    if dt:
        return dt.replace(hour=23, minute=59, second=59)
    # 3) 其它可能的字段名
    for key in ("expireDate", "expireTime", "expiredTime", "validEndTime",
                "endTime", "validEnd", "expiryDate", "useEndTime",
                "effectiveEndTime", "deadline"):
        dt = _parse_date_loose(coupon.get(key))
        if dt:
            return dt
    return None


def _extract_value(coupon: dict[str, Any]) -> tuple[float, str]:
    """提取券面价值。返回 (数值, 展示文本)。"""
    di = coupon.get("discountInfo") or {}
    if isinstance(di, dict):
        raw = di.get("discountValue")
        sym = di.get("discountTypeText") or ""
        desc = di.get("discountDesc") or ""
        try:
            v = float(str(raw).replace(",", ""))
            if sym.strip() == "¥" or "折" not in str(desc):
                return v, f"¥{v:g}"
            return v, f"{v:g}{sym}"
        except (TypeError, ValueError):
            pass
    return 0.0, ""


def _extract_tags(coupon: dict[str, Any]) -> list[str]:
    """提取券上的官方标签，如「今日到期」「到店专用」。"""
    out = []
    for t in (coupon.get("tags") or []):
        if isinstance(t, dict) and t.get("label"):
            out.append(str(t["label"]))
    return out


def _extract_received_days(coupon: dict[str, Any]) -> int | None:
    """已领取天数。真实字段 `receivedDays`。"""
    try:
        v = coupon.get("receivedDays")
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _extract_time_window(coupon: dict[str, Any]) -> str:
    """可用时段，如 "10:30-23:59"。"""
    periods = coupon.get("tradeTimePeriods") or []
    if isinstance(periods, list) and periods:
        p = periods[0]
        if isinstance(p, dict):
            s = str(p.get("startTime") or "")[:5]
            e = str(p.get("endTime") or "")[:5]
            if s and e:
                return f"{s}–{e}"
    return ""


def _extract_name(coupon: dict[str, Any]) -> str:
    for key in ("title", "couponName", "name", "couponTitle", "subtitle"):
        v = coupon.get(key)
        if v:
            return str(v).strip()
    return "未命名优惠券"


def dead_count_of(coupons: list[dict[str, Any]]) -> int:
    """已化灰的券张数。"""
    return sum(1 for c in coupons if c.get("stage") == STAGE_GONE)


# ----------------------------------------------------------------------------
# 引擎
# ----------------------------------------------------------------------------

class CouponLifeEngine:
    """券生命周期引擎。"""

    def __init__(self, client: MCDClient | None = None, now: datetime | None = None):
        self.client = client or MCDClient()
        self.now = now or datetime.now()
        self._store_cache: list[dict[str, Any]] | None = None

    # -- 门店（用于抢救可行性判断）------------------------------------------

    def stores(self, city: str = "深圳") -> list[dict[str, Any]]:
        """拉取周边门店（缓存）。优先按城市搜，失败则回退收藏门店。"""
        if self._store_cache is None:
            for kwargs in (
                {"search_type": 2, "be_type": 1, "city": city, "keyword": ""},
                {"search_type": 1, "be_type": 1},
            ):
                try:
                    raw = self.client.nearby_stores(**kwargs)
                    found = self._dig_list(raw, ("data",), ("stores",))
                    if found:
                        self._store_cache = found
                        break
                except Exception:
                    continue
            if self._store_cache is None:
                self._store_cache = []
        return self._store_cache

    def rescue_feasibility(self) -> dict[str, Any] | None:
        """判断"今晚还来不来得及"——基于最近门店的营业时间。"""
        stores = self.stores()
        if not stores:
            return None
        s0 = stores[0]
        end = str(s0.get("businessEndTime") or "")
        start = str(s0.get("businessStartTime") or "")
        name = s0.get("storeName") or ""
        if not end or ":" not in end:
            return None
        try:
            hh, mm = (int(x) for x in end.split(":")[:2])
        except ValueError:
            return None
        close_at = self.now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        delta = close_at - self.now
        return {
            "store": name,
            "close_at": close_at,
            "open_range": f"{start}–{end}",
            "minutes_left": int(delta.total_seconds() // 60),
            "closed": delta.total_seconds() <= 0,
            "address": s0.get("address") or "",
        }

    # -- 数据获取 -----------------------------------------------------------

    def load_coupons(self) -> list[dict[str, Any]]:
        """拉取我的券。统一成标准结构。"""
        raw = self.client.my_coupons()
        items = self._dig_list(raw, ("data", "coupons"), ("coupons",), ("data",))
        out = []
        for c in items:
            if not isinstance(c, dict):
                continue
            value, value_text = _extract_value(c)
            out.append({
                "id": str(c.get("id") or c.get("couponId") or c.get("code") or ""),
                "code": str(c.get("code") or c.get("couponCode") or ""),
                "name": _extract_name(c),
                "expire": _extract_expiry(c),
                "value": value,
                "value_text": value_text,
                "tags": _extract_tags(c),
                "received_days": _extract_received_days(c),
                "time_window": _extract_time_window(c),
                "available_count": c.get("currentDayAvailableCount"),
                "total_count": c.get("totalCount"),
                "datetime_text": c.get("datetimeText") or "",
                # 券图：官方为该券配的产品图，最有食欲的素材（见 imgcache.py）
                "image": c.get("image") or c.get("bigImage") or "",
                "big_image": c.get("bigImage") or c.get("image") or "",
                "raw": c,
            })
        return out

    def load_account(self) -> dict[str, Any]:
        """拉取积分账户，统一成数字。"""
        raw = self.client.my_account()
        data = self._dig_dict(raw, ("data",), ("account",))
        def num(*keys: str) -> float:
            for k in keys:
                if k in data:
                    try:
                        return float(str(data[k]).replace(",", "") or 0)
                    except (TypeError, ValueError):
                        continue
            return 0.0
        return {
            "available": num("availablePoint", "available"),
            "accumulative": num("accumulativePoint", "total"),
            "expired": num("expiredPoint", "expired"),
            "monthly_expiring": num("currentMouthExpirePoint", "currentMonthExpirePoint"),
            "currency": data.get("currency") or "积分",
            "_demo": bool(raw.get("_demo")),
        }

    @staticmethod
    def _dig_list(raw: Any, *paths: tuple[str, ...]) -> list:
        """按路径集合尝试取 list。"""
        for path in paths:
            cur = raw
            ok = True
            for k in path:
                if isinstance(cur, dict) and k in cur:
                    cur = cur[k]
                else:
                    ok = False
                    break
            if ok and isinstance(cur, list):
                return cur
        # 兜底：如果 raw 本身是 list
        if isinstance(raw, list):
            return raw
        return []

    @staticmethod
    def _dig_dict(raw: Any, *paths: tuple[str, ...]) -> dict:
        for path in paths:
            cur = raw
            ok = True
            for k in path:
                if isinstance(cur, dict) and k in cur:
                    cur = cur[k]
                else:
                    ok = False
                    break
            if ok and isinstance(cur, dict):
                return cur
        if isinstance(raw, dict):
            return raw
        return {}

    # -- 生命周期分析 -------------------------------------------------------

    def classify(self, expire: datetime | None) -> tuple[str, timedelta | None]:
        """根据过期时间划分生命阶段。"""
        if expire is None:
            return STAGE_UNKNOWN, None
        delta = expire - self.now
        if delta.total_seconds() <= 0:
            return STAGE_GONE, delta
        days = delta.total_seconds() / 86400
        if days > 7:
            return STAGE_SAFE, delta
        if days > 3:
            return STAGE_WATCH, delta
        if delta.total_seconds() > 86400:
            return STAGE_URGENT, delta
        return STAGE_TONIGHT, delta

    def analyze_coupons(self) -> list[dict[str, Any]]:
        """给每张券打上生命周期标签，并按名字聚合识别重复券。"""
        coupons = self.load_coupons()

        # 按名字聚合：同名券可能有多张、且寿命可能不同（真实数据常见）
        groups: dict[str, list[dict[str, Any]]] = {}
        for c in coupons:
            groups.setdefault(c["name"], []).append(c)

        # 拟人台词用轮换分配：同一批券按顺序取不同句式，避免相邻两张说同一句话
        mono_idx = 0
        for name, group in groups.items():
            n = len(group)
            # 组内最紧迫的一张决定整组的紧急度
            stages = [self.classify(g["expire"])[0] for g in group]
            order = [STAGE_META[s]["order"] for s in stages]
            group_stage = stages[order.index(min(order))]
            expiries = [g["expire"] for g in group if g["expire"]]
            earliest = min(expiries) if expiries else None
            latest = max(expiries) if expiries else None

            for c in group:
                stage, delta = self.classify(c["expire"])
                c["stage"] = stage
                c["delta"] = delta
                c["stage_label"] = STAGE_META[stage]["label"]
                c["stage_emoji"] = STAGE_META[stage]["emoji"]
                c["life_text"] = _fmt_delta(delta) if delta is not None else "寿命未知"
                c["duplicate_count"] = n
                c["is_duplicate"] = n > 1
                c["group_stage"] = group_stage
                c["group_earliest"] = earliest
                c["group_latest"] = latest
                c["actionable"] = STAGE_META[stage]["actionable"]
                c["remaining_days"] = (delta.total_seconds() / 86400) if delta else None
                # 券的第一人称台词（轮换句式，保证同批券说法各异）
                c["monologue"] = roast.coupon_monologue(
                    c["name"], stage, delta,
                    days=c.get("received_days"),
                    value=c.get("value", 0),
                    variant=mono_idx,
                )
                mono_idx += 1

        return coupons

    # -- 薅券方案 -----------------------------------------------------------

    def rescue_plan(self, c: dict[str, Any]) -> str:
        """为一张券生成"薅它"的行动方案。

        结构：先损友式吐槽（roast.stage_roast）→ 行动细节 → 重复券警告 → 门店三线交叉。
        核心是让用户感到"不用它就白扔了"，但语气要像好朋友，不像家长。
        """
        stage = c["stage"]
        name = c["name"]
        dup = c["duplicate_count"]
        value_text = c.get("value_text") or ""
        window = c.get("time_window") or ""
        days = c.get("received_days")

        if stage == STAGE_GONE:
            return roast.stage_roast(stage)

        parts: list[str] = []

        # 开场吐槽：券卡上方已经展示过拟人台词时跳过，避免同一张卡说两遍同样的话
        if not c.get("monologue"):
            opening = roast.stage_roast(stage, c.get("delta"), seed=name)
            if opening:
                parts.append(opening)

        # 行动细节（券值换算只在值够大时才说，避免"¥9.9 也是一个麦旋风"刷屏）
        detail = []
        if window:
            detail.append(f"可用时段 **{window}**")
        if value_text:
            detail.append(f"券后价 **{value_text}**")
        if days is not None and days > 0:
            detail.append(f"在你手机里躺了 **{days} 天**")
        if detail:
            parts.append(" ｜ ".join(detail) + "。")

        # 省下的钱换算成实物：只有金额够一顿才有画面，小额不硬凑
        food = roast.money_to_food(c.get("value") or 0)
        lunch = roast.money_to_lunch(c.get("value") or 0)
        if lunch:
            parts.append(f"省下来的钱，{lunch}。")
        elif food and (c.get("value") or 0) >= 20:
            parts.append(f"省下来的钱，差不多是{food}。")

        # 重复券：直说会白扔几张
        if c["is_duplicate"]:
            line = roast.duplicate_roast(name, dup, c.get("group_earliest"), c.get("group_latest"))
            if line:
                parts.append("⚠️ " + line)

        # 门店三线交叉（券到期 × 门店营业 × 券可用时段）
        feas = self.rescue_feasibility()
        if feas:
            if stage == STAGE_TONIGHT:
                if feas["closed"]:
                    parts.append(f"🚪 最近的 {feas['store']} 已经打烊了（营业至 {feas['open_range']}）。"
                                 f"今晚大概率是保不住了 —— 这就是白扔的现场。")
                elif feas["minutes_left"] <= 90:
                    parts.append(f"🚪 **{feas['store']} 还有 {feas['minutes_left']} 分钟打烊** —— "
                                 f"这是今晚最后一次机会，现在出门还赶得上。")
                else:
                    parts.append(f"🚪 就近门店：{feas['store']}（营业至 {feas['open_range']}）")
            elif not feas["closed"]:
                parts.append(f"🚪 就近门店：{feas['store']}（营业至 {feas['open_range']}）")

        return "\n".join(parts)

    # -- 浪费博物馆 ---------------------------------------------------------

    def waste_museum(self, coupons: list[dict[str, Any]], account: dict[str, Any]) -> list[dict[str, str]]:
        """把已经白扔掉的东西摆出来，用损友式反问戳一下。

        原则：不写死亡、不写晦气词；换算成实物；落脚点是"你本来能吃到嘴里"。
        """
        out: list[dict[str, str]] = []

        # 1. 已经作废的积分
        if account.get("expired", 0) > 0:
            pts = account["expired"]
            cur = account.get("currency", "积分")
            out.append({
                "type": "points",
                "title": f"{pts:g} 分 {cur}（已作废）",
                "text": roast.points_waste_roast(pts, cur),
            })

        # 2. 本月即将到期的积分
        if account.get("monthly_expiring", 0) > 0:
            pts = account["monthly_expiring"]
            out.append({
                "type": "points-urgent",
                "title": f"{pts:g} 分 本月底到期（还来得及）",
                "text": roast.points_urgent_roast(pts, account.get("currency", "积分")),
            })

        # 3. 已经过期的券
        dead = [c for c in coupons if c["stage"] == STAGE_GONE]
        groups: dict[str, list[dict[str, Any]]] = {}
        for c in dead:
            groups.setdefault(c["name"], []).append(c)

        for name, group in groups.items():
            cnt = len(group)
            sample = group[0]
            if cnt > 1:
                text = (f"你囤了 {cnt} 张，一次消费只能核销一张 —— "
                        f"**{cnt - 1} 张直接白给**。")
            else:
                food = roast.money_to_food(sample.get("value") or 0)
                text = "领的那天你以为会用上。\n结果它一次都没被你点开。"
                if food:
                    text = f"它值{food}。\n" + text

            # 化灰的券也要开口说话 —— 它已经没机会了，那句自述最扎人
            mono = roast.coupon_monologue(
                name, STAGE_GONE,
                days=sample.get("received_days"),
                value=sample.get("value") or 0,
                seed=f"{name}|gone",
            )
            out.append({
                "type": "coupon",
                "title": f"{name}" + (f" ×{cnt}" if cnt > 1 else ""),
                "text": text,
                "monologue": mono,
                "image": sample.get("image") or sample.get("big_image") or "",
            })

        return out

    # -- 薅到率 / 白扔了多少 -------------------------------------------------

    def survival_report(self, coupons: list[dict[str, Any]], account: dict[str, Any]) -> dict[str, Any]:
        counts = {k: 0 for k in STAGE_META}
        for c in coupons:
            counts[c["stage"]] += 1
        total = len(coupons)
        dead = counts[STAGE_GONE]
        unknown = counts[STAGE_UNKNOWN]
        tonight = counts[STAGE_TONIGHT]

        # 「薅到了」= 剔除已化灰、寿命未知、今夜清零（今天还悬着的算没到手）
        grabbed = total - dead - unknown - tonight
        risk_total = total - unknown   # 有明确寿命的券
        grab_rate = (grabbed / risk_total * 100) if risk_total else 0.0

        live_value = sum(c.get("value", 0) for c in coupons if c["stage"] != STAGE_GONE)
        at_risk_value = sum(c.get("value", 0) for c in coupons
                            if c["stage"] in (STAGE_TONIGHT, STAGE_URGENT))
        dead_value = sum(c.get("value", 0) for c in coupons if c["stage"] == STAGE_GONE)

        dead_points = account.get("expired", 0)
        monthly_pts = account.get("monthly_expiring", 0)

        # 「薅到了」= 剔除已化灰、期限不明、今夜清零（今天还悬着的算没到手）
        grabbed = total - dead - unknown - tonight
        risk_total = total - unknown   # 有明确寿命的券
        coupon_grab_rate = (grabbed / risk_total * 100) if risk_total else 0.0

        # 薅到率必须把积分一起算进去 —— 否则会出现"券全用上了但积分白扔 416 分
        # 还显示 100%"这种自相矛盾的结论。
        # 口径：积分按"已作废 / (已作废 + 当前可用)"折算成一次"未薅到"。
        usable_pts = account.get("available", 0)
        pts_denom = dead_points + usable_pts
        if pts_denom > 0:
            pts_grab_rate = usable_pts / pts_denom * 100
            # 券和积分按张数加权（券权重 = 券张数，积分权重 = 1 份虚拟"积分资产"）
            if risk_total + (1 if pts_denom else 0) > 0:
                grab_rate = (coupon_grab_rate * risk_total + pts_grab_rate
                             * (1 if pts_denom else 0)) / (risk_total + (1 if pts_denom else 0))
            else:
                grab_rate = coupon_grab_rate
        else:
            grab_rate = coupon_grab_rate

        live_value = sum(c.get("value", 0) for c in coupons if c["stage"] != STAGE_GONE)
        at_risk_value = sum(c.get("value", 0) for c in coupons
                            if c["stage"] in (STAGE_TONIGHT, STAGE_URGENT))
        dead_value = sum(c.get("value", 0) for c in coupons if c["stage"] == STAGE_GONE)

        # 诊断里要把金额换算成实物，比干数字疼
        at_risk_food = roast.money_to_food(at_risk_value) if at_risk_value else ""
        dead_food = roast.money_to_food(dead_value) if dead_value else ""
        pts_food = roast.money_to_food(dead_points * POINT_RATE) if dead_points else ""

        if total == 0 and dead_points == 0:
            diag = "券包空的。没有在亏的钱，也没有即将亏的。"
        elif tonight > 0:
            tail = f"，等于{at_risk_food}的钱" if at_risk_food else ""
            diag = (f"**今晚有 {tonight} 张券要清零**（约 ¥{at_risk_value:g}{tail}）。"
                    f"你现在不动手，明天这笔就是白扔的。")
        elif counts[STAGE_URGENT] > 0:
            tail = f"（约 ¥{at_risk_value:g}，{at_risk_food}）" if at_risk_food else f"（约 ¥{at_risk_value:g}）"
            diag = (f"{counts[STAGE_URGENT]} 张券进入倒计时{tail}。"
                    f"拖过去，就是白送。")
        elif dead_count_of(coupons) > 0:
            tail = f"，折合{dead_food}" if dead_food else ""
            diag = (f"你手里已经有 {dead_count_of(coupons)} 张券白扔了"
                    f"（约 ¥{dead_value:g}{tail}）。券不会等你，积分也不会。")
        elif dead_points > 0:
            tail = f"，本来够换{pts_food}" if pts_food else ""
            diag = (f"券包看着还行，但 **{dead_points:g} 分积分已经白扔过一次了**{tail}。"
                    f"不会有人提醒你第二次。")
        elif monthly_pts > 0:
            tail = f"，够换{pts_food}" if pts_food else ""
            diag = (f"**{monthly_pts:g} 分积分本月底作废**{tail}。不换就是净亏。")
        elif unknown > 0:
            diag = (f"有 **{unknown} 张券没标到期时间** —— 查不到不等于用得上，"
                    f"它很可能在你没注意的某天就悄悄作废了。")
        else:
            diag = "券包状态不错。没有在亏的钱，也没有即将亏的。"

        return {
            "total": total,
            "alive": total - dead,
            "dead": dead,
            "unknown": unknown,
            "dying": tonight,
            "tonight": tonight,
            "secured": grabbed,
            "grabbed": grabbed,
            "counts": counts,
            "rescue_rate": grab_rate,
            "grab_rate": grab_rate,
            "coupon_grab_rate": coupon_grab_rate,
            "live_value": live_value,
            "at_risk_value": at_risk_value,
            "dead_value": dead_value,
            "dead_points": dead_points,
            "monthly_expiring_points": monthly_pts,
            "diagnosis": diag,
        }

    # -- 汇总 ---------------------------------------------------------------

    def full_exam(self) -> dict[str, Any]:
        """完整券包体检。"""
        coupons = self.analyze_coupons()
        account = self.load_account()
        plans = {c["id"] or c["name"]: self.rescue_plan(c) for c in coupons if c["actionable"]}
        for c in coupons:
            c["plan"] = plans.get(c["id"] or c["name"], "")

        # 按紧迫度排序（今夜清零 > 余额告急 > 悠着点 > 待查明 > 充裕 > 已化灰）
        coupons_sorted = sorted(coupons, key=lambda x: (STAGE_META[x["stage"]]["order"],
                                                        x["remaining_days"] if x["remaining_days"] is not None else 999))

        # 行动清单：按券名去重，每组只保留最紧迫的一张（避免同名券刷屏）
        urgent_raw = [c for c in coupons_sorted
                      if c["stage"] in (STAGE_TONIGHT, STAGE_URGENT, STAGE_WATCH)]
        seen: set[str] = set()
        urgent: list[dict[str, Any]] = []
        for c in urgent_raw:
            if c["name"] in seen:
                continue
            seen.add(c["name"])
            urgent.append(c)

        survival = self.survival_report(coupons, account)
        waste = self.waste_museum(coupons, account)

        # 白扔总额（券面价值 + 已作废积分折算），口径统一在这里算
        waste_amount = survival["dead_value"] + survival["dead_points"] * POINT_RATE

        # 一句话总结 + 副标题（损友式）
        head = roast.headline(waste_amount, dead_points=survival["dead_points"])
        head_sub = roast.headline_sub(
            waste_amount,
            dead_points=survival["dead_points"],
            dead_coupons=survival["dead"],
        )

        return {
            "mode": self.client.mode,
            "exam_time": self.now.strftime("%Y-%m-%d %H:%M:%S"),
            "account": account,
            "coupons": coupons_sorted,
            "urgent": urgent,
            "waste": waste,
            "epitaphs": waste,  # 向后兼容旧调用
            "survival": survival,
            "waste_amount": waste_amount,
            "headline": head,
            "headline_sub": head_sub,
        }


# ----------------------------------------------------------------------------
# 渲染（终端可读）
# ----------------------------------------------------------------------------

def render_report(rep: dict[str, Any]) -> str:
    L: list[str] = []
    s = rep["survival"]
    acc = rep["account"]
    mode = rep["mode"]

    L.append("=" * 56)
    L.append(f"  抢救麦券大作战 · 券包体检    [{mode}]")
    L.append(f"  体检时间：{rep['exam_time']}")
    L.append("=" * 56)
    L.append("")

    # 顶部：扎心总结
    if rep.get("waste_amount"):
        L.append(f"💸 {rep.get('headline') or f'你已经白扔了 ¥{rep['waste_amount']:.1f}'}")
        if rep.get("headline_sub"):
            for line in rep["headline_sub"].split("\n"):
                L.append(f"   {line}")
        L.append("")

    # 行动清单
    urgent = rep["urgent"]
    if urgent:
        L.append("🚨 今日行动清单（按紧迫度）")
        L.append("-" * 56)
        for i, c in enumerate(urgent, 1):
            badge = "  ".join(c.get("tags") or [])
            L.append(f"{i}. {c['stage_emoji']} {c['name']}"
                     + (f"   🏷 {badge}" if badge else ""))
            if c["is_duplicate"]:
                L.append(f"   ⚠️ 重复：共 {c['duplicate_count']} 张")
            L.append(f"   阶段：{c['stage_label']}    {c['life_text']}")
            for line in (c.get("plan") or "").split("\n"):
                L.append(f"   → {line}")
            L.append("")
    else:
        L.append("✅ 今日行动清单：空。没有券需要你现在动手。")
        L.append("")

    # 全部券一览（按券名聚合，每组一行）
    L.append("📋 券包全览")
    L.append("-" * 56)
    groups: dict[str, list[dict[str, Any]]] = {}
    for c in rep["coupons"]:
        groups.setdefault(c["name"], []).append(c)
    # 组按最紧迫顺序排
    for name, group in sorted(
        groups.items(),
        key=lambda kv: min(STAGE_META[x["stage"]]["order"] for x in kv[1]),
    ):
        g = group[0]
        n = len(group)
        cnt_text = f"  ×{n}" if n > 1 else ""
        val = f"  {g['value_text']}" if g.get("value_text") else ""
        if n > 1 and g.get("group_earliest") and g.get("group_latest"):
            span = (f"{g['group_earliest'].strftime('%m-%d')}~{g['group_latest'].strftime('%m-%d')}")
            life = f"{g['stage_emoji']} {g['stage_label']}，到期 {span}"
        else:
            life = f"{g['stage_emoji']} {g['life_text']}"
        L.append(f"  {name}{cnt_text}{val}   [{life}]")
    L.append("")

    # 浪费博物馆
    waste = rep.get("waste") or rep.get("epitaphs") or []
    if waste:
        L.append("🗑 浪费博物馆（这些已经换不回来了）")
        L.append("-" * 56)
        for e in waste:
            L.append(f"  「{e['title']}」")
            for line in e["text"].split("\n"):
                L.append(f"   {line}")
            L.append("")

    # 券包总览
    L.append("📊 券包总览")
    L.append("-" * 56)
    L.append(f"  券包总数：{s['total']}")
    cnt = s["counts"]
    L.append(f"  还能用：{s['alive']}   已化灰：{s['dead']}   期限不明：{s['unknown']}")
    for k, meta in STAGE_META.items():
        if cnt.get(k):
            L.append(f"    {meta['emoji']} {meta['label']}：{cnt[k]}")
    if s.get("live_value"):
        L.append(f"  券面总价值：约 ¥{s['live_value']:g}（还在，但未必会用掉）")
    if s.get("at_risk_value"):
        L.append(f"  🔥 正在倒计时的价值：约 ¥{s['at_risk_value']:g}（余额告急 + 今夜清零）")
    L.append(f"  麦享会积分：{acc['available']:g}   （累计 {acc['accumulative']:g}）")
    if acc["expired"]:
        L.append(f"  💸 已作废积分：{acc['expired']:g}（≈ ¥{acc['expired'] * POINT_RATE:.1f}）")
    if acc["monthly_expiring"]:
        L.append(f"  ⏰ 本月将作废积分：{acc['monthly_expiring']:g}")
    L.append(f"  薅到率：{s['grab_rate']:.1f}%")
    L.append("")
    L.append(f"  诊断：{s['diagnosis']}")
    L.append("")
    return "\n".join(L)


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="麦券生命周期引擎 · 券包体检")
    p.add_argument("--mode", choices=["LIVE", "DEMO"])
    p.add_argument("--json", action="store_true", help="输出 JSON 而非报告")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("exam", help="券包体检")
    sub.add_parser("waste", help="只看浪费博物馆")
    sub.add_parser("epitaph", help="（旧名）只看浪费博物馆")

    args = p.parse_args(argv)
    cmd = args.cmd or "exam"

    client = MCDClient(force_mode=args.mode)
    engine = CouponLifeEngine(client=client)
    rep = engine.full_exam()

    if args.json:
        out = {
            "mode": rep["mode"], "exam_time": rep["exam_time"],
            "account": rep["account"], "survival": rep["survival"],
            "waste_amount": rep["waste_amount"],
            "waste": rep["waste"],
            "urgent": [{"name": c["name"], "stage": c["stage"], "life": c["life_text"],
                        "duplicate": c["duplicate_count"], "plan": c.get("plan")}
                       for c in rep["urgent"]],
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if cmd in ("waste", "epitaph"):
        waste = rep["waste"]
        for e in waste:
            print(f"「{e['title']}」")
            print(e["text"])
            print()
        if not waste:
            print("浪费博物馆是空的。目前没有白扔掉的东西。")
        else:
            print(f"—— 以上合计，你白扔了约 ¥{rep['waste_amount']:.1f}。")
        return 0

    print(render_report(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
