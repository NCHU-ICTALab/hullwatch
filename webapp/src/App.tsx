import { useEffect, useMemo, useRef, useState, type FormEvent, type PointerEvent as ReactPointerEvent } from 'react'
import {
  AlertTriangle,
  Bell,
  Bot,
  CheckCircle2,
  ChevronRight,
  CircleGauge,
  Fuel,
  ImageUp,
  Menu,
  Moon,
  Pause,
  Play,
  Settings,
  Ship,
  Sun,
  Upload,
  X,
} from 'lucide-react'
import type { EChartsOption } from 'echarts'
import { api } from './api'
import { EChart } from './components/EChart'
import { allocateEventLanes, cleaningSavings, EVENT_LANE_HEIGHT, fuelHistoryForGrade } from './dashboardLogic'
import type {
  AdvisorResponse,
  AlertsResponse,
  FleetResponse,
  FleetShip,
  ForecastResponse,
  FuelPriceResponse,
  LogEntry,
  ModelInfo,
  NotificationSubscriptionsResponse,
  NoonReportImportResponse,
  RoiResponse,
  ScheduleItem,
  ScheduleResponse,
  ShipDetail,
  Status,
} from './types'
import './App.css'

type View = 'fleet' | 'diagnose' | 'decide'
type Tool = 'advisor' | 'inspect' | 'settings' | null

const money = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
const number = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 })

const statusMeta: Record<Status, { label: string; symbol: string }> = {
  action: { label: '立即處置', symbol: '▲' },
  watch: { label: '密切留意', symbol: '●' },
  ok: { label: '狀態正常', symbol: '○' },
}

function App() {
  const [view, setView] = useState<View>('fleet')
  const [fleet, setFleet] = useState<FleetResponse | null>(null)
  const [models, setModels] = useState<ModelInfo[]>([])
  const [schedule, setSchedule] = useState<ScheduleResponse | null>(null)
  const [fuel, setFuel] = useState<FuelPriceResponse | null>(null)
  const [alerts, setAlerts] = useState<AlertsResponse | null>(null)
  const [selectedShipId, setSelectedShipId] = useState('')
  const [detail, setDetail] = useState<ShipDetail | null>(null)
  const [roi, setRoi] = useState<RoiResponse | null>(null)
  const [log, setLog] = useState<LogEntry[]>([])
  const [forecasts, setForecasts] = useState<Record<string, ForecastResponse>>({})
  const [primaryModel, setPrimaryModel] = useState('linear-growth')
  const [visibleModels, setVisibleModels] = useState<string[]>([])
  const [scenarioSpeed, setScenarioSpeed] = useState(15)
  const [fuelScenario, setFuelScenario] = useState(600)
  const [statusFilter, setStatusFilter] = useState<'all' | Status>('all')
  const [slMinimum, setSlMinimum] = useState(0)
  const [dark, setDark] = useState(() => localStorage.getItem('hw-theme') === 'dark')
  const [tickerPaused, setTickerPaused] = useState(false)
  const [alertOpen, setAlertOpen] = useState(false)
  const [decisionFocusKey, setDecisionFocusKey] = useState(0)
  const [tool, setTool] = useState<Tool>(null)
  const [refreshVersion, setRefreshVersion] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const seenCriticalAlerts = useRef(new Set<string>())
  const requestedShipId = useRef('')

  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark)
    localStorage.setItem('hw-theme', dark ? 'dark' : 'light')
  }, [dark])

  useEffect(() => {
    let active = true
    Promise.all([api.fleet(), api.models(), api.schedule(), api.fuelPrices(), api.alerts()])
      .then(([fleetData, modelData, scheduleData, fuelData, alertData]) => {
        if (!active) return
        setFleet(fleetData)
        setModels(modelData.models)
        setSchedule(scheduleData)
        setFuel(fuelData)
        setFuelScenario(fuelData.effective_price.usd_per_ton)
        setAlerts(alertData)
        setSelectedShipId(fleetData.ships[0]?.ship_id ?? '')
        setPrimaryModel(modelData.models.find((model) => model.is_primary)?.id ?? 'linear-growth')
        setVisibleModels(modelData.models.slice(0, 2).map((model) => model.id))
      })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : '資料載入失敗'))
      .finally(() => setLoading(false))
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (!selectedShipId || models.length === 0) return
    if (requestedShipId.current !== selectedShipId) {
      requestedShipId.current = selectedShipId
      setDetail(null)
      setRoi(null)
      setLog([])
      setForecasts({})
    }
    let active = true
    Promise.all([
      api.ship(selectedShipId),
      api.roi(
        selectedShipId,
        fuelScenario,
        schedule?.recommendations.find((item) => item.ship_id === selectedShipId)?.action_cost_usd,
        schedule?.recommendations.find((item) => item.ship_id === selectedShipId)?.speed_loss_recovery_pp,
      ),
      api.log(selectedShipId),
      ...models.map((model) => api.forecast(selectedShipId, model.id, scenarioSpeed)),
    ]).then(([shipData, roiData, logData, ...forecastData]) => {
      if (!active) return
      setDetail(shipData as ShipDetail)
      setRoi(roiData as RoiResponse)
      setLog((logData as { entries: LogEntry[] }).entries)
      setForecasts(Object.fromEntries((forecastData as ForecastResponse[]).map((item) => [item.model_id, item])))
    }).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : '單船資料載入失敗'))
    return () => { active = false }
  }, [selectedShipId, models, schedule, scenarioSpeed, fuelScenario, refreshVersion])

  useEffect(() => {
    if (detail?.current.avg_speed) setScenarioSpeed(detail.current.avg_speed)
  }, [detail?.ship_id, detail?.current.avg_speed])

  useEffect(() => {
    const criticalAlerts = alerts?.alerts.filter((alert) => alert.severity === 'critical') ?? []
    const hasNewCritical = criticalAlerts.some((alert) => !alert.read && !seenCriticalAlerts.current.has(alert.id))
    seenCriticalAlerts.current = new Set(criticalAlerts.map((alert) => alert.id))
    if (hasNewCritical) setAlertOpen(true)
  }, [alerts])

  useEffect(() => {
    if (view !== 'decide' || decisionFocusKey === 0) return
    const timer = window.setTimeout(() => {
      const target = document.getElementById('selected-decision')
      target?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      target?.focus({ preventScroll: true })
    }, 80)
    return () => window.clearTimeout(timer)
  }, [view, decisionFocusKey])

  const filteredShips = useMemo(() => fleet?.ships.filter((ship) => (
    (statusFilter === 'all' || ship.status === statusFilter) && ship.speed_loss_pct >= slMinimum
  )) ?? [], [fleet, statusFilter, slMinimum])

  const selectShip = (shipId: string, nextView: View = 'diagnose') => {
    setSelectedShipId(shipId)
    setView(nextView)
    setAlertOpen(false)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const markAlert = async (alertId: string, shipId: string) => {
    await api.markAlertRead(alertId)
    setAlerts(await api.alerts())
    selectShip(shipId)
  }

  const refreshAfterReport = async () => {
    setRefreshVersion((version) => version + 1)
    const [fleetData, scheduleData, alertData] = await Promise.all([api.fleet(), api.schedule(), api.alerts()])
    setFleet(fleetData)
    setSchedule(scheduleData)
    setAlerts(alertData)
  }

  const refreshModels = async () => {
    const [result, scheduleData] = await Promise.all([api.models(), api.schedule()])
    setModels(result.models)
    setSchedule(scheduleData)
    setPrimaryModel(result.models.find((model) => model.is_primary)?.id ?? 'linear-growth')
    setVisibleModels(result.models.filter((model) => model.status !== 'rejected').slice(0, 2).map((model) => model.id))
  }

  if (loading) return <LoadingScreen />

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">跳至主要內容</a>
      <Header
        view={view}
        setView={setView}
        dark={dark}
        setDark={setDark}
        alerts={alerts}
        onAlert={() => setAlertOpen(true)}
        onTool={setTool}
      />

      {error && <div className="error-banner" role="alert"><AlertTriangle size={18} />{error}<button onClick={() => setError('')}>關閉</button></div>}

      <main id="main-content">
        {view === 'fleet' && fleet && (
          <FleetView
            fleet={fleet}
            fuel={fuel}
            tickerPaused={tickerPaused}
            setTickerPaused={setTickerPaused}
            ships={filteredShips}
            statusFilter={statusFilter}
            setStatusFilter={setStatusFilter}
            slMinimum={slMinimum}
            setSlMinimum={setSlMinimum}
            onSelect={selectShip}
          />
        )}
        {view === 'diagnose' && (
          <DiagnoseView
            detail={detail}
            models={models}
            forecasts={forecasts}
            primaryModel={primaryModel}
            setPrimaryModel={setPrimaryModel}
            visibleModels={visibleModels}
            setVisibleModels={setVisibleModels}
            scenarioSpeed={scenarioSpeed}
            setScenarioSpeed={setScenarioSpeed}
            log={log}
            roi={roi}
            onDecide={() => { setView('decide'); setDecisionFocusKey((key) => key + 1) }}
            recommendation={schedule?.recommendations.find((item) => item.ship_id === selectedShipId)}
            dark={dark}
          />
        )}
        {view === 'decide' && schedule && fuel && (roi ? (
          <DecideView
            schedule={schedule}
            fuel={fuel}
            roi={roi}
            selectedShipId={selectedShipId}
            primaryModel={primaryModel}
            onSelect={selectShip}
            onDecisionShipChange={setSelectedShipId}
            dark={dark}
            fuelScenario={fuelScenario}
            setFuelScenario={setFuelScenario}
            focusKey={decisionFocusKey}
          />
        ) : <InlineLoading label="載入所選船舶的經濟決策" />)}
      </main>

      <AlertDrawer alerts={alerts} open={alertOpen} onClose={() => setAlertOpen(false)} onSelect={markAlert} />
      <ToolDialog
        tool={tool}
        onClose={() => setTool(null)}
        shipId={selectedShipId}
        alerts={alerts}
        fuel={fuel}
        models={models}
        onModelsChanged={refreshModels}
        onReportsImported={refreshAfterReport}
      />
    </div>
  )
}

