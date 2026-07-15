import { readFileSync } from 'node:fs'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { BrandIdentity } from './BrandIdentity'

describe('BrandIdentity', () => {
  it('uses the Oi! Hullwatch name, slogan, and icon-only asset', () => {
    const html = renderToStaticMarkup(<BrandIdentity />)

    expect(html).toContain('Oi! Hullwatch')
    expect(html).toContain('Oi! Save the Oil.')
    expect(html).toContain('/oi-hullwatch-symbol.svg')
    expect(html).not.toContain('FLEET PERFORMANCE')
  })

  it('does not crop the loading icon with enlarged dimensions or negative offsets', () => {
    const css = readFileSync(new URL('../App.css', import.meta.url), 'utf8')

    expect(css).not.toMatch(/\.loading-screen \.brand-icon-crop img\s*\{[^}]*(?:left|top)\s*:\s*-/s)
    expect(css).toContain('.loading-screen .brand-icon-crop img { width: 100%; height: 100%; object-fit: contain; }')
  })

  it('ships a square symbol without the source mockup caption', () => {
    const svg = readFileSync(new URL('../../public/oi-hullwatch-symbol.svg', import.meta.url), 'utf8')

    expect(svg).toContain('viewBox="0 0 512 512"')
    expect(svg).not.toContain('Web icon')
    expect(svg).not.toContain('<text')
    expect(svg).toContain('fill-rule="evenodd"')
  })
})
