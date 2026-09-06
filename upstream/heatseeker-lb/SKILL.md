---
name: heatseeker-lb
description: 末日大盘期权(0DTE)NetGEX 热力图 + 点位报文。用 longbridge CLI 实时行情算 SPY / QQQ 当日到期期权的 NetGEX 分布(γ×OI,call 正 put 负),输出 King(最强负墙/翻转位)、Floor(最强正墙/阻力)、Pillow(缓冲垫)、现价档位的竖向阶梯热力图 HTML,并在对话里给出「只讲点位」的支撑/阻力/翻转点报文 + GEX×VEX 交叉确认。零依赖 GUI,只需 longbridge CLI 已登录。触发词:末日大盘期权、最新末日、0DTE GEX、gamma walls。
---

# Heatseeker-LB — 末日大盘期权 NetGEX 热力图

数据源:**longbridge CLI**(`~/bin/longbridge`,需已 `longbridge auth login`)。
覆盖:**SPY + QQQ**。长桥不提供任何美股指数期权,故无指数列。

## 触发条件

用户说以下任一关键词,**直接执行,不要问参数**:

- **末日大盘期权 / 大盘末日期权**(主触发词)
- 最新末日 / 最新末日期权 / 末日期权
- 0DTE GEX / 0DTE 大盘 / 末日 gamma
- dealer gamma 分布 / gamma walls
- heatseeker

## 工作流(每次触发都照做)

```bash
# 1) 取最新 GEX + VEX 数值(给报文用)
python3 ~/.claude/skills/heatseeker-lb/scripts/report_gv.py

# 2) 出热力图 HTML 并打开
python3 ~/.claude/skills/heatseeker-lb/scripts/heatseeker_gex.py && \
  open ~/.claude/skills/heatseeker-lb/output/heatseeker_gex.html
```

两步都跑(各约 40–60 秒,拉链 + 批量取 γ/OI/IV + 渲染)。
**HTML 照出,报文只在对话里给,不写进 HTML。**

## 报文格式规范(核心,严格遵守)

### 结构:点位轴 → GEX×VEX 表 → 一句话小结 → 快照提醒

**① 每个标的一个从高到低的价位轴代码块**,逐档标注:

```
775  ── 🟢 上方最强正墙 Floor（+22.5M），主阻力
773  ── 🔻 上方紧邻负墙 King（−15.5M），翻转位
772  ── ⚪ 现价
770  ── 🔻 下方负墙（−14.2M），翻转位
```

- 🟢/🔴 **正墙** = 阻力(上方)或支撑(下方)。写「当前热图最强/较强的一堵正墙(+XX.XM)」
- ⚪ **现价**(用四舍五入到最近档位的整数)
- 🔻 **负墙** = 翻转/加速位。写「现价上/下方最大的负墙(−XX.XM)」
- 点出「**阻力带**」(连片正墙)和「**不稳定带**」(现价被负墙群夹住的区间)
- 每标的轴下配 2–3 条要点(哪里是主阻力、哪里是翻转位、有无就近托底)

**② 必含「GEX × VEX 交叉确认」表**(标准报文固定部分,**绝对不要漏**):

| 标的 | 档位 | GEX | VEX | 判定 |
|---|---|---|---|---|
| SPY | 775（Floor） | +22.5M | +3.9M | ✅ 同向 |
| SPY | 773（King） | −15.5M | −3.1M | ✅ 同向 |
| SPY | 770（下方） | −14.2M | +2.3M | ⚠️ 背离 |

表后必须附诚实注脚(照抄大意):

> ⚠️ VEX = 本地 BS 估算 vanna(长桥不提供 vanna,假设 0DTE 距收盘 ~3h)。符号在现价上方结构性为正、下方结构性为负,故"同向/背离"多为 moneyness 机械决定,不算独立确认。真正有信息量的是 **|VEX| 最集中档 = IV 一动 dealer 对冲流最猛处**:SPY 在 `X/Y/Z`,QQQ 在 `X/Y/Z`。

**③ 一句话小结**(纯点位,概括两个标的的主阻力 + 翻转位)。

**④ 结尾快照提醒**:实时快照,点位随 OI/价格移动,变盘前重跑确认。

### 措辞纪律(硬要求)

**只客观描述热图上「是什么」,不做方向预测。**

| ❌ 禁用 | ✅ 改成 |
|---|---|
| 很难一口气突破 | 这是当前热图上较强的阻力 |
| 会加速下滑 / 容易乱扫 | 下方最大的负 GEX 在 X,是翻转位 |
| 坚决不做 / 一定 | (不写方向判断) |

