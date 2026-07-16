import type {
  AdvisorResponse,
  AdvisorStreamEvent,
  AlertsResponse,
  DataResetStatus,
  FleetResponse,
  ForecastResponse,
  FuelPriceResponse,
  LogEntry,
  MaintenanceBenefitRequest,
  MaintenanceBenefitResponse,
  ModelInfo,
  NotificationSubscription,
  NotificationSubscriptionsResponse,
  NoonReportImportResponse,
  RoiResponse,
  ScheduleResponse,
  ShipDetail,
  SpeedLossLoadCondition,
  SpeedLossPredictionResponse,
} from './types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init)
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null
    throw new Error(body?.detail ?? `HTTP ${response.status}`)
  }
  return response.json() as Promise<T>
}

async function download(path: string): Promise<Blob> {
  const response = await fetch(path)
  if (!response.ok) throw new Error(`下載失敗（HTTP ${response.status}）`)
  return response.blob()
}

export const api = {
  fleet: () => request<FleetResponse>('/api/fleet'),
  models: () => request<{ models: ModelInfo[]; active_model_id: string }>('/api/models'),
  modelTemplate: () => request<Record<string, unknown>>('/api/models/template'),
  uploadModel: (manifest: string, artifact: File) => {
    const body = new FormData()
    body.set('manifest', manifest)
    body.set('artifact', artifact)
    return request<ModelInfo>('/api/models/upload', { method: 'POST', body })
  },
  activateModel: (modelId: string) => request<ModelInfo>(
    `/api/models/${encodeURIComponent(modelId)}/activate`, { method: 'POST' },
  ),
  restoreModel: () => request<{ active_model_id: string }>('/api/models/restore', { method: 'POST' }),
  ship: (shipId: string) => request<ShipDetail>(`/api/ship/${encodeURIComponent(shipId)}`),
  forecast: (shipId: string, modelId: string, speed: number) =>
    request<ForecastResponse>(
      `/api/ship/${encodeURIComponent(shipId)}/forecast?model=${encodeURIComponent(modelId)}&speed=${speed}`,
    ),
  speedLossPrediction: (shipId: string, params: {
    forecastDays: number
    thresholdPct: number
    maxWindScale: number
    loadCondition: SpeedLossLoadCondition
  }) => {
    const query = new URLSearchParams({
      forecast_days: String(params.forecastDays),
      threshold_pct: String(params.thresholdPct),
      max_wind_scale: String(params.maxWindScale),
      load_condition: params.loadCondition,
    })
    return request<SpeedLossPredictionResponse>(
      `/api/ship/${encodeURIComponent(shipId)}/speed-loss-prediction?${query.toString()}`,
    )
  },
  maintenanceBenefit: (shipId: string, body: MaintenanceBenefitRequest) =>
    request<MaintenanceBenefitResponse>(
      `/api/ship/${encodeURIComponent(shipId)}/maintenance-benefit`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
    ),
  roi: (shipId: string, fuelPrice?: number, cleaningCost?: number, recoveryPp?: number) => request<RoiResponse>(
    `/api/roi?ship_id=${encodeURIComponent(shipId)}${fuelPrice ? `&fuel_price=${fuelPrice}` : ''}${cleaningCost ? `&cleaning_cost=${cleaningCost}` : ''}${recoveryPp !== undefined ? `&speed_loss_recovery_pp=${recoveryPp}` : ''}`,
  ),
  schedule: () => request<ScheduleResponse>('/api/schedule?past_days=90&future_days=180'),
  fuelPrices: () => request<FuelPriceResponse>('/api/fuel-prices'),
  alerts: () => request<AlertsResponse>('/api/alerts'),
  markAlertRead: (alertId: string) =>
    request<{ id: string; read: boolean }>(`/api/alerts/${encodeURIComponent(alertId)}/read`, { method: 'POST' }),
  notificationSubscriptions: () => request<NotificationSubscriptionsResponse>('/api/notification-subscriptions'),
  createNotificationSubscription: (body: { channel: 'email' | 'discord'; kind: 'digest' | 'alert'; destination?: string; ship_ids: string[] }) =>
    request<NotificationSubscription>('/api/notification-subscriptions', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }),
  deleteNotificationSubscription: (id: string) => request<{ id: string; deleted: boolean }>(
    `/api/notification-subscriptions/${encodeURIComponent(id)}`, { method: 'DELETE' },
  ),
  sendNotificationDigest: (id: string) => request<{ delivered: boolean; status: string; ship_count: number }>(
    `/api/notification-subscriptions/${encodeURIComponent(id)}/send`, { method: 'POST' },
  ),
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
  importNoonReport: (file: File) => {
    const body = new FormData()
    body.set('file', file)
    return request<NoonReportImportResponse>('/api/noon-report/file', { method: 'POST', body })
  },
  downloadNoonReportTemplate: () => download('/api/noon-report/template'),
  dataReset: () => request<DataResetStatus>('/api/data/reset', { method: 'POST' }),
  dataResetStatus: () => request<DataResetStatus>('/api/data/reset/status'),
  advisor: (question: string) => request<AdvisorResponse>('/api/advisor', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
  }),
  // SSE 串流版：每收到一個事件（token / tool / done）就回呼 onEvent，前端逐字渲染
  advisorStream: async (question: string, onEvent: (e: AdvisorStreamEvent) => void) => {
    const res = await fetch('/api/advisor/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
    })
    if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`)
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let idx: number
      while ((idx = buffer.indexOf('\n\n')) >= 0) {
        const raw = buffer.slice(0, idx)
        buffer = buffer.slice(idx + 2)
        if (raw.startsWith('data: ')) onEvent(JSON.parse(raw.slice(6)) as AdvisorStreamEvent)
      }
    }
  },
}
