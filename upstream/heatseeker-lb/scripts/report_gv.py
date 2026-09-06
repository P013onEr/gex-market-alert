"""Pull SPY/QQQ 0DTE, compute real GEX + BS-modelled VEX (vanna).
Prints structured numbers for the heatseeker chat report (点位 + GEX×VEX).
Persisted in the skill dir (scratchpad is not durable). Does not write HTML."""
import json, shutil, subprocess, time, math
from datetime import date, datetime
from pathlib import Path
from collections import defaultdict


def _find_longbridge() -> str:
    """定位 longbridge CLI:先看 ~/bin,再退回 PATH(别人可能装在别处)。"""
    p = Path.home() / "bin" / "longbridge"
    if p.exists():
        return str(p)
    return shutil.which("longbridge") or str(p)


LB = _find_longbridge()
N = 25
T_HOURS = 3.0                      # nominal 0DTE time-to-close assumption (stated)
T = T_HOURS / 24 / 365


def lb(args):
    r = subprocess.run([LB, *args, "--format", "json"], capture_output=True, text=True)
    s = r.stdout.lstrip()
    if not s or s[0] not in "[{":
        return []
    try:
        d, _ = json.JSONDecoder().raw_decode(s)
        return d
    except Exception:
        return []


def spot_of(c):
    return float(lb(["quote", f"{c}.US"])[0]["last"])


def near_exp(c):
    d = lb(["option", "chain", f"{c}.US"])
    t = date.today().isoformat()
    fut = sorted(r["expiry_date"] for r in d if r.get("expiry_date") and r["expiry_date"] >= t)
    return fut[0]


def strikes_of(c, e):
    d = lb(["option", "chain", f"{c}.US", "--date", e])
    return sorted({float(r["strike"]) for r in d if r.get("strike")})


def num(v):
    if v is None:
        return 0.0
    if isinstance(v, str):
        v = v.strip()
        if v.endswith("%"):
            return float(v[:-1]) / 100
        return float(v) if v else 0.0
    return float(v)


def phi(x):
    return math.exp(-x * x / 2) / math.sqrt(2 * math.pi)


def m(x):
    return f"{x/1e6:+.1f}M"


for name, code in [("SPY", "SPY"), ("QQQ", "QQQ")]:
    S = spot_of(code)
    e = near_exp(code)
    ks = strikes_of(code, e)
    sk = min(ks, key=lambda x: abs(x - S))
    i = ks.index(sk)
    win = ks[max(0, i - N): i + N + 1]
    ymd = datetime.strptime(e, "%Y-%m-%d").strftime("%y%m%d")
    syms = []
    for k in win:
        kc = int(round(k * 1000))
        syms.append((k, "C", f"{code}{ymd}C{kc}.US"))
        syms.append((k, "P", f"{code}{ymd}P{kc}.US"))
    g = {}
    alls = [s[2] for s in syms]
    for j in range(0, len(alls), 40):
        rows = lb(["calc-index", *alls[j:j + 40], "--fields", "gamma,oi,iv,strike"])
        if not rows:
            for one in alls[j:j + 40]:
                rr = lb(["calc-index", one, "--fields", "gamma,oi,iv,strike"])
                if rr:
                    g[rr[0]["symbol"]] = rr[0]
                time.sleep(0.2)
        else:
            for r in rows:
                g[r["symbol"]] = r
        time.sleep(1)

    gex = defaultdict(float)
    vex = defaultdict(float)
    for k, cp, sym in syms:
        d = g.get(sym)
        if not d:
            continue
        gm = num(d.get("gamma"))
        oi = num(d.get("oi"))
        iv = num(d.get("iv"))
        sign = 1 if cp == "C" else -1
        gex[k] += sign * gm * oi * S * 100
        if iv > 0:
            srt = iv * math.sqrt(T)
            d1 = (math.log(S / k) + 0.5 * iv * iv * T) / srt
            d2 = d1 - srt
            vanna = -phi(d1) * d2 / iv
            vex[k] += sign * vanna * oi * S

    print(f"\n#### {name}  spot {S:.2f}  exp {e}")
    top = sorted(gex.items(), key=lambda kv: abs(kv[1]), reverse=True)[:6]
    for k, gv in top:
        vv = vex.get(k, 0.0)
        pos = "上方" if k > S else ("下方" if k < S else "现价")
        agree = "同向" if (gv >= 0) == (vv >= 0) else "背离"
        print(f"  {k:>6g} {pos}  GEX {m(gv):>7}   VEX {m(vv):>7}  {agree}")
    vtop = sorted(vex.items(), key=lambda kv: abs(kv[1]), reverse=True)[:3]
    print("  |VEX| 最大档:", ", ".join(f"{k:g}({m(v)})" for k, v in vtop))
