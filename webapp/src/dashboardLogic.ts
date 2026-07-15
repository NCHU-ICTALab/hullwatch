import type { FleetShip, FuelPriceResponse, ModelInfo, ScheduleResponse, ShipDetail, Status } from './types'

export const EVENT_LANE_HEIGHT = 24
const EVENT_LABEL_CLEARANCE_DAYS = 14

export function allocateEventLanes(events: ScheduleResponse['maintenance_events']) {
  const laneEnds: number[] = []
  return [...events].sort((a, b) => a.date.localeCompare(b.date)).map((event) => {
    const eventDay = new Date(event.date).getTime() / 86400000
    let lane = laneEnds.findIndex((end) => eventDay - end >= EVENT_LABEL_CLEARANCE_DAYS)
    if (lane < 0) lane = laneEnds.length
    laneEnds[lane] = eventDay
    return { event, lane }
  })
}

export function cleaningSavings(noCleanAverage: number, costs: number[]) {
  return costs.map((cost) => Math.round(noCleanAverage - cost))
}

export function fuelHistoryForGrade(fuel: FuelPriceResponse, grade: string) {
  return fuel.history_by_grade[grade] ?? []
}

const MAINTENANCE_ACTION_LABELS: Record<string, string> = {
  PP: '螺旋槳拋光',
  'UWI+PP': '水下檢查 + 螺旋槳拋光',
  UWC: '船殼清洗',
  'UWC+PP': '船殼清洗 + 螺旋槳拋光',
  DD: '進塢（全面塗裝 + 機械保養）',
  UWI: '水下檢查（僅拍照，無物理介入）',
}

export function maintenanceActionLabel(action?: string | null) {
  const source = action?.trim()
  if (!source) return '—'
  const code = source.toUpperCase()
  const label = MAINTENANCE_ACTION_LABELS[code]
  return label ? `${label}（${code}）` : source
}

export function decisionModelOptions(models: ModelInfo[]) {
  return models.filter((model) => model.status !== 'candidate' && model.status !== 'rejected')
}

export function speedLossMinimumForStatus(
  status: 'all' | Status,
  policy: { action: number; watch: number },
) {
  return { all: 0, action: policy.action, watch: policy.watch, ok: 0 }[status]
}

export function fleetShipMatchesFilters(
  ship: Pick<FleetShip, 'status' | 'speed_loss_pct'>,
  statusFilter: 'all' | Status,
  speedLossMinimum: number,
  watchThreshold: number,
) {
  if (statusFilter !== 'all' && ship.status !== statusFilter) return false
  if (statusFilter === 'watch' && speedLossMinimum <= watchThreshold) return true
  return ship.speed_loss_pct >= speedLossMinimum
}

export function layoutTrendEventMarkers(events: ShipDetail['events'], baseY: number) {
  const laneEnds: number[] = []
  const laneStep = Math.max(0.65, baseY * 0.45)
  return [...events].sort((a, b) => a.date.localeCompare(b.date)).map((event) => {
    const eventDay = new Date(event.date).getTime() / 86400000
    let lane = laneEnds.findIndex((end) => eventDay - end >= EVENT_LABEL_CLEARANCE_DAYS)
    if (lane < 0) lane = laneEnds.length
    laneEnds[lane] = eventDay
    return {
      ...event,
      abbreviation: event.type.toUpperCase(),
      lane,
      y: baseY + lane * laneStep,
    }
  })
}
