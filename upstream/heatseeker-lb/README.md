# Heatseeker-LB

0DTE NetGEX heatmap for SPY / QQQ, built on the [Longbridge](https://longportapp.com/) CLI.

Computes dealer gamma exposure across strikes from live option-chain data, renders a
vertical ladder heatmap, and identifies the structural levels that matter intraday:

| Level | Meaning |
|---|---|
| **King** | Strongest negative-gamma wall — the flip zone where dealer hedging turns pro-cyclical |
| **Floor** | Strongest positive-gamma wall — where hedging flows suppress movement (resistance) |
| **Pillow** | Positive-gamma cushion below spot |

It also emits a companion script that prints GEX alongside a Black-Scholes-modelled
VEX (vanna) for cross-confirmation.

Ships as a [Claude Code](https://claude.com/claude-code) skill (`SKILL.md`), but both
scripts run standalone — you don't need Claude to use them.

![Sample output](output/heatseeker_gex.html)
*(`output/heatseeker_gex.html` is a committed sample render — open it in a browser to
see what the tool produces.)*

## Why Longbridge

Most GEX tools depend on a paid US options data vendor. This one uses a retail broker
CLI, which makes it free if you already have a Longbridge account. That comes with two
real constraints, stated up front:

- **No index options.** Longbridge carries no SPX / NDX / VIX options at all — ETF and
  single-name only. That's why coverage is SPY + QQQ rather than SPX.
- **Open interest is T+1.** OI doesn't update intraday (OCC publishes next day), so
  intraday GEX is *today's* spot and gamma against *yesterday's* OI. That is the normal
  convention for intraday GEX, but it is worth knowing rather than assuming.

## Requirements

- Python 3.9+ with `pandas`
- [Longbridge CLI](https://longportapp.com/), authenticated:

```bash
longbridge auth login
```

The scripts look for the CLI at `~/bin/longbridge`, then fall back to whatever is on
your `PATH`.

## Usage

```bash
python3 scripts/report_gv.py
```

Prints GEX + VEX numbers per strike — King / Floor / Pillow and the levels around spot.

```bash
python3 scripts/heatseeker_gex.py
```

Renders `output/heatseeker_gex.html` — a self-contained ladder heatmap, no external
assets, opens in any browser.

Each takes roughly 40–60 seconds: pulling the chain, batching gamma/OI/IV lookups, then
rendering.

## How it works

For each of SPY and QQQ:

1. Pull the current-day expiry chain, take 25 strikes either side of spot
2. Batch-fetch gamma, open interest and IV per contract via `calc-index`
3. `GEX = |gamma| × OI × 100 × spot`, signed positive for calls and negative for puts
4. Aggregate by strike, rank the walls, render the ladder

Requests are batched and paced (`CALC_BATCH` / `CALC_GAP` in the scripts). This matters:
the Longbridge CLI **returns an empty array rather than an error when you exceed its
rate limit**, so an unpaced collector silently produces partial data. If you fork this,
keep the pacing and validate row counts.

## As a Claude Code skill

Drop the directory into `~/.claude/skills/` and Claude will run it when you ask for
"0DTE GEX", "gamma walls", or the other triggers listed in `SKILL.md`. The skill file
also specifies the chat report format — price-level axis, GEX×VEX table, and a rule
against predictive phrasing (it describes structure, it doesn't forecast).

## Not investment advice

This computes a snapshot of dealer positioning structure. It says nothing about where
price will go. Use it as one input among many.

## License

Apache-2.0 — see [LICENSE](LICENSE).
