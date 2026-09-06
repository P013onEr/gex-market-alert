"""longbridge serve 的常驻进程封装（需要 CLI >= 0.28.0）。

为什么用它：每条 `longbridge <cmd>` 都要重新启动进程、加载 token、探测区域，
一次取 102 个期权合约的 gamma 要 6.7s；serve 把这些开销付一次，同一批数据
0.9s 拿到。上游允许 8 个请求并发，合约越多差距越大。

跟 CLI 的两个关键差异，本模块负责吸收掉：

1. serve 返回的是**原始 OpenAPI 字段名**，不是 `--format json` 重塑过的名字
   （last_done 而非 last、strike_price 而非 strike、open_interest 而非 oi）。
   下面的包装方法把字段名归一化成 CLI 风格，所以上层代码不用改。
   注意只归一化**名字**，不动**值的格式**——CLI 的 calc-index 把 iv 印成
   "11.18%"，serve 给 "0.1118"；oi 在 CLI 是字符串 "3081"，serve 是整数 3081。
   走 float() 的地方两者通用，直接当字符串用的地方要留意。

2. **参数名写错不会报错**，而是静默忽略该参数返回默认结果——把
   history_candlesticks_by_date 的 start 写成 start_date，会安安静静返回
   最近 1000 根而不是你要的那 7 根。所以 call() 对已知方法做参数名白名单
   校验，写错立刻抛异常。

官方文档：https://open.longbridge.com/docs （方法按 SDK 调用名查）
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

__all__ = ["LBServe", "find_longbridge", "ServeError"]


def find_longbridge() -> str:
    """定位 CLI：LONGBRIDGE_BIN → ~/bin → PATH。"""
    env = os.environ.get("LONGBRIDGE_BIN")
    if env and Path(env).exists():
        return env
    p = Path.home() / "bin" / "longbridge"
    if p.exists():
        return str(p)
    return shutil.which("longbridge") or str(p)


class ServeError(RuntimeError):
    """serve 返回 JSON-RPC error，或进程本身出问题。"""

    def __init__(self, method: str, code: int | None, message: str):
        self.method, self.code = method, code
        # -32602 参数错，重试无用；-32000 上游失败，可以重试
        retry = "（重试无用，参数有问题）" if code == -32602 else ""
        super().__init__(f"{method} 失败 [{code}]{retry}: {message}")


# 已知方法的合法参数名。serve 会静默忽略不认识的参数，所以宁可在本地拦住。
# 留空集合 = 该方法不接受参数；方法不在表里 = 不校验（照原样发出去）。
_PARAM_WHITELIST: dict[str, set[str]] = {
    "quote.quote": {"symbols"},
    "quote.static_info": {"symbols"},
    "quote.option_quote": {"symbols"},
    "quote.calc_indexes": {"symbols", "indexes"},
    "quote.option_chain_expiry_date_list": {"symbol"},
    "quote.option_chain_info_by_date": {"symbol", "expiry_date"},
    "quote.history_candlesticks_by_date": {
        "symbol", "period", "adjust_type", "start", "end", "trade_sessions",
    },
    "quote.history_candlesticks_by_offset": {
        "symbol", "period", "adjust_type", "forward", "time", "count", "trade_sessions",
    },
    "quote.candlesticks": {"symbol", "period", "count", "adjust_type", "trade_sessions"},
    "quote.intraday": {"symbol"},
    "quote.depth": {"symbol"},
    "quote.trades": {"symbol", "count"},
    "quote.capital_flow": {"symbol"},
    "quote.capital_distribution": {"symbol"},
    "quote.market_temperature": {"market"},
    "quote.trading_days": {"market", "begin", "end"},
    "initialize": set(),
}

# serve 原始字段名 → CLI --format json 的名字
_RENAME = {
    "last_done": "last",
    "strike_price": "strike",
    "open_interest": "oi",
    "expiry_date": "exp",
    "implied_volatility": "iv",
    "total_market_value": "mktcap",
    "pe_ttm_ratio": "pe",
    "pb_ratio": "pb",
    "dividend_ratio_ttm": "dps_rate",
    "timestamp": "time",
}


def _rename(row: dict, extra: dict[str, str] | None = None) -> dict:
    m = dict(_RENAME)
    if extra:
        m.update(extra)
    return {m.get(k, k): v for k, v in row.items()}


class LBServe:
    """一个常驻 serve 进程。用作 context manager，退出时关掉。

        with LBServe() as lb:
            spot = lb.spot("SPY")
            exp  = lb.nearest_expiry("SPY")
            rows = lb.calc_indexes(syms, ["gamma", "oi"])

    进程随 stdin 关闭而退出，不会变成孤儿。
    """

    def __init__(self, lb_bin: str | None = None):
        self.lb = lb_bin or find_longbridge()
        self._id = 0
        self._pending: dict[int, dict] = {}   # 乱序响应的暂存区
        self.proc = subprocess.Popen(
            [self.lb, "serve"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1,
        )

    # ── 底层 JSON-RPC ────────────────────────────────────────────────────
    def _check_params(self, method: str, params: dict | None) -> None:
        if params is None or method not in _PARAM_WHITELIST:
            return
        allowed = _PARAM_WHITELIST[method]
        bad = set(params) - allowed
        if bad:
            raise ServeError(
                method, -32602,
                f"参数名 {sorted(bad)} 不被识别，serve 会静默忽略它们并返回默认结果；"
                f"该方法接受 {sorted(allowed) or '（无参数）'}")

    def _send(self, method: str, params: dict | None) -> int:
        self._check_params(method, params)
        self._id += 1
        req: dict = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            req["params"] = params
        if self.proc.poll() is not None:
            raise ServeError(method, None, f"serve 进程已退出（码 {self.proc.returncode}）")
        self.proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        return self._id

    def _recv(self, want: int, method: str):
        """读到 id == want 的响应；顺路收下先到的其他响应。"""
        if want in self._pending:
            return self._unwrap(self._pending.pop(want), method)
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise ServeError(method, None, "serve 关闭了 stdout（进程死了？）")
            msg = json.loads(line)
            if "id" not in msg:      # 行情推送通知，本模块不消费
                continue
            if msg["id"] == want:
                return self._unwrap(msg, method)
            self._pending[msg["id"]] = msg

    @staticmethod
    def _unwrap(msg: dict, method: str):
        if "error" in msg:
            e = msg["error"]
            raise ServeError(method, e.get("code"), e.get("message", ""))
        return msg.get("result")

    def call(self, method: str, params: dict | None = None):
        """发一个请求，等它的结果。"""
        return self._recv(self._send(method, params), method)

    def call_many(self, calls: list[tuple[str, dict | None]]) -> list:
        """一次发全部请求再收结果，利用上游 8 并发。顺序与传入一致。"""
        ids = [self._send(m, p) for m, p in calls]
        return [self._recv(i, m) for i, (m, _) in zip(ids, calls)]

    # ── CLI 风格的包装（字段名已归一化）─────────────────────────────────
    def quote(self, symbols: list[str]) -> list[dict]:
        return [_rename(r) for r in self.call("quote.quote", {"symbols": symbols})]

    def spot(self, code: str, market: str = "US") -> float:
        sym = code if "." in code else f"{code}.{market}"
        d = self.quote([sym])
        if not d or d[0].get("last") in (None, ""):
            raise ServeError("quote.quote", None, f"{sym} 没有现价（登录？行情权限？）")
        return float(d[0]["last"])

    def calc_indexes(self, symbols: list[str], indexes: list[str]) -> list[dict]:
        """对应 CLI `calc-index --fields`。一次几百个 symbol 没问题。"""
        rows = self.call("quote.calc_indexes",
                         {"symbols": symbols, "indexes": indexes})
        return [_rename(r) for r in rows]

    def option_expiries(self, code: str, market: str = "US") -> list[dict]:
        """对应 CLI `option chain <sym>`：包成 [{"expiry_date": ...}] 的形状。"""
        sym = code if "." in code else f"{code}.{market}"
        return [{"expiry_date": d}
                for d in self.call("quote.option_chain_expiry_date_list", {"symbol": sym})]

    def nearest_expiry(self, code: str, market: str = "US") -> str:
        from datetime import date
        today = date.today().isoformat()
        exps = sorted(r["expiry_date"] for r in self.option_expiries(code, market))
        future = [e for e in exps if e >= today]
        if not future:
            raise ServeError("quote.option_chain_expiry_date_list", None,
                             f"{code} 没有未来到期日")
        return future[0]

    def option_strikes(self, code: str, expiry: str, market: str = "US") -> list[dict]:
        """对应 CLI `option chain <sym> --date`，但**不含**报价。

        CLI 那版会额外给 call_last/call_iv/put_last/put_iv，serve 这里只有
        strike 和两腿的合约代码——要报价就拿 call_symbol/put_symbol 再走
        option_quote 或 calc_indexes。好处是合约代码由服务端给出，不用自己
        按「行权价×1000 不补零」拼，省掉那个老坑。
        """
        sym = code if "." in code else f"{code}.{market}"
        rows = self.call("quote.option_chain_info_by_date",
                         {"symbol": sym, "expiry_date": expiry})
        return [_rename(r, {"price": "strike"}) for r in rows]

    def kline_history(self, symbol: str, start: str, end: str,
                      period: str = "day", adjust: str = "forward") -> list[dict]:
        """对应 CLI `kline history`。period 必须小写：1m 5m 15m 30m 1h day week month year。"""
        rows = self.call("quote.history_candlesticks_by_date", {
            "symbol": symbol, "period": period, "adjust_type": adjust,
            "start": start, "end": end,
        })
        return [_rename(r) for r in rows]

    # ── 生命周期 ─────────────────────────────────────────────────────────
    def close(self) -> None:
        if self.proc.poll() is None:
            try:
                self.proc.stdin.close()      # stdin EOF → serve 自行退出
            except Exception:
                pass
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def __enter__(self) -> "LBServe":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
