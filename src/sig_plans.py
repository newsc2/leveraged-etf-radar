"""Jason Kelly Sig Plan engine — quarterly rebalance logic for 3Sig / 6Sig / 9Sig.

Pure-function implementation of the rules documented in `docs/jason-kelly/`.
Default parameters match 9Sig (TQQQ + AGG, 9% quarterly target, 60/40 base).

Public surface:
    PlanConfig            — strategy constants (target, base allocation, override rules)
    PlanState             — persistent state between quarters
    QuarterInputs         — what the user supplies each quarter
    RebalanceAction       — what the engine returns (orders + rule activations)
    compute_rebalance()   — the engine
    next_goal()           — signal-line projection for the next quarter

Run as a script with the example state file to see the format:
    python -m src.sig_plans
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

NINESIG = "9Sig"
SIXSIG = "6Sig"
THREESIG = "3Sig"


@dataclass(frozen=True)
class PlanConfig:
    """Stable strategy parameters. Defaults are 9Sig."""

    name: str = NINESIG
    stock_ticker: str = "TQQQ"
    bond_ticker: str = "AGG"
    quarterly_growth_target: float = 0.09  # +9% per quarter
    base_stock_allocation: float = 0.60    # 60% TQQQ on base reset
    dividend_to_goal_share: float = 0.50   # 50% of dividends bump the stock goal
    # 30-Down rule
    thirty_down_threshold: float = 0.30    # 30% drop from 2y rolling quarterly high
    thirty_down_lookback_years: int = 2
    # Spike reset
    spike_reset_quarterly_gain: float = 1.00  # +100% in a quarter triggers reset
    # Buying-power throttle
    buying_power_throttle: float = 0.90    # never deploy >90% of bond balance

    @classmethod
    def for_3sig(cls) -> PlanConfig:
        return cls(
            name=THREESIG, stock_ticker="IJR", bond_ticker="BND",
            quarterly_growth_target=0.03, base_stock_allocation=0.80,
            spike_reset_quarterly_gain=2.00,  # effectively never
            buying_power_throttle=1.00,
        )

    @classmethod
    def for_6sig(cls) -> PlanConfig:
        return cls(
            name=SIXSIG, stock_ticker="MVV", bond_ticker="SCHZ",
            quarterly_growth_target=0.06, base_stock_allocation=0.60,
            spike_reset_quarterly_gain=2.00,  # effectively never
            buying_power_throttle=0.90,
        )


# ---------------------------------------------------------------------------
# State (persisted between quarters)
# ---------------------------------------------------------------------------


@dataclass
class PlanState:
    """Everything the engine needs to remember between quarterly runs."""

    last_rebalance_date: date
    prior_goal: float                       # TQQQ Balance Goal as of last rebalance
    last_quarter_close_price: float         # stock-fund price at last quarterly close
    rolling_2yr_quarterly_high: float       # for 30-Down monitoring
    high_set_on: date                       # date the 2y high was set (for expiry)
    last_rebalance_fill_price: float = 0.0  # actual fill price; spike-reset baseline
                                            # per Kelly: "since we bought TQQQ at $X"
    thirty_down_active: bool = False
    thirty_down_started: date | None = None
    shares_stock: float = 0.0
    shares_bond: float = 0.0
    # Free-form notes for human review
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        d = asdict(self)
        for key in ("last_rebalance_date", "high_set_on", "thirty_down_started"):
            v = d[key]
            d[key] = v.isoformat() if isinstance(v, date) else v
        return json.dumps(d, indent=2)

    @classmethod
    def from_json(cls, s: str) -> PlanState:
        d = json.loads(s)
        for key in ("last_rebalance_date", "high_set_on", "thirty_down_started"):
            if d.get(key):
                d[key] = date.fromisoformat(d[key])
        return cls(**d)


# ---------------------------------------------------------------------------
# Per-quarter inputs and outputs
# ---------------------------------------------------------------------------


@dataclass
class QuarterInputs:
    """What the user supplies for the rebalance about to be calculated."""

    rebalance_date: date
    quarter_close_date: date
    quarter_close_price: float        # stock-fund price at quarter close
    rebalance_price: float            # price expected at the rebalance order
    bond_price: float                 # bond-fund price at rebalance
    dividends_this_quarter: float     # combined stock+bond dividends


@dataclass
class RebalanceAction:
    """Engine output — mirrors Kelly's published quarterly-note structure."""

    # STATUS
    plan: str
    rebalance_date: date
    stock_balance_before: float
    bond_balance_before: float
    new_goal: float                   # signal-line for THIS quarter
    surplus_or_shortfall: float       # +ve = surplus (sell), -ve = shortfall (buy)
    quarterly_stock_gain: float       # vs last quarter close (pre-rebalance)

    # RULES (which overrides triggered)
    thirty_down_active: bool
    spike_reset_triggered: bool
    bond_reservoir_reset: bool
    buying_power_throttled: bool

    # ORDERS (final, post-override)
    sell_stock_shares: float
    buy_stock_shares: float
    sell_bond_shares: float
    buy_bond_shares: float

    # RESULTS (projected, assumes orders fill at the supplied prices)
    stock_balance_after: float
    bond_balance_after: float

    # Human-readable explanation
    notes: list[str]

    def format_note(self) -> str:
        """Pretty-print in Kelly's quarterly-note format."""
        lines = [
            f"{self.rebalance_date.isoformat()} — {self.plan}",
            "",
            "STATUS",
            f"  Stock balance:  ${self.stock_balance_before:,.0f}",
            f"  Bond balance:   ${self.bond_balance_before:,.0f}",
            f"  New goal:       ${self.new_goal:,.0f}",
            f"  Quarterly gain: {self.quarterly_stock_gain:+.1%}",
            f"  {'Surplus' if self.surplus_or_shortfall >= 0 else 'Shortfall'}: "
            f"${abs(self.surplus_or_shortfall):,.0f}",
            "",
            "RULES",
            f"  30 Down active:        {self.thirty_down_active}",
            f"  Spike reset triggered: {self.spike_reset_triggered}",
            f"  Bond reservoir reset:  {self.bond_reservoir_reset}",
            f"  Buying power throttle: {self.buying_power_throttled}",
            "",
            "ORDERS",
        ]
        if self.sell_stock_shares:
            lines.append(f"  Sell {self.sell_stock_shares:,.0f} stock")
        if self.buy_stock_shares:
            lines.append(f"  Buy  {self.buy_stock_shares:,.0f} stock")
        if self.sell_bond_shares:
            lines.append(f"  Sell {self.sell_bond_shares:,.0f} bond")
        if self.buy_bond_shares:
            lines.append(f"  Buy  {self.buy_bond_shares:,.0f} bond")
        if not any((self.sell_stock_shares, self.buy_stock_shares,
                    self.sell_bond_shares, self.buy_bond_shares)):
            lines.append("  No action (signal skipped)")
        lines += ["", "PROJECTED BALANCES",
                  f"  Stock: ${self.stock_balance_after:,.0f}",
                  f"  Bond:  ${self.bond_balance_after:,.0f}"]
        if self.notes:
            lines += ["", "NOTES"] + [f"  - {n}" for n in self.notes]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


