#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCD-Life 麦当劳 MCP 客户端
==========================

统一封装麦当劳官方 MCP (https://mcp.mcd.cn, Streamable HTTP) 的调用，
并在缺少 Token 时自动降级到本地离线数据集，保证 Skill 在任何环境下都能演出完整流程。

用法（命令行）:
    python mcd_client.py status                 # 查看当前模式
    python mcd_client.py nutrition              # 营养表
    python mcd_client.py stores --city 深圳      # 附近门店
    python mcd_client.py meals --store <code>   # 门店菜单
    python mcd_client.py coupons                # 我的优惠券
    python mcd_client.py autobind               # 一键领券
    python mcd_client.py campaign               # 活动日历
    python mcd_client.py price --items "巨无霸:1,大薯:1"
    python mcd_client.py order --store <code> --items "巨无霸:1"

作为模块:
    from mcd_client import MCDClient
    c = MCDClient()
    print(c.mode)                 # 'LIVE' | 'DEMO'
    c.nutrition()
    c.nearby_stores(city="深圳", be_type=1)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# ----------------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------------

MCP_URL = "https://mcp.mcd.cn"
MCP_CONFIG_CANDIDATES = [
    Path.home() / ".workbuddy" / "mcp.json",
    Path(os.environ.get("WORKBUDDY_HOME", Path.home() / ".workbuddy")) / "mcp.json",
]
SKILL_DIR = Path(__file__).resolve().parent.parent
OFFLINE_DATA = SKILL_DIR / "references" / "offline_menu.json"

REQUEST_TIMEOUT = 25

# 配置模板里的占位符文本，识别到就当作"未配置"，避免每次发无效请求
TOKEN_PLACEHOLDERS = (
    "在这里填入",
    "your_mcp_token",
    "your token",
    "填入你的",
    "placeholder",
    "xxxxx",
)

# beType 枚举（来自官方文档）
BE_TYPE = {
    1: "到店取餐",
    2: "麦乐送到家",
    5: "得来速(DT)",
    6: "企业团餐",
}
# orderType 枚举
ORDER_TYPE = {1: "到店", 2: "外送"}


# ----------------------------------------------------------------------------
# Token 发现
# ----------------------------------------------------------------------------

def find_mcd_token() -> str | None:
    """从 WorkBuddy 的 mcp.json 中找出麦当劳 MCP 的 Bearer Token。

    支持三种配置写法：
      1. headers.Authorization = "Bearer xxx"
      2. headers.authorization = "Bearer xxx"
      3. headers.Authorization = "xxx"（不带 Bearer 前缀）
    """
    for cfg_path in MCP_CONFIG_CANDIDATES:
        if not cfg_path.is_file():
            continue
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        servers = cfg.get("mcpServers") or cfg.get("mcp_servers") or {}
        for name, spec in servers.items():
            if not isinstance(spec, dict):
                continue
            url = str(spec.get("url", ""))
            looks_like_mcd = ("mcd.cn" in url) or (name.lower() in {"mcd-mcp", "mcd", "mcd-mcp-server"})
            if not looks_like_mcd:
                continue

            headers = spec.get("headers") or {}
            if not isinstance(headers, dict):
                continue
            for hk, hv in headers.items():
                if str(hk).lower() == "authorization" and hv:
                    token = str(hv).strip()
                    if token.lower().startswith("bearer "):
                        token = token[7:].strip()
                    # 过滤模板占位符，避免误判为已配置
                    low = token.lower()
                    if not token or any(p in low for p in TOKEN_PLACEHOLDERS):
                        return None
                    return token
    return None


# ----------------------------------------------------------------------------
# 客户端
# ----------------------------------------------------------------------------

class MCDClient:
    """麦当劳 MCP 客户端，带自动降级。"""

    def __init__(self, token: str | None = None, force_mode: str | None = None):
        self.token = token if token is not None else find_mcd_token()
        if force_mode:
            self.mode = force_mode.upper()
        else:
            self.mode = "LIVE" if self.token else "DEMO"
        self.degrade_reason: str | None = None
        self._offline: dict[str, Any] | None = None
        self._req_counter = 0

    def _live_or_fallback(self, resp: dict[str, Any], fallback_fn) -> dict[str, Any]:
        """LIVE 调用返回降级信号时，改用本地数据集补齐，保证业务方法永远返回可用数据。"""
        if isinstance(resp, dict) and resp.get("_degraded"):
            data = fallback_fn()
            if isinstance(data, dict):
                data["_degraded"] = True
                data["message"] = resp.get("message", "已降级")
            return data
        return resp

    # -- 基础设施 -----------------------------------------------------------

    @property
    def offline(self) -> dict[str, Any]:
        if self._offline is None:
            if not OFFLINE_DATA.is_file():
                raise FileNotFoundError(f"离线数据集缺失: {OFFLINE_DATA}")
            self._offline = json.loads(OFFLINE_DATA.read_text(encoding="utf-8"))
        return self._offline

    def status(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "has_token": bool(self.token),
            "endpoint": MCP_URL,
            "token_preview": (self.token[:6] + "..." + self.token[-4:]) if self.token and len(self.token) > 12 else None,
        }

    def _call(self, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """调用麦当劳 MCP 的一个 tool。LIVE 模式发真实请求，DEMO 模式返回占位。

        重要：LIVE 模式下若遇到 401（Token 失效）或网络故障，自动降级为 DEMO，
        而不是抛出异常中断整个 Skill —— 保证演示永不翻车。
        """
        if self.mode != "LIVE":
            return {
                "_demo": True,
                "tool": tool,
                "arguments": arguments or {},
                "message": "DEMO 模式：未配置麦当劳 MCP Token，此调用未真实发出。",
            }

        payload = {
            "jsonrpc": "2.0",
            "id": f"mcd-life-{self._req_counter}",
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments or {}},
        }
        self._req_counter += 1

        req = urllib.request.Request(
            MCP_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {self.token}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            if e.code in (401, 403):
                # Token 无效 —— 自动降级，不中断
                self.mode = "DEMO"
                self.degrade_reason = f"Token 失效（HTTP {e.code}），已自动降级为 DEMO 模式"
                return {
                    "_demo": True,
                    "_degraded": True,
                    "tool": tool,
                    "message": self.degrade_reason,
                    "server_message": body[:200],
                }
            hint = {429: "触发限流(600次/分钟)，请降低频率"}.get(e.code, "")
            return {"_error": True, "http_status": e.code, "hint": hint, "body": body[:300]}
        except urllib.error.URLError as e:
            self.mode = "DEMO"
            self.degrade_reason = f"网络不可达（{e.reason}），已自动降级为 DEMO 模式"
            return {
                "_demo": True,
                "_degraded": True,
                "tool": tool,
                "message": self.degrade_reason,
            }

        return _parse_mcp_response(raw)

    # -- 业务能力 -----------------------------------------------------------

    def nutrition(self) -> dict[str, Any]:
        """餐品营养信息。LIVE 调 list-nutrition-foods，DEMO 读离线表。"""
        if self.mode == "LIVE":
            return self._live_or_fallback(
                self._call("list-nutrition-foods"),
                lambda: {"_demo": True, "foods": self.offline["nutrition"]},
            )
        return {"_demo": True, "foods": self.offline["nutrition"]}

    def nearby_stores(self, search_type: int = 2, be_type: int = 1,
                      city: str = "", keyword: str = "",
                      longitude: float | None = None,
                      latitude: float | None = None) -> dict[str, Any]:
        """查询附近门店。search_type: 1 收藏 / 2 按位置 / 3 按经纬度。be_type: 1 到店 / 5 得来速。"""
        args: dict[str, Any] = {"searchType": search_type, "beType": be_type}
        if search_type == 2:
            args["city"] = city
            args["keyword"] = keyword or city
        elif search_type == 3:
            if longitude is None or latitude is None:
                raise ValueError("searchType=3 需要同时提供 longitude 与 latitude")
            args["longitude"] = longitude
            args["latitude"] = latitude

        if self.mode == "LIVE":
            return self._live_or_fallback(
                self._call("query-nearby-stores", args),
                lambda: {"_demo": True, "stores": self._demo_stores(city), "query": args},
            )

        return {"_demo": True, "stores": self._demo_stores(city), "query": args}

    def _demo_stores(self, city: str) -> list[dict[str, Any]]:
        stores = self.offline["stores"]
        hit = [s for s in stores if not city or city in s.get("city", "")]
        return hit or stores

    def meals(self, store_code: str, order_type: int = 1, be_type: int = 1) -> dict[str, Any]:
        """查询门店可售菜单。"""
        args = {"storeCode": store_code, "orderType": order_type, "beType": be_type}
        if self.mode == "LIVE":
            return self._live_or_fallback(
                self._call("query-meals", args),
                lambda: {"_demo": True, "meals": self.offline["meals"], "query": args},
            )
        return {"_demo": True, "meals": self.offline["meals"], "query": args}

    def meal_detail(self, code: str, store_code: str, order_type: int = 1, be_type: int = 1) -> dict[str, Any]:
        """查询餐品详情与套餐组成。"""
        args = {"code": code, "storeCode": store_code, "orderType": order_type, "beType": be_type}

        def _fb() -> dict[str, Any]:
            for m in self.offline["meals"]:
                if m.get("code") == code:
                    return {"_demo": True, "meal": m}
            return {"_demo": True, "meal": None, "message": f"演示数据中无餐品 {code}"}

        if self.mode == "LIVE":
            return self._live_or_fallback(self._call("query-meal-detail", args), _fb)
        return _fb()

    def calculate_price(self, store_code: str, items: list[dict[str, Any]],
                        order_type: int = 1, be_type: int = 1,
                        coupon_id: str | None = None) -> dict[str, Any]:
        """计算价格。items: [{'productCode':..., 'quantity':...}]"""
        args: dict[str, Any] = {
            "storeCode": store_code,
            "orderType": order_type,
            "beType": be_type,
            "items": items,
        }
        if coupon_id:
            args["items"][0]["couponId"] = coupon_id
        if self.mode == "LIVE":
            return self._live_or_fallback(
                self._call("calculate-price", args),
                lambda: self._demo_price(items, coupon_id),
            )
        return self._demo_price(items, coupon_id)

    def _demo_price(self, items: list[dict[str, Any]], coupon_id: str | None) -> dict[str, Any]:
        """DEMO 模式本地算价。"""
        catalog = {m["code"]: m for m in self.offline["meals"]}
        total = 0.0
        kcal = 0
        lines = []
        for it in items:
            code = it.get("productCode") or it.get("code")
            qty = int(it.get("quantity", 1))
            meal = catalog.get(code)
            if not meal:
                lines.append({"productName": f"未知餐品({code})", "quantity": qty, "price": "0"})
                continue
            subtotal = float(meal["price"]) * qty
            total += subtotal
            kcal += int(meal.get("energyKcal", 0)) * qty
            lines.append({
                "productName": meal["name"],
                "quantity": qty,
                "price": f"{subtotal:.2f}",
            })

        discount = 0.0
        used_coupon = None
        if coupon_id:
            for cp in self.offline.get("my_coupons", []):
                if cp.get("couponId") == coupon_id:
                    discount = float(cp.get("value", 0))
                    used_coupon = cp
                    break

        return {
            "_demo": True,
            "orderDetail": {
                "orderProductList": lines,
                "productPrice": f"{total:.2f}",
                "totalDiscountAmount": f"{discount:.2f}",
                "realTotalAmount": f"{max(total - discount, 0):.2f}",
                "totalKcal": kcal,
                "couponList": [used_coupon] if used_coupon else [],
            },
        }

    def create_order(self, store_code: str, items: list[dict[str, Any]],
                     order_type: int = 1, be_type: int = 1,
                     address_id: str | None = None,
                     take_way_code: str | None = None,
                     remark: str = "") -> dict[str, Any]:
        """创建订单。真实下单会返回支付链接。"""
        args: dict[str, Any] = {
            "storeCode": store_code,
            "orderType": order_type,
            "beType": be_type,
            "items": items,
        }
        if address_id:
            args["addressId"] = address_id
        if take_way_code:
            args["takeWayCode"] = take_way_code
        if remark:
            args["remark"] = remark

        if self.mode == "LIVE":
            return self._live_or_fallback(self._call("create-order", args), lambda: self._demo_order(items))

        return self._demo_order(items)

    def _demo_order(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        priced = self._demo_price(items, None)
        return {
            "_demo": True,
            "orderId": "DEMO-000000000000",
            "payH5Url": "(演示模式不产生真实支付链接)",
            "orderDetail": priced["orderDetail"],
            "message": "这是演示订单，未产生任何真实交易。配置 MCP Token 后可真实下单。",
        }

    def available_coupons(self) -> dict[str, Any]:
        """麦麦省可领取的券列表。"""
        if self.mode == "LIVE":
            return self._live_or_fallback(
                self._call("available-coupons"),
                lambda: {"_demo": True, "coupons": self.offline.get("available_coupons", [])},
            )
        return {"_demo": True, "coupons": self.offline.get("available_coupons", [])}

    def auto_bind_coupons(self) -> dict[str, Any]:
        """一键领取麦麦省全部可用券。"""
        if self.mode == "LIVE":
            return self._live_or_fallback(
                self._call("auto-bind-coupons"),
                lambda: {"_demo": True, "bound": self.offline.get("available_coupons", []),
                         "message": "演示模式：模拟已领取全部可用券。"},
            )
        return {
            "_demo": True,
            "bound": self.offline.get("available_coupons", []),
            "message": "演示模式：模拟已领取全部可用券。",
        }

    def my_coupons(self) -> dict[str, Any]:
        """我的优惠券。"""
        if self.mode == "LIVE":
            return self._live_or_fallback(
                self._call("query-my-coupons"),
                lambda: {"_demo": True, "coupons": self.offline.get("my_coupons", [])},
            )
        return {"_demo": True, "coupons": self.offline.get("my_coupons", [])}

    def campaign_calendar(self, specified_date: str | None = None) -> dict[str, Any]:
        """麦当劳当月营销活动日历。"""
        args = {"specifiedDate": specified_date} if specified_date else {}
        if self.mode == "LIVE":
            return self._live_or_fallback(
                self._call("campaign-calendar", args),
                lambda: {"_demo": True, "campaigns": self.offline.get("campaigns", [])},
            )
        return {"_demo": True, "campaigns": self.offline.get("campaigns", [])}

    def my_account(self) -> dict[str, Any]:
        """积分账户。"""
        if self.mode == "LIVE":
            return self._live_or_fallback(
                self._call("query-my-account"),
                lambda: {"_demo": True, "account": self.offline.get("account", {})},
            )
        return {"_demo": True, "account": self.offline.get("account", {})}

    def now(self) -> dict[str, Any]:
        """当前时间（来自 MCP）。"""
        from datetime import datetime
        if self.mode == "LIVE":
            r = self._call("now-time-info")
            if not (isinstance(r, dict) and r.get("_degraded")):
                return r
        now = datetime.now()
        return {"_demo": True, "formatted": now.strftime("%Y-%m-%d %H:%M:%S")}


# ----------------------------------------------------------------------------
# MCP 响应解析
# ----------------------------------------------------------------------------

def _parse_mcp_response(raw: str) -> dict[str, Any]:
    """MCP Streamable HTTP 可能返回纯 JSON，也可能返回 SSE (text/event-stream)。"""
    raw = raw.strip()
    if not raw:
        return {}

    # SSE: 逐行找 data:
    if raw.startswith("event:") or raw.startswith("data:") or "\ndata:" in raw:
        chunks = []
        for line in raw.splitlines():
            if line.startswith("data:"):
                chunks.append(line[5:].strip())
        raw = "".join(chunks)

    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw}

    # 解包 tools/call 的 content
    if isinstance(obj, dict):
        result = obj.get("result")
        if isinstance(result, dict):
            # 优先使用 structuredContent（干净的原始 JSON）
            sc = result.get("structuredContent")
            if isinstance(sc, dict) and sc:
                return sc
            content = result.get("content")
            if isinstance(content, list) and content:
                texts = [c.get("text", "") for c in content if isinstance(c, dict)]
                joined = "".join(texts)
                try:
                    return json.loads(joined)
                except json.JSONDecodeError:
                    return {"_text": joined, "raw_result": result}
            return result
        if "error" in obj:
            return {"_error": obj["error"]}
    return obj


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def _parse_items(spec: str) -> list[dict[str, Any]]:
    """'巨无霸:1,大薯:1' -> [{'productCode':'巨无霸','quantity':1}, ...]"""
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            name, _, qty = part.partition(":")
        else:
            name, qty = part, "1"
        out.append({"productCode": name.strip(), "quantity": int(qty.strip() or 1)})
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="MCD-Life 麦当劳 MCP 客户端")
    p.add_argument("--mode", choices=["LIVE", "DEMO"], help="强制指定模式")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="查看当前模式")
    sub.add_parser("nutrition", help="营养表")
    sub.add_parser("coupons", help="我的优惠券")
    sub.add_parser("autobind", help="一键领券")
    sub.add_parser("campaign", help="活动日历")
    sub.add_parser("account", help="积分账户")

    s = sub.add_parser("stores", help="附近门店")
    s.add_argument("--city", default="深圳")
    s.add_argument("--be-type", type=int, default=1)

    m = sub.add_parser("meals", help="门店菜单")
    m.add_argument("--store", required=True)

    pr = sub.add_parser("price", help="算价")
    pr.add_argument("--store", required=True)
    pr.add_argument("--items", required=True)
    pr.add_argument("--coupon")

    o = sub.add_parser("order", help="下单（需真实 Token）")
    o.add_argument("--store", required=True)
    o.add_argument("--items", required=True)

    args = p.parse_args(argv)
    c = MCDClient(force_mode=args.mode)

    if args.cmd == "status":
        out = c.status()
    elif args.cmd == "nutrition":
        out = c.nutrition()
    elif args.cmd == "stores":
        out = c.nearby_stores(city=args.city, be_type=args.be_type)
    elif args.cmd == "meals":
        out = c.meals(args.store)
    elif args.cmd == "price":
        out = c.calculate_price(args.store, _parse_items(args.items), coupon_id=args.coupon)
    elif args.cmd == "order":
        out = c.create_order(args.store, _parse_items(args.items))
    elif args.cmd == "coupons":
        out = c.my_coupons()
    elif args.cmd == "autobind":
        out = c.auto_bind_coupons()
    elif args.cmd == "campaign":
        out = c.campaign_calendar()
    elif args.cmd == "account":
        out = c.my_account()
    else:
        p.error(f"未知命令 {args.cmd}")
        return 2

    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
