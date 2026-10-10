# WorkBuddy 开发上下文 · workbuddy.md

本文记录「抢救麦券大作战」使用 WorkBuddy 开发的真实过程，用于核验 WorkBuddy 联动活动奖励条件。

- **开发工具**：WorkBuddy（Agent 模式）
- **开发时间**：2026-10-09 ~ 2026-10-10
- **Skill 形态**：WorkBuddy Skill（`SKILL.md` + `scripts/` + `references/` + `assets/`）

---

## 一、开发过程概要

整个项目在 WorkBuddy 对话中完成，共经历五轮迭代。

### 第 1 轮 · 定位探索

**我的输入：**
> 帮我根据这个比赛开发一个麦当劳的创意 skill。我也不确定具体要做什么，你可以向我提问来进一步确定具体做法。

**WorkBuddy 的动作：** 先读比赛页面（发现是图片，读不到文字），改用搜索 + 读取海报图片获取信息；
整理出麦当劳 MCP 的 33 个工具清单；然后反问方向（娱乐向 / 实用向、Token 情况、产品形态）。

### 第 2 轮 · 第一次重构

**我的反馈：**
> 三个模式都不够有意思和有创意，感觉就是把 MCP 给你的内容换个说法表达出来。

**WorkBuddy 的复盘：** 承认原设计是"复读机穿戏服"——MCP 返回"麦旋风 340 kcal"，
加一句"星盘显示此卦热量偏重"，信息量没变。**创意不是换说法，是换用法。**
随后重构为"券生命周期"方向。

### 第 3 轮 · 去忌讳 + 周期统计

**我的要求：**
> 不要写临终，会让人觉得很晦气，换一个说法。尽量用词比较有趣味性……
> 增加一个周期的统计功能，包括周月年。核心是让买家觉得我浪费了东西。

**WorkBuddy 的动作：** 全库替换语义体系（临终→今夜清零、墓志铭→浪费博物馆、救活率→薅到率），
新建 `ledger.py` 做本地累积记账 + 周/月/年统计。

### 第 4 轮 · 趣味性与刺激度

**我的反馈：**
> 但新的语义不够趣味性，也不够刺激性。

**WorkBuddy 的动作：** 提炼出四种手法（换算成实物 / 精确到分钟 / 关注点在失去 / 损友式调侃），
新建独立话术引擎 `roast.py`；顺手修掉一个逻辑矛盾——薅到率只算券不算积分，
导致"券全用上了但积分白扔 416 分还显示 100%"。

### 第 5 轮 · 视觉升级

**我的要求：**
> 浪费的东西不用光用文字描述，还要用图片，图片更有食欲
> UI 方面要有麦当劳的标志金拱门呈现
> 有些文字可以用券拟人说话表达

**WorkBuddy 的动作：**
- 发现麦当劳 MCP 的 `query-my-coupons` 自带 `image` / `bigImage` 字段（官方产品图），
  据此新建图片管线 `imgcache.py`（并发下载 + 本地缓存 + base64 内联，零第三方依赖）
- 用纯 SVG 绘制金拱门
- 新增券第一人称拟人台词 `roast.coupon_monologue()`
- 用 Edge headless 截图逐段验证渲染效果

---

## 二、WorkBuddy 关键能力的使用

| 能力 | 在项目中的具体作用 |
|---|---|
| **MCP 接入** | 通过自定义连接器接入麦当劳 `mcd-mcp`（Streamable HTTP），实时拉取真实券包/积分/门店/菜单数据 |
| **Skill 开发** | 产出标准 WorkBuddy Skill 结构（SKILL.md 触发词 + SOP + 红线，scripts 可执行） |
| **文件读写与执行** | 直接编写、运行、调试 Python 脚本；跑通 LIVE / DEMO 双模式 |
| **自动化** | 设计"每天 9:00 跑券包体检 + 记账"的定时任务方案（SKILL.md 中说明用 automation 工具创建） |
| **自我纠错** | 主动发现并修复了多个真实问题（见下） |

---

## 三、WorkBuddy 主动发现并修复的问题

这些不是我提出的，是 WorkBuddy 在实跑中自己发现的：

1. **误用字段名** —— 14 张券全部显示"寿命未知"，原因是错用 `expireDate`；真实字段是 `tradeEndDateTime`
2. **MCP 响应解包错误** —— 应从 `result.structuredContent` 取数据，而非 `content[0].text`
3. **假 Token 导致流程中断** —— 401 会直接抛异常；改为自动降级，演示永不翻车
4. **救活率计算错误** —— 未剔除"寿命未知"和"今夜清零"的券
5. **重复券刷屏** —— 同名券逐条显示 ×4；改为按券名聚合
6. **病危券误加门店紧迫感** —— 门店打烊提示只应对"今夜清零"生效
7. **薅到率与白扔金额自相矛盾** —— 薅到率只算券不算积分；改为综合口径
8. **文案冗余** —— 拟人台词与行动计划重复表达同一件事
9. **拟人台词撞车** —— 随机选取导致相邻两张券说同一句话；改为轮换分配
10. **浪费博物馆数据源错误** —— 原本扫描券包里 `stage==已化灰`，但 MCP 接口根本不返回过期券；
    改为合并账本的跨快照对比结果

---

## 四、产出物

| 类型 | 文件 |
|---|---|
| Skill 主文件 | `SKILL.md` |
| MCP 集成说明 | `MCP_INTEGRATION.md` |
| 参赛声明 | `CONTEST_DECLARATION.md`（官方原文，未改动） |
| 核心脚本 | `scripts/coupon_life.py`（券生命周期引擎）<br>`scripts/ledger.py`（累积记账 + 化灰检测）<br>`scripts/roast.py`（话术 + 拟人化引擎）<br>`scripts/imgcache.py`（产品图管线）<br>`scripts/settle_card.py`（账本生成器）<br>`scripts/mcd_client.py`（MCP 客户端 + 自动降级） |
| 参考资料 | `references/mcp-tools.md`（33 工具速查 + 真实字段表）<br>`references/offline_menu.json`（离线数据集） |
| 可视化产出 | `examples/麦麦浪费账本.html`<br>`examples/麦麦浪费账本_演示版.html` |

---

## 五、可复现性

本项目**零第三方依赖**，全部脚本仅使用 Python 标准库。评委可直接运行：

```bash
cd scripts

# 真实数据（需自行配置 MCP Token）
python coupon_life.py exam
python settle_card.py --out 麦麦浪费账本.html

# 无需 Token 的离线演示
python settle_card.py --demo --no-record --sample-ledger --out 演示版.html
```

Demo 数据与示例账本均随仓库提供，离线可完整跑通全部流程。
