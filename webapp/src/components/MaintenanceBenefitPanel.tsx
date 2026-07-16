import { useEffect, useId, useMemo, useState } from 'react'
import type { EChartsOption } from 'echarts'
import { api } from '../api'
import type {
  FuelPriceResponse,
  MaintenanceAction,
  MaintenanceBenefitActionResult,
  MaintenanceBenefitRequest,
  MaintenanceBenefitResponse,
} from '../types'
import { EChart } from './EChart'

const ACTION_ORDER: MaintenanceAction[] = ['UWI', 'PP', 'UWC', 'DD']
const ACTION_LABELS: Record<MaintenanceAction, string> = {
  UWI: '水下檢查',
  PP: '螺旋槳拋光',
  'UWI+PP': '水下檢查＋螺旋槳拋光',
  UWC: '水下船殼清洗',
  'UWC+PP': '船殼清洗＋螺旋槳拋光',
  DD: '進塢重塗裝',
}
const DEFAULT_RECOVERY: Record<MaintenanceAction, number> = {
  UWI: 0,
  PP: 15,
  'UWI+PP': 20,
  UWC: 45,
  'UWC+PP': 58,
  DD: 75,
}
const ACTION_STYLE: Record<MaintenanceAction, {
  color: string
  lineType: 'solid' | 'dashed' | 'dotted'
  symbol: 'circle' | 'rect' | 'roundRect' | 'triangle' | 'diamond' | 'pin'
}> = {
  UWI: { color: '#6B7280', lineType: 'dotted', symbol: 'circle' },
  PP: { color: '#2563EB', lineType: 'solid', symbol: 'rect' },
  'UWI+PP': { color: '#7C3AED', lineType: 'dashed', symbol: 'roundRect' },
  UWC: { color: '#0F766E', lineType: 'solid', symbol: 'triangle' },
  'UWC+PP': { color: '#C06A00', lineType: 'dashed', symbol: 'diamond' },
  DD: { color: '#8B1E3F', lineType: 'dotted', symbol: 'pin' },
}

const number = new Intl.NumberFormat('zh-TW', { maximumFractionDigits: 2 })
const money = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 0,
})

function ScenarioControl({
  id,
  label,
  value,
  min,
  max,
  step,
  unit,
  onChange,
}: {
  id: string
  label: string
  value: number
  min: number
  max: number
  step: number
  unit: string
  onChange: (value: number) => void
}) {
  const update = (raw: string) => {
    const next = Number(raw)
    if (Number.isFinite(next)) onChange(Math.min(max, Math.max(min, next)))
  }
  return (
    <div className="benefit-control">
      <label htmlFor={`${id}-range`}>{label}</label>
      <input
        id={`${id}-range`}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => update(event.target.value)}
      />
      <label className="sr-only" htmlFor={`${id}-number`}>{label}數值</label>
      <input
        id={`${id}-number`}
        type="number"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => update(event.target.value)}
      />
      <span>{unit}</span>
    </div>
  )
}

function branchValuesByDay(action: MaintenanceBenefitActionResult) {
  const values = new Map<number, number>()
  action.branch.forEach((point) => values.set(point.day, point.speed_loss_pct))
  return values
}

