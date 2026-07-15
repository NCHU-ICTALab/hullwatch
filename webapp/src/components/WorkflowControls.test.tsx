import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { FleetScheduleDisclosure, StatusFilterButtons, WorkflowSteps } from './WorkflowControls'

describe('WorkflowSteps', () => {
  it('locks ship-specific steps until a ship is selected', () => {
    const html = renderToStaticMarkup(
      <WorkflowSteps currentView="fleet" selectedShip={null} onNavigate={() => undefined} />,
    )

    expect(html).toContain('請先從 Speed Loss 總覽選擇船舶')
    expect(html).toContain('日誌，請先從 Speed Loss 總覽選擇船舶')
    expect(html).toContain('決策，請先從 Speed Loss 總覽選擇船舶')
    expect(html.match(/disabled=""/g)).toHaveLength(2)
  })

  it('unlocks steps and identifies the currently selected ship', () => {
    const html = renderToStaticMarkup(
      <WorkflowSteps
        currentView="diagnose"
        selectedShip={{ ship_id: 'S11', ship_name: 'S11' }}
        onNavigate={() => undefined}
      />,
    )

    expect(html).toContain('目前船舶')
    expect(html).toContain('S11')
    expect(html).toContain('aria-current="step"')
    expect(html).toContain('Speed Loss 總覽')
    expect(html).toContain('日誌')
    expect(html).not.toContain('disabled=""')
  })
})

describe('FleetScheduleDisclosure', () => {
  it('keeps the fleet-wide schedule collapsed by default', () => {
    const html = renderToStaticMarkup(
      <FleetScheduleDisclosure><div>甘特圖內容</div></FleetScheduleDisclosure>,
    )

    expect(html).toContain('全船隊清潔建議甘特圖')
    expect(html).not.toMatch(/<details[^>]*\sopen/)
  })
})

describe('StatusFilterButtons', () => {
  it('exposes selection state and the semantic status colour class', () => {
    const html = renderToStaticMarkup(
      <StatusFilterButtons
        selected="action"
        options={[
          { id: 'all', label: '全部', title: '全部' },
          { id: 'action', label: '立即處置', title: '10% 以上' },
          { id: 'watch', label: '密切留意', title: '5% 以上' },
          { id: 'ok', label: '狀態正常', title: '5% 以下' },
        ]}
        onSelect={() => undefined}
      />,
    )

    expect(html).toContain('status-action selected')
    expect(html).toContain('aria-pressed="true"')
    expect(html).toContain('status-watch')
    expect(html).toContain('status-ok')
  })
})
