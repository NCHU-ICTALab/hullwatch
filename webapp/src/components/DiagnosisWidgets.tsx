import { Menu, Settings } from 'lucide-react'
import type { ShipDetail } from '../types'

export function DashboardToolsMenu({ onSettings }: { onSettings: () => void }) {
  return (
    <details className="tool-menu">
      <summary><Menu size={16} />工具</summary>
      <div>
        <button onClick={onSettings}><Settings size={16} />設定</button>
      </div>
    </details>
  )
}

export function AttributionSplitBar({ attribution }: { attribution: ShipDetail['hull_prop'] }) {
  const safePropShare = Math.min(1, Math.max(0, attribution.prop_share))
  const hullShare = 1 - safePropShare
  const hullValue = `${attribution.hull_pp.toFixed(1)}pp`
  const propValue = `${attribution.prop_pp.toFixed(1)}pp`
  const hullLabel = `船殼 ${hullValue}`
  const propLabel = `螺旋槳 ${propValue}`

  return (
    <div className="split-bar-figure">
      <div className="split-bar" role="img" aria-label={`${hullLabel}，${propLabel}`}>
        <span className="split-bar-segment hull" style={{ width: `${hullShare * 100}%` }} aria-hidden="true" />
        <span className="split-bar-segment propeller" style={{ width: `${safePropShare * 100}%` }} aria-hidden="true" />
      </div>
      <dl className="split-bar-legend">
        <div><dt><i className="hull" aria-hidden="true" />船殼</dt><dd>{hullValue}</dd></div>
        <div><dt><i className="propeller" aria-hidden="true" />螺旋槳</dt><dd>{propValue}</dd></div>
      </dl>
    </div>
  )
}
