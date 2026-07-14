import type {
  AdvisorResponse,
  AlertsResponse,
  FleetResponse,
  ForecastResponse,
  FuelPriceResponse,
  LogEntry,
  ModelInfo,
  RoiResponse,
  ScheduleResponse,
  ShipDetail,
} from './types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init)
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null
    throw new Error(body?.detail ?? `HTTP ${response.status}`)
  }
  return response.json() as Promise<T>
}

export const api = {
  fleet: () => request<FleetResponse>('/api/fleet'),
  models: () => request<{ models: ModelInfo[] }>('/api/models'),
  ship: (shipId: string) => request<ShipDetail>(`/api/ship/${encodeURIComponent(shipId)}`),
  forecast: (shipId: string, modelId: string, speed: number) =>
    request<ForecastResponse>(
      `/api/ship/${encodeURIComponent(shipId)}/forecast?model=${encodeURIComponent(modelId)}&speed=${speed}`,
    ),
  roi: (shipId: string, fuelPrice?: number) => request<RoiResponse>(
    `/api/roi?ship_id=${encodeURIComponent(shipId)}${fuelPrice ? `&fuel_price=${fuelPrice}` : ''}`,
  ),
  schedule: () => request<ScheduleResponse>('/api/schedule'),
  fuelPrices: () => request<FuelPriceResponse>('/api/fuel-prices'),
  alerts: () => request<AlertsResponse>('/api/alerts'),
  markAlertRead: (alertId: string) =>
    request<{ id: string; read: boolean }>(`/api/alerts/${encodeURIComponent(alertId)}/read`, { method: 'POST' }),
  log: (shipId: string) =>
    request<{ entries: LogEntry[] }>(`/api/ship/${encodeURIComponent(shipId)}/log?days=30`),
  noonReport: (report: {
    ship_id: string
    report_date: string
    avg_speed: number
    daily_foc: number
    wind_scale: number
    full_speed_hours: number
  }) => request<{ accepted: boolean; speed_loss_pct: number; excess_foc_tons: number }>('/api/noon-report', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(report),
  }),
  advisor: (question: string) => request<AdvisorResponse>('/api/advisor', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
  }),
  inspect: (shipId: string, file: File) => {
    const body = new FormData()
    body.set('ship_id', shipId)
    body.set('file', file)
    return request<Record<string, unknown>>('/api/inspect', { method: 'POST', body })
  },
}
