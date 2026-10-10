#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
麦麦浪费账本生成器
==================

把券包体检结果 + 本地累积记账，渲染成一份《麦麦浪费账本》HTML。

这不是"我帮你抢救"的乐观报告，而是一张账单：
  - 顶上一个大数字 + 金拱门：你已经白扔了多少钱
  - 中间三张周期卡：本周 / 本月 / 今年
  - 下面列出还能救的（带产品图、倒计时、券的第一人称自述）
  - 最后是浪费博物馆：那些已经换不回来的东西

视觉基调：麦当劳红黄 + 金拱门，明亮、food-friendly。刻意不用黑色/墓碑
元素 —— 吃麦当劳是快乐的事，提醒你省钱也该是快乐的。

图片全部内联为 base64（见 imgcache.py），单文件自包含，断网也能看。

用法:
    python settle_card.py                      # 拉真实券包并生成
    python settle_card.py --demo               # 用离线数据生成
    python settle_card.py --out 浪费账本.html
    python settle_card.py --no-images          # 不内联产品图（体积最小）
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcd_client import MCDClient                # noqa: E402
from coupon_life import CouponLifeEngine, POINT_RATE  # noqa: E402
import ledger as ledger_mod                     # noqa: E402
import roast                                    # noqa: E402
import imgcache                                 # noqa: E402

TEMPLATE = Path(__file__).resolve().parent.parent / "assets" / "settlement_card_template.html"

# 各阶段在生命条上显示的相对长度（今夜清零最短、充裕最长）
BAR_PCT = {
    "tonight": 8, "urgent": 26, "watch": 55, "unknown": 45,
    "safe": 88, "gone": 0,
}

# 浪费博物馆每条配一句补刀
_WASTE_NOTE = {
    "points": "积分不会提醒你它在老去，它只会消失。",
    "points-urgent": "还来得及换。你确定要等到它变成上一条吗？",
    "coupon": "券没了，钱也没省下。",
}


def _esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""))


def _md_to_html(text: str) -> str:
    """极简 markdown：**加粗** 与换行。"""
    t = _esc(text)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    return t.replace("\n", "<br>")


# ----------------------------------------------------------------------------
# 金拱门（Golden Arches）
#
# 纯 SVG 矢量，任意尺寸清晰。用 currentColor 描边，颜色交给外层 CSS，
# 所以同一个组件能用在红底（白拱）和浅底（金拱）两种场景。
# ----------------------------------------------------------------------------

def arches_svg(cls: str = "") -> str:
    klass = f"arches {cls}".strip()
    return (
        f'<svg class="{klass}" viewBox="0 0 200 170" '
        f'xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false">'
        f'<path d="M10 190 Q55 -150 100 190" stroke="currentColor" '
        f'stroke-width="28" fill="none"/>'
        f'<path d="M100 190 Q145 -150 190 190" stroke="currentColor" '
        f'stroke-width="28" fill="none"/>'
        f'</svg>'
    )


# ----------------------------------------------------------------------------
# 区块构建
# ----------------------------------------------------------------------------

def _photo_block(uri: str, alt: str, cls: str = "tk-photo") -> str:
    """券的产品图。没有图就返回一个占位块，由 CSS 自适应。"""
    if not uri:
        return f'<div class="{cls} {cls}--empty"><span>🍟</span></div>'
    return (f'<div class="{cls}">'
            f'<img src="{uri}" alt="{_esc(alt)}" loading="lazy">'
            f'</div>')