function Header({ view, setView, dark, setDark, alerts, onAlert, onTool }: {
  view: View
  setView: (view: View) => void
  dark: boolean
  setDark: (dark: boolean) => void
  alerts: AlertsResponse | null
  onAlert: () => void
  onTool: (tool: Tool) => void
}) {
  const tabs: { id: View; step: string; zh: string; en: string }[] = [
    { id: 'fleet', step: '①', zh: '總覽', en: 'FLEET' },
    { id: 'diagnose', step: '②', zh: '診斷', en: 'DIAGNOSE' },
    { id: 'decide', step: '③', zh: '決策', en: 'DECIDE' },
  ]
  return (
    <header className="topbar">
      <button className="brand" onClick={() => setView('fleet')} aria-label="HullWatch 船隊總覽">
        <span className="brand-mark"><Ship size={22} /></span>
        <span><strong>HULLWATCH</strong><small>FLEET PERFORMANCE</small></span>
      </button>
      <nav className="story-tabs" aria-label="三段故事流">
        {tabs.map((tab) => (
          <button key={tab.id} className={view === tab.id ? 'active' : ''} aria-current={view === tab.id ? 'page' : undefined} onClick={() => setView(tab.id)}>
            <span>{tab.step}</span>{tab.zh}<small>{tab.en}</small>
          </button>
        ))}
      </nav>
      <div className="header-tools">
        <details className="tool-menu">
          <summary><Menu size={16} />工具</summary>
          <div>
            <button onClick={() => onTool('advisor')}><Bot size={16} />AI 顧問</button>
            <button onClick={() => onTool('inspect')}><ImageUp size={16} />水下判讀</button>
            <button onClick={() => onTool('settings')}><Settings size={16} />設定</button>
          </div>
        </details>
        <button className="icon-button" onClick={() => setDark(!dark)} aria-pressed={dark}>{dark ? <Sun size={16} /> : <Moon size={16} />}<span>{dark ? '亮色' : '深色'}</span></button>
        <button className="icon-button" onClick={onAlert} aria-label={`警報中心，${alerts?.unread_count ?? 0} 則未讀`}><Bell size={16} />警報{Boolean(alerts?.unread_count) && <b>{alerts?.unread_count}</b>}</button>
      </div>
    </header>
  )
}

function FleetView({ fleet, fuel, tickerPaused, setTickerPaused, ships, statusFilter, setStatusFilter, slMinimum, setSlMinimum, onSelect }: {
  fleet: FleetResponse
  fuel: FuelPriceResponse | null
  tickerPaused: boolean
  setTickerPaused: (paused: boolean) => void
  ships: FleetShip[]
  statusFilter: 'all' | Status
  setStatusFilter: (value: 'all' | Status) => void
  slMinimum: number
  setSlMinimum: (value: number) => void
  onSelect: (shipId: string) => void
}) {
  return (
    <section className="page fleet-page" aria-labelledby="fleet-title">
      <PageHeading eyebrow="01 / FLEET HEALTH" title="船隊健康總覽" subtitle={`${fleet.stats.n_ships} 艘船 · 依 Speed Loss 風險與清洗急迫度排序`} />
      {fuel && <FuelTicker fuel={fuel} paused={tickerPaused} setPaused={setTickerPaused} />}
      <div className="fleet-stats instrument-grid">
        <Metric label="平均 Speed Loss" value={`${number.format(fleet.stats.avg_speed_loss_pct)}%`} tone="teal" />
        <Metric label="立即處置" value={`${fleet.stats.ships_action}`} unit="艘" tone="red" />
        <Metric label="60 天內留意" value={`${fleet.stats.ships_watch}`} unit="艘" tone="amber" />
        <Metric label="每月超額成本" value={money.format(fleet.stats.monthly_excess_cost_usd)} tone="red" />
        <Metric label="每月超額碳排" value={number.format(fleet.stats.monthly_excess_co2_tons)} unit="tCO₂" />
      </div>
      <div className="filter-bar panel">
        <fieldset><legend>狀態篩選</legend>{(['all', 'action', 'watch', 'ok'] as const).map((status) => <button key={status} className={statusFilter === status ? 'selected' : ''} onClick={() => setStatusFilter(status)}>{status === 'all' ? '全部' : `${statusMeta[status].symbol} ${statusMeta[status].label}`}</button>)}</fieldset>
        <DualInput label="Speed Loss 下限" value={slMinimum} min={0} max={15} step={0.5} unit="%" onChange={setSlMinimum} />
        <span className="result-count" aria-live="polite">顯示 {ships.length} / {fleet.stats.n_ships} 艘</span>
      </div>
      <div className="ship-grid">
        {ships.map((ship) => <ShipCard key={ship.ship_id} ship={ship} onSelect={onSelect} />)}
      </div>
      {ships.length > 0 && <FleetSparkTable ships={ships} />}
      {ships.length === 0 && <div className="empty-state"><CircleGauge /><strong>沒有符合條件的船舶</strong><span>降低 Speed Loss 下限或切換狀態。</span></div>}
    </section>
  )
}

function ShipCard({ ship, onSelect }: { ship: FleetShip; onSelect: (shipId: string) => void }) {
  return (
    <button className={`ship-card status-${ship.status}`} onClick={() => onSelect(ship.ship_id)}>
      <span className="ship-card-head"><span className="status-signal">{statusMeta[ship.status].symbol} {statusMeta[ship.status].label}</span><ChevronRight size={18} /></span>
      <strong>{ship.ship_name}</strong><small>{ship.ship_id}</small>
      <div className="ship-reading"><b>{ship.speed_loss_pct.toFixed(1)}</b><em>% SL</em><Sparkline values={ship.spark} label={`${ship.ship_name} Speed Loss 近期趨勢`} showTable={false} /></div>
      <span className="ship-meta"><span>距清洗 {ship.days_since_clean} 天</span><span>{money.format(ship.excess_cost_per_day)} / 日</span></span>
    </button>
  )
}

