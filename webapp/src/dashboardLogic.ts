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

export function decisionModelOptions(models: ModelInfo[]) {
  return models.filter((model) => model.status !== 'candidate' && model.status !== 'rejected')
}

export function speedLossMinimumForStatus(
  ships: readonly Pick<FleetShip, 'status' | 'speed_loss_pct'>[],
  status: 'all' | Status,
) {
  if (status === 'all') return 0
  const values = ships
    .filter((ship) => ship.status === status)
    .map((ship) => ship.speed_loss_pct)
    .filter(Number.isFinite)
  if (values.length === 0) return 0
  return Math.min(15, Math.max(0, Math.floor(Math.min(...values) * 2) / 2))
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