def build_urgent_html(coupons: list[dict[str, Any]], use_images: bool = True) -> str:
    if not coupons:
        return ('<div class="ticket empty" style="text-align:center;padding:22px;">'
                '<div style="font-size:13px;font-weight:800;color:#7A6A55;">'
                '暂时没有要你现在动手的券</div>'
                '<div style="font-size:11.5px;color:#A79C8B;margin-top:6px;">'
                '不过别高兴太早 —— 新的券每天都在往你包里塞</div></div>')

    cards = []
    for c in coupons:
        stage = c["stage"]
        cls = stage if stage in ("tonight", "urgent", "watch", "unknown") else "unknown"
        emoji = c.get("stage_emoji", "⚪")
        name = _esc(c["name"])
        val = _esc(c.get("value_text") or "")
        n = c.get("duplicate_count", 1)

        count_badge = f'<span class="tk-count">共 {n} 张</span>' if n > 1 else ""
        val_html = f'<span class="tk-val">{val}</span>' if val else ""

        life = c.get("life_text", "")
        life_cls = "hot" if stage in ("tonight", "urgent") else ""
        bar = BAR_PCT.get(stage, 50)

        # 券的第一人称自述
        mono = c.get("monologue") or ""
        mono_html = (f'<div class="tk-mono">{_esc(mono)}</div>') if mono else ""

        plan_html = _md_to_html(c.get("plan") or "")

        uri = imgcache.coupon_image(c) if use_images else ""
        photo = _photo_block(uri, c["name"])

        cards.append(f"""
        <div class="ticket {cls}">
          {photo}
          <div class="tk-body">
            <div class="tk-top">
              <span class="tk-emoji">{emoji}</span>
              <span class="tk-name">{name}</span>
              {count_badge}
              {val_html}
            </div>
            <div class="life-bar"><div class="life-fill {cls}" style="width:{bar}%"></div></div>
            <div class="tk-life {life_cls}">{_esc(c.get('stage_label',''))} · {_esc(life)}</div>
            {mono_html}
            <div class="tk-plan">{plan_html}</div>
          </div>
        </div>""")
    return "\n".join(cards)


def build_waste_html(waste: list[dict[str, str]], use_images: bool = True) -> str:
    if not waste:
        return ('<div class="waste-card" style="text-align:center;padding:26px 24px;">'
                '<div class="waste-text" style="color:#7A6A55;">'
                '这一栏还是空的。<br>很好 —— 别让下次体检时它变满。</div></div>')

    out = []
    for e in waste:
        t = _esc(e["title"])
        body = _esc(e["text"]).replace("\n", "<br>")
        note = _esc(_WASTE_NOTE.get(e.get("type", ""), ""))
        mono = e.get("monologue") or ""
        mono_html = f'<div class="waste-mono">{_esc(mono)}</div>' if mono else ""

        # 只有券才有产品图。积分条目不放图 —— 用不相关的图标凑数反而廉价。
        photo = ""
        if e.get("type") == "coupon":
            uri = imgcache.data_uri(e["image"]) if (use_images and e.get("image")) else ""
            photo = _photo_block(uri, e["title"], cls="waste-photo")

        out.append(f"""
        <div class="waste-card">
          {photo}
          <div class="waste-body">
            <div class="waste-title">{t}</div>
            <div class="waste-text">{body}</div>
            {mono_html}
            <div class="waste-note">{note}</div>
          </div>
        </div>""")
    return "\n".join(out)


def build_period_html(stats: dict[str, dict[str, Any]]) -> str:
    """周 / 月 / 年三张卡，每张给一句损友式评价。"""
    cards = []
    icons = {"week": "📅", "month": "🗓", "year": "📈"}
    for p in ("week", "month", "year"):
        s = stats[p]
        label = s["label"]
        icon = icons[p]
        if not s.get("has_data"):
            cards.append(f"""
        <div class="period-card">
          <div class="pc-head">{icon} {label}</div>
          <div class="pc-empty">还没有记账数据<br><span>每天跑一次体检，这里就会开始累积</span></div>
        </div>""")
            continue

        food = roast.money_to_food(s["wasted_total"]) if s["wasted_total"] > 0 else ""
        hot = "hot" if s["wasted_total"] > 0 else ""
        range_text = f"{s['since']} ~ {s['until']}" if s["since"] != s["until"] else s["since"]
        gone_names = ""
        if s.get("gone_coupons"):
            names = "、".join(g["name"] for g in s["gone_coupons"][:3])
            more = f" 等 {len(s['gone_coupons'])} 张" if len(s["gone_coupons"]) > 3 else ""
            gone_names = f'<div class="pc-gone">🗑 {_esc(names)}{more}</div>'

        food_html = f'<div class="pc-food">≈ {_esc(food)}</div>' if food else ""

        cards.append(f"""
        <div class="period-card">
          <div class="pc-head">{icon} {label}<span class="pc-range">{_esc(range_text)}</span></div>
          <div class="pc-waste {hot}">
            <span class="pc-waste-label">白扔</span>
            <span class="pc-waste-num">¥{s['wasted_total']:g}</span>
          </div>
          {food_html}
          <div class="pc-rows">
            <div class="pc-row"><span>体检</span><b>{s['checks']} 次</b></div>
            <div class="pc-row"><span>券包存量</span><b>{s['coupon_total']} 张</b></div>
            <div class="pc-row"><span>薅到率</span><b class="{'good' if s['grabbed_rate'] >= 80 else 'bad'}">{s['grabbed_rate']}%</b></div>
          </div>
          {gone_names}
        </div>""")
    return "\n".join(cards)