function DiagnoseView({ detail, models, forecasts, primaryModel, setPrimaryModel, visibleModels, setVisibleModels, scenarioSpeed, setScenarioSpeed, log, roi, onDecide, recommendation, dark }: {
  detail: ShipDetail | null
  models: ModelInfo[]
  forecasts: Record<string, ForecastResponse>
  primaryModel: string
  setPrimaryModel: (id: string) => void
  visibleModels: string[]
  setVisibleModels: (ids: string[]) => void
  scenarioSpeed: number
  setScenarioSpeed: (value: number) => void
  log: LogEntry[]
  roi: RoiResponse | null
  onDecide: () => void
  recommendation?: ScheduleItem
  dark: boolean
}) {
  const trendOption = useMemo<EChartsOption>(() => {
    if (!detail) return {}
    const chartText = dark ? '#A7B8C0' : '#4A5A63'
    const chartGrid = dark ? '#2A3B43' : '#D8DFE4'
    const legendNames = ['歷史 Speed Loss']
    const series: NonNullable<EChartsOption['series']> = [{
      name: '歷史 Speed Loss', type: 'line', showSymbol: false, data: detail.series.map((point) => [point.date, point.speed_loss]), lineStyle: { width: 3, color: '#0E5E6F' }, itemStyle: { color: '#0E5E6F' },
      markLine: { silent: true, symbol: 'none', label: { formatter: `清洗門檻 ${detail.current.threshold_pct}%`, color: chartText }, lineStyle: { color: '#A33B2E', type: 'dashed', width: 2 }, data: [{ yAxis: detail.current.threshold_pct }] },
      markArea: { silent: true, itemStyle: { color: dark ? 'rgba(232,144,127,.12)' : 'rgba(163,59,46,.08)' }, data: [[{ yAxis: detail.current.threshold_pct }, { yAxis: 35 }]] },
      markPoint: { symbol: 'diamond', symbolSize: 14, itemStyle: { color: '#C77400' }, label: { show: true, position: 'top', formatter: '{b}', color: chartText, fontSize: 11 }, data: detail.events.map((event) => ({ name: event.type, value: event.notes, coord: [event.date, Math.max(0, detail.current.threshold_pct * .15)] })) },
    }]
    const colors = ['#C77400', '#A33B2E', '#647984']
    visibleModels.forEach((modelId, index) => {
      const forecast = forecasts[modelId]
      if (!forecast) return
      legendNames.push(forecast.model_name)
      if (modelId === primaryModel) {
        series.push(
          { name: '預測下界', type: 'line', stack: 'primary-band', symbol: 'none', silent: true, lineStyle: { opacity: 0 }, areaStyle: { opacity: 0 }, data: forecast.forecast.map((point) => [point.date, point.lo]) },
          { name: '主模型範圍', type: 'line', stack: 'primary-band', symbol: 'none', silent: true, lineStyle: { opacity: 0 }, areaStyle: { color: dark ? 'rgba(91,192,208,.22)' : 'rgba(14,94,111,.18)' }, data: forecast.forecast.map((point) => [point.date, point.hi - point.lo]) },
        )
      }
      series.push({ name: forecast.model_name, type: 'line', symbol: index === 0 ? 'triangle' : 'circle', symbolSize: 6, data: forecast.forecast.map((point) => [point.date, point.mid]), lineStyle: { width: modelId === primaryModel ? 3 : 2, type: index === 0 ? 'dashed' : 'dotted', color: colors[index] }, itemStyle: { color: colors[index] } })
    })
    return {
      animation: !window.matchMedia('(prefers-reduced-motion: reduce)').matches,
      textStyle: { color: chartText, fontSize: 14 }, tooltip: { trigger: 'axis' }, legend: { data: legendNames, bottom: 0, textStyle: { color: chartText, fontSize: 13 } },
      grid: { left: 48, right: 24, top: 32, bottom: 66 },
      xAxis: { type: 'time', axisLabel: { color: chartText, fontSize: 13 }, axisLine: { lineStyle: { color: chartGrid } } },
      yAxis: { type: 'value', name: 'Speed Loss %', nameTextStyle: { color: chartText, fontSize: 13 }, axisLabel: { color: chartText, fontSize: 13 }, splitLine: { lineStyle: { color: chartGrid } } },
      series,
    }
  }, [dark, detail, forecasts, primaryModel, visibleModels])

  if (!detail) return <InlineLoading label="載入單船診斷" />
  const current = detail.current
  const nextAction = recommendation?.action ?? '待 ROI 引擎評估'
  return (
    <section className="page diagnose-page" aria-labelledby="diagnose-title">
      <PageHeading eyebrow="02 / PERFORMANCE DIAGNOSIS" title={detail.ship_name} subtitle={`${detail.ship_id} · 最新正午日報與 16 週效能預測`} badge={`${statusMeta[detail.status].symbol} ${statusMeta[detail.status].label}`} />
      <div className="kpi-grid">
        <Metric label="今日均速" value={current.avg_speed?.toFixed(1) ?? '—'} unit="kn" spark={detail.kpi_sparks.avg_speed} />
        <Metric label="今日油耗" value={current.daily_foc?.toFixed(1) ?? '—'} unit="t/day" spark={detail.kpi_sparks.daily_foc} />
        <Metric label="Speed Loss" value={`${current.speed_loss_pct.toFixed(1)}%`} tone={detail.status === 'action' ? 'red' : 'amber'} spark={detail.kpi_sparks.speed_loss} />
        <Metric label="超額油耗" value={current.excess_foc?.toFixed(1) ?? '—'} unit="t/day" tone="red" spark={detail.kpi_sparks.excess_foc} />
        <Metric label="風級" value={current.wind_scale?.toFixed(0) ?? '—'} unit="Bft" spark={detail.kpi_sparks.wind_scale} />
        <Metric label="距上次清潔" value={`${current.days_since_clean}`} unit="天" spark={detail.kpi_sparks.days_since_clean} />
        <Metric label="最近清潔動作" value={current.last_event?.type ?? '—'} unit={current.last_event?.date ?? ''} />
        <Metric label="每日超額成本" value={money.format(current.excess_cost_per_day)} tone="red" />
      </div>
      <div className="diagnose-layout">
        <section className="panel chart-panel wide-panel">
          <div className="panel-heading"><div><span>SL TREND / FORECAST</span><h2>Speed Loss 趨勢與模型比較</h2></div><span className="model-basis">下游依據：{models.find((model) => model.id === primaryModel)?.name}</span></div>
          <div className="chart-controls">
            <label>決策主模型<select value={primaryModel} onChange={(event) => setPrimaryModel(event.target.value)} disabled={models.filter((model) => model.is_primary).length < 2}>{models.filter((model) => model.is_primary).map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}</select></label>
            <fieldset><legend>顯示比較模型</legend>{models.map((model) => <label key={model.id}><input type="checkbox" checked={visibleModels.includes(model.id)} onChange={(event) => setVisibleModels(event.target.checked ? [...visibleModels, model.id] : visibleModels.filter((id) => id !== model.id))} />{model.name}</label>)}</fieldset>
            <DualInput label="情境船速" value={scenarioSpeed} min={12} max={20} step={0.5} unit="kn" onChange={setScenarioSpeed} />
          </div>
          <EChart option={trendOption} className="main-chart" ariaLabel={`${detail.ship_name} Speed Loss 歷史與多模型預測圖`} />
          <p className="event-legend"><b aria-hidden="true">◆</b> 黃色菱形代表水下維護事件（PP 螺槳拋光、UWC 水下船殼清洗、UWI 水下檢查）；事件名稱會直接標在圖上。</p>
          {detail.events.length > 0 && <details className="data-fallback"><summary>查看維護事件說明</summary><ul className="event-list">{detail.events.map((event) => <li key={`${event.date}-${event.type}`}><time>{event.date}</time><b>{event.type}</b><span>{event.notes || '無附註'}</span></li>)}</ul></details>}
          <details className="data-fallback"><summary>查看圖表資料表</summary><TrendTable detail={detail} forecasts={forecasts} visibleModels={visibleModels} /></details>
        </section>
        <section className="panel attribution-panel">
          <div className="panel-heading"><div><span>FOULING ATTRIBUTION</span><h2>船殼／螺旋槳歸因</h2></div></div>
          <div className="split-bar" role="img" aria-label={`船殼 ${detail.hull_prop.hull_pp} 個百分點，螺旋槳 ${detail.hull_prop.prop_pp} 個百分點`}><span style={{ width: `${(1 - detail.hull_prop.prop_share) * 100}%` }}>船殼 {detail.hull_prop.hull_pp.toFixed(1)}pp</span><span style={{ width: `${detail.hull_prop.prop_share * 100}%` }}>螺槳 {detail.hull_prop.prop_pp.toFixed(1)}pp</span></div>
          {detail.attribution && <div className="waterfall"><span>乾淨基準<b>{detail.attribution.baseline_tons.toFixed(1)}t</b></span>{detail.attribution.factors.map((factor) => <span key={factor.name} className={factor.is_fouling ? 'fouling' : ''}>{factor.name}<b>{factor.tons > 0 ? '+' : ''}{factor.tons.toFixed(1)}t</b></span>)}<span className="actual">實測<b>{detail.attribution.actual_tons.toFixed(1)}t</b></span></div>}
        </section>
        <section className="panel delay-panel">
          <div className="panel-heading"><div><span>COST OF DELAY</span><h2>延遲代價</h2></div><AlertTriangle /></div>
          <strong>現在每天多花 {money.format(current.excess_cost_per_day)}</strong>
          <p>若再拖 30 天，依目前成本至少增加 <b>{money.format(current.excess_cost_per_day * 30)}</b>。建議動作：{nextAction}。</p>
          <button className="primary-action" onClick={onDecide}>前往清洗決策 <ChevronRight size={16} /></button>
        </section>
        <section className="panel log-panel wide-panel">
          <div className="panel-heading"><div><span>30-DAY LOG</span><h2>正午日報與水下事件</h2></div><Upload size={18} /></div>
          <LogTable entries={log} />
        </section>
      </div>
      {roi && <span className="sr-only" aria-live="polite">目前最佳清洗日為 {roi.target.best_day ?? '無建議'}</span>}
    </section>
  )
}

