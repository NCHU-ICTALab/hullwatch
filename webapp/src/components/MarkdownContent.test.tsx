import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { MarkdownContent } from './MarkdownContent'

describe('MarkdownContent', () => {
  it('renders accessible headings, emphasis, lists, and GFM tables', () => {
    const html = renderToStaticMarkup(
      <MarkdownContent content={'## 建議\n\n**先處理**高風險船舶。\n\n- 檢查資料\n- 安排 UWI\n\n| 船舶 | 狀態 |\n| --- | --- |\n| A | 密切留意 |'} />,
    )

    expect(html).toContain('<h2>建議</h2>')
    expect(html).toContain('<strong>先處理</strong>')
    expect(html).toContain('<ul>')
    expect(html).toContain('<table>')
  })

  it('does not turn raw HTML or unsafe links into executable markup', () => {
    const html = renderToStaticMarkup(
      <MarkdownContent content={'<script>alert(1)</script>\n\n[不安全連結](javascript:alert(1))'} />,
    )

    expect(html).not.toContain('<script>')
    expect(html).not.toContain('href="javascript:')
  })
})
