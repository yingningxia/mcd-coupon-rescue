# MCP 集成说明 · MCP_INTEGRATION.md

本文说明「抢救麦券大作战」实际使用的麦当劳 MCP Server、Tool、调用流程与业务价值。

---

## 一、使用的 MCP Server

| 项 | 值 |
|---|---|
| Server 名称 | `mcd-mcp` |
| 端点 | `https://mcp.mcd.cn` |
| 传输协议 | Streamable HTTP |
| 鉴权方式 | `Authorization: Bearer <MCP Token>` |
| Token 获取 | [open.mcd.cn/mcp](https://open.mcd.cn/mcp)，手机号登录后在控制台激活 |
| 限流 | 官方限制 600 次/分钟；本项目实际调用量极低（一次体检约 3–5 次） |

**配置方式**（WorkBuddy 自定义连接器 / 任意 MCP 客户端）：

```json
{
  "mcpServers": {
    "mcd-mcp": {
      "type": "streamablehttp",
      "url": "https://mcp.mcd.cn",
      "headers": { "Authorization": "Bearer YOUR_MCP_TOKEN" }
    }
  }
}
```

> 仓库内所有配置均使用 `YOUR_MCP_TOKEN` 占位符，不含任何真实凭据。

**响应解包要点**：MCP 返回体中的干净业务数据位于 `result.structuredContent`，
而非 `content[0].text`。本项目 `scripts/mcd_client.py` 的 `_parse_mcp_response()`
优先读取前者，避免解析到外层包装。

---

## 二、实际调用的 Tool 清单

| Tool | 用途 | 本项目用它做什么 |
|---|---|---|
| `query-my-coupons` | 我的券包 | **核心数据源**。取 `tradeEndDateTime`（到期时间）、`discountInfo`（券值）、`tags`（官方标签）、`tradeTimePeriods`（可用时段）、`receivedDays`（已躺天数）、`image`（官方产品图） |
| `query-my-account` | 积分账户 | 取 `availablePoint` / `accumulativePoint` / **`expiredPoint`（已作废积分）** / `currentMouthExpirePoint`（本月将作废） |
| `query-nearby-stores` | 附近门店 | 取 `businessEndTime`（营业结束时间）与 `reservationTimeOptions`，用于判断"现在出门还来不来得及" |
| `query-meals` | 门店菜单 | 取真实价格与产品图，供实物换算与下单链路使用 |
| `calculate-price` | 算价 | 券后真实到手价（**返回单位是「分」**，1500 = ¥15.00） |
| `available-coupons` | 可领券 | 提示还有哪些券可以领 |
| `auto-bind-coupons` | 一键领券 | 用户确认后批量领取麦麦省可用券 |
| `campaign-calendar` | 活动日历 | 当月营销活动，用于组合推荐 |
| `create-order` | 下单 | 用户**二次确认**后执行，返回支付链接 |

> 项目只读取数据，不写入 —— 唯一写入动作 `create-order` 需要用户在对话中明确确认才执行。

---

## 三、调用流程

### 主流程：券包体检 → 浪费账单

```
① query-my-coupons ─┐
                     ├─→ ② 生命周期引擎（coupon_life.py）
   query-my-account ─┘        · 算每张券剩余时间，划分六阶段
                              · 识别重复券 / 僵尸券 / 积分作废
                              · 按券名聚合，组内最紧迫者决定整组紧急度
                                        │
③ query-nearby-stores ──────────────────┤ ④ 三线交叉运算
                                        │   券到期 × 门店营业 × 券可用时段
                                        ↓
                              ⑤ 本地累积记账（ledger.py）
                                 · 快照落盘，跨天对比
                                 · "上次有、这次没" = 化灰 → 白扔金额
                                        │
                                        ↓
                              ⑥ 渲染《麦麦浪费账本》（settle_card.py）
                                 · 图片管线内联官方产品图
                                 · 周/月/年周期账单
```

### 下单流程（需用户确认）

```
query-nearby-stores（卡营业时间）
  → query-meals（找券对应餐品）
  → calculate-price（算券后到手价，注意单位是分）
  → 【二次确认】
  → create-order
```

---

## 四、三线交叉运算（核心技术点）

项目最硬的判断来自三份异源数据的交叉：

| 数据线 | 来源 | 字段 |
|---|---|---|
| 券的到期时间 | `query-my-coupons` | `tradeEndDateTime` |
| 门店营业结束时间 | `query-nearby-stores` | `businessEndTime` |
| 券的可用时段 | `query-my-coupons` | `tradeTimePeriods` |

交叉后产出：

> 🚪 最近的麦当劳深圳北站站内一店已经打烊了（营业至 06:00–22:00）。
> 今晚大概率是保不住了 —— **这就是白扔的现场**。

**这句话无法从任何一个 Tool 单独得到。** 例如一张早餐券限用 05:00–10:29，
即使券还有 3 天有效期，今天也已经用不掉了 —— 必须把券的时段和门店的营业时间一起算。

实现见 `scripts/coupon_life.py` 的 `rescue_feasibility()`。

---

## 五、业务价值

### 1. 把 MCP 的"存量查询"变成"损失洞察"

`query-my-coupons` 单独用，只能回答"我有什么券"。本项目做了三件 MCP 原生不提供的事：

- **时间维度**：把 `tradeEndDateTime` 转成剩余寿命与六阶段状态
- **结构维度**：识别同名券的不同到期日（真实数据中普遍存在，一次消费只核销一张 → 注定白扔 N−1 张）
- **金额维度**：把券值与 `expiredPoint` 折算成人民币，并换算成实物（"一顿麦当劳正餐"）

### 2. 补上 MCP 拿不到的数据：化灰检测

**这是本项目最关键的发现。** 麦当劳券包接口只返回**当前还在的券**，已过期的券会被静默移除 ——
所以任何单次查询都查不到"我一共白扔了几张"。

本项目通过本地累积记账补上这一环：

```
2026-10-08 快照：麦旋风任选、薯薯任选、火腿扒堡、大脆鸡扒堡
2026-10-10 快照：火腿扒堡、大脆鸡扒堡          ← 少了 2 张
                     ↓
             这 2 张化灰了，白扔 ¥19.8
```

实现见 `scripts/ledger.py` 的 `_detect_gone()`。

### 3. 驱动真实的下单行为

产品目标不是"帮用户看券"，而是让用户**看见自己正在亏钱**，从而真的去用掉。

顶部账单只讲一件事：

> **你已经白扔了 ¥61.41** —— 这笔钱够你吃 1.8 顿麦当劳。
> 它们没有消失在你的胃里，而是消失在了你的券包里。

所有文案都过统一的话术引擎（`scripts/roast.py`）：金额换算成实物、倒计时精确到分钟、
主语从"你有什么"换成"你已经让什么躺到作废了"。券还会以第一人称开口：

> 我是火腿扒堡早餐两件套。我还能撑 2 天 2 小时。你要不要考虑一下我？

---

## 六、工程上的稳健性设计

| 设计 | 目的 |
|---|---|
| **自动降级**（`_live_or_fallback`） | Token 失效 / 401 / 403 / 断网时不抛异常，切换到本地数据集继续跑。演示与开发永不中断 |
| **零第三方依赖** | 全部脚本只用 Python 标准库，评委 clone 下来即可运行 |
| **图片失败不阻塞** | `imgcache.py` 任何一张图下载失败都返回空串，模板兜底占位，绝不因图片导致报告生成失败 |
| **本地缓存** | 券图按 URL 缓存到 `assets/img/`，重复生成秒出 |

---

## 七、数据合规

- 所有数据来自使用者**自己账号**的 MCP 实时查询，本地处理，不外传
- 仓库不含任何真实 Token、账号或个人信息（详见 `CONTEST_DECLARATION.md`）
- `data/`（本地账本，含个人券信息）与运行时图片缓存已在 `.gitignore` 中排除
- 只读为主，唯一写入动作 `create-order` 需用户明确二次确认