# ----------------------------------------------------------------------------
# 图片预热：先把会用到的图并发下载/读缓存，后面取就是内存级
# ----------------------------------------------------------------------------

def _warm_images(rep: dict[str, Any], use_images: bool,
                 stats: dict[str, dict[str, Any]] | None = None) -> int:
    if not use_images:
        return 0
    urls: list[str] = []
    for c in rep.get("urgent", []):
        u = c.get("image") or c.get("big_image")
        if u:
            urls.append(u)
    for e in rep.get("waste", []):
        if e.get("image"):
            urls.append(e["image"])
    for g in (stats or {}).get("year", {}).get("gone_coupons", []):
        if g.get("image"):
            urls.append(g["image"])
    return imgcache.warm(urls)


# ----------------------------------------------------------------------------
# 渲染
# ----------------------------------------------------------------------------

def render(rep: dict[str, Any], stats: dict[str, dict[str, Any]] | None = None,
           use_images: bool = True) -> str:
    if not TEMPLATE.is_file():
        raise FileNotFoundError(f"模板缺失: {TEMPLATE}")
    tpl = TEMPLATE.read_text(encoding="utf-8")

    s = rep["survival"]
    acc = rep["account"]
    mode = rep.get("mode", "DEMO")
    waste_amount = rep.get("waste_amount", 0)
    stats = stats or ledger_mod.all_period_stats()

    # 主数字 = 今年白扔总额（口径统一到账本）
    year_waste = stats.get("year", {}).get("wasted_total", waste_amount)
    year_food = roast.money_to_food(year_waste)
    year_lunch = roast.money_to_lunch(year_waste)

    # 浪费博物馆 = 当前券包里已过期的券 + 本地账本里"消失了"的券。
    # 后者更重要：麦当劳券包接口只返回当前还在的券，过期的会被静默移除，
    # 所以真正的白扔只能靠跨快照对比发现（见 ledger._detect_gone）。
    waste = list(rep.get("waste", []))
    known = {w.get("title", "").split(" ×")[0] for w in waste}
    for g in stats.get("year", {}).get("gone_coupons", []):
        name = g.get("name") or ""
        if not name or name in known:
            continue
        known.add(name)
        waste.append({
            "type": "coupon",
            "title": name,
            "text": (f"最后一次见到它，是 {g.get('last_seen')}。\n"
                     f"之后它就再也没出现过 —— 已经作废了。"),
            "monologue": roast.coupon_monologue(
                name, "gone", value=g.get("value") or 0, seed=f"{name}|gone"),
            "image": g.get("image") or "",
        })

    repl = {
        "{{DATE}}": _esc(rep.get("exam_time", "")),
        "{{MODE}}": _esc(mode),
        "{{MODE_CLASS}}": "live" if str(mode).upper() == "LIVE" else "demo",
        "{{ARCHES_HERO}}": arches_svg("arches--hero"),
        "{{ARCHES_SMALL}}": arches_svg("arches--small"),
        "{{DIAGNOSIS}}": _md_to_html(s.get("diagnosis", "")),
        "{{HEADLINE}}": f"{year_waste:g}",
        "{{HEADLINE_FOOD}}": _esc(year_lunch or (f"折合{year_food}" if year_food else "")),
        "{{HEADLINE_SUB}}": _esc(rep.get("headline_sub", "")).replace("\n", "<br>"),
        "{{YEAR_WASTE}}": f"{year_waste:g}",
        "{{WEEK_WASTE}}": f"{stats.get('week', {}).get('wasted_total', 0):g}",
        "{{MONTH_WASTE}}": f"{stats.get('month', {}).get('wasted_total', 0):g}",
        "{{URGENT_COUNT}}": str(len(rep.get("urgent", []))),
        "{{URGENT_HTML}}": build_urgent_html(rep.get("urgent", []), use_images),
        "{{WASTE_HTML}}": build_waste_html(waste, use_images),
        "{{PERIOD_HTML}}": build_period_html(stats),
        "{{TOTAL}}": str(s.get("total", 0)),
        "{{TONIGHT}}": str(s.get("tonight", s.get("dying", 0))),
        "{{GRAB_RATE}}": f"{s.get('grab_rate', 0):.0f}",
        "{{LIVE_VALUE}}": f"{s.get('live_value', 0):g}",
        "{{AT_RISK}}": f"{s.get('at_risk_value', 0):g}",
        "{{DEAD_POINTS}}": f"{s.get('dead_points', 0):g}",
        "{{DEAD_POINTS_VALUE}}": f"{s.get('dead_points', 0) * POINT_RATE:g}",
        "{{AVAILABLE_POINTS}}": f"{acc.get('available', 0):g}",
    }
    for k, v in repl.items():
        tpl = tpl.replace(k, v)
    return tpl


