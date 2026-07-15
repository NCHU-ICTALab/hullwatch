import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

describe('dashboard information architecture', () => {
  const app = readFileSync(new URL('../App.tsx', import.meta.url), 'utf8')
  const css = readFileSync(new URL('../App.css', import.meta.url), 'utf8')

  it('keeps the log page read-only and moves interaction to decisions', () => {
    const logPage = app.slice(app.indexOf('function DiagnoseView'), app.indexOf('function DecideView'))

    expect(logPage).toContain('Speed Loss 歷史趨勢')
    expect(logPage).not.toContain('模型比較')
    expect(logPage).not.toContain('決策主模型')
    expect(logPage).not.toContain('情境船速')
    expect(app).toContain('立即清潔效益試算')
    expect(app).not.toContain('清洗日淨節省曲線')
  })

  it('contains long advisor content within the panel width', () => {
    expect(css).toMatch(/\.advisor-thread\s*\{[^}]*overflow-x:\s*hidden/s)
    expect(css).toMatch(/\.advisor-exchange\s*\{[^}]*min-width:\s*0/s)
    expect(css).toMatch(/\.advisor-answer\s*\{[^}]*max-width:\s*100%/s)
  })
})
