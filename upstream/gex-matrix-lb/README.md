# GEX-Matrix-LB

NetGEX matrix heatmap for **any US ticker** across its next N option expiries, built on
the [Longbridge](https://longportapp.com/) CLI.

Strikes run down the rows, expiries across the columns, so you can see at a glance where
dealer gamma is concentrated — and whether that concentration sits in this week's expiry
or further out.

| Marker | Meaning |
|---|---|
| **FLOOR** (amber) | Strongest positive-gamma wall in the matrix — hedging flows suppress movement here (resistance) |
| **★ KING** (violet) | Strongest negative-gamma wall — where dealer hedging turns pro-cyclical (flip zone) |
| **◀ white row** | Current spot |
| `·` | That expiry has no such strike listed |
| `$0.0K` | Strike is listed but carries no published open interest |

Sample renders for NVDA and AVGO are committed under `output/` — open them in a browser.

Sister project to [heatseeker-lb](https://github.com/NineLooms/heatseeker-lb), which does
SPY/QQQ 0DTE as a single-column ladder. This one is the multi-expiry, single-name view.

## Why Longbridge

Free if you already have a Longbridge account, instead of a paid options data vendor.
Two constraints come with that, stated up front rather than buried:

- **No index options.** Longbridge carries no SPX / NDX / VIX options — ETF and
  single-name only. For index 0DTE you need a different source.
- **Open interest is T+1.** OCC publishes OI once daily, so intraday GEX is *today's*
  spot and gamma against *yesterday's* OI.

That second point is worth internalising rather than glossing over. Measured on the same
contract during a live session:

```
15:03:48Z   OI=31355   vol=19891
15:04:48Z   OI=31355   vol=19920
15:06:16Z   OI=31355   vol=19941
```

Fifty contracts traded in two and a half minutes; OI never moved. No vendor can give you
intraday OI, because it does not exist until nightly clearing.

What *does* move intraday is gamma, because gamma is a function of moneyness. A clean
15-minute A/B on NVDA, same session and same OI vintage:

| | T0 | T1 (+15 min) |
|---|---|---|
| spot | 225.83 | 227.27 (+0.6%) |
| FLOOR @ 230 | $249.8M | **$289.3M (+15.8%)** |

Spot moved 0.6%; the wall's strength moved 15.8%. So: **wall positions are set by OI and
hold still through the session, while wall strengths re-price continuously.** Pull once a
day to find the walls; pull often to watch spot approach one.

## Requirements

- Python 3.9+ with `pandas`
- [Longbridge CLI](https://longportapp.com/), authenticated via `longbridge auth login`

The script looks for the CLI at `~/bin/longbridge`, then falls back to `PATH`.

Optional: if your CLI is >= 0.28.0, `scripts/lb_serve.py` wraps `longbridge serve` as a
persistent process and makes bulk greek fetches 7–9x faster. It is used automatically when
available and silently skipped otherwise. Set `LB_NO_SERVE=1` to force the plain CLI path.

## Usage

```bash
python3 scripts/gex_matrix.py NVDA        # 5 expiries, ±20 strikes (default)
python3 scripts/gex_matrix.py AVGO 3      # nearest 3 expiries only
```

Writes `output/gex_matrix_<TICKER>.html` — self-contained, no external assets.

Roughly 25–40 seconds for a typical name: one `option chain` call per expiry to get the
strike grid, then batched `calc-index` calls for gamma and OI.

## How it works

1. `option chain` per expiry → the strike grid (each expiry has its own grid: weeklies are
   fine, monthlies coarse, so rows are the *union* and gaps render as `·`)
2. Build contract symbols: `{CODE}{YYMMDD}{C|P}{strike×1000}.US`, no zero-padding
3. Batched `calc-index --fields gamma,oi` for every contract
4. `GEX = gamma × OI × spot × 100`, positive for calls and negative for puts, summed per strike
5. Colour-normalise against the global `|max|`, then render

Cell colour is normalised across the **whole matrix**, not per column. Near-dated columns
therefore look brighter — that is the point, since near-dated gamma genuinely is larger.

### Rate limiting will bite you

The Longbridge CLI **returns an empty array rather than an error when you exceed its rate
limit** — no exception, exit code 0. An unpaced collector silently produces partial data
and then draws a plausible-looking but wrong chart.

`CALC_BATCH` and `CALC_GAP` exist for this reason; don't remove them. Empty batches fall
back to retrying contracts one at a time. If you fork this, validate row counts.

## As a Claude Code skill

Drop the directory into `~/.claude/skills/` and ask for "NVDA 最近 5 个到期日的 GEX 分布"
or "gex matrix AVGO". `SKILL.md` specifies the chat report format alongside the chart: a
price-level axis, per-expiry column totals, and a rule against predictive phrasing — it
describes structure, it does not forecast.

## Not investment advice

This is a snapshot of dealer positioning structure. It says nothing about where price will
go. One input among many.

## License

Apache-2.0 — see [LICENSE](LICENSE).
