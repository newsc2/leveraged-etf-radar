export type NineSigActionType =
  | "initial"
  | "buy_tqqq"
  | "buy_tqqq_30_down"
  | "buy_tqqq_limited"
  | "sell_tqqq"
  | "no_action"
  | "reset_60_40";

export type NineSigSimulationActionType =
  | "initial"
  | "buy_tqqq"
  | "buy_tqqq_limited"
  | "sell_tqqq"
  | "skip_sell_30_down"
  | "no_action"
  | "reset_60_40";

export interface NineSigQuarter {
  quarter: string;
  date: string;
  action: string;
  action_type: NineSigActionType;
  tqqq_allocation: number;
  agg_allocation: number;
  portfolio_value: number;
  qoq_change: number | null;
  notes: string;
}

export interface NineSigAdjustedQuarter {
  quarter: string;
  date: string;
  tqqq_adjusted_close: number;
  agg_adjusted_close: number;
}

export interface NineSigSimulationQuarter {
  quarter: string;
  rebalanceDate: string;
  tqqqAdjustedClose: number;
  aggAdjustedClose: number;
  signalTarget: number;
  tqqqShares: number;
  aggShares: number;
  tqqqValue: number;
  aggValue: number;
  portfolioValue: number;
  tqqqAllocation: number;
  aggAllocation: number;
  actionType: NineSigSimulationActionType;
  actionSummary: string;
  thirtyDownActive: boolean;
  skippedSellSignals: number;
  qoqChange: number | null;
}
