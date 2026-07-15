import type { FuelPriceResponse, ModelInfo, ScheduleResponse, ShipDetail } from './types'

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
