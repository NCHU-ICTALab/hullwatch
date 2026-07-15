import { describe, expect, it } from 'vitest'
import { allocateEventLanes, cleaningSavings, decisionModelOptions, fleetShipMatchesFilters, fuelHistoryForGrade, layoutTrendEventMarkers, speedLossMinimumForStatus } from './dashboardLogic'
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

  it('synchronizes the Speed Loss floor with the API operational policy', () => {
    const policy = { action: 12, watch: 6 }

    expect(speedLossMinimumForStatus('all', policy)).toBe(0)
    expect(speedLossMinimumForStatus('action', policy)).toBe(12)
    expect(speedLossMinimumForStatus('watch', policy)).toBe(6)
    expect(speedLossMinimumForStatus('ok', policy)).toBe(0)
  })

  it('keeps forecast-only watch ships visible at the synchronized policy floor', () => {
    const forecastWatch = { status: 'watch', speed_loss_pct: 5.2 } as const

    expect(fleetShipMatchesFilters(forecastWatch, 'watch', 6, 6)).toBe(true)
    expect(fleetShipMatchesFilters(forecastWatch, 'all', 6, 6)).toBe(false)
    expect(fleetShipMatchesFilters(forecastWatch, 'watch', 6.5, 6)).toBe(false)
  })
})
