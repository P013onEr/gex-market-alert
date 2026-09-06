"""GEX-Matrix-LB — 任意标的 × 未来 N 个到期日的 NetGEX 矩阵热力图。

heatseeker-lb 的姊妹脚本:那边是 SPY/QQQ 0DTE 单列阶梯,这边是
「strike 行 × 到期日列」的二维矩阵(SpotGamma 多到期视图的样子)。
取数链、GEX 公式、调色板与自动对比文字色全部沿用 heatseeker-lb。

用法:
    python3 gex_matrix.py NVDA                     # 默认最近 5 个到期日,±20 strikes
    python3 gex_matrix.py NVDA --expiries 8        # 最近 8 个到期日
    python3 gex_matrix.py NVDA --days 30           # 未来 30 天内的所有到期日
    python3 gex_matrix.py TSLA --width 25          # 现价上下各 25 档
    python3 gex_matrix.py NVDA --out /tmp/x.html   # 指定输出路径

GEX_cell = Σ sign · |gamma| · OI · 100 · spot   (call 正 / put 负,按 strike×到期聚合)

口径与限制(与 heatseeker-lb 相同):
  - OI 为 T+1(OCC 次日发布),盘中跑 = 今日 gamma/spot × 昨收 OI
  - 颜色按整张矩阵的全局 |max| 归一 → 近月列自然更醒目,远月列偏淡,这是特征不是 bug
  - 参考图里的 ±% 小气泡(相对前日变化)需要逐日快照基线,长桥无此数据,不做
  - longbridge CLI 超限时静默返空数组:批量 CALC_BATCH + 间隔 CALC_GAP 不能去掉

Output: <skill>/output/gex_matrix_<TICKER>.html
"""
from __future__ import annotations

import argparse
import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

# ---- Config(与 heatseeker-lb 一致的节流与视觉)----
CALC_BATCH = 40
CALC_GAP = 1.0


def _find_longbridge() -> str:
    """定位 longbridge CLI:先看 ~/bin,再退回 PATH(别人可能装在别处)。"""
    p = Path.home() / "bin" / "longbridge"
    if p.exists():
        return str(p)
    return shutil.which("longbridge") or str(p)


LB = _find_longbridge()


# ── serve 后端（需要 CLI >= 0.28.0，可选）─────────────────────
# 每条 `longbridge <cmd>` 都要重启进程、加载 token、探测区域。serve 把这份
# 开销付一次，之后走 JSON-RPC，且没有 CLI 那个「一次传太多 symbol 就静默
# 返空」的限制，所以 serve 分支不需要 CALC_GAP 那种节流。
# 起不来就自动回退到逐条 CLI 路径。强制走 CLI：LB_NO_SERVE=1
SERVE_BATCH = 300
_SERVE = None
_SERVE_TRIED = False


def _serve():
    global _SERVE, _SERVE_TRIED
    if _SERVE_TRIED:
        return _SERVE
    _SERVE_TRIED = True
    if os.environ.get("LB_NO_SERVE") == "1":
        return None
    try:
        # 优先用仓库内自带的副本(开源用户 clone 下来即可用);
        # 再退回本机 ~/.claude/skills/_shared(个人环境里的共享版本)。
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        sys.path.insert(1, str(Path.home() / ".claude/skills/_shared"))
        from lb_serve import LBServe
        _SERVE = LBServe(LB)
        atexit.register(_SERVE.close)
    except Exception as e:
        print(f"  (serve 不可用，回退 CLI：{e})", flush=True)
        _SERVE = None
    return _SERVE
OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output"
COL_CYAN = "#00f5d4"
COL_GOLD = "#ffd166"
COL_BG = "#0a1216"
COL_PANEL = "#0d1f24"
COL_TEXT = "#e8f4f0"
COL_DIM = "#8aa5a0"
COL_AXIS = "#2a3a40"


# ============================================================
# Longbridge CLI fetchers(照抄 heatseeker-lb,自包含)
# ============================================================