def next_goal(prior_goal: float, dividends: float, cfg: PlanConfig) -> float:
    """Project the upcoming quarter's signal-line balance.

    new_goal = prior_goal × (1 + target) + dividend_share × dividends
    """
    return prior_goal * (1 + cfg.quarterly_growth_target) + \
        cfg.dividend_to_goal_share * dividends


def _check_thirty_down(
    state: PlanState,
    quarter_close_price: float,
    quarter_close_date: date,
    cfg: PlanConfig,
) -> tuple[bool, date | None, float, date]:
    """Return (active, started_on, updated_2yr_high, updated_high_date).

    The 30-Down phase activates when the stock fund's quarterly close drops
    >= 30% from its rolling 2-year quarterly high. It deactivates either when
    a new quarterly high is set OR when 2 years pass since the original high.
    """
    high = state.rolling_2yr_quarterly_high
    high_date = state.high_set_on

    # New high resets the clock and clears 30-Down
    if quarter_close_price > high:
        return False, None, quarter_close_price, quarter_close_date

    # Expire the lookback window
    years_since_high = (quarter_close_date - high_date).days / 365.25
    if years_since_high > cfg.thirty_down_lookback_years:
        # Treat current price as the new reference high (best-effort within stateless input)
        return False, None, quarter_close_price, quarter_close_date

    drawdown = 1 - quarter_close_price / high if high > 0 else 0.0
    if drawdown >= cfg.thirty_down_threshold:
        return True, state.thirty_down_started or quarter_close_date, high, high_date

    # Drawdown < threshold: keep prior 30-Down status (don't auto-clear without new high)
    return state.thirty_down_active, state.thirty_down_started, high, high_date


