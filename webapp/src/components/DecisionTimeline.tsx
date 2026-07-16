import { useId } from 'react'
import { dayToDisplayDate, formatUsd } from '../dashboardLogic'
import type { MaintenanceAction, SpeedLossPredictionResponse } from '../types'
import type { MaintenanceBenefitSnapshot } from './MaintenanceBenefitPanel'

const SINGLE_ACTIONS: MaintenanceAction[] = ['UWI', 'PP', 'UWC', 'DD']
const PAST_WINDOW_DAYS = 365
const EVENT_LANE_CLEARANCE_DAYS = 60

const number = new Intl.NumberFormat('zh-TW', { maximumFractionDigits: 2 })

/**
 * 決策摘要時間軸：只彙整上方兩個面板已算出的結論（過去事件、預測門檻窗口、
 * 效益試算最佳動作），自己不做任何模型計算。日期一律為 Day 0 = 2021-01-01 映射座標。
 */
export function DecisionTimeline({ shipName, threshold, prediction, benefit, highlight }: {
  shipName: string
  threshold: number
  prediction: SpeedLossPredictionResponse | null
  benefit: MaintenanceBenefitSnapshot | null
  highlight?: boolean
}) {
  const id = useId().replace(/:/g, '')
  const response = benefit?.response && benefit.response.available ? benefit.response : null
  const predictionGroups = (prediction?.available ? prediction.groups : []).filter((group) => group.available)
  const latestDay = response?.latest_day
    ?? predictionGroups.find((group) => group.latest_day !== null)?.latest_day
    ?? null

  if (latestDay === null) {
    return (
      <section id="selected-decision" tabIndex={-1} className={`panel decision-timeline-panel ${highlight ? 'focus-highlight' : ''}`} aria-labelledby={`${id}-title`}>
        <div className="panel-heading">
          <div><span>DECISION SUMMARY TIMELINE</span><h2 id={`${id}-title`}>決策時間軸</h2></div>
          <span className="model-basis">彙整上方預測與效益試算 · 不另行計算 · {shipName}</span>
        </div>
        <p className="benefit-request-state" role="status" aria-live="polite">等待上方預測與效益試算結果…</p>
      </section>
    )
  }

  const horizonDays = Math.max(
    response?.parameters.horizon_days ?? 0,
    prediction?.parameters.forecast_days ?? 0,
    120,
  )
  const domainStart = latestDay - PAST_WINDOW_DAYS
  const domainEnd = latestDay + horizonDays
  const pos = (day: number) => ((Math.min(Math.max(day, domainStart), domainEnd) - domainStart) / (domainEnd - domainStart)) * 100

  const pastEvents = (response?.past_events ?? []).filter((event) => event.day >= domainStart && event.day <= latestDay)
  const laneEnds: number[] = []
  const placedEvents = [...pastEvents].sort((a, b) => a.day - b.day).map((event) => {
    let lane = laneEnds.findIndex((end) => event.day - end >= EVENT_LANE_CLEARANCE_DAYS)
    if (lane < 0) lane = laneEnds.length
    laneEnds[lane] = event.day
    return { event, lane: lane % 2 }
  })

  const windows = predictionGroups
    .filter((group) => group.latest_day !== null)
    .map((group) => {
      const base = group.latest_day!
      const { earliest_days: earliest, eta_days: eta, latest_days: latest } = group.threshold_crossing
      return {
        loadCondition: group.load_condition,
        loadLabel: group.load_label,
        crossed: earliest !== null,
        earliestDay: earliest !== null ? base + earliest : null,
        etaDay: eta !== null ? base + eta : null,
        latestEnd: latest !== null ? base + latest : domainEnd,
        openEnded: earliest !== null && latest === null,
      }
    })

  const singles = response
    ? response.actions.filter((action) => SINGLE_ACTIONS.includes(action.event_type)
      && (benefit?.selectedActions ?? []).includes(action.event_type))
    : []
  const best = [...singles].sort((a, b) => b.cost_saving_usd - a.cost_saving_usd)[0] ?? null
  const executionDay = latestDay + (response?.parameters.execution_delay_days ?? 0)
  const actionCross = best?.branch.find((point) => point.day > executionDay && point.speed_loss_pct >= threshold)
  const actionEnd = actionCross?.day ?? domainEnd
  const actionOpenEnded = best !== null && !actionCross

  const quarter = (domainEnd - domainStart) / 4
  const ticks = [0, 1, 2, 3, 4].map((step) => domainStart + step * quarter)

  const windowSummary = windows.map((window) => window.crossed
    ? `${window.loadLabel}預測窗口 ${dayToDisplayDate(window.earliestDay!)} 至 ${window.openEnded ? '預測期外' : dayToDisplayDate(window.latestEnd)}${window.etaDay !== null ? `、ETA ${dayToDisplayDate(window.etaDay)}` : ''}`
    : `${window.loadLabel}於預測期內未達門檻`).join('；')
  const actionSummary = best
    ? `行動方案 ${best.event_type} ${best.label} 於 ${dayToDisplayDate(executionDay)} 執行，預估門檻下維持${actionOpenEnded ? '超過展望期' : `至 ${dayToDisplayDate(actionEnd)}`}，展望期間成本節省 ${formatUsd(best.cost_saving_usd)}`
    : '尚未有可顯示的行動方案'
  const ariaLabel = `${shipName} 決策時間軸（映射日期，非真實日曆）：過去一年 ${pastEvents.length} 筆養護事件；${windowSummary || '尚無預測窗口'}；${actionSummary}。詳細數值見下方資料表。`

  return (
    <section id="selected-decision" tabIndex={-1} className={`panel decision-timeline-panel ${highlight ? 'focus-highlight' : ''}`} aria-labelledby={`${id}-title`}>
      <div className="panel-heading">
        <div><span>DECISION SUMMARY TIMELINE</span><h2 id={`${id}-title`}>決策時間軸</h2></div>
        <span className="model-basis">彙整上方預測與效益試算 · 不另行計算 · {shipName}</span>
      </div>

      <div className="timeline-visual" role="img" aria-label={ariaLabel}>
        <div className="timeline-lane">
          <div className="timeline-lane-head"><b>過去養護</b><small>真實事件</small></div>
          <div className="timeline-track timeline-track-events">
            {placedEvents.map(({ event, lane }) => (
              <span
                key={`${event.day}-${event.event_type}`}
                className={`timeline-event timeline-event-lane-${lane}`}
                style={{ left: `${pos(event.day)}%` }}
                title={`${dayToDisplayDate(event.day)} ${event.event_type} ${event.label}`}
              >
                <i aria-hidden="true" />{event.event_type}
              </span>
            ))}
            {placedEvents.length === 0 && <span className="timeline-note">過去一年沒有紀錄到養護事件。</span>}
            <i className="timeline-today-line" style={{ left: `${pos(latestDay)}%` }} aria-hidden="true" />
          </div>
        </div>

        {(windows.length > 0 ? windows : [null]).map((window) => (
          <div className="timeline-lane" key={window?.loadCondition ?? 'no-window'}>
            <div className="timeline-lane-head"><b>預測窗口</b><small>{window?.loadLabel ?? 'strict 預測'}</small></div>
            <div className="timeline-track">
              {window?.crossed ? (
                <>
                  <span
                    className={`timeline-window timeline-window-${window.loadCondition} ${window.openEnded ? 'open-ended' : ''}`}
                    style={{ left: `${pos(window.earliestDay!)}%`, width: `${Math.max(pos(window.latestEnd) - pos(window.earliestDay!), 0.8)}%` }}
                  />
                  {window.etaDay !== null && (
                    <span className="timeline-eta" style={{ left: `${pos(window.etaDay)}%` }} title={`ETA ${dayToDisplayDate(window.etaDay)}`} />
                  )}
                  <small className="timeline-caption" style={{ left: `${Math.min(pos(window.earliestDay!), 68)}%` }}>
                    {dayToDisplayDate(window.earliestDay!)} ～ {window.openEnded ? `>${prediction?.parameters.forecast_days ?? horizonDays} 天` : dayToDisplayDate(window.latestEnd)}
                    {window.etaDay !== null && ` · ETA ${dayToDisplayDate(window.etaDay)}`}
                  </small>
                </>
              ) : (
                <span className="timeline-note">
                  {window ? `預測期（${prediction?.parameters.forecast_days ?? horizonDays} 天）內未達 ${number.format(threshold)}% 門檻。` : '此船／載況資料不足，無法產生 strict 預測。'}
                </span>
              )}
              <i className="timeline-today-line" style={{ left: `${pos(latestDay)}%` }} aria-hidden="true" />
            </div>
          </div>
        ))}

        <div className="timeline-lane">
          <div className="timeline-lane-head"><b>行動方案</b><small>分岔模擬</small></div>
          <div className="timeline-track">
            {best ? (
              <>
                <span
                  className={`timeline-action-bar ${actionOpenEnded ? 'open-ended' : ''}`}
                  style={{ left: `${pos(executionDay)}%`, width: `${Math.max(pos(actionEnd) - pos(executionDay), 0.8)}%` }}
                />
                <small className="timeline-caption" style={{ left: `${Math.min(pos(executionDay), 55)}%` }}>
                  {best.event_type} {best.label} · {dayToDisplayDate(executionDay)} 執行 · 門檻下{actionOpenEnded ? `＞展望期` : `至 ${dayToDisplayDate(actionEnd)}`} · 省 {formatUsd(best.cost_saving_usd)}
                </small>
              </>
            ) : (
              <span className="timeline-note">{response ? '在上方勾選至少一個養護動作以顯示方案。' : benefit === null ? '等待效益試算結果…' : '效益試算資料不足，無法顯示行動方案。'}</span>
            )}
            <i className="timeline-today-line" style={{ left: `${pos(latestDay)}%` }} aria-hidden="true" />
          </div>
        </div>

        <div className="timeline-lane timeline-axis">
          <div className="timeline-lane-head" aria-hidden="true" />
          <div className="timeline-track">
            {ticks.map((tick) => (
              <small className="timeline-tick" key={tick} style={{ left: `${pos(tick)}%` }}>{dayToDisplayDate(tick)}</small>
            ))}
            <small className="timeline-tick timeline-tick-today" style={{ left: `${pos(latestDay)}%` }}>今天 {dayToDisplayDate(latestDay)}</small>
          </div>
        </div>
      </div>

      <p className="chart-explanation">日期為 Day 0 = 2021-01-01 映射座標，非真實日曆。「預測窗口」＝上方預測面板的 {number.format(threshold)}% 門檻交叉範圍與 ETA；「行動方案」＝效益試算勾選動作中成本節省最高者，長條為動作後預估維持在門檻下的期間。</p>

      <details className="data-fallback">
        <summary>查看決策時間軸資料表</summary>
        <div className="table-wrap">
          <table>
            <caption>過去一年養護事件（真實事件紀錄）</caption>
            <thead><tr><th>映射日期</th><th>動作</th></tr></thead>
            <tbody>
              {pastEvents.map((event) => <tr key={`${event.day}-${event.event_type}`}><td>{dayToDisplayDate(event.day)}</td><td>{event.event_type} {event.label}</td></tr>)}
              {pastEvents.length === 0 && <tr><td colSpan={2}>無</td></tr>}
            </tbody>
          </table>
          <table>
            <caption>預測窗口（{number.format(threshold)}% 門檻，strict STW／功率預測）</caption>
            <thead><tr><th>載況</th><th>最早</th><th>ETA</th><th>最晚</th></tr></thead>
            <tbody>
              {windows.map((window) => (
                <tr key={window.loadCondition}>
                  <td>{window.loadLabel}</td>
                  <td>{window.crossed ? dayToDisplayDate(window.earliestDay!) : '預測期內未達'}</td>
                  <td>{window.etaDay !== null ? dayToDisplayDate(window.etaDay) : '—'}</td>
                  <td>{window.crossed ? (window.openEnded ? '超出預測期' : dayToDisplayDate(window.latestEnd)) : '—'}</td>
                </tr>
              ))}
              {windows.length === 0 && <tr><td colSpan={4}>此船資料不足，無法產生 strict 預測。</td></tr>}
            </tbody>
          </table>
          <table>
            <caption>行動方案（效益試算勾選動作中成本節省最高者）</caption>
            <thead><tr><th>動作</th><th>執行日</th><th>門檻下維持至</th><th>油耗節省</th><th>成本節省</th></tr></thead>
            <tbody>
              {best ? (
                <tr>
                  <td>{best.event_type} {best.label}</td>
                  <td>{dayToDisplayDate(executionDay)}</td>
                  <td>{actionOpenEnded ? '超出展望期' : dayToDisplayDate(actionEnd)}</td>
                  <td>{number.format(best.fuel_saving_mt)} MT</td>
                  <td>{formatUsd(best.cost_saving_usd)}</td>
                </tr>
              ) : <tr><td colSpan={5}>無</td></tr>}
            </tbody>
          </table>
        </div>
      </details>
    </section>
  )
}
