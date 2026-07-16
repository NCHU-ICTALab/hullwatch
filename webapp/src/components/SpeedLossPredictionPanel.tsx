import { useEffect, useId, useMemo, useState } from 'react'
import type { EChartsOption } from 'echarts'
import { api } from '../api'
import { dayToDisplayDate } from '../dashboardLogic'
import type {
  SpeedLossLoadCondition,
  SpeedLossPredictionGroup,
  SpeedLossPredictionResponse,
} from '../types'
import { EChart } from './EChart'

const LOAD_COLORS = {
  laden: '#0E5E6F',
  ballast: '#C77400',
} as const

const LOAD_BANDS = {
  laden: 'rgba(14,94,111,.18)',
  ballast: 'rgba(199,116,0,.16)',
} as const

function PredictionRange({ label, value, min, max, step, unit, onChange }: {
  label: string
  value: number
  min: number
  max: number
  step: number
  unit: string
  onChange: (value: number) => void
}) {
  const labelId = useId()
  const setValue = (next: number) => onChange(Math.max(min, Math.min(max, next)))
  return (
    <div className="dual-input prediction-range" role="group" aria-labelledby={labelId}>
      <span id={labelId}>{label}</span>
      <input aria-label={`${label}滑桿`} type="range" value={value} min={min} max={max} step={step} onChange={(event) => setValue(Number(event.target.value))} />
      <input aria-label={`${label}數值`} type="number" value={value} min={min} max={max} step={step} onChange={(event) => setValue(Number(event.target.value))} />
      <em>{unit}</em>
    </div>
  )
}

function PredictionKpi({ label, groups, render }: {
  label: string
  groups: SpeedLossPredictionGroup[]
  render: (group: SpeedLossPredictionGroup) => string
}) {
  return (
    <article className="metric prediction-kpi">
      <span>{label}</span>
      <div>
        {groups.map((group) => (
          <p key={group.load_condition}>
            <small>{group.load_label}</small>
            <strong>{group.available ? render(group) : '—'}</strong>
          </p>
        ))}
      </div>
    </article>
  )
}

function etaText(group: SpeedLossPredictionGroup, horizon: number) {
  const eta = group.threshold_crossing.eta_days
  if (eta === null) return `>${horizon} 天`
  if (eta === 0) return '已達門檻'
  return group.latest_day === null ? `${eta} 天` : `${eta} 天 · ${dayToDisplayDate(group.latest_day + eta)}`
}

function rangeText(group: SpeedLossPredictionGroup, horizon: number) {
  const { earliest_days: earliest, latest_days: latest } = group.threshold_crossing
  if (earliest === null) return '預測期內未達'
  if (group.latest_day === null) return latest === null ? `${earliest}–>${horizon} 天` : `${earliest}–${latest} 天`
  const earliestDate = dayToDisplayDate(group.latest_day + earliest)
  if (latest === null) return `${earliestDate} 起（>${horizon} 天）`
  if (earliest === latest) return earliestDate
  return `${earliestDate} ～ ${dayToDisplayDate(group.latest_day + latest)}`
}

