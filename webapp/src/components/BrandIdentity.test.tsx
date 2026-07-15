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
})