function DecideView({ schedule, fuel, roi, selectedShipId, primaryModel, onSelect, onDecisionShipChange, dark, fuelScenario, setFuelScenario, focusKey }: {
  schedule: ScheduleResponse
  fuel: FuelPriceResponse
  roi: RoiResponse
  selectedShipId: string
  primaryModel: string
  onSelect: (shipId: string, view?: View) => void
  onDecisionShipChange: (shipId: string) => void
  dark: boolean
  fuelScenario: number
  setFuelScenario: (value: number) => void
  focusKey: number
}) {
  const [selectedRecommendation, setSelectedRecommendation] = useState<ScheduleItem | null>(
    schedule.recommendations.find((item) => item.ship_id === selectedShipId) ?? schedule.recommendations[0] ?? null,
  )
  const [cleaningDay, setCleaningDay] = useState(roi.target.best_day ?? 0)
  const [sortBy, setSortBy] = useState<'id' | 'name' | 'risk' | 'cost' | 'speed-loss'>('id')
  const [zoom, setZoom] = useState(1.5)
  const [fuelGrade, setFuelGrade] = useState('VLSFO')
  const ganttViewport = useRef<HTMLDivElement>(null)
  useEffect(() => setCleaningDay(roi.target.best_day ?? 0), [roi.target.best_day])
  useEffect(() => {
    setSelectedRecommendation(
      schedule.recommendations.find((item) => item.ship_id === selectedShipId) ?? schedule.recommendations[0] ?? null,
    )
  }, [schedule.recommendations, selectedShipId])
  const sortedRecommendations = useMemo(() => [...schedule.recommendations].sort((a, b) => {
    if (sortBy === 'name') return a.ship_name.localeCompare(b.ship_name, 'zh-Hant')
    if (sortBy === 'risk') return a.risk_rank - b.risk_rank || b.excess_cost_per_day - a.excess_cost_per_day
    if (sortBy === 'cost') return b.excess_cost_per_day - a.excess_cost_per_day
    if (sortBy === 'speed-loss') return b.speed_loss_pct - a.speed_loss_pct
    return a.ship_id.localeCompare(b.ship_id, undefined, { numeric: true })
  }), [schedule.recommendations, sortBy])
  const timelineDays = schedule.past_days + schedule.future_days
  const todayRatio = schedule.past_days / timelineDays
  const scrollGantt = (direction: -1 | 1) => ganttViewport.current?.scrollBy({ left: direction * 360, behavior: 'smooth' })
  const scrollToday = () => {
    const viewport = ganttViewport.current
    if (!viewport) return
    viewport.scrollTo({ left: Math.max(0, viewport.scrollWidth * todayRatio - viewport.clientWidth / 2), behavior: 'smooth' })
  }
  const chartText = dark ? '#A7B8C0' : '#4A5A63'
  const chartGrid = dark ? '#2A3B43' : '#D8DFE4'
  const selectedFuelHistory = useMemo(
    () => fuelHistoryForGrade(fuel, fuelGrade),
    [fuel, fuelGrade],
  )
  const selectedFuelPrice = fuel.prices.find((price) => price.grade === fuelGrade)
  const roiOption = useMemo<EChartsOption>(() => ({
    textStyle: { color: chartText, fontSize: 14 },
    tooltip: { trigger: 'axis', valueFormatter: (value) => money.format(Number(value)) },
    grid: { left: 66, right: 26, top: 24, bottom: 44 },
    xAxis: { type: 'category', name: '延後天數', nameTextStyle: { color: chartText, fontSize: 13 }, axisLabel: { color: chartText, fontSize: 13 }, data: roi.target.days.filter((_, index) => index % 5 === 0) },
    yAxis: { type: 'value', name: '相較不清洗淨節省／日', nameTextStyle: { color: chartText, fontSize: 13 }, axisLabel: { color: chartText, fontSize: 13, formatter: (value: number) => money.format(value) }, splitLine: { lineStyle: { color: chartGrid } } },
    series: [{ name: '平均每日淨節省', type: 'line', data: cleaningSavings(roi.target.no_clean_avg, roi.target.avg_cost.filter((_, index) => index % 5 === 0)), smooth: true, showSymbol: false, lineStyle: { width: 3, color: '#0E5E6F' }, areaStyle: { color: 'rgba(14,94,111,.12)' }, markLine: { data: [{ yAxis: 0, name: '損益兩平' }], lineStyle: { color: '#A33B2E', type: 'dashed' } }, markPoint: roi.target.best_day === null ? undefined : { data: [{ coord: [Math.floor(roi.target.best_day / 5), roi.target.no_clean_avg - roi.target.best_avg], name: '最佳日' }], itemStyle: { color: '#C77400' } } }],
  }), [chartGrid, chartText, roi])
  const fuelOption = useMemo<EChartsOption>(() => ({
    textStyle: { color: chartText, fontSize: 14 },
    tooltip: { trigger: 'axis' }, grid: { left: 56, right: 20, top: 20, bottom: 38 },
    xAxis: { type: 'category', axisLabel: { color: chartText, fontSize: 13 }, data: selectedFuelHistory.map((point) => point.date.slice(5)) },
    yAxis: { type: 'value', min: 'dataMin', axisLabel: { color: chartText, fontSize: 13, formatter: '${value}' }, splitLine: { lineStyle: { color: chartGrid } } },
    series: [{ name: fuelGrade, type: 'line', data: selectedFuelHistory.map((point) => point.usd_per_ton), showSymbol: false, lineStyle: { width: 3, color: '#0E5E6F' }, areaStyle: { color: 'rgba(14,94,111,.12)' } }],
  }), [chartGrid, chartText, fuelGrade, selectedFuelHistory])
  return (
    <section className="page decide-page" aria-labelledby="decide-title">
      <PageHeading eyebrow="03 / MAINTENANCE DECISION" title="維護排程與經濟決策" subtitle={`未來 ${schedule.horizon_days} 天 · 唯讀系統建議 · 主模型 ${primaryModel}`} />
      <section className="panel schedule-panel">
        <div className="panel-heading"><div><span>RECOMMENDED WINDOWS</span><h2>全船隊清潔建議甘特圖</h2></div><span className="model-basis">過去 {schedule.past_days} 天 · 未來 {schedule.future_days} 天</span></div>
        <div className="gantt-controls">
          <label>排列方式<select value={sortBy} onChange={(event) => setSortBy(event.target.value as typeof sortBy)}><option value="id">船舶 ID</option><option value="name">船名</option><option value="risk">警報風險</option><option value="cost">每日超額成本</option><option value="speed-loss">Speed Loss</option></select></label>
          <label>時間縮放<input type="range" min="1" max="3" step="0.25" value={zoom} onChange={(event) => setZoom(Number(event.target.value))} /><span>{Math.round(zoom * 100)}%</span></label>
          <div><button onClick={() => scrollGantt(-1)}>← 前段</button><button onClick={scrollToday}>回到今天</button><button onClick={() => scrollGantt(1)}>後段 →</button></div>
        </div>
        <div className="gantt-viewport" ref={ganttViewport}>
          <div className="gantt" style={{ width: `${zoom * 100}%` }} role="region" aria-label="全船隊過去 90 天至未來 180 天清潔建議甘特圖" tabIndex={0}>
            <div className="gantt-axis"><span>{schedule.timeline_start}</span><span>−23 天</span><span>+45 天</span><span>+113 天</span><span>{schedule.timeline_end}</span></div>
            {sortedRecommendations.map((item) => <GanttRow key={item.ship_id} item={item} timelineStart={schedule.timeline_start} totalDays={timelineDays} todayRatio={todayRatio} dryDock={schedule.dry_docks.find((event) => event.ship_id === item.ship_id)?.date} events={schedule.maintenance_events.filter((event) => event.ship_id === item.ship_id && event.type !== 'DD')} selected={selectedRecommendation?.ship_id === item.ship_id} onOpen={(item) => { setSelectedRecommendation(item); onDecisionShipChange(item.ship_id) }} />)}
          </div>
        </div>
        {selectedRecommendation && <article id="selected-decision" tabIndex={-1} className={`schedule-detail ${focusKey ? 'focus-highlight' : ''}`} aria-live="polite"><div><span>建議詳情 · 唯讀</span><strong>{selectedRecommendation.ship_name} / {selectedRecommendation.action}</strong></div><p>{selectedRecommendation.window_start}–{selectedRecommendation.window_end}，作業成本 {money.format(selectedRecommendation.action_cost_usd)}，預期回復 <b>{selectedRecommendation.speed_loss_recovery_pp.toFixed(1)}pp SL</b>、每日省 {selectedRecommendation.daily_fuel_saving_tons.toFixed(2)} 噸、每月省 {money.format(selectedRecommendation.monthly_saving_usd)}。若延後，優先遞補：{selectedRecommendation.backfill.ship_name}。{selectedRecommendation.inspection_recommended && <b> 不確定性較高，建議先安排 UWI 檢查。</b>}</p><button onClick={() => onSelect(selectedRecommendation.ship_id, 'diagnose')}>查看單船診斷 <ChevronRight size={15} /></button></article>}
        <details className="data-fallback"><summary>查看排程與維護事件資料表</summary><ScheduleTable items={sortedRecommendations} onSelect={onSelect} /><MaintenanceEventTable schedule={schedule} /></details>
      </section>
      <div className="decision-grid">
        <section className="panel roi-panel">
          <div className="panel-heading"><div><span>WHAT-IF / 180 DAYS</span><h2>{roi.target.ship_name} 清洗日淨節省曲線</h2></div></div>
          <div className="decision-callout"><span>系統最佳日<b>{roi.target.best_day === null ? '暫不清洗' : `第 ${roi.target.best_day} 天`}</b></span><span>回本期<b>{roi.target.payback_days ?? '—'} 天</b></span><span>每日超額碳排<b>{roi.target.excess_co2_per_day} tCO₂</b></span><span>所選方案淨節省<b>{money.format(roi.target.no_clean_avg - (roi.target.avg_cost[cleaningDay] ?? roi.target.no_clean_avg))}/日</b></span></div>
          <div className="scenario-control"><DualInput label="清洗日 What-if" value={cleaningDay} min={0} max={180} step={1} unit="天後" onChange={setCleaningDay} /><DualInput label="有效油價情境" value={fuelScenario} min={300} max={1500} step={10} unit="USD/mt" onChange={setFuelScenario} /><small>情境由後端 ROI 曲線計算；不改寫行情來源。</small></div>
          <p className="chart-explanation">每一點都會重新計算「該日才清洗」在 180 天內的燃油超額成本與清洗費，再減去完全不清洗的成本；高於零代表清洗較省。</p>
          <EChart option={roiOption} className="decision-chart" ariaLabel={`${roi.target.ship_name} 未來 180 天不同清洗日相較不清洗的平均每日淨節省曲線`} />
          <details className="data-fallback"><summary>查看 What-if 資料</summary><table><thead><tr><th>清洗日</th><th>平均每日成本</th><th>相較不清洗淨節省</th></tr></thead><tbody>{roi.target.days.filter((_, index) => index % 15 === 0).map((day, index) => <tr key={day}><td>第 {day} 天</td><td>{money.format(roi.target.avg_cost[index * 15])}</td><td>{money.format(roi.target.no_clean_avg - roi.target.avg_cost[index * 15])}/日</td></tr>)}</tbody></table></details>
        </section>
        <section className="panel fuel-panel">
          <div className="panel-heading"><div><span>FUEL MARKET</span><h2>市場行情與決策情境價</h2></div><b className={`market-badge market-${fuel.market_status}`}>{fuel.market_status}</b></div>
          <label className="fuel-grade-select">行情油種<select value={fuelGrade} onChange={(event) => setFuelGrade(event.target.value)}>{Object.keys(fuel.history_by_grade).map((grade) => <option key={grade} value={grade}>{grade}</option>)}</select></label>
          <div className="fuel-cards">{fuel.prices.map((price) => <article key={price.grade}><span>{price.grade}{price.estimated && <em>EST</em>}</span><strong>${number.format(price.usd_per_ton)}</strong><small>USD / mt</small></article>)}</div>
          {fuel.prices.length === 0 && <div className="market-unavailable"><Fuel /><strong>即時行情暫時無法取得</strong><span>ROI 使用上方明確標示的手動情境價。</span></div>}
          {selectedFuelHistory.length > 0 && <EChart option={fuelOption} className="fuel-chart" ariaLabel={`${fuelGrade} 近期價格趨勢`} />}
          <details className="data-fallback"><summary>查看燃油趨勢資料表</summary><div className="table-wrap"><table><thead><tr><th>日期</th><th>{fuelGrade} USD/mt</th><th>來源</th></tr></thead><tbody>{selectedFuelHistory.map((point) => <tr key={point.date}><td>{point.date}</td><td>${point.usd_per_ton.toFixed(2)}</td><td>{point.source}{point.estimated ? '（估算）' : ''}</td></tr>)}</tbody></table></div></details>
          <p className="source-note">{selectedFuelPrice ? <>Source: <a href={selectedFuelPrice.source_url} target="_blank" rel="noreferrer">{selectedFuelPrice.source}</a>{selectedFuelPrice.estimated ? '（估算）' : ''} · </> : null}{fuel.effective_price.method}</p>
        </section>
      </div>
    </section>
  )
}