- **默认不给期权策略**(用户明确要"策略/怎么做"时才给)。
- 术语对应:正墙 = 吸收/压制波动(上方=阻力、下方=支撑);负墙 = 放大波动 = 翻转/加速位。

## 输出结构(HTML)

每个标的一个面板,竖向 strike 阶梯(现价 ±25 个真实 strike,51 行):

- 左 = 行权价档位(现价行显示四舍五入后的档位数,实时价在面板标题)
- 右 = NetGEX,单位 **M**(≥100万→`$XX.XM`,不足→`$XXXK`)
- 背景色:绿(正/吸收)→ 黄(极大正);紫红/品红(负/放大)
- 文字色按背景亮度自动切换(暗底白字、亮底深字)
- Badge:`★ KING`(最强负墙)、`FLOOR`(最强正墙)、`PILLOW`(缓冲垫)、`◀ PRICE`
- 面板底部自动生成结构叙事:箱体 / 非对称 / 正 gamma 主导

## 数据链(改脚本前必读)

```
1. quote SPY.US                      → last = spot
2. option chain SPY.US               → 到期日列表,取最近 ≥ 今天(=0DTE)
3. option chain SPY.US --date <d>     → 真实 strike 列表
4. 自拼合约符号 {CODE}{YYMMDD}{C|P}{strike×1000}.US
                                        如 SPY260806C770000.US
5. calc-index <符号...> --fields gamma,oi,strike,exp
6. NetGEX = Σ sign × gamma × OI × spot × 100   (call +, put −)
7. VEX  = Σ sign × vanna × OI × spot           (vanna 本地 BS 算)
```

**长桥的 option chain 不给合约代码、不给 gamma、不给 OI**——必须自拼符号 + `calc-index` 取,这是和其他数据源最大的差异。

## 已知坑

| 坑 | 说明 / 处理 |
|---|---|
| **`calc-index` all-or-nothing** | 一批里混进**一个不存在的合约**,整批返回空 `[]`(不是限流)。所以**必须用 chain 返回的真实 strike** 拼符号,绝不能自己凭空拼连续整数。脚本已内置:整批空→逐个重试兜底 |
| **JSON 被污染** | `--format json` 输出会被 CLI 的 "New version … available" 提示污染。统一用 `json.JSONDecoder().raw_decode` 只取首个 JSON 值 |
| **批量上限** | 40–50 符号/批稳定;每批间隔 ~1s |
| **无指数期权** | `.SPX.US` / `.NDX.US` / `.VIX.US` 期权链全空,直接拼 SPXW 合约符号也全空。长桥只有 ETF/个股期权。指数**现价**能拿(`quote .SPX.US`)但无期权链,拼不出 GEX |
| **OI 是收盘值** | T+1 口径,对 0DTE 影响很小,但严格说不是盘中实时 OI |
| **跨日切换** | 0DTE 到期会跨日切换。若两个脚本取到不同到期(如一个 08-12、一个 08-13),**必须在报文里标注**,不可混着对照 |
| **新到期墙偏薄** | 刚切到新到期时 OI 仍在累积,墙普遍比旧到期薄,属正常,报文里说明即可 |
| **不要用 scratchpad** | 临时目录不持久会被清,脚本必须留在本 skill 的 `scripts/` 下 |

## 关键配置(脚本顶部常量)

| 常量 | 默认 | 含义 |
|---|---|---|
| `TICKERS` | `SPY, QQQ` | 标的列表 |
| `N_STRIKES_EACH_SIDE` | 25 | 现价每侧 ±N 档,共 51 行 |
| `CALC_BATCH` | 40 | 每次 `calc-index` 的符号数 |
| `T_HOURS`(report_gv) | 3.0 | VEX 用的"距收盘小时数"假设 |

## 错误处理

| 错误 | 处理 |
|---|---|
| `quote failed` / 空结果 | 检查 `longbridge check`;token 失效需 `longbridge auth login` |
| 某批 greeks 缺失 | 脚本自动逐个重试;若仍缺,报文里说明覆盖率 |
| 找不到 `~/bin/longbridge` | 确认 CLI 已安装且路径正确 |
| 盘后运行 | 能出图,但为收盘快照(OI/价格不再变),报文里注明 |

## 不要做的事

- ❌ 不要问"要跑哪些标的"——固定 SPY / QQQ
- ❌ 不要问"要不要 ±25 档"——已固定
- ❌ 不要漏掉 GEX×VEX 表
- ❌ 不要用预测性绝对表述(见措辞纪律)
- ❌ 不要把报文写进 HTML
- ❌ 不要试图从长桥拿 SPX/NDX/VIX 期权(已验证全空,别浪费调用)
