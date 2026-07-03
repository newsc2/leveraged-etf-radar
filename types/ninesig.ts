export type NineSigActionType =
  | "initial"
  | "buy_tqqq"
  | "buy_tqqq_30_down"
  | "buy_tqqq_limited"
  | "sell_tqqq"
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
