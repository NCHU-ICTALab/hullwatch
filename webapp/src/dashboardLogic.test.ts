import { describe, expect, it } from 'vitest'
import { advisorWidthBounds, allocateEventLanes, clampAdvisorWidth, cleaningSavings, decisionModelOptions, fleetShipMatchesFilters, fuelHistoryForGrade, GANTT_EVENT_LABEL_CLEARANCE_DAYS, layoutTrendEventMarkers, maintenanceActionLabel, maintenanceActionPresentation, maintenanceTimelineMarkerLabel, scheduleForSelectedShip, speedLossMinimumForStatus } from './dashboardLogic'
import type { FuelPriceResponse, ModelInfo, ScheduleResponse } from './types'

describe('dashboard behavior', () => {
  it('places overlapping maintenance events in separate lanes', () => {
    const result = allocateEventLanes([
      { ship_id: 'HW-001', date: '2026-07-01', type: 'UWC', notes: '' },
      { ship_id: 'HW-001', date: '2026-07-01', type: 'DD', notes: '' },
      { ship_id: 'HW-001', date: '2026-07-20', type: 'PP', notes: '' },
    ])

    expect(result.map(({ lane }) => lane)).toEqual([0, 1, 0])
  })

  it('uses wider lane clearance when Gantt event names must remain readable', () => {
    const result = allocateEventLanes([
      { ship_id: 'HW-001', date: '2026-07-01', type: 'cleaning', notes: '' },
      { ship_id: 'HW-001', date: '2026-07-20', type: 'propeller_polish', notes: '' },
    ], GANTT_EVENT_LABEL_CLEARANCE_DAYS)

    expect(result.map(({ lane }) => lane)).toEqual([0, 1])
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
    expect(result.map(({ offsetY }) => offsetY)).toEqual([0, -32, -64])
    expect(result.every(({ y }) => y === 1)).toBe(true)
    expect(result.map(({ markerLabel }) => markerLabel)).toEqual(['1', '2', '3'])
    expect(result.map(({ actionLabel }) => actionLabel)).toEqual(['船殼清洗', '螺旋槳拋光', '水下檢查（僅拍照，無物理介入）'])
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

  it('treats immediately actionable ships as part of the watch-or-worse filter', () => {
    const actionShip = { status: 'action', speed_loss_pct: 12.4 } as const

    expect(fleetShipMatchesFilters(actionShip, 'watch', 5, 5)).toBe(true)
    expect(fleetShipMatchesFilters(actionShip, 'ok', 0, 5)).toBe(false)
  })

  it('limits the decision schedule to the ship selected from fleet overview', () => {
    const schedule = {
      recommendations: [
        { ship_id: 'S1', ship_name: 'S1' },
        { ship_id: 'S2', ship_name: 'S2' },
      ],
      dry_docks: [
        { ship_id: 'S1', date: '2026-08-01', read_only: true },
        { ship_id: 'S2', date: '2026-09-01', read_only: true },
      ],
      maintenance_events: [
        { ship_id: 'S1', date: '2026-01-01', type: 'UWC', notes: '' },
        { ship_id: 'S2', date: '2026-02-01', type: 'PP', notes: '' },
      ],
    } as unknown as ScheduleResponse

    const selected = scheduleForSelectedShip(schedule, 'S2')

    expect(selected.recommendations.map(({ ship_id }) => ship_id)).toEqual(['S2'])
    expect(selected.dry_docks.map(({ ship_id }) => ship_id)).toEqual(['S2'])
    expect(selected.maintenance_events.map(({ ship_id }) => ship_id)).toEqual(['S2'])
  })

  it('presents source maintenance action codes as Chinese-only UI names', () => {
    expect(maintenanceActionLabel('PP')).toBe('螺旋槳拋光')
    expect(maintenanceActionLabel('UWI+PP')).toBe('水下檢查 + 螺旋槳拋光')
    expect(maintenanceActionLabel('UWC')).toBe('船殼清洗')
    expect(maintenanceActionLabel('UWC+PP')).toBe('船殼清洗 + 螺旋槳拋光')
    expect(maintenanceActionLabel('DD')).toBe('進塢（全面塗裝 + 機械保養）')
    expect(maintenanceActionLabel('UWI')).toBe('水下檢查（僅拍照，無物理介入）')
    expect(maintenanceActionLabel('unknown')).toBe('unknown')
    expect(maintenanceActionLabel()).toBe('—')
  })

  it('normalizes API maintenance aliases and keeps timeline event names visible', () => {
    expect(maintenanceActionLabel('propeller_polish')).toBe('螺旋槳拋光')
    expect(maintenanceActionLabel('cleaning')).toBe('船殼清洗')
    expect(maintenanceActionLabel('inspection')).toBe('水下檢查（僅拍照，無物理介入）')
    expect(maintenanceActionLabel('drydock')).toBe('進塢（全面塗裝 + 機械保養）')
    expect(maintenanceTimelineMarkerLabel('propeller_polish')).toBe('螺旋槳拋光')
    expect(maintenanceTimelineMarkerLabel('inspection')).toBe('水下檢查')
    expect(maintenanceTimelineMarkerLabel('drydock')).toBe('進塢大修')
    expect(maintenanceActionPresentation('drydock').kind).toBe('DD')
    expect(maintenanceActionPresentation('propeller_polish').kind).toBe('PP')
  })

  it('keeps the resizable advisor usable without consuming the dashboard', () => {
    expect(advisorWidthBounds(1600)).toEqual({ min: 360, max: 720 })
    expect(advisorWidthBounds(1024)).toEqual({ min: 300, max: 480 })
    expect(advisorWidthBounds(600)).toEqual({ min: 600, max: 600 })
    expect(clampAdvisorWidth(900, 1600)).toBe(720)
    expect(clampAdvisorWidth(200, 1024)).toBe(300)
  })
})