def lb_json(args: list[str]) -> list:
    """跑 `longbridge <args> --format json`，返回解析后的 JSON。

    CLI 真的出错时抛异常，不再静默返回 []——静默返空会让上层照常算下去、
    画出一张全 0 的热图，比直接失败危险得多。
    「CLI 成功但该标的确实没数据」仍然正常返回 []，上层的分批重试逻辑照旧。
    更新提示走 stderr，不污染 stdout 的 JSON（0.28.4 实测）。
    """
    r = subprocess.run([LB, *args, "--format", "json"],
                       capture_output=True, text=True)
    cmd = "longbridge " + " ".join(args)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd} 退出码 {r.returncode}: "
                           f"{(r.stderr or r.stdout).strip()[:300]}")
    s = r.stdout.lstrip()
    if not s:
        raise RuntimeError(f"{cmd} 无输出（stderr: {r.stderr.strip()[:200]}）")
    if s[0] not in "[{":
        raise RuntimeError(f"{cmd} 输出不是 JSON（未登录？）: {s[:200]!r}")
    try:
        d, _ = json.JSONDecoder().raw_decode(s)
    except ValueError as e:
        raise RuntimeError(f"{cmd} JSON 解析失败: {e}；输出开头 {s[:200]!r}")
    return d


def get_quote(code: str) -> tuple[float, float | None]:
    s = _serve()
    if s is not None:
        d = s.quote([f"{code}.US"])
    else:
        d = lb_json(["quote", f"{code}.US"])
    if not d:
        raise RuntimeError(f"{code}: quote failed(auth? 行情权限?)")
    last = float(d[0]["last"])
    chg = None
    try:
        pc = float(d[0].get("prev_close") or 0)
        if pc > 0:
            chg = (last - pc) / pc * 100
    except (TypeError, ValueError):
        pass
    return last, chg


def future_expiries(code: str, *, n: int | None, days: int | None) -> list[str]:
    """未来到期日:--expiries 取最近 n 个;--days 取窗口内全部(至少给 1 个)。"""
    s = _serve()
    if s is not None:
        d = s.option_expiries(code)
    else:
        d = lb_json(["option", "chain", f"{code}.US"])
    today = date.today().isoformat()
    exps = sorted({r["expiry_date"] for r in d if r.get("expiry_date")})
    future = [e for e in exps if e >= today]
    if not future:
        raise RuntimeError(f"{code}: 期权链里没有未来到期日(标的没有期权?)")
    if days is not None:
        cutoff = (date.today() + timedelta(days=days)).isoformat()
        got = [e for e in future if e <= cutoff]
        return got or future[:1]
    return future[: (n or 5)]


def get_strikes(code: str, exp: str) -> list[float]:
    s = _serve()
    if s is not None:
        d = s.option_strikes(code, exp)
    else:
        d = lb_json(["option", "chain", f"{code}.US", "--date", exp])
    return sorted({float(r["strike"]) for r in d if r.get("strike") is not None})


def build_symbols(code: str, exp: str, strikes: list[float]) -> list[tuple]:
    """(strike, CALL/PUT, symbol, exp) 四元组。符号规则:strike×1000 不补零 + .US"""
    ymd = datetime.strptime(exp, "%Y-%m-%d").strftime("%y%m%d")
    out = []
    for k in strikes:
        kc = int(round(k * 1000))
        out.append((k, "CALL", f"{code}{ymd}C{kc}.US", exp))
        out.append((k, "PUT", f"{code}{ymd}P{kc}.US", exp))
    return out


def fetch_greeks(sym_tuples: list[tuple]) -> dict:
    """批量拉 gamma/oi。serve 一次几百个;CLI 路径空批(限流/无效符号)降级逐个重试。"""
    result: dict = {}
    syms = [t[2] for t in sym_tuples]
    s = _serve()
    if s is not None:
        n_batches = (len(syms) + SERVE_BATCH - 1) // SERVE_BATCH
        for i in range(0, len(syms), SERVE_BATCH):
            chunk = syms[i:i + SERVE_BATCH]
            for r in s.calc_indexes(chunk, ["gamma", "oi", "strike", "exp"]):
                if r.get("gamma") is not None:
                    result[r["symbol"]] = r
            print(f"  greeks batch {i // SERVE_BATCH + 1}/{n_batches} "
                  f"({len(result)} contracts)", flush=True)
    else:
        n_batches = (len(syms) + CALC_BATCH - 1) // CALC_BATCH
        for i in range(0, len(syms), CALC_BATCH):
            chunk = syms[i:i + CALC_BATCH]
            rows = lb_json(["calc-index", *chunk, "--fields", "gamma,oi,strike,exp"])
            if not rows:
                for one in chunk:
                    r = lb_json(["calc-index", one, "--fields", "gamma,oi,strike,exp"])
                    if r:
                        result[r[0]["symbol"]] = r[0]
                    time.sleep(0.25)
            else:
                for r in rows:
                    result[r["symbol"]] = r
            done = i // CALC_BATCH + 1
            print(f"  greeks batch {done}/{n_batches} ({len(result)} contracts)", flush=True)
            time.sleep(CALC_GAP)
    # 数据完整性闸门：宁可报错，也不要拿半张表算出一张看似正常的热图
    if not result:
        raise RuntimeError(
            f"calc-index 一个合约都没取到（共 {len(syms)} 个）——"
            "检查 `longbridge check` 登录状态与美股期权行情权限")
    if len(result) < len(syms) * 0.5:
        missing = [x for x in syms if x not in result][:5]
        raise RuntimeError(
            f"calc-index 只取到 {len(result)}/{len(syms)}，数据不足以出图；"
            f"缺失示例 {missing}")
    return result


