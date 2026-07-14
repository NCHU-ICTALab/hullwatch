import { describe, expect, it } from 'vitest'
import { allocateEventLanes, cleaningSavings, fuelHistoryForGrade } from './dashboardLogic'
import type { FuelPriceResponse } from './types'

describe('dashboard behavior', () => {
  it('places overlapping maintenance events in separate lanes', () => {
    const result = allocateEventLanes([
      { ship_id: 'HW-001', date: '2026-07-01', type: 'UWC', notes: '' },
      { ship_id: 'HW-001', date: '2026-07-01', type: 'DD', notes: '' },
      { ship_id: 'HW-001', date: '2026-07-20', type: 'PP', notes: '' },
    ])

    expect(result.map(({ lane }) => lane)).toEqual([0, 1, 0])
  })

  it('expresses cleaning scenarios as savings relative to no cleaning', () => {
    expect(cleaningSavings(1_000, [1_100, 900, 800])).toEqual([-100, 100, 200])
  })

  it('returns the selected grade history rather than a hard-coded VLSFO series', () => {
    const fuel = { history_by_grade: { LSMGO: [{ date: '2026-07-15', usd_per_ton: 900, source: 'test' }] } } as unknown as FuelPriceResponse
    expect(fuelHistoryForGrade(fuel, 'LSMGO')[0].usd_per_ton).toBe(900)
    expect(fuelHistoryForGrade(fuel, 'VLSFO')).toEqual([])
  })
})