function predictionOption(
  prediction: SpeedLossPredictionResponse,
  dark: boolean,
): EChartsOption {
  const groups = prediction.groups.filter((group) => group.available)
  const chartText = dark ? '#A7B8C0' : '#4A5A63'
  const chartGrid = dark ? '#2A3B43' : '#D8DFE4'
  const series: Array<Record<string, unknown>> = []
  const legend: string[] = []

  groups.forEach((group, index) => {
    const color = LOAD_COLORS[group.load_condition]
    const historyName = `${group.load_label}量測`
    const trendName = `${group.load_label}劣化趨勢`
    const forecastName = `${group.load_label}外推`
    legend.push(historyName, trendName, forecastName)
    series.push({
      name: historyName,
      type: 'line',
      showSymbol: true,
      symbolSize: 6,
      data: group.history.map((point) => [point.day, point.speed_loss_pct]),
      lineStyle: { width: 2, color },
      itemStyle: { color },
      markLine: index === 0 ? {
        symbol: 'none',
        label: { formatter: `清底門檻 ${prediction.parameters.threshold_pct}%`, color: chartText },
        lineStyle: { color: '#A64036', type: 'dashed', width: 2 },
        data: [{ yAxis: prediction.parameters.threshold_pct }],
      } : undefined,
    })
    series.push({
      name: trendName,
      type: 'line',
      symbol: 'none',
      data: group.trend.map((point) => [point.day, point.mid]),
      lineStyle: { width: 3, color },
    })
    series.push({
      name: `${group.load_label}信賴下界`,
      type: 'line',
      stack: `confidence-${group.load_condition}`,
      symbol: 'none',
      silent: true,
      tooltip: { show: false },
      lineStyle: { opacity: 0 },
      areaStyle: { opacity: 0 },
      data: group.forecast.map((point) => [point.day, point.lo]),
    })
    series.push({
      name: `${group.load_label} 90% 信賴帶`,
      type: 'line',
      stack: `confidence-${group.load_condition}`,
      symbol: 'none',
      silent: true,
      tooltip: { show: false },
      lineStyle: { opacity: 0 },
      areaStyle: { color: LOAD_BANDS[group.load_condition] },
      data: group.forecast.map((point) => [point.day, point.hi - point.lo]),
    })
    series.push({
      name: forecastName,
      type: 'line',
      symbol: 'none',
      data: group.forecast.map((point) => [point.day, point.mid]),
      lineStyle: { width: 3, type: 'dashed', color },
    })
    const eta = group.threshold_crossing.eta_days
    if (eta !== null && group.latest_day !== null) {
      series.push({
        name: `${group.load_label}門檻交叉`,
        type: 'scatter',
        symbol: 'diamond',
        symbolSize: 14,
        data: [[group.latest_day + eta, prediction.parameters.threshold_pct]],
        itemStyle: { color: '#A64036', borderColor: dark ? '#071A20' : '#FFFFFF', borderWidth: 2 },
        tooltip: { formatter: `${group.load_label}預估 ${dayToDisplayDate(group.latest_day + eta)} 達門檻（最新紀錄後 ${eta} 天）` },
      })
    }
  })

  return {
    animation: !window.matchMedia('(prefers-reduced-motion: reduce)').matches,
    textStyle: { color: chartText, fontSize: 13 },
    tooltip: { trigger: 'axis', renderMode: 'richText' },
    legend: { data: legend, bottom: 0, textStyle: { color: chartText, fontSize: 12 } },
    grid: { left: 62, right: 28, top: 36, bottom: 82 },
    xAxis: {
      type: 'value',
      name: '映射日期（Day 0 = 2021-01-01）',
      nameLocation: 'middle',
      nameGap: 38,
      nameTextStyle: { color: chartText },
      axisLabel: { color: chartText, formatter: (value: number) => dayToDisplayDate(value) },
      axisPointer: { label: { formatter: (params: { value: number | string | Date }) => dayToDisplayDate(Number(params.value)) } },
      axisLine: { lineStyle: { color: chartGrid } },
      splitLine: { lineStyle: { color: chartGrid } },
    },
    yAxis: {
      type: 'value',
      scale: true,
      name: 'Speed Loss %',
      nameTextStyle: { color: chartText },
      axisLabel: { color: chartText, formatter: '{value}%' },
      splitLine: { lineStyle: { color: chartGrid } },
    },
    dataZoom: [{ type: 'inside', filterMode: 'none' }],
    series: series as EChartsOption['series'],
  }
}