def main() -> int:
    p = argparse.ArgumentParser(description="生成麦麦浪费账本")
    p.add_argument("--demo", action="store_true", help="强制使用离线数据")
    p.add_argument("--out", default="麦麦浪费账本.html")
    p.add_argument("--no-record", action="store_true", help="不写入账本")
    p.add_argument("--no-images", action="store_true", help="不内联产品图")
    p.add_argument("--ledger", help="指定账本文件（如 assets/ledger_sample.json）")
    p.add_argument("--sample-ledger", action="store_true",
                   help="用内置示例账本，演示周期统计与化灰效果")
    args = p.parse_args()

    if args.sample_ledger and not args.ledger:
        args.ledger = str(Path(__file__).resolve().parent.parent
                          / "assets" / "ledger_sample.json")
    if args.ledger:
        used = ledger_mod.use_ledger(args.ledger)
        print(f"📒 使用账本：{used}")

    client = MCDClient(force_mode="DEMO" if args.demo else None)
    engine = CouponLifeEngine(client=client)
    rep = engine.full_exam()

    use_images = not args.no_images

    # 顺手记一笔账，让周期统计有数据（要在读 stats 之前）
    if not args.no_record:
        try:
            ledger_mod.record_snapshot(rep)
        except Exception as exc:   # noqa: BLE001
            print(f"⚠️ 记账失败（不影响账本生成）：{exc}")

    stats = ledger_mod.all_period_stats()
    got = _warm_images(rep, use_images, stats)

    htmlout = render(rep, stats, use_images=use_images)
    out = Path(args.out).expanduser().resolve()
    out.write_text(htmlout, encoding="utf-8")

    s = rep["survival"]
    year_waste = stats.get("year", {}).get("wasted_total", rep.get("waste_amount", 0))
    size_kb = out.stat().st_size / 1024
    print(f"✅ 浪费账本已生成: {out}")
    print(f"   模式 {rep.get('mode')} ｜ 券包 {s['total']} 张 ｜ "
          f"今夜清零 {s.get('tonight', s.get('dying', 0))} 张 ｜ "
          f"薅到率 {s.get('grab_rate', 0):.0f}% ｜ 今年白扔 ¥{year_waste:g}")
    print(f"   产品图 {got} 张 ｜ 文件 {size_kb:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
