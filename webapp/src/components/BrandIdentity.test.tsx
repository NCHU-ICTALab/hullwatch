import { readFileSync } from 'node:fs'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { BrandIdentity } from './BrandIdentity'

describe('BrandIdentity', () => {
  it('uses the Oi! Hullwatch name, slogan, and icon-only asset', () => {
    const html = renderToStaticMarkup(<BrandIdentity />)

    expect(html).toContain('Oi! Hullwatch')
    expect(html).toContain('Oi! Save the Oil.')
    expect(html).toContain('/Oi.ico')
    expect(html).not.toContain('FLEET PERFORMANCE')
  })

  it('does not crop the loading icon with enlarged dimensions or negative offsets', () => {
    const css = readFileSync(new URL('../App.css', import.meta.url), 'utf8')

    expect(css).not.toMatch(/\.loading-screen \.brand-icon-crop img\s*\{[^}]*(?:left|top)\s*:\s*-/s)
    expect(css).toContain('.loading-screen .brand-icon-crop img { width: 100%; height: 100%; object-fit: contain; }')
  })

  it('ships the user-provided Windows icon asset', () => {
    const icon = readFileSync(new URL('../../public/Oi.ico', import.meta.url))
    const frameCount = icon.readUInt16LE(4)
    const widths = Array.from({ length: frameCount }, (_, index) => {
      const width = icon[6 + index * 16]
      return width === 0 ? 256 : width
    })

    expect([...icon.subarray(0, 4)]).toEqual([0, 0, 1, 0])
    expect(widths).toContain(64)
    expect(widths).toContain(128)

    const indexHtml = readFileSync(new URL('../../index.html', import.meta.url), 'utf8')
    expect(indexHtml).toContain('type="image/x-icon" href="/Oi.ico"')
    expect(indexHtml).not.toContain('/favicon.svg')
  })
})