export function SpeedLossPredictionPanel({ shipId, shipName, threshold, dark, onResult }: {
  shipId: string
  shipName: string
  threshold: number
  dark: boolean
  onResult?: (prediction: SpeedLossPredictionResponse | null) => void
}) {
  const [forecastDays, setForecastDays] = useState(180)
  const [maxWindScale, setMaxWindScale] = useState(4)
  const [loadCondition, setLoadCondition] = useState<SpeedLossLoadCondition>('all')
  const [prediction, setPrediction] = useState<SpeedLossPredictionResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    setPrediction(null)
    setLoading(true)
    setError('')
    const timer = window.setTimeout(() => {
      api.speedLossPrediction(shipId, {
        forecastDays,
        thresholdPct: threshold,
        maxWindScale,
        loadCondition,
      }).then((result) => {
        if (!active) return
        setPrediction(result)
        onResult?.(result)
      }).catch((reason: unknown) => {
        if (!active) return
        setPrediction(null)
        onResult?.(null)
        setError(reason instanceof Error ? reason.message : 'Speed Loss 預測重算失敗')
      }).finally(() => {
        if (active) setLoading(false)
      })
    }, 140)
    return () => {
      active = false
      window.clearTimeout(timer)
    }
  }, [forecastDays, loadCondition, maxWindScale, onResult, shipId, threshold])

  const chartOption = useMemo(
    () => prediction && prediction.available ? predictionOption(prediction, dark) : {},
    [dark, prediction],
  )
  const groups = prediction?.groups ?? []
  const unavailableGroups = groups.filter((group) => !group.available)

  return (
    <section className="panel decision-forecast-panel" aria-busy={loading}>
      <div className="panel-heading">
        <div><span>STW / POWER OLS</span><h2>Speed Loss 預測</h2></div>
        <span className="model-basis">逐船 · 重載／壓艙分模 · 90% 信賴帶</span>
      </div>
      <div className="chart-controls prediction-controls">
        <PredictionRange label="預測天數" value={forecastDays} min={30} max={365} step={5} unit="天" onChange={setForecastDays} />
        <PredictionRange label="天候上限" value={maxWindScale} min={0} max={12} step={1} unit="風級" onChange={setMaxWindScale} />
        <fieldset className="load-condition-toggle">
          <legend>載況</legend>
          {([
            ['all', '全部'],
            ['laden', '重載'],
            ['ballast', '壓艙'],
          ] as const).map(([value, label]) => (
            <button type="button" key={value} className={loadCondition === value ? 'selected' : ''} aria-pressed={loadCondition === value} onClick={() => setLoadCondition(value)}>{label}</button>
          ))}
        </fieldset>
        <small className="prediction-request-state" aria-live="polite">{loading ? '依新條件重算中…' : `有效紀錄 ${prediction?.filter_counts.with_displacement_rows ?? 0} 筆`}</small>
      </div>

      {error && <div className="prediction-error" role="alert">{error}</div>}
      {prediction && (
        <div className="prediction-kpi-grid instrument-grid">
          <PredictionKpi label="目前 Speed Loss" groups={groups} render={(group) => `${group.current_speed_loss_pct?.toFixed(2)}%`} />
          <PredictionKpi label="劣化速率" groups={groups} render={(group) => `${group.deterioration_rate_pct_per_month !== null && group.deterioration_rate_pct_per_month > 0 ? '+' : ''}${group.deterioration_rate_pct_per_month?.toFixed(2)} %／月`} />
          <PredictionKpi label="距門檻天數" groups={groups} render={(group) => etaText(group, forecastDays)} />
          <PredictionKpi label="預估清底日範圍" groups={groups} render={(group) => rangeText(group, forecastDays)} />
        </div>
      )}

      {!prediction && loading && <div className="prediction-empty">正在建立 {shipName} 的逐載況 STW 基準…</div>}
      {prediction && prediction.available && <EChart option={chartOption} className="main-chart prediction-chart" ariaLabel={`${shipName} 依重載與壓艙分模的 Speed Loss 預測圖`} />}
      {prediction && prediction.available && <details className="data-fallback prediction-data-table"><summary>查看預測圖表資料</summary>{groups.filter((group) => group.available).map((group) => <div className="table-wrap" key={group.load_condition}><table><caption>{group.load_label} Speed Loss 量測與每 7 天預測點</caption><thead><tr><th>映射日期</th><th>資料</th><th>Speed Loss</th><th>90% 下界</th><th>90% 上界</th></tr></thead><tbody>{group.history.map((point) => <tr key={`history-${group.load_condition}-${point.day}`}><td>{dayToDisplayDate(point.day)}</td><td>7 天箱量測（{point.observations} 筆）</td><td>{point.speed_loss_pct.toFixed(2)}%</td><td>—</td><td>—</td></tr>)}{group.forecast.filter((_, index) => index % 7 === 0 || index === group.forecast.length - 1).map((point) => <tr key={`forecast-${group.load_condition}-${point.day}`}><td>{dayToDisplayDate(point.day)}</td><td>趨勢外推</td><td>{point.mid.toFixed(2)}%</td><td>{point.lo.toFixed(2)}%</td><td>{point.hi.toFixed(2)}%</td></tr>)}</tbody></table></div>)}</details>}
      {prediction && !prediction.available && <div className="prediction-empty" role="status" aria-live="polite"><strong>無法產生 strict 預測</strong><span>{prediction.reason}</span></div>}
      {unavailableGroups.length > 0 && <div className="prediction-warnings" role="status" aria-live="polite">{unavailableGroups.map((group) => <p key={group.load_condition}><b>{group.load_label}</b>：{group.reason}</p>)}</div>}

      <details className="prediction-method data-fallback">
        <summary>計算方法與假設</summary>
        <div>
          <p><b>量測與篩選：</b>只用 STW（對水航速），不使用含洋流影響的 SOG。要求 STW &gt; 0、HORSE_POWER &gt; 0、WIND_SCALE ≤ 所選上限；HOURS_FULL_SPEED／HOURS_TOTAL ≥ 0.5，任一時數欄缺值時才略過此 ratio 條件。</p>
          <p><b>載況與乾淨基準：</b>以該船有效 DISPLACEMENT 中位數切重載／壓艙，兩組永不混合；各取最早 30% 紀錄，以 OLS 擬合 STW_expected = a + b × HORSE_POWER^(1/3)。缺少外部海試曲線會使絕對值有偏差，相對劣化趨勢較適合決策。</p>
          <p><b>降噪與預測：</b>逐筆 Speed Loss = (預期 STW − 實測 STW)／預期 STW，排除 &lt;−8% 或 &gt;45%；每 7 天平均並用箱中點作 NOON_UTC 座標。相鄰箱下跳超過 3pp 且不在尾端視為清洗，僅用最近一次清洗後資料做線性 OLS。</p>
          <p><b>不確定性：</b>填色帶是 90% 迴歸平均反應信賴帶，半寬 1.645 × s × √(1/n + (x−x̄)²/Sxx)，距觀測期越遠越寬；上界／中線／下界首次達門檻分別形成最早／ETA／最晚。</p>
          <p><b>主要誤差源：</b>STW 計程儀校準是最大量測誤差源。資料沒有舵角欄位，因此以全速時數比例代理穩態直航，不能完全排除操舵造成的功率變化。</p>
          <p className="day-zero-note"><b>時間解讀：</b>顯示日期為 Day 0 = 2021-01-01 的映射座標，只供排序與計算日距，不是真實日曆日期。{prediction?.day0_note ?? ''}</p>
        </div>
      </details>
    </section>
  )
}
