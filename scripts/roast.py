#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
损友话术引擎
============

把冷冰冰的数字翻译成"损友会怎么跟你说"。

设计原则（重要）：
  1. **关注点在失去，不在拥有。** 不说"你有 14 张券"，说"你已经让 14 张券在你手机里
     躺到过期"。同一份数据，主语换一下，痛感完全不同。
  2. **损友式调侃，不刻薄。** 像好朋友损你一句，笑完还愿意听。绝不写"你真蠢"
     "你太浪费了"这种指责——用户会关掉页面。
  3. **换算成实物。** ¥41.6 没感觉，"一杯中薯 + 一个麦旋风"有感觉。
  4. **精确到分钟。** "还剩 2 小时"不如"1 小时 59 分后它就从你的世界消失了"。
  5. **不用晦气词。** 不写死/临终/病危/墓志铭。用"化灰/清零/白扔/白给"。

这个模块只负责"怎么说"，不负责"算什么"。所有数字由 coupon_life / ledger 传入。
"""

from __future__ import annotations

import random
from typing import Any

# ----------------------------------------------------------------------------
# 食物换算表：把元换算成"你能吃到嘴里的东西"
#
# 用真实菜单价格（query-meals 实测）。顺序按"单份价格从低到高"，方便就近取。
# 第二项是"量词 + 名词"，不带"一"，方便拼数量。
# ----------------------------------------------------------------------------

FOOD_FOR_MONEY = [
    (4.0,    "份", "小薯"),
    (5.0,    "根", "麦炫酷"),
    (7.0,    "份", "中薯"),
    (9.9,    "个", "麦旋风"),
    (11.0,   "份", "麦乐鸡"),
    (13.0,   "个", "双层吉士汉堡"),
    (15.5,   "杯", "中杯拿铁"),
    (21.0,   "个", "麦辣鸡腿堡"),
    (25.0,   "个", "巨无霸"),
    (29.9,   "份", "厚松饼猪柳蛋套餐"),
    (36.0,   "套", "中薯+可乐"),
    (45.0,   "套", "巨无霸套餐"),
    (60.0,   "份", "早餐套餐"),
    (80.0,   "顿", "双人午餐"),
]

_CN_NUM = {1: "一", 2: "两", 3: "三", 4: "四", 5: "五",
           6: "六", 7: "七", 8: "八", 9: "九", 10: "十"}

# 常用组合锚点：比"3 个麦旋风"更有画面
_COMBO_ANCHORS = [
    (13.0,  "一个双层吉士汉堡"),
    (19.8,  "两个麦旋风"),
    (21.0,  "一个麦辣鸡腿堡"),
    (25.0,  "一个巨无霸"),
    (29.9,  "一份厚松饼猪柳蛋套餐"),
    (35.0,  "一顿麦当劳正餐"),
    (45.0,  "一个巨无霸套餐"),
    (55.0,  "一份早餐套餐 + 一个麦旋风"),
    (68.0,  "两份早餐套餐"),
    (80.0,  "一顿双人午餐"),
    (120.0, "三顿麦当劳"),
    (200.0, "五顿麦当劳"),
]


def money_to_food(amount: float) -> str:
    """把金额换算成最贴近、最有画面的实物描述。

    策略：取"不超过该金额的最大锚点"。宁可少说，不夸大。
    """
    if amount <= 0:
        return ""
    # 1) 优先用组合锚点（更有画面）
    pick = None
    for price, name in _COMBO_ANCHORS:
        if amount >= price:
            pick = name
    if pick:
        return pick

    # 2) 小额用单品 + 数量
    unit_pick = None
    for price, unit, name in FOOD_FOR_MONEY:
        if amount >= price:
            unit_pick = (price, unit, name)
    if unit_pick is None:
        return ""
    price, unit, name = unit_pick
    times = int(amount // price)
    if times <= 1:
        return f"一{unit}{name}"
    return f"{_CN_NUM.get(times, str(times))}{unit}{name}"


def money_to_lunch(amount: float) -> str:
    """另一层锚点：换算成"几顿饭"。只在金额够一顿时才用。"""
    if amount <= 0:
        return ""
    if amount < 30:
        return ""
    if amount < 45:
        return "差不多是一顿麦当劳正餐"
    meals = amount / 35.0
    if meals < 1.6:
        return "够吃一顿还有剩"
    return f"够你吃 {meals:.1f} 顿麦当劳"


# ----------------------------------------------------------------------------
# 时间格式化：精确到分钟，且带"消失感"
# ----------------------------------------------------------------------------

def countdown_phrase(td) -> str:
    """把 timedelta 变成纯时长文本，如 "1 小时 59 分"、"2 天 12 小时"。"""
    if td is None:
        return ""
    total = int(td.total_seconds())
    if total < 0:
        total = -total
    d, rem = divmod(total, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)

    if d > 0:
        return f"{d} 天 {h} 小时" if h else f"{d} 天"
    if h > 0:
        return f"{h} 小时 {m} 分" if m else f"{h} 小时"
    if m > 0:
        return f"{m} 分钟"
    return "不到 1 分钟"


def vanish_phrase(td) -> str:
    """带"消失"意象的倒计时，如 "1 小时 59 分后"。"""
    c = countdown_phrase(td)
    return f"{c}后" if c else ""


# ----------------------------------------------------------------------------
# 损友式吐槽库：同一件事给几个说法，随机挑一个，避免每次体检都一个腔调
# ----------------------------------------------------------------------------

_TONIGHT_LINES = [
    "今晚 24:00 准时清零，只剩 {t}。",
    "只剩 {t} —— 这是它留在你手机里的最后时间。",
    "倒计时 {t}，过了今晚它连个影子都不剩。",
    "它今晚就走。留给你的时间：{t}。",
]

_URGENT_LINES = [
    "只剩 {t}。这券已经开始倒计时了，别等它变成遗憾。",
    "{t}的余量。你现在还有点主动权，再拖就没了。",
    "余额告急：只剩 {t}。要么现在安排，要么看着它走。",
    "还剩 {t}。够你安排，也够你忘掉它。",
]

_WATCH_LINES = [
    "还剩 {t}，目前还算从容。但你知道自己上次是什么时候用的。",
    "只有 {t}了。不着急，但别等到只剩几小时才想起来。",
    "还有 {t}。现在花 5 分钟安排掉，比最后一天赶场强。",
]

_UNKNOWN_LINES = [
    "券面没写什么时候到期 —— 这不叫安全，这叫不知道炸弹什么时候响。",
    "它没说什么时候作废。你猜是明天，还是下个月？赌输的是你。",
    "无到期信息。别赌，3 天内用掉它，把这悬念终结了。",
]

_GONE_LINES = [
    "已经清零了。这次没法补救，只能记住：券不会等你。",
    "它没了。你甚至不记得上次点开它是什么时候。",
    "作废。这钱你本来可以吃进嘴里的。",
]

_SAFE_LINES = [
    "余量充足，暂时不用管它。",
    "时间还早，先记着有这张就行。",
]

_STAGE_LINES = {
    "tonight": _TONIGHT_LINES,
    "urgent": _URGENT_LINES,
    "watch": _WATCH_LINES,
    "unknown": _UNKNOWN_LINES,
    "gone": _GONE_LINES,
    "safe": _SAFE_LINES,
}


def stage_roast(stage: str, td=None, seed: str | None = None) -> str:
    """给某个阶段的券来一句损友式吐槽。

    seed 用于稳定随机（同一次体检同一条券不要每次刷新都变），
    传券名即可。
    """
    lines = _STAGE_LINES.get(stage)
    if not lines:
        return ""
    if seed:
        rng = random.Random(seed)
        line = rng.choice(lines)
    else:
        line = random.choice(lines)
    return line.format(t=countdown_phrase(td) or "没多久")


# ----------------------------------------------------------------------------
# 重复券吐槽：这是最扎心的场景，要单独下狠手
# ----------------------------------------------------------------------------

def duplicate_roast(name: str, count: int, earliest=None, latest=None) -> str:
    """同名券囤了 N 张。核心逻辑：一次消费只核销一张，多出来的必白给。"""
    if count <= 1:
        return ""
    wasted = count - 1
    lines = []
    if earliest and latest and earliest.date() != latest.date():
        lines.append(
            f"这一张你**囤了 {count} 份**，到期日还各不一样"
            f"（{earliest.strftime('%m-%d')} 到 {latest.strftime('%m-%d')}）。"
            f"一次消费只核销一张 —— 想不白扔，你得跑 **{count} 趟**。"
        )
        lines.append(
            f"你手里有 {count} 张，但一次只能吃一份。"
            f"别的先不说，**这 {wasted} 张大概率是白给**。"
        )
    else:
        lines.append(
            f"同一张券你囤了 {count} 份，到期日还一样。"
            f"一次消费只核销一张 —— **你有 {wasted} 张注定白扔**。"
        )
        lines.append(
            f"{count} 张一模一样的券，睡在你的券包里。"
            f"它们不会互相抵消，只会一起过期。**白扔 {wasted} 张**。"
        )
    rng = random.Random(f"{name}{count}")
    return rng.choice(lines)


# ----------------------------------------------------------------------------
# 积分吐槽：积分最容易被忽略，要强调"它自己会消失"
# ----------------------------------------------------------------------------

def points_waste_roast(points: float, currency: str = "积分") -> str:
    food = money_to_food(points * 0.1)
    base = f"这 {points:g} 分{currency}，"
    if food:
        base += f"换{food}绰绰有余。"
    else:
        base += "本来能换点东西。"
    endings = [
        "你当时没换。现在它没了。",
        "你没当回事，它就自己走了。",
        "它没等你，直接清零了。",
    ]
    return base + random.Random(str(points)).choice(endings)


def points_urgent_roast(points: float, currency: str = "积分") -> str:
    food = money_to_food(points * 0.1)
    base = f"这 {points:g} 分{currency}，本月底作废。"
    if food:
        base += f"\n现在还换得到{food}。"
    return base + "\n你确定要等到它变成上一栏那种吗？"


# ----------------------------------------------------------------------------
# 顶部账单：整个产品最有冲击力的一句话
# ----------------------------------------------------------------------------

def headline(wasted_total: float, grabbed_total: float = 0.0,
             dead_points: float = 0.0) -> str:
    """顶部那句"扎心总结"。"""
    if wasted_total <= 0:
        return "目前没有白扔。保持住。"

    food = money_to_food(wasted_total)
    lunch = money_to_lunch(wasted_total)

    options = [
        f"这笔钱够你吃{food}。" if food else "",
        f"{lunch}。" if lunch else "",
    ]
    detail = next((o for o in options if o), "")

    if wasted_total < 10:
        head = f"你已经白扔了 ¥{wasted_total:.1f}"
    elif wasted_total < 50:
        head = f"你已经白扔了 ¥{wasted_total:.1f}"
    else:
        head = f"你已经白扔了 ¥{wasted_total:.1f}"

    if detail:
        return f"{head} —— {detail}"
    return head


def headline_sub(wasted_total: float, dead_points: float = 0.0,
                 dead_coupons: int = 0) -> str:
    """副标题：说清"这些本来是什么"。"""
    parts = []
    if dead_coupons:
        parts.append(f"{dead_coupons} 张券")
    if dead_points:
        parts.append(f"{dead_points:g} 分积分")
    if not parts:
        return "这些券和积分，都是你花的钱换来的 —— 它们没被你用掉，就这么没了。"
    subject = " 和 ".join(parts)
    return (f"{subject}，本来都是你花的钱换来的。\n"
            f"它们没有消失在你的胃里，而是消失在了你的券包里。")


# ----------------------------------------------------------------------------
# 周期对比：制造"一直在亏"的感觉
# ----------------------------------------------------------------------------

def period_punch(label: str, wasted: float, dead: int, grab_rate: float) -> str:
    """给某个周期来一句评价。"""
    if wasted <= 0 and dead == 0:
        return f"{label}没白扔，干得不错。"

    food = money_to_food(wasted) if wasted > 0 else ""
    tail = f"，折合{food}" if food else ""

    if grab_rate >= 90:
        mood = "还行，基本都用上了"
    elif grab_rate >= 70:
        mood = "有点漏，但不算严重"
    elif grab_rate >= 50:
        mood = "漏得有点多"
    else:
        mood = "基本是在白送"

    return f"{label}白扔 ¥{wasted:g}{tail} · 薅到率 {grab_rate:.0f}%（{mood}）"


# ----------------------------------------------------------------------------
# 券拟人化：让券自己开口说话
#
# 这是损友话术的进阶 —— 把券变成一个有点委屈、但不会指责你的角色。
# 用户对"一张券"没有感情，但对"一个在你手机里躺了 3 天、你一次都没点开过的
# 家伙"会有一丝内疚。这一丝内疚，就是行动的起点。
#
# 分寸（重要）：委屈，但不控诉。绝不写"你为什么不用我""你太过分了"。
# 券的姿态是"我一直在这儿等你"，不是"你辜负了我"。
# ----------------------------------------------------------------------------

_MONO_TONIGHT = [
    "我是{name}。今晚 24:00 我就没了 —— 只剩 {t}。你真的不来吗？",
    "我是{name}，在你手机里躺了 {days} 天，你一次都没点开过我。今晚我就要走了。",
    "喂，我是{name}。我还有 {t}，然后我就当你从来没领过。",
    "我是{name}。{t}之后，你手机里就再也找不到我了。",
]

_MONO_URGENT = [
    "我是{name}。我还能撑 {t}。你要不要考虑一下我？",
    "我是{name}。别等到我快没了才想起我，行吗。",
    "我是{name}，{t}的耐心。你上次说会用我的。",
]

_MONO_WATCH = [
    "我是{name}。我还有 {t}，不着急 —— 但你也别真把我忘了。",
    "我是{name}。时间还够，只是提醒你一声：我还在这儿。",
    "我是{name}。{t}，够你慢慢想。",
]

_MONO_UNKNOWN = [
    "我是{name}。我自己都不知道什么时候过期，你更不知道。",
    "我是{name}。没有到期日的意思是 —— 哪天没了都不奇怪。",
]

_MONO_GONE = [
    "我是{name}。我在你手机里躺了很久，最后谁也没来。",
    "我是{name}。我曾经值{food}。现在什么都不值了。",
]

_MONO_SAFE = [
    "我是{name}。我还有 {t}，暂时不用管我。",
]

_MONO = {
    "tonight": _MONO_TONIGHT,
    "urgent": _MONO_URGENT,
    "watch": _MONO_WATCH,
    "unknown": _MONO_UNKNOWN,
    "gone": _MONO_GONE,
    "safe": _MONO_SAFE,
}


def coupon_monologue(name: str, stage: str, td=None,
                     days: int | None = None, value: float = 0,
                     seed: str | None = None, variant: int | None = None) -> str:
    """让券以第一人称开口说话。

    variant：指定用第几句（会取模）。同一批券按顺序传不同 variant，可以保证
    相邻的券不会说同一句话 —— 比纯随机稳定。
    不传 variant 时用 seed 做稳定随机。
    """
    lines = _MONO.get(stage)
    if not lines:
        return ""
    if variant is not None:
        line = lines[variant % len(lines)]
    else:
        rng = random.Random(seed or name)
        line = rng.choice(lines)
    return line.format(
        name=name,
        t=countdown_phrase(td) or "没多久",
        days=days if days else 3,
        food=money_to_food(value) or "一顿饭",
    )