function GanttRow({ item, timelineStart, totalDays, todayRatio, dryDock, events, selected, onOpen }: { item: ScheduleItem; timelineStart: string; totalDays: number; todayRatio: number; dryDock?: string; events: ScheduleResponse['maintenance_events']; selected: boolean; onOpen: (item: ScheduleItem) => void }) {
  const day = (value: string) => (new Date(value).getTime() - new Date(timelineStart).getTime()) / 86400000
  const start = day(item.window_start)
  const end = day(item.window_end)
  const eventLanes = allocateEventLanes([
    ...events,
    ...(dryDock ? [{ ship_id: item.ship_id, date: dryDock, type: 'DD', notes: '既定乾塢事件' }] : []),
  ])
  const laneCount = Math.max(1, ...eventLanes.map(({ lane }) => lane + 1))
  return <button className={`gantt-row ${selected ? 'selected' : ''}`} onClick={() => onOpen(item)} aria-label={`選擇 ${item.ship_name}，建議動作 ${item.action}`}><span className="gantt-name"><b>{item.ship_name}</b><small>{item.ship_id}</small></span><span className="gantt-track" style={{ height: `${laneCount * EVENT_LANE_HEIGHT + 30}px` }}><span className="today-line" style={{ left: `${todayRatio * 100}%` }} aria-hidden="true" />{eventLanes.map(({ event, lane }, index) => { const position = day(event.date); return position >= 0 && position <= totalDays ? <span key={`${event.date}-${event.type}-${index}`} className={event.type === 'DD' ? 'dd-block' : 'event-mark'} title={`${event.date} ${event.type}：${event.notes}`} style={{ left: `${position / totalDays * 100}%`, top: `${lane * EVENT_LANE_HEIGHT + 1}px` }}>{event.type}</span> : null })}<span className={`gantt-bar action-${item.action.replace('+', '-plus-')}`} style={{ left: `${Math.max(0, start / totalDays * 100)}%`, width: `${Math.max(2.5, (end - start) / totalDays * 100)}%` }}>{item.action}</span></span><span className="gantt-impact">+{item.speed_loss_recovery_pp.toFixed(1)}pp<small>{money.format(item.monthly_saving_usd)}/月</small></span></button>
}

