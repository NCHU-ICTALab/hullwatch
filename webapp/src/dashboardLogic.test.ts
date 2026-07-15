import { describe, expect, it } from 'vitest'
import { allocateEventLanes, cleaningSavings, decisionModelOptions, fuelHistoryForGrade, layoutTrendEventMarkers, speedLossMinimumForStatus } from './dashboardLogic'
import type { FuelPriceResponse, ModelInfo } from './types'

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

  it('stacks nearby Speed Loss maintenance markers into separate lanes', () => {
    const result = layoutTrendEventMarkers([
      { date: '2026-07-01', type: 'UWC', notes: '船殼清洗' },
      { date: '2026-07-01', type: 'PP', notes: '螺槳拋光' },
      { date: '2026-07-04', type: 'UWI', notes: '水下檢查' },
    ], 1)

    expect(result.map(({ lane }) => lane)).toEqual([0, 1, 2])
    expect(new Set(result.map(({ y }) => y)).size).toBe(3)
    expect(result.map(({ abbreviation }) => abbreviation)).toEqual(['UWC', 'PP', 'UWI'])
  })

  it('allows every usable forecast model to be selected as the decision model', () => {
    const model = (id: string, isPrimary: boolean, status: ModelInfo['status'] = 'available'): ModelInfo => ({
      id,
      name: id,
      description: `${id} forecast`,
      validation_mape: null,
      needs_speed: false,
      is_primary: isPrimary,
      status,
    })

    const options = decisionModelOptions([
      model('linear-growth', true, 'active'),
      model('physics-scenario', false),
      model('persistence', false),
      model('unvalidated-upload', false, 'candidate'),
      model('failed-upload', false, 'rejected'),
    ])

    expect(options.map(({ id }) => id)).toEqual(['linear-growth', 'physics-scenario', 'persistence'])
  })

  it('synchronizes the Speed Loss floor with the selected fleet status', () => {
    const ships = [
      { status: 'action', speed_loss_pct: 11.2 },
      { status: 'action', speed_loss_pct: 13.8 },
      { status: 'watch', speed_loss_pct: 7.8 },
      { status: 'watch', speed_loss_pct: 8.4 },
      { status: 'ok', speed_loss_pct: 2.2 },
    ] as const

    expect(speedLossMinimumForStatus(ships, 'all')).toBe(0)
    expect(speedLossMinimumForStatus(ships, 'action')).toBe(11)
    expect(speedLossMinimumForStatus(ships, 'watch')).toBe(7.5)
    expect(speedLossMinimumForStatus(ships, 'ok')).toBe(2)
  })
})
