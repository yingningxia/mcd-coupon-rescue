# 麦当劳 MCP 工具速查（33 个）

**接入地址：** `https://mcp.mcd.cn`
**协议：** Streamable HTTP
**鉴权：** `Authorization: Bearer YOUR_MCP_TOKEN`
**限流：** 每个 Token 600 次/分钟，超出返回 429
**错误码：** 401 = Token 无效/过期；429 = 触发限流

**申请 Token：** 打开 https://open.mcd.cn/mcp → 右上角【登录】→ 手机号验证码登录 → 右上角【控制台】→ 点击【激活】→ 同意协议 → 复制 Token（5 步，约 3 分钟）

**写入 WorkBuddy 的 `~/.workbuddy/mcp.json`：**

```json
{
  "mcpServers": {
    "mcd-mcp": {
      "type": "streamableHttp",
      "url": "https://mcp.mcd.cn",
      "headers": {
        "Authorization": "Bearer YOUR_MCP_TOKEN"
      }
    }
  }
}
```

写完不会自动生效，需要在「连接器管理」页右上角的自定义连接器入口点【信任】。

---

## 一、营养类

| 工具 | 说明 | 关键入参 |
|---|---|---|
| `list-nutrition-foods` | 全餐品营养成分（能量/蛋白/脂肪/碳水/钠/钙）。返回 TOON 格式以省 Token | 无 |

## 二、门店与地址

| 工具 | 说明 | 关键入参 |
|---|---|---|
| `query-nearby-stores` | 查附近门店（到店/得来速） | searchType `1收藏/2按位置/3经纬度`、beType `1到店/5得来速`；searchType=2 需 city+keyword |
| `delivery-query-addresses` | 查我的配送地址 | 无 |
| `delivery-create-address` | 新增配送地址 | city、contactName、phone、address、addressDetail |
| `delivery-query-stores` | 查可配送门店 | addressId、beType `2麦乐送/6团餐` |
| `query-meal-assistance` | 团餐助餐服务 | storeCode、beCode、beType=6、orderType=2 |

## 三、菜单与算价

| 工具 | 说明 | 关键入参 |
|---|---|---|
| `query-meals` | 门店可售菜单（分类/编码/标签，含促销价 `originalPrice`、随单购 `withOrder`） | storeCode、orderType `1到店/2外送`、beType |
| `query-meal-detail` | 餐品详情、套餐组成、可替换项（`modification`） | code、storeCode、orderType、beType |
| `calculate-price` | 算价（可含券） | storeCode、items[]、orderType、beType、withOrder、needTableware |

**items 结构：** `productCode`、`quantity`、`couponId`、`couponCode`、`modification`、`roundList`

## 四、订单

| 工具 | 说明 | 关键入参 |
|---|---|---|
| `create-order` | 创建订单，返回 orderId 与 **payH5Url 支付链接** | storeCode、items、beType、addressId(外送)/takeWayCode(到店)、remark |
| `cancel-order` | 取消订单 | orderId、cancelReasonCode `1改主意/2重复下单/3点错/4地址错/5时间错/-1其它` |
| `query-order` | 查订单详情 | orderId |
| `order-list` | 近 10 笔历史订单 | 无 |

**订单状态示例：** `orderStatus: "待支付"`、`realTotalAmount`、`totalDiscountAmount`、`pickupCode`、`lockerQrCode`

## 五、优惠券

| 工具 | 说明 | 关键入参 |
|---|---|---|
| `available-coupons` | 麦麦省可领取券列表 | 无 |
| `auto-bind-coupons` | **一键领取**麦麦省全部可用券 | 无 |
| `query-my-coupons` | 我的所有可用券 | 无 |
| `query-store-coupons` | 指定门店可用券 | storeCode、orderType、beType |

## 六、积分与商城

| 工具 | 说明 | 关键入参 |
|---|---|---|
| `query-my-account` | 积分账户（可用/累计/冻结/将过期） | 无 |
| `mall-points-products` | 商城商品列表（可兑换或现金买） | catRuleIds（如 `1>4`商品券、`2`实物、`2>8`周边） |
| `mall-product-detail` | 商品详情 | spuId |
| `mall-create-order` | 积分兑换下单 | skuId、count、addressId(实物必填)、spuCategory `1虚拟/2实物` |
| `mall-order-list` | 商城订单列表 | lastId、size |
| `mall-order-detail` | 商城订单详情 | orderId |

## 七、活动与互动

| 工具 | 说明 | 关键入参 |
|---|---|---|
| `campaign-calendar` | 当月营销活动日历（进行中/往期/未来） | specifiedDate（查该日前后三天） |
| `query-lottery-info` | 积分抽奖活动信息 | 无 |
| `draw-lottery` | 积分抽奖（1 秒内不可重复调用） | 无 |
| `query-my-prizes` | 我的抽奖奖品 | pageNum、pageSize |

## 八、主题活动（派对/品鉴会）

| 工具 | 说明 | 关键入参 |
|---|---|---|
| `query-party-city` | 可参与城市 | spuId |
| `query-party-store` | 城市下可参与门店 | spuId、code |
| `query-partystore-date` | 可预约日期 | spuId、storeCode |
| `query-partystore-session` | 可预约场次 | spuId、storeCode、dateStr |
| `party-order-create` | 主题活动下单 | spuId、skuId、storeCode、dateStr、code、partyType、id、timeStart、timeEnd、leftNum、count |