def compute_rebalance(
    state: PlanState, q: QuarterInputs, cfg: PlanConfig | None = None,
) -> RebalanceAction:
    """Compute the quarterly rebalance action, applying all four override rules.

    Override precedence (per Kelly): 30 Down → bond reservoir reset → buying-power
    throttle → spike reset. The engine evaluates all four and reports which fired.
    """
    cfg = cfg or PlanConfig()

    # Current portfolio snapshot (pre-rebalance, mark-to-market at quarter close)
    stock_value = state.shares_stock * q.quarter_close_price
    bond_value = state.shares_bond * q.bond_price

    # Signal line for THIS quarter
    new_goal = next_goal(state.prior_goal, q.dividends_this_quarter, cfg)
    surplus = stock_value - new_goal

    # Quarterly gain for spike-reset check.
    # Per Kelly's Note 19 phrasing ("Since we bought more shares of TQQQ at $X..."),
    # the spike-reset baseline is the actual rebalance fill price, not the prior
    # quarter close. Fall back to quarter close if fill price isn't set.
    spike_baseline = state.last_rebalance_fill_price or state.last_quarter_close_price
    if spike_baseline > 0:
        quarterly_gain = q.quarter_close_price / spike_baseline - 1
    else:
        quarterly_gain = 0.0

    # 30-Down evaluation (uses current quarter close)
    thirty_down, td_started, _new_high, _new_high_date = _check_thirty_down(
        state, q.quarter_close_price, q.quarter_close_date, cfg,
    )

    notes: list[str] = []
    spike_reset = False
    reservoir_reset = False
    throttled = False

    sell_stock = buy_stock = sell_bond = buy_bond = 0.0

    if surplus >= 0:
        # SELL signal
        if thirty_down:
            notes.append("30 Down active — sell signal SKIPPED.")
        else:
            # Spike reset check (overrides ordinary sell sizing).
            # Kelly's user-guide rule: "TQQQ's balance after the quarterly
            # rebalance is between 60% and 100%". We interpret this as the
            # CURRENT (pre-rebalance) allocation — under the alternative
            # post-rebalance reading, the rule would essentially never fire
            # because the 9% compounded goal grows slower than a 100% quarter.
            total = stock_value + bond_value
            current_alloc = stock_value / total if total else 0
            if (quarterly_gain >= cfg.spike_reset_quarterly_gain
                    and 0.60 <= current_alloc <= 1.00):
                spike_reset = True
                target_stock = total * cfg.base_stock_allocation
                excess = stock_value - target_stock
                sell_stock = excess / q.rebalance_price if q.rebalance_price else 0
                buy_bond = (excess) / q.bond_price if q.bond_price else 0
                notes.append(
                    f"Spike reset: TQQQ +{quarterly_gain:.0%} this quarter — "
                    f"resetting to {cfg.base_stock_allocation:.0%} allocation.")
            else:
                # Ordinary sell-the-surplus
                sell_stock = surplus / q.rebalance_price if q.rebalance_price else 0
                buy_bond = surplus / q.bond_price if q.bond_price else 0
    else:
        # BUY signal
        shortfall = -surplus
        # Bond reservoir reset check
        bond_drain = shortfall
        would_drain_reservoir = bond_value > 0 and bond_drain >= bond_value
        if would_drain_reservoir and not thirty_down:
            reservoir_reset = True
            total = stock_value + bond_value
            target_stock = total * cfg.base_stock_allocation
            buy_amount = target_stock - stock_value
            # Throttle the buy to 90% of bond balance regardless
            max_buy = bond_value * cfg.buying_power_throttle
            if buy_amount > max_buy:
                throttled = True
                buy_amount = max_buy
            sell_bond = buy_amount / q.bond_price if q.bond_price else 0
            buy_stock = buy_amount / q.rebalance_price if q.rebalance_price else 0
            notes.append("Bond reservoir reset to base 60/40 allocation.")
        else:
            # Ordinary buy-the-shortfall, modulated by throttle
            max_buy = bond_value * cfg.buying_power_throttle
            buy_amount = shortfall
            if buy_amount > max_buy:
                throttled = True
                buy_amount = max_buy
                notes.append(
                    f"Buying-power throttled to {cfg.buying_power_throttle:.0%} "
                    f"of bond balance.")
            sell_bond = buy_amount / q.bond_price if q.bond_price else 0
            buy_stock = buy_amount / q.rebalance_price if q.rebalance_price else 0

    # Project post-rebalance balances
    stock_after = stock_value + (buy_stock - sell_stock) * q.rebalance_price
    bond_after = bond_value + (buy_bond - sell_bond) * q.bond_price

    if td_started and not state.thirty_down_active:
        notes.append(f"30 Down phase BEGAN on {q.quarter_close_date.isoformat()}.")

    return RebalanceAction(
        plan=cfg.name,
        rebalance_date=q.rebalance_date,
        stock_balance_before=stock_value,
        bond_balance_before=bond_value,
        new_goal=new_goal,
        surplus_or_shortfall=surplus,
        quarterly_stock_gain=quarterly_gain,
        thirty_down_active=thirty_down,
        spike_reset_triggered=spike_reset,
        bond_reservoir_reset=reservoir_reset,
        buying_power_throttled=throttled,
        sell_stock_shares=sell_stock,
        buy_stock_shares=buy_stock,
        sell_bond_shares=sell_bond,
        buy_bond_shares=buy_bond,
        stock_balance_after=stock_after,
        bond_balance_after=bond_after,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# CLI for quick what-if
# ---------------------------------------------------------------------------


def _demo() -> None:
    """Run the engine against the live 9Sig snapshot — May 2026 spike-reset watch."""
    cfg = PlanConfig()
    # State as of 4/6/26 rebalance (from jasonkelly.com/kellyletter/9sig/
    # and verified against Note 19 emailed 5/10/26)
    state = PlanState(
        last_rebalance_date=date(2026, 4, 6),
        prior_goal=5_824_994,
        last_quarter_close_price=43.33,        # 4/2/26 close
        last_rebalance_fill_price=44.03,       # 4/6/26 fill — spike-reset baseline
        rolling_2yr_quarterly_high=52.72,      # 12/31/25 close per Kelly Note 19
        high_set_on=date(2025, 12, 31),
        shares_stock=133_842,                  # post-4/6/26 buy
        shares_bond=12_425,                    # incl AGG dividend reinvestments
    )
    # Hypothetical Q2 close — current price as of 5/8/26 is $76.28
    # We're checking: what if it stays here at quarter close?
    q = QuarterInputs(
        rebalance_date=date(2026, 7, 6),
        quarter_close_date=date(2026, 6, 30),
        quarter_close_price=76.28,    # if today's price held to quarter end
        rebalance_price=76.28,
        bond_price=99.00,
        dividends_this_quarter=0,     # placeholder
    )
    action = compute_rebalance(state, q, cfg)
    print(action.format_note())

    print("\n--- Spike reset trigger price ---")
    baseline = state.last_rebalance_fill_price or state.last_quarter_close_price
    trigger_price = baseline * (1 + cfg.spike_reset_quarterly_gain)
    print(f"Baseline (rebal fill): ${baseline:.2f}")
    print(f"TQQQ must close >= ${trigger_price:.2f} on or before 6/29/26 to trigger reset.")
    print(f"Current TQQQ $76.28 → +73.2% from baseline. {trigger_price - 76.28:.2f} more to trigger.")


if __name__ == "__main__":
    _demo()
