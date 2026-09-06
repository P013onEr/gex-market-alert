---
name: gex-matrix-lb
description: 任意美股标的 × 未来 N 个到期日的 NetGEX 矩阵热力图(strike 行 × 到期日列)。heatseeker-lb 的姊妹技能:那边是 SPY/QQQ 0DTE 单列阶梯,这边是个股多到期二维矩阵,标 ★KING(全局最强负墙/翻转位)与 FLOOR(全局最强正墙/阻力)。数据走 longbridge CLI(chain 拿 strike、calc-index 批量拿 gamma/OI)。触发词:GEX矩阵、GEX分布、多到期GEX、"我要 XXX 最近 N 个到期日的 GEX"、"XXX 未来 N 日 GEX"、gex matrix。
---

# GEX-Matrix-LB — 个股多到期 NetGEX 矩阵

数据源:**longbridge CLI**(需已 `longbridge auth login`)。
与 [heatseeker-lb](../heatseeker-lb/SKILL.md) 同一套取数链与视觉,差别:

| | heatseeker-lb | gex-matrix-lb(本技能) |
|---|---|---|
| 标的 | 固定 SPY + QQQ | **任意代码**(NVDA / TSLA / …) |
| 到期 | 仅 0DTE 一列 | **未来 N 个到期日成列** |
| 版式 | 单列竖向阶梯 | strike 行 × 到期日列的矩阵 |

## 触发条件

用户说以下任一说法,**从话里解析出代码和范围,直接执行**:

- "我要 **NVDA** 最近 **5 个到期日**的 GEX 分布" → `NVDA --expiries 5`
- "**TSLA** 未来 **30 日** GEX" / "最近30天" → `TSLA --days 30`
- "**AAPL** GEX 矩阵" (没说范围) → 默认 `--expiries 5`
- GEX矩阵 / 多到期 GEX / gex matrix + 任意代码

范围词映射:**"N 个到期日 / N 个合约" → `--expiries N`;"N 日 / N 天" → `--days N`**。
两者都没说 → `--expiries 5`。

## 工作流

```bash
python3 ~/.claude/skills/gex-matrix-lb/scripts/gex_matrix.py <TICKER> [--expiries N | --days N] [--width 20] && \
  open ~/.claude/skills/gex-matrix-lb/output/gex_matrix_<TICKER>.html
```

- 运行时长 ≈ 合约数 ÷ 40 × 2.5s;默认档(5 到期 × ±20 档 × call/put ≈ 400 合约)约 40–60 秒
- stdout 会报 `greeks got/total`;**缺口 >10% 说明撞了静默限流,等 1 分钟重跑**,别把残缺数据当结果
- 到期日很多的标的(周权)想看更远,用 `--days 45` 比 `--expiries 9` 更直观

## 对话报文规范(沿用 heatseeker 纪律:只讲点位,客观描述,禁预测措辞)

跑完后在对话里给:

### ① 一句定位
标的 · spot · 数据时点(盘前/盘中/盘后)· greeks 覆盖率(缺口 >10% 要标出来)

### ② 价位轴代码块(必出,这是报文主体)

从高到低逐档排列,**放进 ``` 代码块**以保证等宽对齐。规则:
- 只列现价上下各 ~8 档(全矩阵太长,读者要的是现价附近)
- 数值取该 strike 的**跨列合计**(所有到期日之和)
- `──────── {spot} 现价 ────────` 横线插在正确档位之间
- 关键档位右侧加箭头注释:`FLOOR ★ 最强正墙(阻力)`、`★ KING …(翻转位)`、
  `← 翻负分界`、`← 现价下方紧贴的正墙(支撑)`、`┐┴ 密集吸收带`

样例:

```
385.0   +$1,910K
380.0   +$4,565K   FLOOR ★ 最强正墙(阻力)
375.0   +$2,608K
──────── 371.54 现价 ────────
370.0   +$3,363K   ← 现价下方紧贴的正墙(支撑)
360.0      -$46K   ← 翻负分界
340.0     -$394K   ★ KING -$1,570K @09-04
```

### ③ 列合计一行
`08-28 +$17.4M · 08-31 +$5.8M · …`,并指出哪列是主力

### ④ 结构描述(2–3 句)
现价被哪两道墙夹住、吸收带范围、上下对称性。**只描述结构。**

### ⑤ 口径声明
OI 为 T+1 vintage(盘中不变);gamma/spot 为快照时点实时值;颜色全局归一。

### 禁止
**不写**"会涨/会跌/目标价/预计";只说"吸收/放大、支撑/阻力/翻转位"。

HTML 照出并 `open`,报文只在对话里给,不写进 HTML。

## 实现要点(改代码前先读)

- **符号规则**:`{CODE}{YYMMDD}{C|P}{strike×1000}.US`,不补零(NVDA 213 → `NVDA260828C213000.US`)
- **chain 不给 OI/greeks**,必须逐合约走 `calc-index --fields gamma,oi`(可批量,`CALC_BATCH=40`)
- **限流是静默返空**(不报错、退出码 0):`CALC_GAP` 别删;空批自动降级逐个重试
- 每个到期日的 strike 网格不同(周权细、月权粗):逐到期日各取现价 ±width 档,行 = 并集;
  "·" = 该到期日无此挂牌,"$0.0K" = 有挂牌但无持仓
- 颜色按**全局** |max| 归一 → 近月亮、远月淡,这是特征(近月 gamma 天然大)
- 参考图里的 ±% 变化气泡需要逐日快照基线,长桥无此数据,不做

## 已知限制

| 症状 | 处理 |
|---|---|
| `quote failed` | `longbridge check`;token 失效则 `longbridge auth login` |
| greeks 缺口大 | 静默限流:等 60s 重跑;或调小 `--width` / `--expiries` |
| 标的无期权 | 报"期权链里没有未来到期日",换代码 |
| 指数(SPX/NDX/VIX) | 长桥无美股指数期权,用 SPY/QQQ 替代 |
