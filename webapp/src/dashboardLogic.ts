import type { FleetShip, FuelPriceResponse, ModelInfo, ScheduleResponse, ShipDetail, Status } from './types'

export const EVENT_LANE_HEIGHT = 24
export const TREND_EVENT_LANE_OFFSET_PX = 32
export const GANTT_EVENT_LABEL_CLEARANCE_DAYS = 90
const EVENT_LABEL_CLEARANCE_DAYS = 14

export function allocateEventLanes(events: ScheduleResponse['maintenance_events'], clearanceDays = EVENT_LABEL_CLEARANCE_DAYS) {
  const laneEnds: number[] = []
  return [...events].sort((a, b) => a.date.localeCompare(b.date)).map((event) => {
    const eventDay = new Date(event.date).getTime() / 86400000
    let lane = laneEnds.findIndex((end) => eventDay - end >= clearanceDays)
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

export type MaintenanceActionKind = 'PP' | 'UWI+PP' | 'UWC' | 'UWC+PP' | 'DD' | 'UWI' | 'unknown'

const MAINTENANCE_ACTIONS: Record<Exclude<MaintenanceActionKind, 'unknown'>, { label: string; timelineLabel: string }> = {
  PP: { label: '螺旋槳拋光', timelineLabel: '螺旋槳拋光' },
  'UWI+PP': { label: '水下檢查 + 螺旋槳拋光', timelineLabel: '水檢＋螺旋槳拋光' },
  UWC: { label: '船殼清洗', timelineLabel: '船殼清洗' },
  'UWC+PP': { label: '船殼清洗 + 螺旋槳拋光', timelineLabel: '船洗＋螺旋槳拋光' },
  DD: { label: '進塢（全面塗裝 + 機械保養）', timelineLabel: '進塢大修' },
  UWI: { label: '水下檢查（僅拍照，無物理介入）', timelineLabel: '水下檢查' },
}

const MAINTENANCE_ACTION_ALIASES: Partial<Record<string, Exclude<MaintenanceActionKind, 'unknown'>>> = {
  PP: 'PP',
  PROPELLER_POLISH: 'PP',
  PROPELLER_POLISHING: 'PP',
  'UWI+PP': 'UWI+PP',
  UWC: 'UWC',
  CLEANING: 'UWC',
  HULL_CLEANING: 'UWC',
  UNDERWATER_CLEANING: 'UWC',
  'UWC+PP': 'UWC+PP',
  DD: 'DD',
  DRYDOCK: 'DD',
  DRY_DOCK: 'DD',
  UWI: 'UWI',
  INSPECTION: 'UWI',
  UNDERWATER_INSPECTION: 'UWI',
}

function maintenanceActionKey(action: string) {
  return action.trim().toUpperCase().replace(/[\s-]+/g, '_')
}

export function maintenanceActionLabel(action?: string | null) {
  return maintenanceActionPresentation(action).label
}

export function maintenanceTimelineMarkerLabel(action?: string | null) {
  return maintenanceActionPresentation(action).timelineLabel
}

export function maintenanceActionPresentation(action?: string | null): { kind: MaintenanceActionKind; label: string; timelineLabel: string } {
  const source = action?.trim()
  if (!source) return { kind: 'unknown', label: '—', timelineLabel: '—' }
  const kind = MAINTENANCE_ACTION_ALIASES[maintenanceActionKey(source)] ?? 'unknown'
  if (kind === 'unknown') return { kind, label: source, timelineLabel: source }
  return { kind, ...MAINTENANCE_ACTIONS[kind] }
}

export function advisorWidthBounds(viewportWidth: number) {
  if (viewportWidth < 768) return { min: viewportWidth, max: viewportWidth }
  if (viewportWidth < 1200) return { min: 300, max: Math.min(480, Math.floor(viewportWidth * .5)) }
  return { min: 360, max: Math.min(720, Math.floor(viewportWidth * .55)) }
}

export function clampAdvisorWidth(width: number, viewportWidth: number) {
  const { min, max } = advisorWidthBounds(viewportWidth)
  return Math.min(max, Math.max(min, width))
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
  if (statusFilter === 'action' && ship.status !== 'action') return false
  if (statusFilter === 'watch' && ship.status !== 'watch' && ship.status !== 'action') return false
  if (statusFilter === 'ok' && ship.status !== 'ok') return false
  if (statusFilter === 'watch' && ship.status === 'watch' && speedLossMinimum <= watchThreshold) return true
  return ship.speed_loss_pct >= speedLossMinimum
}

export function scheduleForSelectedShip(schedule: ScheduleResponse, shipId: string): ScheduleResponse {
  return {
    ...schedule,
    recommendations: schedule.recommendations.filter((item) => item.ship_id === shipId),
    dry_docks: schedule.dry_docks.filter((item) => item.ship_id === shipId),
    maintenance_events: schedule.maintenance_events.filter((item) => item.ship_id === shipId),
  }
}

export function layoutTrendEventMarkers(events: ShipDetail['events'], baseY: number) {
  const laneEnds: number[] = []
  return [...events].sort((a, b) => a.date.localeCompare(b.date)).map((event, index) => {
    const eventDay = new Date(event.date).getTime() / 86400000
    let lane = laneEnds.findIndex((end) => eventDay - end >= EVENT_LABEL_CLEARANCE_DAYS)
    if (lane < 0) lane = laneEnds.length
    laneEnds[lane] = eventDay
    return {
      ...event,
      markerLabel: String(index + 1),
      actionLabel: maintenanceActionLabel(event.type),
      lane,
      y: baseY,
      offsetY: lane === 0 ? 0 : -lane * TREND_EVENT_LANE_OFFSET_PX,
    }
  })
}