function Metric({ label, value, unit, tone, spark }: { label: string; value: string; unit?: string; tone?: 'teal' | 'amber' | 'red'; spark?: number[] }) {
  return <article className={`metric ${tone ? `tone-${tone}` : ''}`}><span>{label}</span><strong>{value}</strong>{unit && <small>{unit}</small>}{spark && spark.length > 1 && <Sparkline values={spark} label={`${label}近期趨勢`} />}</article>
}

function PageHeading({ eyebrow, title, subtitle, badge }: { eyebrow: string; title: string; subtitle: string; badge?: string }) {
  const id = eyebrow.startsWith('01') ? 'fleet-title' : eyebrow.startsWith('02') ? 'diagnose-title' : 'decide-title'
  return <div className="page-heading"><div><span>{eyebrow}</span><h1 id={id}>{title}</h1><p>{subtitle}</p></div>{badge && <b>{badge}</b>}</div>
}

function DualInput({ label, value, min, max, step, unit, onChange }: { label: string; value: number; min: number; max: number; step: number; unit: string; onChange: (value: number) => void }) {
  const set = (next: number) => onChange(Math.max(min, Math.min(max, next)))
  return <label className="dual-input"><span>{label}</span><input type="range" value={value} min={min} max={max} step={step} onChange={(event) => set(Number(event.target.value))} /><input type="number" value={value} min={min} max={max} step={step} onChange={(event) => set(Number(event.target.value))} /><em>{unit}</em></label>
}

function Sparkline({ values, label, showTable = true }: { values: number[]; label: string; showTable?: boolean }) {
  if (values.length < 2) return null
  const min = Math.min(...values), max = Math.max(...values), span = Math.max(max - min, 0.1)
  const points = values.map((value, index) => `${index / (values.length - 1) * 90},${28 - (value - min) / span * 24}`).join(' ')
  return <><svg className="sparkline" viewBox="0 0 90 32" role="img" aria-label={`${label}，由 ${values[0].toFixed(1)} 至 ${values.at(-1)?.toFixed(1)}`}><polyline points={points} /></svg>{showTable && <table className="sr-only"><caption>{label}完整數列</caption><thead><tr><th>順序</th><th>數值</th></tr></thead><tbody>{values.map((value, index) => <tr key={index}><td>{index + 1}</td><td>{value.toFixed(2)}</td></tr>)}</tbody></table>}</>
}

function FleetSparkTable({ ships }: { ships: FleetShip[] }) {
  const pointCount = Math.max(...ships.map((ship) => ship.spark.length))
  return <details className="data-fallback fleet-spark-table"><summary>查看船隊 Speed Loss 近期趨勢資料表</summary><div className="table-wrap"><table><caption>目前篩選船舶的 Speed Loss sparkline 完整數列</caption><thead><tr><th>船舶</th>{Array.from({ length: pointCount }, (_, index) => <th key={index}>資料點 {index + 1}</th>)}</tr></thead><tbody>{ships.map((ship) => <tr key={ship.ship_id}><td>{ship.ship_name}（{ship.ship_id}）</td>{Array.from({ length: pointCount }, (_, index) => <td key={index}>{ship.spark[index]?.toFixed(2) ?? '—'}</td>)}</tr>)}</tbody></table></div></details>
}

function LogTable({ entries }: { entries: LogEntry[] }) {
  return <div className="table-wrap"><table><thead><tr><th>日期</th><th>類型</th><th>均速</th><th>DailyFOC</th><th>風級</th><th>SL</th><th>超額油耗</th></tr></thead><tbody>{entries.slice(0, 30).map((entry, index) => entry.kind === 'event' ? <tr className="event-row" key={`${entry.date}-${index}`}><td>{entry.date}</td><td colSpan={6}>◆ {entry.event_type} · {entry.notes || '水下事件'}</td></tr> : <tr key={`${entry.date}-${index}`}><td>{entry.date}</td><td>正午日報</td><td>{entry.avg_speed?.toFixed(1)} kn</td><td>{entry.daily_foc?.toFixed(1)} t</td><td>{entry.wind_scale ?? '—'}</td><td>{entry.speed_loss_pct?.toFixed(1)}%</td><td>{entry.excess_foc_tons?.toFixed(1)} t</td></tr>)}</tbody></table></div>
}

function NoonReportUpload({ onUploaded }: { onUploaded: () => Promise<void> | void }) {
  const [result, setResult] = useState<NoonReportImportResponse | null>(null)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [downloadMessage, setDownloadMessage] = useState('')
  const downloadNoonReportTemplate = async () => {
    setDownloadMessage('正在準備範本…')
    try {
      const blob = await api.downloadNoonReportTemplate()
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = 'hullwatch-noon-report-template.csv'
      document.body.append(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
      setDownloadMessage('標準 CSV 範本已開始下載。')
    } catch (reason) {
      setDownloadMessage(reason instanceof Error ? reason.message : '範本下載失敗')
    }
  }
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const file = new FormData(event.currentTarget).get('file')
    if (!(file instanceof File) || file.size === 0) return
    setBusy(true)
    setMessage('')
    try {
      const response = await api.importNoonReport(file)
      setResult(response)
      setMessage(`已處理 ${response.summary.rows} 列；接受 ${response.summary.accepted} 列，拒絕 ${response.summary.rejected} 列。`)
      await onUploaded()
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : '上傳失敗')
    } finally {
      setBusy(false)
    }
  }
  return <section className="settings-section"><div className="settings-section-heading"><div><span>CSV IMPORT</span><h3>正午日報批次匯入</h3></div><button className="secondary-action" type="button" onClick={downloadNoonReportTemplate}>下載標準 CSV 範本</button></div><p className="sr-only" aria-live="polite">{downloadMessage}</p><p>每列代表一艘船的一天；正確列會匯入，錯誤列會保留原因。同船同日資料會更新覆蓋。</p><form className="settings-upload" onSubmit={submit}><label className="upload-zone"><Upload size={30} /><strong>選擇標準正午日報 CSV</strong><span>最大 5MB</span><input name="file" type="file" accept=".csv,text/csv" required /></label><button className="primary-action" disabled={busy}>{busy ? '驗證與匯入中…' : '驗證並匯入'}</button></form>{message && <p className="import-message" aria-live="polite">{message}</p>}{result && result.errors.length > 0 && <div className="import-errors"><strong>需要修正的資料列</strong><ul>{result.errors.map((error) => <li key={`${error.row}-${error.message}`}>第 {error.row} 列：{error.message}</li>)}</ul></div>}</section>
}

function TrendTable({ detail, forecasts, visibleModels }: { detail: ShipDetail; forecasts: Record<string, ForecastResponse>; visibleModels: string[] }) {
  return <div className="table-wrap"><table><thead><tr><th>日期</th><th>歷史 SL</th>{visibleModels.map((id) => <th key={id}>{forecasts[id]?.model_name ?? id}</th>)}</tr></thead><tbody>{detail.series.slice(-12).map((point) => <tr key={point.date}><td>{point.date}</td><td>{point.speed_loss.toFixed(2)}%</td>{visibleModels.map((id) => <td key={id}>—</td>)}</tr>)}{(forecasts[visibleModels[0]]?.forecast ?? []).map((point, index) => <tr key={point.date}><td>{point.date}</td><td>—</td>{visibleModels.map((id) => <td key={id}>{forecasts[id]?.forecast[index]?.mid.toFixed(2) ?? '—'}%</td>)}</tr>)}</tbody></table></div>
}

function ScheduleTable({ items, onSelect }: { items: ScheduleItem[]; onSelect: (shipId: string, view?: View) => void }) {
  return <div className="table-wrap"><table><thead><tr><th>船舶</th><th>動作</th><th>建議窗口</th><th>SL 回復</th><th>回本</th><th>每月節省</th><th>遞補船</th></tr></thead><tbody>{items.map((item) => <tr key={item.ship_id}><td><button className="table-link" onClick={() => onSelect(item.ship_id, 'diagnose')}>{item.ship_name}</button></td><td>{item.action}</td><td>{item.window_start}–{item.window_end}</td><td>+{item.speed_loss_recovery_pp}pp</td><td>{item.payback_days ?? '—'} 天</td><td>{money.format(item.monthly_saving_usd)}</td><td>{item.backfill.ship_name}</td></tr>)}</tbody></table></div>
}