# ============================================================
# 矩阵计算
# ============================================================

def compute_matrix(sym_tuples: list[tuple], greeks: dict,
                   spot: float) -> pd.DataFrame:
    """返回 index=strike(降序)、columns=到期日(升序)的 NetGEX 矩阵。
    某(strike,到期)在链上存在但无 gamma/OI → 0(参考图里的 $0.0K 行)。"""
    net: dict = defaultdict(float)
    seen: set = set()
    for strike, cp, symbol, exp in sym_tuples:
        seen.add((strike, exp))
        g = greeks.get(symbol)
        if not g:
            continue
        gamma = float(g.get("gamma") or 0)
        oi = float(g.get("oi") or 0)
        sign = 1 if cp == "CALL" else -1
        net[(strike, exp)] += sign * gamma * oi * spot * 100
    for key in seen:
        net.setdefault(key, 0.0)
    if not net:
        return pd.DataFrame()
    df = pd.DataFrame([{"strike": k, "exp": e, "gex": v}
                       for (k, e), v in net.items()])
    mat = df.pivot(index="strike", columns="exp", values="gex")
    return mat.sort_index(ascending=False)


# ============================================================
# Formatting + palette(照抄 heatseeker-lb)
# ============================================================

def fmt_cell(v: float) -> str:
    """参考图的 $x,xxx.xK 格式(全部以 K 计,千分位)。NaN = 该到期日没这个 strike。"""
    if pd.isna(v):
        return "·"
    a = abs(v) / 1e3
    sign = "-" if v < 0 else ""
    return f"{sign}${a:,.1f}K"


def _hex2rgb(h: str):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def _blend(c0: str, c1: str, t: float) -> str:
    r0, g0, b0 = _hex2rgb(c0)
    r1, g1, b1 = _hex2rgb(c1)
    return f"rgb({int(r0 + (r1 - r0) * t)},{int(g0 + (g1 - g0) * t)},{int(b0 + (b1 - b0) * t)})"


NEG_STOPS = [(0.00, "#1c3a3f"), (0.20, "#1e4d6e"), (0.50, "#4a2670"),
             (0.80, "#9b2a7a"), (1.00, "#d946ef")]
POS_STOPS = [(0.00, "#1c3a3f"), (0.15, "#1a8a7d"), (0.40, "#26d9b0"),
             (0.70, "#9bdf3e"), (0.90, "#e6e040"), (1.00, "#ffd166")]


def _interp(stops, t: float) -> str:
    t = max(0.0, min(1.0, t))
    for i in range(len(stops) - 1):
        p0, c0 = stops[i]
        p1, c1 = stops[i + 1]
        if p0 <= t <= p1:
            local = (t - p0) / (p1 - p0) if p1 > p0 else 0
            return _blend(c0, c1, local)
    return stops[-1][1]


def value_color(v: float, vmax_abs: float) -> str:
    if pd.isna(v) or vmax_abs <= 0:
        return "#12262b"
    intensity = min(abs(v) / vmax_abs, 1.0)
    return _interp(POS_STOPS if v >= 0 else NEG_STOPS, intensity)


def text_on(rgb_str: str) -> str:
    nums = re.findall(r"\d+", rgb_str)
    if len(nums) < 3:
        return "#eef6f3"
    r, g, b = (int(x) for x in nums[:3])
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    return "#0a1216" if lum > 150 else "#eef6f3"


# ============================================================
# 渲染
# ============================================================