export function MaintenanceBenefitPanel({ shipId, shipName, fuel, dark }: {
  shipId: string
  shipName: string
  fuel: FuelPriceResponse
  dark: boolean
}) {
  const id = useId().replace(/:/g, '')
  const [executionDelay, setExecutionDelay] = useState(0)
  const [horizon, setHorizon] = useState(180)
  const [threshold, setThreshold] = useState(8)
  const [fuelFactor, setFuelFactor] = useState(3)
  const [fuelPrice, setFuelPrice] = useState(600)
  const [priceGrade, setPriceGrade] = useState<string>(() => (
    fuel.prices.some((price) => price.grade === 'VLSFO') ? 'VLSFO' : fuel.prices[0]?.grade ?? 'manual'
  ))
  const [seaRatio, setSeaRatio] = useState(0.65)
  const [recoveries, setRecoveries] = useState<Record<MaintenanceAction, number>>(DEFAULT_RECOVERY)
  const [selectedActions, setSelectedActions] = useState<MaintenanceAction[]>(ACTION_ORDER)
  const [data, setData] = useState<MaintenanceBenefitResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const marketPrice = priceGrade === 'manual'
    ? undefined
    : fuel.prices.find((price) => price.grade === priceGrade)
  const effectiveFuelPrice = marketPrice?.usd_per_ton ?? fuelPrice

  useEffect(() => {
    const controller = new AbortController()
    let active = true
    setLoading(true)
    setError('')
    setData(null)
    const body: MaintenanceBenefitRequest = {
      execution_delay_days: Math.min(executionDelay, horizon),
      horizon_days: horizon,
      threshold_pct: threshold,
      fuel_factor: fuelFactor,
      fuel_price_usd_per_mt: effectiveFuelPrice,
      sea_ratio: seaRatio,
      recovery_pct: recoveries,
    }
    const timer = window.setTimeout(() => {
      api.maintenanceBenefit(shipId, body)
        .then((response) => { if (active) setData(response) })
        .catch((reason: unknown) => {
          if (active && !controller.signal.aborted) {
            setError(reason instanceof Error ? reason.message : '養護效益計算失敗')
          }
        })
        .finally(() => { if (active) setLoading(false) })
    }, 160)
    return () => {
      active = false
      controller.abort()
      window.clearTimeout(timer)
    }
  }, [effectiveFuelPrice, executionDelay, fuelFactor, horizon, recoveries, seaRatio, shipId, threshold])

  useEffect(() => {
    if (executionDelay > horizon) setExecutionDelay(horizon)
  }, [executionDelay, horizon])

  const selectedResults = useMemo(() => (
    data?.actions.filter((action) => selectedActions.includes(action.event_type)) ?? []
  ), [data, selectedActions])
  const resultsByAction = useMemo(() => new Map(
    data?.actions.map((action) => [action.event_type, action]) ?? [],
  ), [data])
  const evidenceByAction = useMemo(() => new Map(
    data?.evidence.map((evidence) => [evidence.event_type, evidence]) ?? [],
  ), [data])

  const chartOption = useMemo<EChartsOption>(() => {
    if (!data?.available || data.latest_day === undefined) return {}
    const chartText = dark ? '#A7B8C0' : '#4A5A63'
    const chartGrid = dark ? '#2A3B43' : '#D8DFE4'
    const lastDay = data.latest_day + horizon
    const executionDay = data.latest_day + executionDelay
    // 所有未來序列取同一組日子（每 7 天＋執行日＋末日），axis tooltip 才會同時列出各動作的值。
    const sampleWeekly = <T extends { day: number }>(points: T[]) => points.filter((point, index) => (
      index % 7 === 0 || point.day === executionDay || index === points.length - 1
    ))
    const selectedSeries = selectedResults.map((action) => {
      const style = ACTION_STYLE[action.event_type]
      const chartPoints = sampleWeekly(action.branch)
      return {
        name: `${action.event_type} ${action.label}`,
        type: 'line' as const,
        data: chartPoints.map((point) => [point.day, point.speed_loss_pct]),
        showSymbol: true,
        symbol: style.symbol,
        symbolSize: 6,
        lineStyle: { color: style.color, width: 2.5, type: style.lineType },
        itemStyle: { color: style.color },
        emphasis: { focus: 'series' as const },
      }
    })
    return {
      animation: !window.matchMedia('(prefers-reduced-motion: reduce)').matches,
      textStyle: { color: chartText, fontSize: 13 },
      tooltip: { trigger: 'axis', renderMode: 'richText', valueFormatter: (value) => `${number.format(Number(value))}%` },
      legend: { top: 0, itemGap: 14, itemWidth: 22, textStyle: { color: chartText, fontSize: 12 } },
      grid: { left: 58, right: 24, top: 96, bottom: 52 },
      dataZoom: [{ type: 'inside', filterMode: 'none' }],
      xAxis: {
        type: 'value',
        name: 'NOON_UTC 相對日',
        min: 'dataMin',
        max: 'dataMax',
        nameLocation: 'middle',
        nameGap: 34,
        axisLabel: { color: chartText },
        axisLine: { lineStyle: { color: chartGrid } },
      },
      yAxis: {
        type: 'value',
        name: 'Speed Loss %',
        min: 0,
        nameTextStyle: { color: chartText },
        axisLabel: { color: chartText },
        splitLine: { lineStyle: { color: chartGrid } },
      },
      series: [
        {
          name: '歷史重新錨定 SL',
          type: 'line',
          data: data.history.map((point) => [point.day, point.speed_loss_pct]),
          showSymbol: true,
          symbolSize: 4,
          lineStyle: { color: '#0E8790', width: 2 },
          itemStyle: { color: '#0E8790' },
        },
        {
          name: '不作為（紅虛線）',
          type: 'line',
          data: sampleWeekly(data.no_action).map((point) => [point.day, point.speed_loss_pct]),
          showSymbol: false,
          lineStyle: { color: '#B42318', width: 3, type: 'dashed' },
          itemStyle: { color: '#B42318' },
        },
        ...selectedSeries,
        {
          name: `門檻 ${threshold}%`,
          type: 'line',
          data: [[data.latest_day, threshold], [lastDay, threshold]],
          showSymbol: false,
          silent: true,
          lineStyle: { color: '#111827', width: 2, type: 'dotted' },
        },
        {
          name: '過去養護事件（倒三角）',
          type: 'scatter',
          symbol: 'triangle',
          symbolRotate: 180,
          symbolSize: 12,
          data: data.past_events
            .filter((event) => event.day <= data.latest_day!)
            .map((event) => ({ name: `${event.event_type} ${event.label}`, value: [event.day, 0.15] })),
          itemStyle: { color: '#C06A00' },
        },
      ],
    }
  }, [dark, data, executionDelay, horizon, selectedResults, threshold])

  const futureTableRows = useMemo(() => {
    if (!data?.available) return []
    const executionDay = (data.latest_day ?? 0) + executionDelay
    const actionMaps = new Map(selectedResults.map((action) => [
      action.event_type,
      branchValuesByDay(action),
    ]))
    return data.no_action.filter((point, index) => (
      index % 7 === 0
      || point.day === executionDay
      || index === data.no_action.length - 1
    )).map((point) => ({
      day: point.day,
      noAction: point.speed_loss_pct,
      actions: Object.fromEntries(selectedResults.map((action) => [
        action.event_type,
        actionMaps.get(action.event_type)?.get(point.day),
      ])) as Partial<Record<MaintenanceAction, number>>,
    }))
  }, [data, executionDelay, selectedResults])

  const toggleAction = (action: MaintenanceAction) => {
    setSelectedActions((current) => current.includes(action)
      ? current.filter((candidate) => candidate !== action)
      : [...current, action])
  }
  const setRecovery = (action: MaintenanceAction, value: number) => {
    setRecoveries((current) => ({ ...current, [action]: value }))
  }

  return (
    <section className="panel maintenance-benefit-panel" aria-labelledby={`${id}-title`}>
      <div className="panel-heading">
        <div><span>MAINTENANCE BRANCH SIMULATION</span><h2 id={`${id}-title`}>立即清潔效益試算</h2></div>
        <span className="model-basis">真實事件證據＋可調物理先驗 · {shipName}</span>
      </div>
      <p className="benefit-intro">回復比例是可調的物理先驗；觀測事件只作證據，不把不同船的復發傾向直接套到目前船舶。</p>

      <fieldset className="benefit-controls">
        <legend>分岔模擬控制（即時重算）</legend>
        <p className="control-hint">執行時機＝幾天後才執行養護（0＝現在；等待期間照常汙損，可看出拖延的代價）。展望天數＝效益累計的模擬期間。清底門檻＝視為需要清潔的 Speed Loss 水準，用於「門檻下天數增益」與圖上的門檻虛線。</p>
        <ScenarioControl id={`${id}-delay`} label="執行時機" value={executionDelay} min={0} max={Math.min(365, horizon)} step={1} unit={executionDelay === 0 ? '現在' : `+${executionDelay} 天`} onChange={setExecutionDelay} />
        <ScenarioControl id={`${id}-horizon`} label="展望天數" value={horizon} min={30} max={730} step={10} unit="天" onChange={setHorizon} />
        <ScenarioControl id={`${id}-threshold`} label="清底門檻" value={threshold} min={1} max={30} step={0.5} unit="% SL" onChange={setThreshold} />
      </fieldset>
      <fieldset className="benefit-controls assumptions-controls">
        <legend>效益換算假設</legend>
        <p className="control-hint">燃油係數＝每 1pp Speed Loss 約增加的油耗百分比（P∝V³ 粗估先驗）。燃油價格＝節省油耗換算金額的單價，可直接選儀表板市場行情油種或手動輸入。出海比例＝展望期間全速航行天數占比，效益只在航行日累計。</p>
        <ScenarioControl id={`${id}-factor`} label="燃油係數" value={fuelFactor} min={0} max={10} step={0.1} unit="×" onChange={setFuelFactor} />
        <div className="benefit-control benefit-price-control">
          <label htmlFor={`${id}-price-grade`}>燃油價格</label>
          <select id={`${id}-price-grade`} value={priceGrade} onChange={(event) => setPriceGrade(event.target.value)}>
            {fuel.prices.map((price) => (
              <option key={price.grade} value={price.grade}>
                {price.grade} · {number.format(price.usd_per_ton)} USD/MT{price.estimated ? '（估算）' : ''}
              </option>
            ))}
            <option value="manual">手動輸入</option>
          </select>
          <span>{marketPrice ? `行情日 ${marketPrice.as_of}${marketPrice.estimated ? '（估算）' : ''}` : '自訂情境價'}</span>
        </div>
        {priceGrade === 'manual' && (
          <ScenarioControl id={`${id}-price`} label="手動情境價" value={fuelPrice} min={100} max={3000} step={10} unit="USD/MT" onChange={setFuelPrice} />
        )}
        <ScenarioControl id={`${id}-sea-ratio`} label="出海比例" value={seaRatio} min={0} max={1} step={0.05} unit={new Intl.NumberFormat('zh-TW', { style: 'percent' }).format(seaRatio)} onChange={setSeaRatio} />
      </fieldset>

      <div className="benefit-workspace">
        <section className="benefit-actions" aria-labelledby={`${id}-actions-title`}>
          <h3 id={`${id}-actions-title`}>養護動作 · 效益模型</h3>
          <div className="maintenance-action-list">
            {ACTION_ORDER.map((action) => {
              const evidence = evidenceByAction.get(action)
              const result = resultsByAction.get(action)
              const selected = selectedActions.includes(action)
              return (
                <article className={`maintenance-action-card ${selected ? 'selected' : ''}`} key={action}>
                  <div className="maintenance-action-heading">
                    <input id={`${id}-${action}-selected`} type="checkbox" checked={selected} onChange={() => toggleAction(action)} />
                    <label htmlFor={`${id}-${action}-selected`}><b>{action}</b> {ACTION_LABELS[action]}</label>
                  </div>
                  <ScenarioControl id={`${id}-${action}-recovery`} label={`${action} ${ACTION_LABELS[action]} 移除當前汙損比例`} value={recoveries[action]} min={0} max={100} step={1} unit="%" onChange={(value) => setRecovery(action, value)} />
                  <dl className="evidence-list">
                    <div><dt>實測回復</dt><dd>{evidence?.observed_recovery_median_pp == null ? '樣本不足' : `${number.format(evidence.observed_recovery_median_pp)} pp`}</dd></div>
                    <div><dt>觀測復發</dt><dd>{evidence?.observed_recurrence_median_pct_per_month == null ? '樣本不足' : `${number.format(evidence.observed_recurrence_median_pct_per_month)} %/月`}</dd></div>
                    <div><dt>回復樣本</dt><dd>{evidence ? `${evidence.n_used}/${evidence.n_total}` : '—'}</dd></div>
                  </dl>
                  {action === 'UWI' && recoveries.UWI === 0 && <small className="zero-effect-note">0%：與不作為完全重合，效益為零。</small>}
                  {result && <small>分岔復發採本船 {number.format(result.branch_rate_pct_per_month)} %/月{action === 'DD' ? '（×0.5）' : ''}</small>}
                </article>
              )
            })}
          </div>
        </section>

        <section className="benefit-results" aria-labelledby={`${id}-results-title`}>
          <h3 id={`${id}-results-title`}>分岔結果</h3>
          <div className="benefit-request-state" role="status" aria-live="polite">
            {loading ? '正在重算養護分岔…' : error ? '計算失敗' : data?.available ? `已用 ${data.counts?.speed_loss_rows ?? 0} 個有效點更新` : '資料不足'}
          </div>
          {error && <p className="prediction-error" role="alert">{error}</p>}
          {!error && data && !data.available && <p className="prediction-error" role="status">{data.reason ?? '資料不足，無法模擬。'}</p>}
          {data?.available && (
            <>
              <div className="benefit-readings" aria-label="目前船舶效益模型讀數">
                <article><span>目前 Speed Loss</span><strong>{number.format(data.now_speed_loss_pct ?? 0)}%</strong></article>
                <article><span>近期趨勢</span><strong>{number.format(data.recent_rate_pct_per_month ?? 0)} %/月</strong></article>
                <article><span>模擬汙損率</span><strong>{number.format(data.dn_rate_pct_per_month ?? 0)} %/月</strong></article>
                <article><span>全速日油耗</span><strong>{number.format(data.full_speed_daily_consumption_mt ?? 0)} MT/day</strong></article>
              </div>
              <EChart option={chartOption} className="maintenance-branch-chart" ariaLabel={`${shipName} 養護動作 Speed Loss 分岔圖；歷史為青色，不作為為紅色虛線，各動作另以線型與名稱區分`} />
              <p className="chart-explanation">x 軸為 NOON_UTC 相對日；倒三角是過去養護事件。滑鼠滾輪／觸控板可縮放，點圖例可隱藏線條，或展開下方資料表。</p>
              <details className="data-fallback maintenance-chart-data">
                <summary>查看分岔圖資料表</summary>
                <div className="table-wrap">
                  <table>
                    <caption>歷史 7 日分箱重新錨定 Speed Loss</caption>
                    <thead><tr><th>NOON_UTC day</th><th>Speed Loss %</th><th>觀測數</th></tr></thead>
                    <tbody>{data.history.map((point) => <tr key={point.day}><td>{point.day}</td><td>{number.format(point.speed_loss_pct)}</td><td>{point.observations}</td></tr>)}</tbody>
                  </table>
                  <table>
                    <caption>未來每 7 天與執行日分岔值</caption>
                    <thead><tr><th>NOON_UTC day</th><th>不作為 %</th>{selectedResults.map((action) => <th key={action.event_type}>{action.event_type} %</th>)}</tr></thead>
                    <tbody>{futureTableRows.map((row) => <tr key={row.day}><td>{row.day}</td><td>{number.format(row.noAction)}</td>{selectedResults.map((action) => <td key={action.event_type}>{row.actions[action.event_type] == null ? '—' : number.format(row.actions[action.event_type]!)}</td>)}</tr>)}</tbody>
                  </table>
                </div>
              </details>

              <div className="table-wrap benefit-comparison-wrap" aria-live="polite">
                <table className="benefit-comparison-table">
                  <caption>效益比較，依展望期間燃油節省由高至低排序</caption>
                  <thead><tr><th>動作</th><th>回復後 SL</th><th>門檻下天數增益</th><th>油耗節省</th><th>成本節省</th><th>CO₂ 減量</th></tr></thead>
                  <tbody>{selectedResults.map((action, index) => <tr key={action.event_type} className={index === 0 ? 'best-benefit' : ''}><th scope="row">{index === 0 && <span className="best-label">最佳</span>}{action.event_type} {action.label}</th><td>{number.format(action.post_action_speed_loss_pct)}%</td><td>+{action.days_below_threshold_gain} 天</td><td>{number.format(action.fuel_saving_mt)} MT</td><td>{money.format(action.cost_saving_usd)}</td><td>{number.format(action.co2_avoided_t)} t</td></tr>)}</tbody>
                </table>
                {selectedResults.length === 0 && <p className="benefit-empty-selection" role="status">勾選至少一個養護動作以加入比較。</p>}
              </div>
            </>
          )}
        </section>
      </div>

      <details className="prediction-method benefit-method">
        <summary>方法與限制</summary>
        <div>
          <p><b>自建基準：</b>每船最早 30% 合格資料以 STW、HP^(1/3) 與載況 OLS 建基準；不是外部海試曲線，絕對值受量測校準影響。</p>
          <p><b>重新錨定：</b>每船 7 日中位數減去自身第 5 百分位，底部雜訊歸零，表示相對最乾淨狀態的汙損。</p>
          <p><b>回復兩層：</b>卡片滑桿是物理先驗；實測回復、觀測復發與 n 僅作證據。UWC 原始樣本只有 {evidenceByAction.get('UWC')?.n_total ?? 6} 筆，且多為船體仍乾淨時的主動清洗。</p>
          <p><b>復發：</b>分岔後採本船 dnRate=max(近期趨勢, 0.3%/月)，只對 DD 乘 0.5；不把不同船的事件復發率直接套用。</p>
          <p><b>燃油換算：</b>是 P∝V³ 的粗估，不是量測節省；燃油節省以平均 SL 差、燃油係數、HOURS_FULL_SPEED≥20 的 ME_CONSUMPTION 中位數、展望天數與出海比例計算。</p>
          <p className="day-zero-note"><b>相對日期：</b>{data?.day0_note ?? 'NOON_UTC 與 event_day 沒有可驗證的日曆 Day 0。'}</p>
        </div>
      </details>
    </section>
  )
}
