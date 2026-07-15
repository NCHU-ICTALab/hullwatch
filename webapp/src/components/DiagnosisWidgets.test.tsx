import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { AttributionSplitBar, DashboardToolsMenu } from './DiagnosisWidgets'

describe('AttributionSplitBar', () => {
  it('keeps both labels outside narrow proportional segments', () => {
    const html = renderToStaticMarkup(
      <AttributionSplitBar attribution={{ hull_pp: 3.3, prop_pp: 0.3, prop_share: 0.09 }} />,
    )

    expect(html).toContain('width:91%')
    expect(html).toContain('width:9%')
    expect(html).toContain('船殼 3.3pp')
    expect(html).toContain('螺旋槳 0.3pp')
    expect(html).toContain('split-bar-legend')
    expect(html).not.toMatch(/split-bar-segment[^>]*>[^<]*船殼/)
  })
})

describe('DashboardToolsMenu', () => {
  it('only exposes settings after underwater image interpretation is removed', () => {
    const html = renderToStaticMarkup(<DashboardToolsMenu onSettings={() => undefined} />)

    expect(html).toContain('設定')
    expect(html).not.toContain('水下判讀')
  })
})
