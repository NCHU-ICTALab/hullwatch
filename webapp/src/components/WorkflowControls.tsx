import type { ReactNode } from 'react'

import type { Status } from '../types'

export type WorkflowView = 'fleet' | 'diagnose' | 'decide'

const WORKFLOW_STEPS: { id: WorkflowView; number: string; label: string; caption: string }[] = [
  { id: 'fleet', number: '1', label: 'Speed Loss 總覽', caption: 'FLEET' },
  { id: 'diagnose', number: '2', label: '日誌', caption: 'LOG' },
  { id: 'decide', number: '3', label: '決策', caption: 'DECIDE' },
]

export function WorkflowSteps({ currentView, selectedShip, onNavigate }: {
  currentView: WorkflowView
  selectedShip: { ship_id: string; ship_name: string } | null
  onNavigate: (view: WorkflowView) => void
}) {
  return (
    <nav className="workflow-steps" aria-label="Oi! Hullwatch 分析步驟">
      <ol>
        {WORKFLOW_STEPS.map((step) => {
          const locked = step.id !== 'fleet' && !selectedShip
          return (
            <li key={step.id} className={`${currentView === step.id ? 'current' : ''} ${locked ? 'locked' : ''}`}>
              <button
                type="button"
                disabled={locked}
                aria-current={currentView === step.id ? 'step' : undefined}
                aria-label={locked ? `${step.label}，請先從 Speed Loss 總覽選擇船舶` : step.label}
                onClick={() => onNavigate(step.id)}
              >
                <span className="workflow-step-number" aria-hidden="true">{step.number}</span>
                <span className="workflow-step-copy"><strong>{step.label}</strong><small>{step.caption}</small></span>
              </button>
            </li>
          )
        })}
      </ol>
      <div className={`workflow-vessel ${selectedShip ? '' : 'empty'}`} aria-live="polite">
        <span>{selectedShip ? '目前船舶' : '下一步'}</span>
        {selectedShip
          ? <strong>{selectedShip.ship_name}<small>{selectedShip.ship_id}</small></strong>
          : <strong>請先從 Speed Loss 總覽選擇船舶</strong>}
      </div>
    </nav>
  )
}

export function FleetScheduleDisclosure({ children }: { children: ReactNode }) {
  return (
    <details className="fleet-schedule-disclosure panel">
      <summary>
        <span><b>全船隊清潔建議甘特圖</b><small>預設收起 · 點擊展開排程</small></span>
        <i aria-hidden="true">＋</i>
      </summary>
      {children}
    </details>
  )
}

export type StatusFilterOption = {
  id: 'all' | Status
  label: string
  title: string
}

export function StatusFilterButtons({ selected, options, onSelect }: {
  selected: 'all' | Status
  options: StatusFilterOption[]
  onSelect: (status: 'all' | Status) => void
}) {
  return options.map((option) => (
    <button
      type="button"
      key={option.id}
      className={`status-${option.id} ${selected === option.id ? 'selected' : ''}`}
      aria-pressed={selected === option.id}
      title={option.title}
      onClick={() => onSelect(option.id)}
    >
      {option.label}
    </button>
  ))
}
