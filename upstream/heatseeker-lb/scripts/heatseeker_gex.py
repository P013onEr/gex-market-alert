"""Heatseeker-LB — 0DTE NetGEX map for SPY / QQQ, SpotGamma-style.

One panel per ticker, each a vertical strike ladder:
  - King   = strongest NEGATIVE GEX strike  (ceiling / amplification pole) ★
  - Floor  = strongest POSITIVE GEX strike  (absorption floor / support)
  - Pillow = the biggest positive wall wedged between Price and King (a cushion)
  - Price  = current spot, snapped to its nearest strike, white ◀ marker
  - Auto-generated structure narrative at the bottom (box / asymmetric /
    positive-gamma-dominant), plus a per-row auto-contrast text colour.

Colour: green = positive GEX (absorbs / dampens volatility), rose→magenta =
negative (amplifies), yellow = extreme positive.

Data source: longbridge CLI only (~/bin/longbridge). Coverage is SPY + QQQ —
Longbridge serves no US index options, so there is no index column.

NOT included: the left % bubbles (need intraday snapshots for a baseline) and
the VEX/vanna surface (no vanna in the feed). For VEX numbers in the chat
report, run report_gv.py alongside this script.

Output: <skill>/output/heatseeker_gex.html
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import pandas as pd

# ---- Config ----
TICKERS = [("SPY", "SPY"), ("QQQ", "QQQ")]   # Longbridge has no index options
N_STRIKES_EACH_SIDE = 25
CALC_BATCH = 40
CALC_GAP = 1.0
def _find_longbridge() -> str:
    """定位 longbridge CLI:先看 ~/bin,再退回 PATH(别人可能装在别处)。"""
    p = Path.home() / "bin" / "longbridge"
    if p.exists():
        return str(p)
    return shutil.which("longbridge") or str(p)


LB = _find_longbridge()
OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output"
COL_CYAN = "#00f5d4"
COL_GOLD = "#ffd166"
COL_BG = "#0a1216"
COL_PANEL = "#0d1f24"
COL_TEXT = "#e8f4f0"
COL_DIM = "#8aa5a0"
COL_AXIS = "#2a3a40"


# ============================================================
# Longbridge CLI fetchers  (self-contained copy)
# ============================================================

def lb_json(args: list[str]) -> list:
    r = subprocess.run([LB, *args, "--format", "json"],
                       capture_output=True, text=True)
    s = r.stdout.lstrip()
    if not s or s[0] not in "[{":
        return []
    try:
        d, _ = json.JSONDecoder().raw_decode(s)
        return d
    except Exception:
        return []


def get_spot(code: str) -> float:
    d = lb_json(["quote", f"{code}.US"])
    if not d:
        raise RuntimeError(f"{code}: quote failed (auth? market data?)")
    return float(d[0]["last"])


def nearest_expiry(code: str) -> str:
    d = lb_json(["option", "chain", f"{code}.US"])
    today = date.today().isoformat()
    exps = sorted(r["expiry_date"] for r in d if r.get("expiry_date"))
    future = [e for e in exps if e >= today]
    if not future:
        raise RuntimeError(f"{code}: no future expiry in chain")
    return future[0]


def get_strikes(code: str, exp: str) -> list[float]:
    d = lb_json(["option", "chain", f"{code}.US", "--date", exp])
    return sorted({float(r["strike"]) for r in d if r.get("strike") is not None})


def build_symbols(code: str, exp: str, strikes: list[float]) -> list[tuple]:
    ymd = datetime.strptime(exp, "%Y-%m-%d").strftime("%y%m%d")
    out = []
    for k in strikes:
        kc = int(round(k * 1000))
        out.append((k, "CALL", f"{code}{ymd}C{kc}.US"))
        out.append((k, "PUT", f"{code}{ymd}P{kc}.US"))
    return out


def fetch_greeks(sym_tuples: list[tuple]) -> dict:
    result: dict = {}
    syms = [t[2] for t in sym_tuples]
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
        time.sleep(CALC_GAP)
    return result


def compute_net_gex(sym_tuples: list[tuple], greeks: dict, spot: float) -> pd.DataFrame:
    net: dict = defaultdict(float)
    for strike, cp, symbol in sym_tuples:
        g = greeks.get(symbol)
        if not g:
            continue
        gamma = float(g.get("gamma") or 0)
        oi = float(g.get("oi") or 0)
        sign = 1 if cp == "CALL" else -1
        net[strike] += sign * gamma * oi * spot * 100
    if not net:
        return pd.DataFrame(columns=["strike", "net_gex"])
    df = pd.DataFrame([{"strike": k, "net_gex": v} for k, v in net.items()])
    return df.sort_values("strike", ascending=False).reset_index(drop=True)


# ============================================================
# Formatting + palette
# ============================================================

def fmt_money(v: float) -> str:
    if pd.isna(v):
        return "—"
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= 1e6:
        return f"{sign}${a / 1e6:.1f}M"
    if a >= 1e3:
        return f"{sign}${a / 1e3:.0f}K"
    return f"{sign}${a:.0f}"


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
    if vmax_abs <= 0:
        return "#1c3a3f"
    intensity = min(abs(v) / vmax_abs, 1.0)
    return _interp(POS_STOPS if v >= 0 else NEG_STOPS, intensity)


def text_on(rgb_str: str) -> str:
    """Pick a readable text colour for a given background: dark on light rows,
    near-white on dark rows (perceived luminance, threshold ~150)."""
    nums = re.findall(r"\d+", rgb_str)
    r, g, b = (int(x) for x in nums[:3])
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    return "#0a1216" if lum > 150 else "#eef6f3"


# ============================================================
# Structure analysis + narrative
# ============================================================

def build_story(name, spot, spot_k, price_gex, local_gex, king_k, king_v,
                floor_k, floor_v, pillow_k, pillow_v) -> str:
    lines = []
    lo, hi = sorted([king_k, floor_k])
    is_box = (king_k != floor_k) and (lo <= spot <= hi)
    # Is there a real negative wall, or is positive gamma just dominant?
    strong_king = abs(king_v) >= floor_v * 0.25
    # Regime from the net gamma of the strikes AROUND price, not one noisy strike.
    regime = ("正 gamma —— 被压制、低波动"
              if local_gex >= 0 else "负 gamma —— 波动被放大、不稳")

    if is_box:
        lines.append('<p class="h">FLOOR 与 CEILING 框定的箱体</p>')
        lines.append(
            f'<p><b>FLOOR {floor_k:g}</b> {fmt_money(floor_v)} 托底、'
            f'<b>CEILING(King){king_k:g}</b> {fmt_money(king_v)} 封顶,'
            f'现价 {spot:.2f} 夹在中间（该档 {fmt_money(price_gex)}）。'
            f'箱体宽 <b>{hi - lo:g} 点</b>,下方吸收、上方放大 → 区间内倾向被 pin。</p>')
    elif not strong_king:
        lines.append('<p class="h">正 gamma 主导,无有效硬顶</p>')
        lines.append(
            f'<p>最强正墙 <b>FLOOR {floor_k:g}</b> {fmt_money(floor_v)} 是支撑/磁吸;'
            f'负墙都很小（最负 {king_k:g} 仅 {fmt_money(king_v)}）,'
            f'没有能封顶的负 gamma → 倾向被钉、低波动。</p>')
    else:
        if king_k > spot and floor_k > spot:
            where = "都在现价上方"
        elif king_k < spot and floor_k < spot:
            where = "都在现价下方"
        else:
            where = "分列现价两侧、但现价不在其间"
        lines.append('<p class="h">非对称结构（不是干净箱体）</p>')
        lines.append(
            f'<p>最强负墙 <b>KING {king_k:g}</b> {fmt_money(king_v)}、'
            f'最强正墙 <b>FLOOR {floor_k:g}</b> {fmt_money(floor_v)} {where}。</p>')

    lines.append(f'<p>现价 {spot:.2f} 一带净 gamma <b>{fmt_money(local_gex)}</b> → <b>{regime}</b>。</p>')
    if pillow_k is not None:
        lines.append(
            f'<p><b>PILLOW {pillow_k:g}</b> {fmt_money(pillow_v)} 垫在 King 与现价之间,'
            f'上冲时的缓冲正墙。</p>')
    lines.append('<p class="legend">绿 = 正 GEX（吸收/压制波动）　·　'
                 '红紫 = 负 GEX（放大波动）　·　白 = 现价</p>')
    return f'<div class="story">{"".join(lines)}</div>'


def render_detail(name, spot, exp, gex_df: pd.DataFrame) -> str:
    if gex_df.empty:
        return f'<section class="ticker"><div class="thead">{name} · no data</div></section>'

    vmax = float(gex_df["net_gex"].abs().max())
    king_k = float(gex_df.loc[gex_df["net_gex"].idxmin(), "strike"])
    king_v = float(gex_df["net_gex"].min())
    floor_k = float(gex_df.loc[gex_df["net_gex"].idxmax(), "strike"])
    floor_v = float(gex_df["net_gex"].max())
    spot_k = float(gex_df.iloc[(gex_df["strike"] - spot).abs().argmin()]["strike"])
    price_gex = float(gex_df.loc[gex_df["strike"] == spot_k, "net_gex"].iloc[0])

    # Local regime = net GEX of the ±2 strikes around price (one strike is noisy).
    order = gex_df.reset_index(drop=True)
    pi = int((order["strike"] - spot).abs().idxmin())
    local_gex = float(order.iloc[max(0, pi - 2): pi + 3]["net_gex"].sum())

    # Pillow only makes sense when King is a real ceiling ABOVE price: the biggest
    # positive wall wedged between price and King, excluding the Floor itself.
    pillow_k = pillow_v = None
    if king_k > spot_k:
        cand = gex_df[(gex_df["strike"] > spot_k) & (gex_df["strike"] < king_k)
                      & (gex_df["net_gex"] > 0) & (gex_df["strike"] != floor_k)]
        if not cand.empty:
            pillow_k = float(cand.loc[cand["net_gex"].idxmax(), "strike"])
            pillow_v = float(cand["net_gex"].max())

    rows_html = []
    for _, r in gex_df.iterrows():
        k = float(r["strike"])
        g = float(r["net_gex"])
        color = value_color(g, vmax)
        fg = text_on(color)
        cls = []
        tag = ""
        if k == spot_k:
            cls.append("price")
            tag = '<span class="tag t-price">◀ PRICE</span>'
        elif k == king_k:
            cls.append("king")
            tag = '<span class="tag t-king">★ KING</span>'
        elif k == floor_k:
            cls.append("floor")
            tag = '<span class="tag t-floor">FLOOR</span>'
        elif pillow_k is not None and k == pillow_k:
            tag = '<span class="tag t-pillow">PILLOW</span>'
        kd = f"{int(round(spot))}" if k == spot_k else f"{k:g}"
        rows_html.append(
            f'<div class="drow {" ".join(cls)}" style="background:{color};color:{fg}">'
            f'<span class="dk">{kd}</span>'
            f'<span class="dv">{fmt_money(g)}</span>{tag}</div>')

    story = build_story(name, spot, spot_k, price_gex, local_gex, king_k, king_v,
                        floor_k, floor_v, pillow_k, pillow_v)
    return (
        f'<section class="ticker">'
        f'<div class="thead">{name} <span class="g">GEX</span> · spot {spot:.2f} '
        f'· <span class="dte">0DTE {exp}</span></div>'
        f'<div class="ladder">{"".join(rows_html)}</div>{story}</section>')


PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Heatseeker-LB — 0DTE NetGEX</title>
<style>
  :root {{ --bg:{bg}; --panel:{panel}; --text:{text}; --dim:{dim}; --axis:{axis};
           --cyan:{cyan}; --gold:{gold}; }}
  * {{ box-sizing: border-box; }}
  body {{ background: var(--bg); color: var(--text); margin: 0; padding: 20px 24px;
    font-family: -apple-system, "SF Pro Display", Inter, system-ui, sans-serif; }}
  h1 {{ font-size: 17px; margin: 0 0 4px; }}
  .sub {{ color: var(--dim); font-size: 12px; margin-bottom: 18px; }}
  .grid {{ display: grid; grid-template-columns: repeat({ncols}, minmax(340px, 460px));
    gap: 26px; align-items: start; }}
  .ticker {{ background: var(--panel); border: 1px solid var(--axis);
    border-radius: 10px; overflow: hidden; }}
  .thead {{ padding: 12px 16px; font-weight: 800; font-size: 15px;
    border-bottom: 1px solid var(--axis); letter-spacing: .3px; }}
  .thead .g {{ color: var(--cyan); }}
  .thead .dte {{ color: var(--gold); font-weight: 700; }}
  .ladder {{ display: flex; flex-direction: column; }}
  .drow {{ display: grid; grid-template-columns: 62px 1fr auto; align-items: center;
    gap: 8px; height: 30px; padding: 0 12px;
    font-family: "SF Mono", Menlo, monospace; font-size: 13px;
    border-left: 3px solid transparent; }}
  .drow .dk {{ color: inherit; opacity: .82; }}
  .drow .dv {{ text-align: right; font-weight: 700; color: inherit; }}
  .drow.price {{ border-left-color: #fff; }}
  .drow.price .dk {{ font-weight: 800; opacity: 1; }}
  .drow.king {{ outline: 2px solid #f0abfc; outline-offset: -2px; }}
  .tag {{ font-size: 10px; font-weight: 800; letter-spacing: .4px;
    padding: 2px 7px; border-radius: 10px; white-space: nowrap; }}
  .t-price {{ background: #fff; color: #0a1216; }}
  .t-king {{ background: #f0abfc; color: #3b0764; }}
  .t-floor {{ background: #0a1216; color: var(--gold); border: 1px solid var(--gold); }}
  .t-pillow {{ background: rgba(0,0,0,.35); color: #d1fae5; border: 1px solid #34d399; }}
  .story {{ padding: 14px 16px; border-top: 1px solid var(--axis); font-size: 12.5px;
    line-height: 1.55; }}
  .story p {{ margin: 0 0 8px; color: #cfe3de; }}
  .story .h {{ color: var(--gold); font-weight: 800; font-size: 13px; }}
  .story .legend {{ color: var(--dim); font-size: 11px; margin-top: 10px; }}
  .story b {{ color: var(--text); }}
</style></head>
<body>
  <h1>🎯 末日大盘期权 — NetGEX <span style="color:var(--dim);font-size:12px">· 0DTE · GEX per $1 move (call+ / put−)</span></h1>
  <div class="sub">{generated} · ±{n} strikes · King = strongest −GEX (ceiling) · Floor = strongest +GEX · VEX/vanna not included (no Longbridge data)</div>
  <div class="grid">{sections}</div>
</body></html>
"""


