"""Independent 9Sig new-plan simulation helpers."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Literal, TypeAlias

import pandas as pd

NineSigSimulationActionType: TypeAlias = Literal[
    "initial",
    "buy_tqqq",
    "buy_tqqq_limited",
    "sell_tqqq",
    "skip_sell_30_down",
    "no_action",
    "reset_60_40",
]


@dataclass(frozen=True)
class NineSigAdjustedQuarter:
    quarter: str
    date: str
    tqqq_adjusted_close: float
    agg_adjusted_close: float


@dataclass(frozen=True)
class NineSigSimulationRow:
    quarter: str
    rebalanceDate: str
    tqqqAdjustedClose: float
    aggAdjustedClose: float
    signalTarget: float
    tqqqShares: float
    aggShares: float
    tqqqValue: float
    aggValue: float
    portfolioValue: float
    tqqqAllocation: float
    aggAllocation: float
    actionType: NineSigSimulationActionType
    actionSummary: str
    thirtyDownActive: bool
    skippedSellSignals: int
    qoqChange: float | None


def _quarter_label(timestamp: pd.Timestamp) -> str:
    period = timestamp.to_period("Q")
    return f"{period.year} Q{period.quarter}"


def build_ninesig_adjusted_quarters(
    tqqq_adjusted: pd.Series | None,
    agg_adjusted: pd.Series | None,
    as_of: date | None = None,
) -> list[NineSigAdjustedQuarter]:
    """Return completed-quarter adjusted closes for TQQQ and AGG on shared dates."""
    if tqqq_adjusted is None or agg_adjusted is None or tqqq_adjusted.empty or agg_adjusted.empty:
        return []

    tqqq = tqqq_adjusted.rename("tqqq").sort_index()
    agg = agg_adjusted.rename("agg").sort_index()
    joined = pd.concat([tqqq, agg], axis=1).dropna()
    if joined.empty:
        return []

    date_index = pd.DatetimeIndex(joined.index).normalize()
    joined.index = date_index
    joined = joined[~joined.index.duplicated(keep="last")].sort_index()
    quarter_index = pd.DatetimeIndex(joined.index).to_period("Q")
    joined["period"] = quarter_index
    quarterly = joined.groupby("period", sort=True).tail(1)
    as_of_date = as_of or date.today()
    complete_quarter_mask = [
        isinstance(period, pd.Period) and period.end_time.date() <= as_of_date
        for period in quarterly["period"]
    ]
    quarterly = quarterly.loc[complete_quarter_mask]

    rows: list[NineSigAdjustedQuarter] = []
    for ts in pd.DatetimeIndex(quarterly.index):
        row = quarterly.loc[ts]
        rows.append(
            NineSigAdjustedQuarter(
                quarter=_quarter_label(ts),
                date=ts.date().isoformat(),
                tqqq_adjusted_close=float(row["tqqq"]),
                agg_adjusted_close=float(row["agg"]),
            )
        )
    return rows


def _parse_iso_date(value: str) -> date:
    return date.fromisoformat(value)


def _round_row(row: NineSigSimulationRow) -> NineSigSimulationRow:
    return NineSigSimulationRow(
        quarter=row.quarter,
        rebalanceDate=row.rebalanceDate,
        tqqqAdjustedClose=round(row.tqqqAdjustedClose, 6),
        aggAdjustedClose=round(row.aggAdjustedClose, 6),
        signalTarget=round(row.signalTarget, 2),
        tqqqShares=round(row.tqqqShares, 6),
        aggShares=round(row.aggShares, 6),
        tqqqValue=round(row.tqqqValue, 2),
        aggValue=round(row.aggValue, 2),
        portfolioValue=round(row.portfolioValue, 2),
        tqqqAllocation=round(row.tqqqAllocation, 6),
        aggAllocation=round(row.aggAllocation, 6),
        actionType=row.actionType,
        actionSummary=row.actionSummary,
        thirtyDownActive=row.thirtyDownActive,
        skippedSellSignals=row.skippedSellSignals,
        qoqChange=None if row.qoqChange is None else round(row.qoqChange, 6),
    )


def _row_from_state(
    quarter: NineSigAdjustedQuarter,
    signal_target: float,
    tqqq_value: float,
    agg_value: float,
    action_type: NineSigSimulationActionType,
    action_summary: str,
    thirty_down_active: bool,
    skipped_sell_signals: int,
    previous_portfolio_value: float | None,
) -> NineSigSimulationRow:
    portfolio_value = tqqq_value + agg_value
    tqqq_allocation = tqqq_value / portfolio_value if portfolio_value else 0.0
    agg_allocation = agg_value / portfolio_value if portfolio_value else 0.0
    qoq_change = (
        None
        if previous_portfolio_value is None or previous_portfolio_value <= 0
        else portfolio_value / previous_portfolio_value - 1
    )
    return _round_row(
        NineSigSimulationRow(
            quarter=quarter.quarter,
            rebalanceDate=quarter.date,
            tqqqAdjustedClose=quarter.tqqq_adjusted_close,
            aggAdjustedClose=quarter.agg_adjusted_close,
            signalTarget=signal_target,
            tqqqShares=tqqq_value / quarter.tqqq_adjusted_close,
            aggShares=agg_value / quarter.agg_adjusted_close,
            tqqqValue=tqqq_value,
            aggValue=agg_value,
            portfolioValue=portfolio_value,
            tqqqAllocation=tqqq_allocation,
            aggAllocation=agg_allocation,
            actionType=action_type,
            actionSummary=action_summary,
            thirtyDownActive=thirty_down_active,
            skippedSellSignals=skipped_sell_signals,
            qoqChange=qoq_change,
        )
    )


def _prior_two_year_high(quarters: list[NineSigAdjustedQuarter], idx: int) -> float | None:
    current = _parse_iso_date(quarters[idx].date)
    candidates = [
        q.tqqq_adjusted_close
        for q in quarters[:idx]
        if (current - _parse_iso_date(q.date)).days <= 731
    ]
    return max(candidates) if candidates else None


def simulate_ninesig_new_plan(
    quarters: list[NineSigAdjustedQuarter],
    start_date: str,
    starting_capital: float,
    *,
    apply_buying_power_throttle: bool = True,
) -> list[NineSigSimulationRow]:
    """Simulate a fresh independent 9Sig plan from adjusted quarterly closes."""
    if starting_capital <= 0:
        return []
    start = _parse_iso_date(start_date)
    start_index = next(
        (idx for idx, quarter in enumerate(quarters) if _parse_iso_date(quarter.date) >= start),
        None,
    )
    if start_index is None:
        return []

    rows: list[NineSigSimulationRow] = []
    initial = quarters[start_index]
    tqqq_value = starting_capital * 0.60
    agg_value = starting_capital * 0.40
    signal_target = tqqq_value
    thirty_down_active = False
    skipped_sell_signals = 0
    post_thirty_down_awaiting_reset = False

    rows.append(
        _row_from_state(
            initial,
            signal_target,
            tqqq_value,
            agg_value,
            "initial",
            "Start fresh at 60/40.",
            thirty_down_active,
            skipped_sell_signals,
            None,
        )
    )
    previous_portfolio_value = rows[-1].portfolioValue

    for idx in range(start_index + 1, len(quarters)):
        quarter = quarters[idx]
        tqqq_value = rows[-1].tqqqShares * quarter.tqqq_adjusted_close
        agg_value = rows[-1].aggShares * quarter.agg_adjusted_close
        signal_target *= 1.09

        prior_high = _prior_two_year_high(quarters, idx)
        if prior_high is not None and quarter.tqqq_adjusted_close <= prior_high * 0.70:
            thirty_down_active = True
            skipped_sell_signals = 0
            post_thirty_down_awaiting_reset = False

        action_type: NineSigSimulationActionType = "no_action"
        action_summary = "No trade; TQQQ matched the signal target."

        if tqqq_value < signal_target:
            shortfall = signal_target - tqqq_value
            buying_power = agg_value * 0.90 if apply_buying_power_throttle else agg_value
            amount_to_move = min(shortfall, buying_power)
            agg_value -= amount_to_move
            tqqq_value += amount_to_move
            if amount_to_move < shortfall:
                action_type = "buy_tqqq_limited"
                action_summary = f"Bought ${amount_to_move:,.0f} TQQQ; AGG buying-power throttle limited the buy."
            else:
                action_type = "buy_tqqq"
                action_summary = f"Bought ${amount_to_move:,.0f} TQQQ to restore the signal target."
        elif tqqq_value > signal_target:
            surplus = tqqq_value - signal_target
            if thirty_down_active and skipped_sell_signals < 2:
                skipped_sell_signals += 1
                action_type = "skip_sell_30_down"
                action_summary = f"Skipped sell signal {skipped_sell_signals} of 2 during 30 Down."
                if skipped_sell_signals == 2:
                    thirty_down_active = False
                    post_thirty_down_awaiting_reset = True
            elif post_thirty_down_awaiting_reset:
                portfolio_value = tqqq_value + agg_value
                tqqq_value = portfolio_value * 0.60
                agg_value = portfolio_value * 0.40
                signal_target = tqqq_value
                post_thirty_down_awaiting_reset = False
                action_type = "reset_60_40"
                action_summary = "Reset the independent plan to 60/40 after 30 Down."
            else:
                tqqq_value -= surplus
                agg_value += surplus
                action_type = "sell_tqqq"
                action_summary = f"Sold ${surplus:,.0f} TQQQ above the signal target."

        rows.append(
            _row_from_state(
                quarter,
                signal_target,
                tqqq_value,
                agg_value,
                action_type,
                action_summary,
                thirty_down_active,
                skipped_sell_signals,
                previous_portfolio_value,
            )
        )
        previous_portfolio_value = rows[-1].portfolioValue

    return rows


def ninesig_adjusted_quarters_to_dicts(
    quarters: list[NineSigAdjustedQuarter],
) -> list[dict[str, object]]:
    return [asdict(quarter) for quarter in quarters]
