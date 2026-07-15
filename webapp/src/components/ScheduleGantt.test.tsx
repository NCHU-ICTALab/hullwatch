import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { ScheduleGantt } from '../App'
import type { ScheduleItem, ScheduleResponse } from '../types'

const item = (shipId: string): ScheduleItem => ({
  ship_id: shipId,
  ship_name: shipId,
  action: 'UWC',
  action_options: [{
    action: 'UWC',
    speed_loss_recovery_pp: 5.7,
    post_clean_speed_loss_pct: 6.3,
    action_cost_usd: 25_000,
    payback_days: 8,
    daily_fuel_saving_tons: 8,
    monthly_saving_usd: 100_000,
  }],
  window_start: '2026-08-01',
  window_end: '2026-08-15',
  speed_loss_recovery_pp: 5.7,
  payback_days: 8,
  action_cost_usd: 25_000,
  monthly_saving_usd: 100_000,
  daily_fuel_saving_tons: 8,
  inspection_recommended: false,
  backfill: { ship_id: 'S2', ship_name: 'S2' },
  read_only: true,
  speed_loss_pct: 12,
  excess_cost_per_day: 4_000,
  risk_rank: 1,
})

const schedule = (recommendations: ScheduleItem[]): ScheduleResponse => ({
  as_of: '2026-07-15',
  horizon_days: 180,
  past_days: 90,
  future_days: 180,
  timeline_start: '2026-04-16',
  timeline_end: '2027-01-11',
  primary_model_id: 'linear-growth',
  recommendations,
  dry_docks: [],
  maintenance_events: [],
})

describe('ScheduleGantt controls', () => {
  it('keeps a selected-vessel Gantt at 100% without redundant controls', () => {
    const html = renderToStaticMarkup(
      <ScheduleGantt schedule={schedule([item('S1')])} showSorting={false} onOpen={() => undefined} onSelect={() => undefined} />,
    )

    expect(html).not.toContain('排列方式')
    expect(html).not.toContain('時間縮放')
    expect(html).not.toContain('回到今天')
    expect(html).not.toContain('前段')
    expect(html).not.toContain('後段')
    expect(html).toContain('width:100%')
    expect(html).toContain('US$100,000／月')
  })

  it('retains sorting only when the fleet Gantt has multiple vessels', () => {
    const html = renderToStaticMarkup(
      <ScheduleGantt schedule={schedule([item('S1'), item('S2')])} onOpen={() => undefined} onSelect={() => undefined} />,
    )

    expect(html).toContain('排列方式')
    expect(html).not.toContain('時間縮放')
    expect(html).not.toContain('回到今天')
    expect(html).toContain('width:100%')
  })
})
