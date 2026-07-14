import type { FuelPriceResponse, ScheduleResponse } from './types'

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