PAGE = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><title>{ticker} — GEX Matrix</title>
<style>
  :root {{ --bg:{bg}; --panel:{panel}; --text:{text}; --dim:{dim}; --axis:{axis};
           --cyan:{cyan}; --gold:{gold}; }}
  * {{ box-sizing: border-box; }}
  body {{ background: var(--bg); color: var(--text); margin: 0; padding: 20px 24px;
    font-family: -apple-system, "SF Pro Display", Inter, system-ui, sans-serif; }}
  h1 {{ font-size: 17px; margin: 0 0 4px; }}
  h1 .g {{ color: var(--cyan); }}
  h1 .px {{ color: var(--gold); }}
  .sub {{ color: var(--dim); font-size: 12px; margin-bottom: 16px; }}
  .wrap {{ overflow-x: auto; }}
  table {{ border-collapse: collapse; background: var(--panel);
    border: 1px solid var(--axis); border-radius: 10px; overflow: hidden;
    font-family: "SF Mono", Menlo, monospace; font-size: 12.5px; }}
  th {{ position: sticky; top: 0; background: #0a1216; color: var(--dim);
    font-weight: 700; padding: 8px 14px; text-align: right; white-space: nowrap;
    border-bottom: 1px solid var(--axis); }}
  th.k {{ text-align: left; color: var(--text); }}
  td {{ padding: 0; height: 26px; }}
  td .cell {{ display: flex; align-items: center; justify-content: flex-end;
    gap: 6px; height: 26px; padding: 0 14px; font-weight: 700;
    white-space: nowrap; }}
  td.k {{ background: #0a1216; color: var(--text); font-weight: 800;
    padding: 0 12px; white-space: nowrap; border-right: 1px solid var(--axis); }}
  tr.spot td.k {{ background: #fff; color: #0a1216; }}
  tr.spot td.k::after {{ content: " ◀"; }}
  .star {{ font-weight: 900; }}
  .cell.king {{ outline: 2px solid #f0abfc; outline-offset: -2px; }}
  .cell.floor {{ outline: 2px solid #b98900; outline-offset: -2px; }}
  .story {{ margin-top: 14px; max-width: 900px; font-size: 12.5px; line-height: 1.55;
    color: #cfe3de; }}
  .story .h {{ color: var(--gold); font-weight: 800; }}
  .story b {{ color: var(--text); }}
  .legend {{ color: var(--dim); font-size: 11px; margin-top: 8px; }}
</style></head>
<body>
  <h1>{ticker} <span class="g">GEX</span> · spot <span class="px">{spot:.2f}{chg}</span>
      <span style="color:var(--dim);font-size:12px">· {nexp} 个到期日 · GEX per $1 move (call+ / put−)</span></h1>
  <div class="sub">{generated} · 颜色按全局 |max| 归一(近月自然更亮) · ★KING = 全局最强负墙 · FLOOR = 全局最强正墙 · OI 为 T+1</div>
  <div class="wrap">{table}</div>
  {story}
</body></html>
"""


def render(ticker: str, spot: float, chg: float | None,
           mat: pd.DataFrame) -> str:
    vmax = float(mat.abs().max().max())
    # 全局 King / Floor(带到期维度)
    king_v = float(mat.min().min())
    floor_v = float(mat.max().max())
    king_pos = mat.stack().idxmin() if king_v < 0 else None   # (strike, exp)
    floor_pos = mat.stack().idxmax() if floor_v > 0 else None
    strikes = list(mat.index)
    spot_k = min(strikes, key=lambda s: abs(s - spot))

    # 表头
    head = ['<tr><th class="k">Strike</th>']
    head += [f"<th>{e}</th>" for e in mat.columns]
    head.append("</tr>")

    rows = []
    for k in strikes:
        cls = ' class="spot"' if k == spot_k else ""
        cells = [f'<td class="k">{k:g}</td>']
        for e in mat.columns:
            v = mat.at[k, e]
            color = value_color(v, vmax)
            fg = text_on(color)
            mark = ""
            cellcls = "cell"
            if king_pos is not None and (k, e) == king_pos:
                cellcls += " king"
                mark = '<span class="star">★</span>'
            elif floor_pos is not None and (k, e) == floor_pos:
                cellcls += " floor"
            cells.append(f'<td style="background:{color};color:{fg}">'
                         f'<div class="{cellcls}">{fmt_cell(v)}{mark}</div></td>')
        rows.append(f"<tr{cls}>{''.join(cells)}</tr>")

    table = f"<table><thead>{''.join(head)}</thead><tbody>{''.join(rows)}</tbody></table>"

    # 摘要:全局 King/Floor + 每列合计
    col_sums = mat.sum()
    parts = ['<div class="story">']
    if king_pos:
        parts.append(f'<p><span class="h">★ KING</span> <b>{king_pos[0]:g}</b> @ {king_pos[1]}'
                     f'　{fmt_cell(king_v)} —— 全矩阵最强负墙(放大波动/翻转位)。</p>')
    if floor_pos:
        parts.append(f'<p><span class="h">FLOOR</span> <b>{floor_pos[0]:g}</b> @ {floor_pos[1]}'
                     f'　{fmt_cell(floor_v)} —— 全矩阵最强正墙(吸收/阻力)。</p>')
    sums = " · ".join(f"{e} <b>{fmt_cell(v)}</b>" for e, v in col_sums.items())
    parts.append(f"<p>各到期日净 GEX:{sums}</p>")
    parts.append('<p class="legend">绿 = 正 GEX(吸收/压制波动) · 红紫 = 负 GEX(放大波动) · '
                 '白行 = 现价档 · "·" = 该到期日无此 strike · $0.0K = 有挂牌但无持仓</p>')
    parts.append("</div>")

    chg_s = f" ({chg:+.2f}%)" if chg is not None else ""
    return PAGE.format(
        ticker=ticker, spot=spot, chg=chg_s, nexp=len(mat.columns),
        generated=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        table=table, story="".join(parts),
        bg=COL_BG, panel=COL_PANEL, text=COL_TEXT, dim=COL_DIM,
        axis=COL_AXIS, cyan=COL_CYAN, gold=COL_GOLD)


# ============================================================
# Main
# ============================================================

def main() -> int:
    ap = argparse.ArgumentParser(description="NetGEX matrix: strikes × expiries")
    ap.add_argument("ticker", help="US 股票代码,如 NVDA / TSLA / AAPL")
    ap.add_argument("--expiries", type=int, default=None,
                    help="最近 N 个到期日(默认 5;与 --days 互斥)")
    ap.add_argument("--days", type=int, default=None,
                    help="未来 N 天内的全部到期日(与 --expiries 互斥)")
    ap.add_argument("--width", type=int, default=20,
                    help="现价上下各取多少档 strike(默认 20)")
    ap.add_argument("--out", default=None, help="输出 HTML 路径(默认 skill 的 output/)")
    args = ap.parse_args()

    if args.expiries is not None and args.days is not None:
        ap.error("--expiries 与 --days 只能二选一")
    code = args.ticker.upper().strip()

    print(f"GEX-Matrix-LB — {code} via {LB}")
    spot, chg = get_quote(code)
    exps = future_expiries(code, n=args.expiries or (None if args.days else 5),
                           days=args.days)
    print(f"  spot ${spot:.2f} · 到期日 {len(exps)} 个: {', '.join(exps)}")

    # 每个到期日:以现价为中心取 ±width 档(各到期日 strike 网格可能不同)
    all_tuples: list[tuple] = []
    for e in exps:
        ks = get_strikes(code, e)
        if not ks:
            print(f"  [{e}] 无 strike,跳过")
            continue
        center = min(ks, key=lambda s: abs(s - spot))
        i = ks.index(center)
        window = ks[max(0, i - args.width): i + args.width + 1]
        all_tuples += build_symbols(code, e, window)
        if _serve() is None:
            time.sleep(0.3)      # CLI 路径:chain 调用之间留口气,别撞静默限流
    n_contracts = len(all_tuples)
    if _serve() is not None:
        print(f"  合约 {n_contracts} 个 · serve 批量,约 1-2s")
    else:
        est = n_contracts / CALC_BATCH * (CALC_GAP + 1.5)
        print(f"  合约 {n_contracts} 个 · 预计 ~{est:.0f}s")

    greeks = fetch_greeks(all_tuples)
    got = len(greeks)
    print(f"  greeks {got}/{n_contracts}"
          + ("" if got >= n_contracts * 0.9 else "  ⚠️ 缺口偏大,可能被限流,建议稍后重跑"))

    mat = compute_matrix(all_tuples, greeks, spot)
    if mat.empty:
        print("  ❌ 无数据(限流或标的无期权)")
        return 1

    html = render(code, spot, chg, mat)
    out = Path(args.out) if args.out else OUTPUT_DIR / f"gex_matrix_{code}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")

    king_v = float(mat.min().min())
    floor_v = float(mat.max().max())
    if king_v < 0:
        kk, ke = mat.stack().idxmin()
        print(f"  ★ KING  {kk:g} @ {ke}  {fmt_cell(king_v)}")
    if floor_v > 0:
        fk, fe = mat.stack().idxmax()
        print(f"  FLOOR   {fk:g} @ {fe}  {fmt_cell(floor_v)}")
    print(f"  ✓ {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