function MaintenanceEventTable({ schedule }: { schedule: ScheduleResponse }) {
  const ships = new Map(schedule.recommendations.map((item) => [item.ship_id, item.ship_name]))
  const rows = [
    ...schedule.maintenance_events.filter((event) => event.type !== 'DD'),
    ...schedule.dry_docks.map((event) => ({ ship_id: event.ship_id, date: event.date, type: 'DD', notes: '既定乾塢事件' })),
  ].sort((a, b) => a.date.localeCompare(b.date))
  return <div className="table-wrap"><table><caption>歷史維護與既定乾塢事件</caption><thead><tr><th>日期</th><th>船舶</th><th>事件</th><th>附註</th></tr></thead><tbody>{rows.map((event, index) => <tr key={`${event.ship_id}-${event.date}-${event.type}-${index}`}><td>{event.date}</td><td>{ships.get(event.ship_id) ?? event.ship_id}</td><td>{event.type}</td><td>{event.notes || '—'}</td></tr>)}</tbody></table></div>
}

function FuelTicker({ fuel, paused, setPaused }: { fuel: FuelPriceResponse; paused: boolean; setPaused: (paused: boolean) => void }) {
  const statusLabel = { live: '最新', cached: '快取', stale: '資料延遲', unavailable: '行情暫無' }[fuel.market_status]
  const content = fuel.prices.length ? [...fuel.prices, ...fuel.prices] : []
  return <div className={`fuel-ticker market-${fuel.market_status}`} role="region" aria-label="船用燃油價格跑馬燈"><strong><Fuel size={18} /><span>FUEL WATCH<small>{fuel.port} · {statusLabel}</small></span></strong><div className={paused ? 'paused' : ''}><div>{content.length ? content.map((price, index) => <span key={`${price.grade}-${index}`}><b>{price.grade}</b> ${number.format(price.usd_per_ton)} <small>USD/mt · {price.as_of}</small>{price.estimated && <em>EST</em>}</span>) : <span>市場來源暫時無法取得；決策頁使用明確標示的手動情境價。</span>}</div></div><button onClick={() => setPaused(!paused)} aria-pressed={paused}>{paused ? <Play size={16} /> : <Pause size={16} />}<span>{paused ? '繼續' : '暫停'}</span></button></div>
}

function AlertDrawer({ alerts, open, onClose, onSelect }: { alerts: AlertsResponse | null; open: boolean; onClose: () => void; onSelect: (alertId: string, shipId: string) => void }) {
  const [width, setWidth] = useState(420)
  const startResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId)
    const move = (moveEvent: PointerEvent) => setWidth(Math.max(320, Math.min(window.innerWidth * .5, window.innerWidth - moveEvent.clientX)))
    const stop = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', stop) }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', stop)
  }
  const changeWidth = (delta: number) => setWidth((current) => Math.max(320, Math.min(window.innerWidth * .5, current + delta)))
  return <aside className={`alert-drawer ${open ? 'open' : ''}`} style={{ width: `min(${width}px, 95vw)` }} aria-hidden={!open} inert={!open} aria-label="警報中心"><div className="drawer-resizer" role="separator" aria-label="調整警報側欄寬度" aria-orientation="vertical" tabIndex={open ? 0 : -1} onPointerDown={startResize} onKeyDown={(event) => { if (event.key === 'ArrowLeft') changeWidth(24); if (event.key === 'ArrowRight') changeWidth(-24) }} /><div className="drawer-heading"><div><span>ALERT CENTER</span><h2>警報中心</h2></div><button onClick={onClose} aria-label="關閉警報中心"><X /></button></div><div className="channel-state"><span>站內 ●</span><span>SES {alerts?.channels.ses === 'configured' ? '●' : '○'}</span><span>Discord {alerts?.channels.discord === 'configured' ? '●' : '○'}</span></div>{alerts?.alerts.map((alert) => <button className={`alert-item ${alert.read ? 'read' : ''}`} key={alert.id} onClick={() => onSelect(alert.id, alert.ship_id)}><span>{alert.severity === 'critical' ? <AlertTriangle /> : <Bell />}</span><strong>{alert.ship_name}</strong><p>{alert.message}</p><small>{alert.created_at}</small></button>)}{!alerts?.alerts.length && <div className="empty-state"><CheckCircle2 />目前沒有警報</div>}</aside>
}

function ToolDialog({ tool, onClose, shipId, alerts, fuel, models, onModelsChanged, onReportsImported }: { tool: Tool; onClose: () => void; shipId: string; alerts: AlertsResponse | null; fuel: FuelPriceResponse | null; models: ModelInfo[]; onModelsChanged: () => Promise<void>; onReportsImported: () => Promise<void> }) {
  if (!tool) return null
  return <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }} onKeyDown={(event) => { if (event.key === 'Escape') onClose() }}><section className={`tool-dialog ${tool === 'settings' ? 'settings-dialog' : ''}`} role="dialog" aria-modal="true" aria-labelledby="tool-title"><div className="drawer-heading"><div><span>HULLWATCH TOOL</span><h2 id="tool-title">{tool === 'advisor' ? 'AI 顧問' : tool === 'inspect' ? '水下判讀' : '系統設定'}</h2></div><button onClick={onClose} aria-label="關閉" autoFocus><X /></button></div>{tool === 'advisor' && <AdvisorTool />}{tool === 'inspect' && <InspectTool shipId={shipId} />}{tool === 'settings' && <SettingsTool alerts={alerts} fuel={fuel} models={models} onModelsChanged={onModelsChanged} onReportsImported={onReportsImported} />}</section></div>
}

function AdvisorTool() {
  const [question, setQuestion] = useState('這一季哪幾艘船該優先清洗？為什麼？')
  const [answer, setAnswer] = useState<AdvisorResponse | null>(null)
  const [busy, setBusy] = useState(false)
  const submit = async (event: FormEvent) => { event.preventDefault(); setBusy(true); try { setAnswer(await api.advisor(question)) } finally { setBusy(false) } }
  return <form className="tool-body" onSubmit={submit}><label>用白話詢問船隊資料<textarea value={question} onChange={(event) => setQuestion(event.target.value)} rows={3} /></label><button className="primary-action" disabled={busy || !question.trim()}>{busy ? '查詢資料中…' : '詢問 Bedrock 顧問'}</button>{answer && <article className="advisor-answer" aria-live="polite"><span>{answer.mode.toUpperCase()} MODE</span><p>{answer.answer}</p>{answer.citations.length > 0 && <small>來源：{answer.citations.join('、')}</small>}</article>}</form>
}

function InspectTool({ shipId }: { shipId: string }) {
  const [result, setResult] = useState<Record<string, unknown> | null>(null)
  const [busy, setBusy] = useState(false)
  const submit = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const file = new FormData(event.currentTarget).get('image'); if (!(file instanceof File) || file.size === 0) return; setBusy(true); try { setResult(await api.inspect(shipId, file)) } finally { setBusy(false) } }
  return <form className="tool-body" onSubmit={submit}><label className="upload-zone"><ImageUp size={36} /><strong>上傳船殼水下照片</strong><span>JPEG / PNG，最大 8MB</span><input name="image" type="file" accept="image/jpeg,image/png" required /></label><button className="primary-action" disabled={busy}>{busy ? 'Bedrock 判讀中…' : '開始判讀'}</button>{result && <pre className="inspection-result">{JSON.stringify(result, null, 2)}</pre>}</form>
}

