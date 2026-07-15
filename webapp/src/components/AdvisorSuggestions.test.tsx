import { Children, type ReactElement, type ReactNode } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

import { AdvisorSuggestions } from './AdvisorSuggestions'

describe('AdvisorSuggestions', () => {
  it('renders ten accessible demo questions with the selected ship context', () => {
    const html = renderToStaticMarkup(
      <AdvisorSuggestions shipId="S11" onSelect={vi.fn()} />,
    )

    expect(html.match(/<button/g)).toHaveLength(10)
    expect(html).toContain('建議提問')
    expect(html).toContain('S11 現在的 Speed Loss')
    expect(html).toContain('市場行情多久更新一次')
  })

  it('passes the complete question to the composer callback', () => {
    const onSelect = vi.fn()
    const suggestions = AdvisorSuggestions({ shipId: 'S11', onSelect })
    const group = Children.toArray(suggestions.props.children)[1] as ReactElement<{ children: ReactNode }>
    const buttons = Children.toArray(group.props.children) as ReactElement<{ onClick: () => void }>[]

    buttons[1].props.onClick()

    expect(onSelect).toHaveBeenCalledWith('S11 現在的 Speed Loss、狀態與每日超額成本是多少？')
  })
})