## 九、通用

| 工具 | 说明 | 关键入参 |
|---|---|---|
| `now-time-info` | 当前完整时间（让 LLM 知道今天几号） | 无 |

---

## MCD-Life 常用组合

**心情推演：** `now-time-info` → `list-nutrition-foods` → `query-nearby-stores` → `query-meals` → `query-my-coupons` → `calculate-price`

**薅羊毛：** `auto-bind-coupons` → `query-my-coupons` → `available-coupons` → `campaign-calendar`

**下单：** `query-nearby-stores` → `query-meals` → `calculate-price` → **（用户确认）** → `create-order` → 返回 `payH5Url`

---

## 版本日志

| 日期 | 版本 | 变更 |
|---|---|---|
| 2025-12-09 | 1.0.0 | 麦麦日历 + 麦麦省领券 |
| 2026-01-23 | 1.0.1 | 新增餐品营养信息 |
| 2026-02-13 | 1.0.2 | 新增麦乐送点餐、积分兑换券 |
| 2026-04-02 | 1.0.3 | 新增到店取餐、团餐 |
| 2026-05-21 | 1.0.4 | 积分兑实物、商城订单、得来速下单、全部场景预约 |
| 2026-06-16 | 1.0.5 | 套餐内商品可更换、部分餐品特制 |
| 2026-07-16 | 1.0.6 | 历史订单、促销价展示、随单购麦金卡/早餐卡 |
| 2026-07-29 | 1.0.7 | 派对、品鉴会等主题活动查询预约下单 |
| 2026-08-27 | 1.0.8 | 积分抽奖（查信息/抽奖/查奖品） |
| 2026-09-10 | 1.0.9 | 取消订单、餐具选择、外送备注、取餐柜二维码 |

## 真实返回字段速查（实跑验证，2026-10-09）

### `query-my-coupons` → `data.coupons[]`

关键字段（**券的生命周期全靠这些**）：

| 字段 | 示例 | 用途 |
|---|---|---|
| `id` / `code` | `71F939A...` / `MCD690765...` | 券标识与核销码 |
| `title` | `麦旋风任选` | 券名 |
| `tradeEndDateTime` | `2026-10-09 23:59:59` | **过期时间（核心）** |
| `tradeStartDate` / `tradeEndDate` | `2026-10-05` / `2026-10-09` | 有效期区间 |
| `discountInfo.discountValue` | `9.9` | **用券价** |
| `discountInfo.discountTypeText` | `¥` | 金额单位 |
| `tags[].label` | `今日到期` / `到店专用` | **官方打的标签** |
| `receivedDays` / `receivedDaysMsg` | `3` / `3日前收到` | 已躺天数 |
| `tradeTimePeriods[].startTime/endTime` | `10:30:00` / `23:59:59` | **可用时段** |
| `currentDayAvailableCount` / `totalCount` | `1` / `1` | 今日可用次数 |
| `datetimeText` | `2026-10-05 10:30-2026-10-09 23:59 ...` | 人读文案 |
| `weekdays` | `[2,3,4,5,6]` | 限定星期（1=周日） |

### `query-my-account` → `data`

| 字段 | 示例 | 说明 |
|---|---|---|
| `availablePoint` | `116.4` | 可用积分 |
| `accumulativePoint` | `532.5` | 累计积分 |
| `expiredPoint` | `416.1` | **已作废积分（进浪费博物馆，折算成钱）** |
| `currentMouthExpirePoint` | `0` | **本月将过期（最紧急）** |
| `currency` | `麦享会积分` | 单位 |

### `query-nearby-stores` → `data[]`

| 字段 | 示例 |
|---|---|
| `storeCode` | `1420227` |
| `storeName` | `麦当劳深圳北站站内一店餐厅(B18/B19检票口上方)` |
| `businessStartTime` / `businessEndTime` | `06:00` / `22:00` |
| `reservationTimeOptions[].reservationOptionText` | `早餐(06:14至10:15)，午餐(10:44至14:15)...` |
| `distance` | `62`（米） |

### `query-meals` → `data.categories[]` + `data.meals{}`

- `categories[]`：`{name, meals:[{code, tags}]}`
- `meals{}`：以 code 为键，`{name, image, currentPrice, originalPrice, canWithOrder}`
- **价格单位是「分」**：`"49"` = ¥49.00（注意：`query-meals` 的 currentPrice 看起来是元，`calculate-price` 返回的是分，以实测为准）

### `calculate-price` → `data`

| 字段 | 示例 | 说明 |
|---|---|---|
| `productPrice` / `price` | `1500` | **单位是「分」** = ¥15.00 |
| `discount` | `0` | 优惠额（分） |
| `productList[]` | `{productCode, productName, quantity, subtotal}` | 明细 |
| `takeWayList[]` | `[{code:"eat-in",title:"堂食"},{code:"take-in-store",title:"外带"}]` | 取餐方式 |

---

**官方推荐 LLM：** qwen-plus / qwen3-max、Doubao-Seed-1.6、Kimi k2.5、GLM-5、DeepSeek-V3.2

---

## 版本日志