def main() -> int:
    print(f"Heatseeker-LB — 0DTE NetGEX via {LB}")
    sections = []
    for name, code in TICKERS:
        print(f"\n=== {name} ({code}.US) ===")
        spot = get_spot(code)
        exp = nearest_expiry(code)
        strikes = get_strikes(code, exp)
        print(f"  spot ${spot:.2f} · expiry {exp} · {len(strikes)} strikes")
        if not strikes:
            sections.append(render_detail(name, spot, exp,
                                          pd.DataFrame(columns=["strike", "net_gex"])))
            continue
        spot_strike = min(strikes, key=lambda s: abs(s - spot))
        idx = strikes.index(spot_strike)
        window = strikes[max(0, idx - N_STRIKES_EACH_SIDE): idx + N_STRIKES_EACH_SIDE + 1]
        sym_tuples = build_symbols(code, exp, window)
        greeks = fetch_greeks(sym_tuples)
        gex_df = compute_net_gex(sym_tuples, greeks, spot)
        print(f"  greeks {len(greeks)}/{len(sym_tuples)} · strikes {len(gex_df)}")
        if not gex_df.empty:
            kk = gex_df.loc[gex_df['net_gex'].idxmin()]
            ff = gex_df.loc[gex_df['net_gex'].idxmax()]
            print(f"  KING  {kk['strike']:g}  {fmt_money(kk['net_gex'])}")
            print(f"  FLOOR {ff['strike']:g}  {fmt_money(ff['net_gex'])}")
        sections.append(render_detail(name, spot, exp, gex_df))

    html = PAGE.format(
        generated=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        n=N_STRIKES_EACH_SIDE, ncols=len(TICKERS), sections="".join(sections),
        bg=COL_BG, panel=COL_PANEL, text=COL_TEXT, dim=COL_DIM, axis=COL_AXIS,
        cyan=COL_CYAN, gold=COL_GOLD)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUTPUT_DIR / "heatseeker_gex.html"
    out.write_text(html, encoding="utf-8")
    print(f"\n✅ Saved: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
