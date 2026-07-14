export type Status = 'action' | 'watch' | 'ok'

export interface FleetShip {
  ship_id: string
  ship_name: string
  speed_loss_pct: number
  fouling_level: string
  status: Status
  days_since_clean: number
  days_to_threshold: number | null
  excess_cost_per_day: number
  spark: number[]
}

export interface FleetResponse {
  stats: {
    avg_speed_loss_pct: number
    ships_action: number
    ships_watch: number
    monthly_excess_cost_usd: number
    monthly_excess_co2_tons: number
    threshold_pct: number
    n_ships: number
  }
  ships: FleetShip[]
}

export interface ModelInfo {
  id: string
  name: string
  description: string
  validation_mape: number | null
  needs_speed: boolean
  is_primary: boolean
}

export interface TrendPoint {
  date: string
  speed_loss: number
}

export interface ForecastPoint {
  date: string
  mid: number
  lo: number
  hi: number
}

export interface ShipDetail {
  ship_id: string
  ship_name: string
  status: Status
  fouling_level: string
  hull_prop: { hull_pp: number; prop_pp: number; prop_share: number }
  current: {
    speed_loss_pct: number
    avg_speed: number | null
    days_since_clean: number
    growth_pp_per_day: number
    days_to_threshold: number | null
    excess_cost_per_day: number
    daily_foc: number | null
    expected_foc: number | null
    excess_foc: number | null
    wind_scale: number | null
    full_speed_hours: number | null
    last_event: { date: string; type: string } | null
    threshold_pct: number
  }
  kpi_sparks: {
    avg_speed: number[]
    daily_foc: number[]
    speed_loss: number[]
    excess_foc: number[]
    wind_scale: number[]
    days_since_clean: number[]
  }
  attribution: null | {
    baseline_tons: number
    factors: { name: string; tons: number; is_fouling?: boolean }[]
    actual_tons: number
    window_days: number
  }
  series: TrendPoint[]
  events: { date: string; type: string; notes: string }[]
  maintenance_effects: {
    date: string
    type: string
    orig_type: string
    pre_pp: number
    post_pp: number
    delta_pp: number
  }[]
}

export interface ForecastResponse {
  ship_id: string
  model_id: string
  model_name: string
  scenario_speed_kn: number
  needs_speed: boolean
  forecast: ForecastPoint[]
}

export interface RoiResponse {
  target: {
    ship_id: string
    ship_name: string
    days: number[]
    avg_cost: number[]
    no_clean_avg: number
    best_day: number | null
    best_avg: number
    current_excess_cost: number
    payback_days: number | null
    excess_co2_per_day: number
  }
  per_ship: {
    ship_id: string
    ship_name: string
    excess_cost_per_day: number
    hull_usd: number
    prop_usd: number
    best_day: number | null
    payback_days: number | null
  }[]
  stats: {
    fleet_daily_excess_usd: number
    annual_saving_potential_usd: number
    fuel_price_usd: number
    cleaning_cost_usd: number
    prop_share: number
  }
}

export interface ScheduleItem {
  ship_id: string
  ship_name: string
  action: 'PP' | 'UWC' | 'UWC+PP'
  window_start: string
  window_end: string
  speed_loss_recovery_pp: number
  payback_days: number | null
  action_cost_usd: number
  monthly_saving_usd: number
  daily_fuel_saving_tons: number
  inspection_recommended: boolean
  backfill: { ship_id: string; ship_name: string }
  read_only: true
}

export interface ScheduleResponse {
  as_of: string
  horizon_days: number
  primary_model_id: string
  recommendations: ScheduleItem[]
  dry_docks: { ship_id: string; date: string; read_only: true }[]
}

export interface FuelPriceResponse {
  port: string
  currency: string
  unit: string
  prices: {
    grade: string
    usd_per_ton: number
    source: string
    source_url: string
    as_of: string
    estimated: boolean
  }[]
  history: { date: string; vlsfo_usd_per_ton: number; source: string }[]
  effective_price: { usd_per_ton: number; method: string; estimated: boolean }
}

export interface AlertItem {
  id: string
  ship_id: string
  ship_name: string
  severity: 'critical' | 'warning'
  message: string
  created_at: string
  read: boolean
}

export interface AlertsResponse {
  alerts: AlertItem[]
  unread_count: number
  channels: { in_app: string; ses: string; discord: string }
}

export interface LogEntry {
  kind: 'report' | 'event'
  date: string
  avg_speed?: number
  daily_foc?: number
  speed_loss_pct?: number
  excess_foc_tons?: number
  wind_scale?: number | null
  full_speed_hours?: number | null
  event_type?: string
  notes?: string
}

export interface AdvisorResponse {
  answer: string
  mode: string
  steps: string[]
  citations: string[]
}