function SettingsTool({ alerts, fuel, models, onModelsChanged, onReportsImported }: { alerts: AlertsResponse | null; fuel: FuelPriceResponse | null; models: ModelInfo[]; onModelsChanged: () => Promise<void>; onReportsImported: () => Promise<void> }) {
  const [section, setSection] = useState<'data' | 'models' | 'sources' | 'notifications' | 'interface'>('data')
  return <div className="settings-layout"><nav aria-label="設定分類"><button className={section === 'data' ? 'active' : ''} onClick={() => setSection('data')}>資料匯入</button><button className={section === 'models' ? 'active' : ''} onClick={() => setSection('models')}>模型管理</button><button className={section === 'sources' ? 'active' : ''} onClick={() => setSection('sources')}>資料來源</button><button className={section === 'notifications' ? 'active' : ''} onClick={() => setSection('notifications')}>電子報訂閱</button><button className={section === 'interface' ? 'active' : ''} onClick={() => setSection('interface')}>介面</button></nav><div className="settings-content">{section === 'data' && <NoonReportUpload onUploaded={onReportsImported} />}{section === 'models' && <ModelManager models={models} onChanged={onModelsChanged} />}{section === 'sources' && <section className="settings-section"><div className="settings-section-heading"><div><span>MARKET DATA</span><h3>油價來源與狀態</h3></div></div><div className="settings-list"><article><strong>目前狀態</strong><span>{fuel?.market_status ?? 'unknown'}</span></article><article><strong>市場</strong><span>{fuel?.port ?? '—'}</span></article><article><strong>更新策略</strong><span>每 {fuel?.refresh_interval_hours ?? 6} 小時；{fuel?.stale_after_hours ?? 24} 小時後標示延遲</span></article><article><strong>行情來源</strong><span>Ship & Bunker Singapore／USDA Open Ag Transport Data</span></article></div></section>}{section === 'notifications' && <NotificationManager />}{section === 'interface' && <section className="settings-section"><div className="settings-list"><article><strong>資料模式</strong><span>LIVE · FastAPI</span></article><article><strong>主題</strong><span>由頂部切換</span></article><article><strong>通知通道</strong><span>SES：{alerts?.channels.ses} · Discord：{alerts?.channels.discord}</span></article></div></section>}</div></div>
}

function NotificationManager() {
  const [data, setData] = useState<NotificationSubscriptionsResponse | null>(null)
  const [channel, setChannel] = useState<'email' | 'discord'>('email')
  const [email, setEmail] = useState('')
  const [shipIds, setShipIds] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const load = async () => setData(await api.notificationSubscriptions())
  useEffect(() => { load().catch((reason: unknown) => setMessage(reason instanceof Error ? reason.message : '訂閱資料載入失敗')) }, [])
  const toggleShip = (shipId: string) => setShipIds((current) => current.includes(shipId) ? current.filter((id) => id !== shipId) : [...current, shipId])
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setMessage('')
    try {
      await api.createNotificationSubscription({ channel, destination: channel === 'email' ? email : undefined, ship_ids: shipIds })
      setEmail(''); setShipIds([]); await load(); setMessage('訂閱已儲存。')
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : '訂閱儲存失敗') } finally { setBusy(false) }
  }
  const send = async (id: string) => {
    setBusy(true); setMessage('')
    try {
      const result = await api.sendNotificationDigest(id)
      setMessage(result.delivered ? '船隊摘要已送出。' : '通道尚未設定，請先完成後端環境變數。')
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : '摘要發送失敗') } finally { setBusy(false) }
  }
  const remove = async (id: string) => {
    setBusy(true); setMessage('')
    try { await api.deleteNotificationSubscription(id); await load(); setMessage('訂閱已刪除。') }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : '訂閱刪除失敗') } finally { setBusy(false) }
  }
  return <section className="settings-section notification-settings"><div className="settings-section-heading"><div><span>NEWSLETTER</span><h3>Email／Discord 電子報訂閱</h3></div></div><p>每筆訂閱可選擇關注船舶。Email 寄到個別地址；Discord 使用系統設定的單一頻道 webhook。</p><div className="channel-readiness" aria-label="通知通道狀態"><span>SES {data?.channels.ses === 'configured' ? '● 已設定' : '○ 未設定'}</span><span>Discord {data?.channels.discord === 'configured' ? '● 已設定' : '○ 未設定'}</span></div><form onSubmit={submit}><fieldset><legend>通知通道</legend><label><input type="radio" name="channel" checked={channel === 'email'} onChange={() => setChannel('email')} />Email</label><label><input type="radio" name="channel" checked={channel === 'discord'} onChange={() => setChannel('discord')} />Discord</label></fieldset>{channel === 'email' && <label>Email 收件地址<input type="email" value={email} onChange={(event) => setEmail(event.target.value)} required autoComplete="email" /></label>}<fieldset className="ship-subscriptions"><legend>選擇要訂閱的船隻</legend>{data?.available_ships.map((ship) => <label key={ship.ship_id}><input type="checkbox" checked={shipIds.includes(ship.ship_id)} onChange={() => toggleShip(ship.ship_id)} /> <span>{ship.ship_name}<small>{ship.ship_id}</small></span></label>)}</fieldset><button className="primary-action" disabled={busy || shipIds.length === 0}>新增訂閱</button></form>{message && <p className="import-message" aria-live="polite">{message}</p>}<div className="subscription-list"><h4>現有訂閱</h4>{data?.subscriptions.map((subscription) => <article key={subscription.id}><div><strong>{subscription.channel === 'email' ? 'Email' : 'Discord'} · {subscription.destination_masked}</strong><span>{subscription.ship_ids.join('、')}</span></div><div><button type="button" onClick={() => send(subscription.id)} disabled={busy}>寄送目前摘要</button><button type="button" onClick={() => remove(subscription.id)} disabled={busy}>刪除</button></div></article>)}{data && data.subscriptions.length === 0 && <p>目前沒有訂閱。</p>}</div></section>
}

function ModelManager({ models, onChanged }: { models: ModelInfo[]; onChanged: () => Promise<void> }) {
  const [manifest, setManifest] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (!manifest) api.modelTemplate().then((value) => setManifest(JSON.stringify(value, null, 2))) }, [manifest])
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const artifact = new FormData(event.currentTarget).get('artifact')
    if (!(artifact instanceof File) || artifact.size === 0) return
    setBusy(true); setMessage('')
    try {
      const result = await api.uploadModel(manifest, artifact)
      setMessage(result.validation?.passed ? '候選模型已通過共同驗證集，可手動啟用。' : `模型已完成驗證但未通過門檻：MAE ${result.validation?.candidate_mae ?? '—'}。`)
      await onChanged()
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : '模型上傳失敗') } finally { setBusy(false) }
  }
  const activate = async (id: string) => { setBusy(true); try { await api.activateModel(id); await onChanged(); setMessage(`已啟用 ${id}`) } catch (reason) { setMessage(reason instanceof Error ? reason.message : '啟用失敗') } finally { setBusy(false) } }
  const restore = async () => { setBusy(true); try { await api.restoreModel(); await onChanged(); setMessage('已回復內建線性結垢趨勢模型。') } finally { setBusy(false) } }
  return <section className="settings-section"><div className="settings-section-heading"><div><span>MODEL REGISTRY</span><h3>Speed Loss 趨勢模型</h3></div><button className="secondary-action" onClick={restore} disabled={busy}>回復內建模型</button></div><div className="model-registry">{models.map((model) => <article key={model.id} className={model.is_primary ? 'active' : ''}><div><strong>{model.name}</strong><small>{model.id} · {model.version ?? 'builtin'} · {model.model_format ?? 'builtin'}</small></div><span>{model.is_primary ? '使用中' : model.status ?? '可用'}</span>{model.validation && <p>候選 MAE {model.validation.candidate_mae}／現行 {model.validation.current_model_mae} · {model.validation.rows} 筆</p>}{model.status === 'validated' && !model.is_primary && <button onClick={() => activate(model.id)} disabled={busy}>啟用</button>}</article>)}</div><form className="model-upload" onSubmit={submit}><label>模型 manifest<textarea rows={13} value={manifest} onChange={(event) => setManifest(event.target.value)} spellCheck={false} /></label><label className="upload-zone"><Upload size={30} /><strong>選擇 XGBoost JSON 模型</strong><span>第一版只接受資料型模型檔；不接受 pickle/joblib</span><input name="artifact" type="file" accept=".json,application/json" required /></label><button className="primary-action" disabled={busy}>{busy ? '檢查與驗證中…' : '上傳為候選模型'}</button></form>{message && <p className="import-message" aria-live="polite">{message}</p>}</section>
}

function LoadingScreen() { return <div className="loading-screen"><span className="brand-mark"><Ship /></span><strong>HULLWATCH</strong><p>載入船隊效能資料…</p></div> }
function InlineLoading({ label }: { label: string }) { return <div className="inline-loading" role="status"><span />{label}…</div> }

export default App
