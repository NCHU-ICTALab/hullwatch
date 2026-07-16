import { useEffect, useId, useMemo, useState } from 'react'
import { api } from '../api'
import { dayToDisplayDate } from '../dashboardLogic'
import type { FleetSpeedLossWindowsResponse } from '../types'

const number = new Intl.NumberFormat('zh-TW', { maximumFractionDigits: 1 })

type ShipRow = FleetSpeedLossWindowsResponse['ships'][number] & { urgency: number }

/**
 * 全船隊清潔窗口總覽：與決策頁同一支 strict STW／功率預測，
 * 每船一列，畫出各載況的門檻交叉窗口與 ETA，依最急迫排序。
 * 只呈現 API 結論，不在前端另行計算窗口。
 */
export function FleetWindowsTimeline({ onSelect }: { onSelect: (shipId: string) => void }) {
  const id = useId().replace(/:/g, '')
  const [threshold, setThreshold] = useState(8)
  const [data, setData] = useState<FleetSpeedLossWindowsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    setLoading(true)
    setError('')
    const timer = window.setTimeout(() => {
      api.fleetSpeedLossWindows({ thresholdPct: threshold })
        .then((response) => { if (active) setData(response) })
        .catch((reason: unknown) => {
          if (active) setError(reason instanceof Error ? reason.message : '全船隊窗口計算失敗')
        })
        .finally(() => { if (active) setLoading(false) })
    }, 180)
    return () => {
      active = false
      window.clearTimeout(timer)
    }
  }, [threshold])

  const rows = useMemo<ShipRow[]>(() => {
    if (!data) return []
    return [...data.ships]
      .map((ship) => ({
        ...ship,
        urgency: Math.min(...ship.groups.map((group) => (
          group.available && group.threshold_crossing.eta_days !== null
            ? group.threshold_crossing.eta_days
            : Number.POSITIVE_INFINITY
        ))),
      }))
      .sort((a, b) => a.urgency - b.urgency || a.ship_id.localeCompare(b.ship_id))
  }, [data])

  const domain = useMemo(() => {
    if (!data) return null
    const latestDays = data.ships.flatMap((ship) => ship.groups
      .map((group) => group.latest_day)
      .filter((day): day is number => day !== null))
    if (latestDays.length === 0) return null
    const start = Math.min(...latestDays) - 14
    const end = Math.max(...latestDays) + data.parameters.forecast_days
    return { start, end }
  }, [data])
  const pos = (day: number) => domain
    ? ((Math.min(Math.max(day, domain.start), domain.end) - domain.start) / (domain.end - domain.start)) * 100
    : 0
  const ticks = domain
    ? [0, 1, 2, 3, 4].map((step) => domain.start + step * ((domain.end - domain.start) / 4))
    : []

  const urgentSummary = rows.length > 0 && Number.isFinite(rows[0].urgency)
    ? `最急迫為 ${rows[0].ship_name}，ETA ${rows[0].urgency} 天`
    : '預測期內沒有船達到門檻'

  return (
    <div className="fleet-windows-timeline">
      <div className="fleet-windows-controls">
        <div className="benefit-control fleet-windows-threshold">
          <label htmlFor={`${id}-threshold-range`}>清底門檻</label>
          <input id={`${id}-threshold-range`} type="range" min={1} max={30} step={0.5} value={threshold} onChange={(event) => setThreshold(Number(event.target.value))} />
          <label className="sr-only" htmlFor={`${id}-threshold-number`}>清底門檻數值</label>
          <input id={`${id}-threshold-number`} type="number" min={1} max={30} step={0.5} value={threshold} onChange={(event) => { const next = Number(event.target.value); if (Number.isFinite(next)) setThreshold(Math.min(30, Math.max(1, next))) }} />
          <span>% Speed Loss</span>
        </div>
        <small className="fleet-windows-state" role="status" aria-live="polite">
          {loading ? '依門檻重算 15 艘窗口…' : error ? '計算失敗' : urgentSummary}
        </small>
        <div className="fleet-windows-legend" aria-hidden="true">
          <span><i className="legend-window-laden" />重載窗口</span>
          <span><i className="legend-window-ballast" />壓艙窗口</span>
          <span><i className="legend-eta" />ETA</span>
        </div>
      </div>
      {error && <p className="prediction-error" role="alert">{error}</p>}

      {data && domain && (
        <>
          <div className="timeline-visual" role="img" aria-label={`全船隊 ${number.format(threshold)}% 門檻清潔窗口時間軸，依 ETA 由近到遠排序；${urgentSummary}。日期為 Day 0 = 2021-01-01 映射座標，詳細數值見下方資料表。`}>
            {rows.map((ship) => {
              const shipToday = Math.max(...ship.groups.map((group) => group.latest_day ?? domain.start))
              return (
                <div className="timeline-lane fleet-windows-row" key={ship.ship_id}>
                  <div className="timeline-lane-head">
                    <button type="button" className="fleet-windows-ship" onClick={() => onSelect(ship.ship_id)}>
                      <b>{ship.ship_name}</b><small>{ship.ship_id}</small>
                    </button>
                    <small>{Number.isFinite(ship.urgency) ? `ETA ${ship.urgency} 天` : ship.available ? '期內未達' : '資料不足'}</small>
                  </div>
                  <div className="timeline-track fleet-windows-track">
                    {ship.groups.map((group) => {
                      if (!group.available || group.latest_day === null || group.threshold_crossing.earliest_days === null) return null
                      const base = group.latest_day
                      const earliest = base + group.threshold_crossing.earliest_days
                      const latestEnd = group.threshold_crossing.latest_days !== null
                        ? base + group.threshold_crossing.latest_days
                        : domain.end
                      const openEnded = group.threshold_crossing.latest_days === null
                      const eta = group.threshold_crossing.eta_days !== null ? base + group.threshold_crossing.eta_days : null
                      return (
                        <span key={group.load_condition}>
                          <span
                            className={`timeline-window timeline-window-${group.load_condition} fleet-window-${group.load_condition} ${openEnded ? 'open-ended' : ''}`}
                            style={{ left: `${pos(earliest)}%`, width: `${Math.max(pos(latestEnd) - pos(earliest), 0.6)}%` }}
                            title={`${group.load_label} ${dayToDisplayDate(earliest)} ～ ${openEnded ? '超出預測期' : dayToDisplayDate(latestEnd)}`}
                          />
                          {eta !== null && <span className={`timeline-eta fleet-eta-${group.load_condition}`} style={{ left: `${pos(eta)}%` }} title={`${group.load_label} ETA ${dayToDisplayDate(eta)}`} />}
                        </span>
                      )
                    })}
                    <i className="timeline-today-line" style={{ left: `${pos(shipToday)}%` }} aria-hidden="true" />
                  </div>
                </div>
              )
            })}
            <div className="timeline-lane timeline-axis">
              <div className="timeline-lane-head" aria-hidden="true" />
              <div className="timeline-track">
                {ticks.map((tick) => <small className="timeline-tick" key={tick} style={{ left: `${pos(tick)}%` }}>{dayToDisplayDate(tick)}</small>)}
              </div>
            </div>
          </div>
          <p className="chart-explanation">與決策頁同一支 strict STW／功率預測、同語意門檻；紅線為各船最新紀錄日。日期為 Day 0 = 2021-01-01 映射座標，非真實日曆。單船的信賴錐與方法細節請點船名前往決策頁。</p>
          <details className="data-fallback">
            <summary>查看全船隊窗口資料表</summary>
            <div className="table-wrap">
              <table>
                <caption>全船隊 {number.format(threshold)}% 門檻清潔窗口（映射日期）</caption>
                <thead><tr><th>船舶</th><th>載況</th><th>目前 SL</th><th>最早</th><th>ETA</th><th>最晚</th></tr></thead>
                <tbody>
                  {rows.flatMap((ship) => ship.groups.map((group) => (
                    <tr key={`${ship.ship_id}-${group.load_condition}`}>
                      <th scope="row">{ship.ship_name}（{ship.ship_id}）</th>
                      <td>{group.load_label}</td>
                      <td>{group.current_speed_loss_pct === null ? '—' : `${number.format(group.current_speed_loss_pct)}%`}</td>
                      <td>{group.available && group.latest_day !== null && group.threshold_crossing.earliest_days !== null ? dayToDisplayDate(group.latest_day + group.threshold_crossing.earliest_days) : group.available ? '期內未達' : '資料不足'}</td>
                      <td>{group.available && group.latest_day !== null && group.threshold_crossing.eta_days !== null ? dayToDisplayDate(group.latest_day + group.threshold_crossing.eta_days) : '—'}</td>
                      <td>{group.available && group.latest_day !== null && group.threshold_crossing.latest_days !== null ? dayToDisplayDate(group.latest_day + group.threshold_crossing.latest_days) : group.available && group.threshold_crossing.earliest_days !== null ? '超出預測期' : '—'}</td>
                    </tr>
                  )))}
                </tbody>
              </table>
            </div>
          </details>
        </>
      )}
      {!data && loading && <p className="benefit-request-state">正在計算全船隊清潔窗口…</p>}
    </div>
  )
}
