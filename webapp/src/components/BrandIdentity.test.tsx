import { readFileSync } from 'node:fs'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { BrandIdentity } from './BrandIdentity'

describe('BrandIdentity', () => {
  it('uses the Oi! Hullwatch name, slogan, and supplied icon asset', () => {
    const html = renderToStaticMarkup(<BrandIdentity />)

    expect(html).toContain('Oi! Hullwatch')
    expect(html).toContain('Oi! Save the Oil.')
    expect(html).toContain('/oi-hullwatch-icon.png')
    expect(html).not.toContain('FLEET PERFORMANCE')
  })

  it('keeps the complete loading icon inside its crop viewport', () => {
    const css = readFileSync(new URL('../App.css', import.meta.url), 'utf8')

    expect(css).toContain('.loading-screen .brand-icon-crop img { width: 110px; height: 110px; left: -23px; top: -9px; }')
  })
})
