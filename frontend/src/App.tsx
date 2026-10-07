import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

type Status = any

type Trade = {
  id: number
  symbol: string
  direction: string
  entry: number
  stop: number
  target: number
  quantity: number
  lotSize: number
  currentPrice: number
  pnl: number
  status: string
  meta?: Record<string, any>
  openedAt?: string | null
  closedAt?: string | null
  chartUrl?: string | null
  chartSymbol?: string | null
  displayName?: string | null
  expiry?: string | null
  strike?: number | null
  priceSource?: string | null
  quoteTime?: string | null
}

const API = import.meta.env.VITE_API_URL || 'http://localhost:8000'
const INR = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

function money(value: unknown) {
  const n = Number(value ?? 0)
  return INR.format(Number.isFinite(n) ? n : 0)
}

function percent(value: unknown) {
  const n = Number(value ?? 0)
  return `${Number.isFinite(n) ? n.toFixed(2) : '0.00'}%`
}

function formatDateTime(value?: string | null) {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString('en-IN', {
    timeZone: 'Asia/Kolkata',
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

function formatClock(value: Date) {
  return value.toLocaleString('en-IN', {
    timeZone: 'Asia/Kolkata',
    weekday: 'short',
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

function instrumentName(symbol: string) {
  const parts = String(symbol || '').split('|')
  if (parts[0] === 'SIM' && parts.length >= 4) return `${parts[1]} ${parts[3]} ${parts[2]}`
  return symbol || 'Unknown'
}

function tradePnlPct(t: Trade) {
  const deployed = Math.abs(Number(t.entry || 0) * Number(t.quantity || 0))
  return deployed > 0 ? Number(t.pnl || 0) / deployed * 100 : 0
}

function pnlClass(value: unknown) {
  const n = Number(value ?? 0)
  return n > 0 ? 'positive' : n < 0 ? 'negative' : 'neutral'
}

function urlBase64ToUint8Array(value: string) {
  const padding = '='.repeat((4 - value.length % 4) % 4)
  const base64 = (value + padding).replace(/-/g, '+').replace(/_/g, '/')
  const raw = window.atob(base64)
  return Uint8Array.from([...raw].map(char => char.charCodeAt(0)))
}

let apexAudioContext: AudioContext | null = null

async function playApexTradeChime(soft = false) {
  try {
    apexAudioContext = apexAudioContext || new AudioContext()
    if (apexAudioContext.state === 'suspended') await apexAudioContext.resume()

    const ctx = apexAudioContext
    const start = ctx.currentTime + 0.015
    const master = ctx.createGain()
    master.gain.setValueAtTime(soft ? 0.035 : 0.055, start)
    master.connect(ctx.destination)

    const strike = (frequency: number, at: number, duration: number, gainScale = 1) => {
      const osc = ctx.createOscillator()
      const harmonic = ctx.createOscillator()
      const gain = ctx.createGain()
      const harmonicGain = ctx.createGain()

      osc.type = 'sine'
      harmonic.type = 'triangle'
      osc.frequency.setValueAtTime(frequency, at)
      harmonic.frequency.setValueAtTime(frequency * 2.01, at)

      gain.gain.setValueAtTime(0.0001, at)
      gain.gain.exponentialRampToValueAtTime(0.72 * gainScale, at + 0.012)
      gain.gain.exponentialRampToValueAtTime(0.0001, at + duration)

      harmonicGain.gain.setValueAtTime(0.0001, at)
      harmonicGain.gain.exponentialRampToValueAtTime(0.12 * gainScale, at + 0.008)
      harmonicGain.gain.exponentialRampToValueAtTime(0.0001, at + duration * 0.72)

      osc.connect(gain)
      harmonic.connect(harmonicGain)
      gain.connect(master)
      harmonicGain.connect(master)

      osc.start(at)
      harmonic.start(at)
      osc.stop(at + duration + 0.03)
      harmonic.stop(at + duration + 0.03)
    }

    // Short two-note major interval: clean bell/chime, not an alarm.
    strike(659.25, start, 0.34, 0.92)
    strike(987.77, start + 0.19, 0.42, 0.78)

    window.setTimeout(() => {
      try { master.disconnect() } catch { /* no-op */ }
    }, 900)
  } catch {
    // Audio is enhancement-only; never break notifications/trading.
  }
}

async function fetchJson(path: string, options?: RequestInit) {
  const response = await fetch(`${API}${path}`, options)
  let data: any = null
  try { data = await response.json() } catch { /* no-op */ }
  if (!response.ok) {
    throw new Error(data?.detail || data?.message || `Request failed (${response.status})`)
  }
  return data
}

export default function App() {
  const [status, setStatus] = useState<Status>(null)
  const [trades, setTrades] = useState<Trade[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')
  const [notice, setNotice] = useState<{kind:'ok'|'error', text:string} | null>(null)
  const [lastSync, setLastSync] = useState<Date | null>(null)
  const [clock, setClock] = useState(new Date())
  const [editingTradeId, setEditingTradeId] = useState<number | null>(null)
  const [planDraft, setPlanDraft] = useState({ stop: '', target: '' })
  const [pushConfig, setPushConfig] = useState<any>(null)
  const [pushPermission, setPushPermission] = useState<NotificationPermission>('default')
  const [pushSubscribed, setPushSubscribed] = useState(false)
  const [pushBusy, setPushBusy] = useState(false)
  const [pushError, setPushError] = useState('')
  const [chartIntel, setChartIntel] = useState<any>(null)
  const [chartView, setChartView] = useState<'tradingview' | 'apex'>('tradingview')
  const previousOpenTradeIdsRef = useRef<Set<number> | null>(null)

  const loadStatus = useCallback(async (silent = false) => {
    try {
      const data = await fetchJson('/api/system/status')
      setStatus(data)
      if (!silent) setError('')
    } catch (e: any) {
      setError(e?.message || 'Could not load system status')
    }
  }, [])

  const loadTrades = useCallback(async (silent = false) => {
    try {
      const data = await fetchJson('/api/trades?limit=100')
      setTrades(Array.isArray(data) ? data : [])
      setLastSync(new Date())
      if (!silent) setError('')
    } catch (e: any) {
      setError(e?.message || 'Could not load trades')
    }
  }, [])

  const loadChartIntel = useCallback(async (silent = false) => {
    try {
      const data = await fetchJson('/api/market/chart-intelligence')
      setChartIntel(data)
      if (!silent) setError('')
    } catch (e: any) {
      if (!silent) setError(e?.message || 'Could not load chart intelligence')
    }
  }, [])

  const loadAll = useCallback(async () => {
    await Promise.all([loadStatus(), loadTrades(), loadChartIntel()])
  }, [loadStatus, loadTrades, loadChartIntel])

  useEffect(() => {
    void loadAll()
    const tradePoll = window.setInterval(() => void loadTrades(true), 1000)
    const statusPoll = window.setInterval(() => void loadStatus(true), 3000)
    const chartPoll = window.setInterval(() => void loadChartIntel(true), 5000)
    const clockPoll = window.setInterval(() => setClock(new Date()), 1000)
    return () => {
      window.clearInterval(tradePoll)
      window.clearInterval(statusPoll)
      window.clearInterval(chartPoll)
      window.clearInterval(clockPoll)
    }
  }, [loadAll, loadStatus, loadTrades, loadChartIntel])

  useEffect(() => {
    let cancelled = false
    async function syncPushState() {
      const supported = 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window
      if (!supported) {
        if (!cancelled) setPushConfig({ configured: false, supported: false, subscriberCount: 0 })
        return
      }
      try {
        const config = await fetchJson('/api/notifications/config')
        const registration = await navigator.serviceWorker.register('/sw.js')
        const subscription = await registration.pushManager.getSubscription()
        if (!cancelled) {
          setPushConfig({ ...config, supported: true })
          setPushPermission(Notification.permission)
          setPushSubscribed(Boolean(subscription))
          setPushError('')
        }
      } catch (e: any) {
        if (!cancelled) setPushError(e?.message || 'Could not initialize trade alerts')
      }
    }
    void syncPushState()
    return () => { cancelled = true }
  }, [])

  async function enableTradeAlerts() {
    if (pushBusy) return
    setPushBusy(true)
    setPushError('')
    try {
      if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) {
        throw new Error('This browser does not support background web push notifications.')
      }
      const config = pushConfig?.publicKey ? pushConfig : await fetchJson('/api/notifications/config')
      setPushConfig({ ...config, supported: true })
      if (!config?.configured || !config?.publicKey) {
        throw new Error('Trade alerts are not configured on the server yet.')
      }

      const permission = await Notification.requestPermission()
      setPushPermission(permission)
      if (permission !== 'granted') {
        throw new Error('Notification permission was not granted.')
      }

      const registration = await navigator.serviceWorker.register('/sw.js')
      await navigator.serviceWorker.ready
      let subscription = await registration.pushManager.getSubscription()
      if (!subscription) {
        subscription = await registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(config.publicKey),
        })
      }

      const saved = await fetchJson('/api/notifications/subscribe', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify(subscription.toJSON()),
      })
      setPushConfig({ ...saved, supported: true })
      setPushSubscribed(true)
      await playApexTradeChime(true)
      await registration.showNotification('APEX Trade Alerts enabled', {
        body: 'You will be notified when APEX executes a new paper trade.',
        icon: '/favicon.ico',
        badge: '/favicon.ico',
        tag: 'apex-alerts-enabled',
      })
    } catch (e: any) {
      setPushError(e?.message || 'Could not enable trade alerts')
    } finally {
      setPushBusy(false)
    }
  }

  async function disableTradeAlerts() {
    if (pushBusy) return
    setPushBusy(true)
    setPushError('')
    try {
      if (!('serviceWorker' in navigator)) return
      const registration = await navigator.serviceWorker.register('/sw.js')
      const subscription = await registration.pushManager.getSubscription()
      if (subscription) {
        const saved = await fetchJson('/api/notifications/unsubscribe', {
          method: 'POST',
          headers: {'Content-Type':'application/json'},
          body: JSON.stringify({ endpoint: subscription.endpoint }),
        })
        await subscription.unsubscribe()
        setPushConfig({ ...saved, supported: true })
      }
      setPushSubscribed(false)
    } catch (e: any) {
      setPushError(e?.message || 'Could not disable trade alerts')
    } finally {
      setPushBusy(false)
    }
  }

  async function testTradeAlert() {
    setPushError('')
    try {
      if (Notification.permission !== 'granted') throw new Error('Enable notifications first.')
      const registration = await navigator.serviceWorker.register('/sw.js')
      await playApexTradeChime(false)
      await registration.showNotification('APEX Test Trade Alert', {
        body: 'SENSEX CE • Qty 20 • Entry alert test\nBackground notifications are working on this device.',
        icon: '/favicon.ico',
        badge: '/favicon.ico',
        tag: 'apex-test-alert',
        requireInteraction: true,
      })
    } catch (e: any) {
      setPushError(e?.message || 'Could not show test alert')
    }
  }

  async function runAction(key: string, successText: string, action: () => Promise<any>) {
    if (busy) return
    setBusy(key)
    setNotice(null)
    try {
      await action()
      setNotice({ kind: 'ok', text: successText })
      await loadAll()
    } catch (e: any) {
      setNotice({ kind: 'error', text: e?.message || 'Action failed' })
    } finally {
      setBusy('')
    }
  }

  async function doTradeNow() {
    if (busy) return
    const config = automation?.manualDoTrade || {}
    const confirmed = window.confirm(
      [
        '⚠ DANGER — MANUAL DO TRADE (PAPER ONLY)',
        '',
        `APEX will scan fresh SENSEX momentum + SMC + 1m/5m/15m structure and may immediately open one option trade.`,
        `Target: +${config.targetPoints ?? 30} option points`,
        `Stop: -${config.stopPoints ?? 15} option points`,
        `Capital request: up to ${config.capitalUsagePct ?? 100}% (hard loss-lock headroom can reduce quantity)`,
        `Carry forward: ${config.carryForward ? 'YES, only for this button trade' : 'NO'}`,
        `New trade cutoff: ${config.cutoffTime ?? '15:20'} IST`,
        '',
        'This does NOT guarantee a trade; stale/weak/conflicting setups are refused.',
        '',
        'Continue?',
      ].join('\n'),
    )
    if (!confirmed) return

    setBusy('do-trade')
    setNotice(null)
    try {
      const result = await fetchJson('/api/paper/do-trade', { method: 'POST' })
      const trade = result?.trade || {}
      const danger = result?.danger || {}
      setNotice({
        kind: 'ok',
        text: `DO TRADE executed #${trade.id ?? '—'} ${trade.direction ?? ''} • Qty ${trade.quantity ?? '—'} • deployed ${money(danger.deployedCapital)} • risk ${money(danger.initialRisk)}`,
      })
      await loadAll()
    } catch (e: any) {
      setNotice({ kind: 'error', text: e?.message || 'Do Trade was refused by safety/market gates' })
    } finally {
      setBusy('')
    }
  }

  function beginPlanEdit(t: Trade) {
    setEditingTradeId(t.id)
    setPlanDraft({ stop: String(t.stop), target: String(t.target) })
  }

  async function savePlan(t: Trade) {
    await runAction(`plan-${t.id}`, 'SL / Target updated.', () => fetchJson(`/api/paper/trades/${t.id}/plan`, {
      method: 'PATCH',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ stop: Number(planDraft.stop), target: Number(planDraft.target) }),
    }))
    setEditingTradeId(null)
  }

  async function exitTrade(t: Trade) {
    if (!window.confirm(`Exit paper trade #${t.id} now?`)) return
    await runAction(`exit-${t.id}`, 'Paper trade exited manually.', () => fetchJson(`/api/paper/trades/${t.id}/exit`, {method:'POST'}))
    setEditingTradeId(null)
  }

  const openTrades = useMemo(() => trades.filter(t => t.status === 'OPEN'), [trades])

  useEffect(() => {
    const currentIds = new Set(openTrades.map(t => t.id))
    const previousIds = previousOpenTradeIdsRef.current
    if (previousIds) {
      const newlyOpened = openTrades.filter(t => !previousIds.has(t.id))
      if (newlyOpened.length > 0 && pushSubscribed && pushPermission === 'granted') {
        void playApexTradeChime(false)
      }
    }
    previousOpenTradeIdsRef.current = currentIds
  }, [openTrades, pushPermission, pushSubscribed])

  const closedTrades = useMemo(() => trades.filter(t => t.status !== 'OPEN'), [trades])
  const invalidClosedTrades = useMemo(() => closedTrades.filter(t => t.status === 'INVALID_CONTRACT'), [closedTrades])
  const validatedClosedTrades = useMemo(() => closedTrades.filter(t => t.status !== 'INVALID_CONTRACT'), [closedTrades])
  const runningPnl = useMemo(() => openTrades.reduce((sum, t) => sum + Number(t.pnl || 0), 0), [openTrades])
  const runningDeployed = useMemo(
    () => openTrades.reduce((sum, t) => sum + Math.abs(Number(t.entry || 0) * Number(t.quantity || 0)), 0),
    [openTrades],
  )
  const runningPnlPct = runningDeployed > 0 ? runningPnl / runningDeployed * 100 : 0

  if (!status) {
    return <main className="shell loading">
      <div className="brand"><span className="brand-mark">A</span><div><h1>APEX Algo AI</h1><p>Connecting to trading engine…</p></div></div>
      {error && <div className="notice error">{error}</div>}
    </main>
  }

  const p = status.performance || {}
  const r = status.risk || {}
  const learning = status.learning || {}
  const mt = r.monthlyTarget || {}
  const market = status.market || {}
  const automation = status.automation || {}
  const marketResearch = status.marketResearch || {}
  const researchMarkets = marketResearch.analytics?.markets || {}
  const positionMonitor = automation.positionMonitor || {}
  const worker = status.learningWorker || {}
  const ollama = status.ollama || {}
  const liveLearning = status.liveLearning || {}
  const liveMoments = Array.isArray(liveLearning.latest) ? liveLearning.latest : []
  const intelligence = status.researchIntelligence || {}
  const neuralModel = status.neuralModel || worker.neural || {}
  const datasetModel = status.datasetModel || {}
  const patternInsights = Array.isArray(datasetModel.patternInsights) ? datasetModel.patternInsights : []
  const newsHealth = intelligence.newsHealth || worker.newsHealth || {}
  const latestNews = Array.isArray(intelligence.latestNews) ? intelligence.latestNews : []
  const researchActivities = Array.isArray(intelligence.activities) ? intelligence.activities : []
  const readingQueue = Array.isArray(intelligence.readingQueue) ? intelligence.readingQueue : []
  const neuralProduction = neuralModel.production || {}
  const neuralLatest = neuralModel.latest || {}
  const activeNeural = Object.keys(neuralProduction).length ? neuralProduction : neuralLatest
  const neuralMetrics = activeNeural.metrics || {}
  const neuralOos = neuralMetrics.outOfSample || {}
  const trainingReadiness = neuralModel.trainingReadiness || {}
  const rewardEvents = Array.isArray(learning.recentRewards) ? learning.recentRewards : []
  const nextPlan = intelligence.latestPlan || {}
  const researchSources = Array.isArray(intelligence.sources) ? intelligence.sources : []
  const hypotheses = Array.isArray(intelligence.hypotheses) ? intelligence.hypotheses : []
  const sectors = Array.isArray(intelligence.latestSectors) ? intelligence.latestSectors : []
  const providerReady = market.marketDataConfigured !== false
  const killed = Boolean(status.killSwitch || r.killSwitch)
  const phase = automation.lastDecision?.phase || {}
  const manualDoTrade = automation.manualDoTrade || {}
  const freshnessMap = automation.marketDataFreshness || {}
  const latestSignal = Array.isArray(automation.lastDecision?.signals) ? automation.lastDecision.signals[0] : null
  const sensexFreshness = freshnessMap.SENSEX || latestSignal?.dataFreshness || {}
  const freshnessState = String(sensexFreshness.state || 'UNKNOWN').toUpperCase()
  const feedStale = ['STALE', 'MISSING', 'INVALID_TIMESTAMP'].includes(freshnessState)
  const feedDelayed = freshnessState === 'DELAYED'
  const marketResearchOnly = Boolean(phase?.researchPhase && !phase?.tradable && !phase?.liveScan)
  const showFeedWarning = !marketResearchOnly && (feedStale || feedDelayed)
  const lastMoment = liveMoments[0] || {}
  const lastVerifiedTrend = String(
    latestSignal?.context?.trend ||
    lastMoment?.context?.trend ||
    researchMarkets?.SENSEX?.frames?.['1m']?.trend ||
    'UNKNOWN'
  ).toUpperCase()
  const targetAmount = Number(mt.targetAmount || 0)
  const monthPnl = Number(mt.monthPnl || 0)
  const targetProgress = targetAmount > 0 ? Math.max(0, Math.min(100, monthPnl / targetAmount * 100)) : 0

  const scanDisabled = Boolean(busy || killed || status.mode !== 'PAPER' || !providerReady)
  const doTradeDisabled = Boolean(
    busy ||
    killed ||
    status.mode !== 'PAPER' ||
    !providerReady ||
    feedStale ||
    !manualDoTrade.enabled ||
    !manualDoTrade.timeEligible ||
    openTrades.length > 0
  )
  const flattenDisabled = Boolean(busy || openTrades.length === 0)
  const killDisabled = Boolean(busy || killed)
  const resetDisabled = Boolean(busy || !killed)

  return <main className="shell">
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark">A</span>
        <div>
          <h1>APEX Algo AI</h1>
          <p>Paper trading control room • Indian index options</p>
        </div>
      </div>
      <div className="top-meta">
        <div className="clock">{formatClock(clock)} IST</div>
        <div className="status-line">
          <span className={`dot ${automation.running ? 'online' : 'offline'}`} />
          Engine {automation.running ? 'Online' : 'Stopped'}
          <b className={`mode-pill ${status.mode}`}>{status.mode}</b>
        </div>
      </div>
    </header>

    {error && <div className="notice error">Connection issue: {error}</div>}
    {notice && <div className={`notice ${notice.kind === 'ok' ? 'ok' : 'error'}`} aria-live="polite">{notice.text}</div>}

    {market.syntheticPaper && <section className="demo-banner">
      <div>
        <b>Testing / Synthetic Paper Mode</b>
        <span>Yahoo supplies index data; option premium and execution are simulated. Live entries now require a fresh candle; stale Yahoo data is blocked from trading.</span>
      </div>
      <span className={`badge ${!marketResearchOnly && feedStale ? 'freshness-bad' : !marketResearchOnly && feedDelayed ? 'freshness-warn' : ''}`}>
        {marketResearchOnly ? 'MARKET CLOSED • RESEARCH MODE' : feedStale ? 'STALE FEED • NO ENTRY' : feedDelayed ? 'DELAYED FEED • PAPER' : 'FRESHNESS GUARD ON • PAPER'}
      </span>
    </section>}

    {showFeedWarning && <section className={`freshness-banner ${feedStale ? 'stale' : 'delayed'}`}>
      <div className="freshness-icon">{feedStale ? '!' : '◷'}</div>
      <div>
        <b>{feedStale ? 'Market data too old for a new trade' : 'Market data is delayed but still inside the live-entry limit'}</b>
        <span>
          SENSEX candle {sensexFreshness.candleTime ? formatDateTime(sensexFreshness.candleTime) : '—'}
          {' • '}age {sensexFreshness.ageSeconds != null ? `${Math.max(0, Number(sensexFreshness.ageSeconds)).toFixed(0)}s` : '—'}
          {' • '}max {sensexFreshness.maxAgeSeconds ?? '—'}s.
          {feedStale ? ' APEX will not score or execute this candle.' : ''}
        </span>
      </div>
      <strong>{freshnessState}</strong>
    </section>}

    <section className="summary-grid">
      <Metric title="Running P&L" value={money(runningPnl)} sub={openTrades.length ? `${runningPnlPct >= 0 ? '+' : ''}${percent(runningPnlPct)} on open positions` : 'No open position'} tone={pnlClass(runningPnl)} />
      <Metric title="Validated Realized P&L" value={money(p.netPnl)} sub={`${p.trades || 0} validated closed trades`} tone={pnlClass(p.netPnl)} />
      <Metric title="Effective Capital" value={money(r.effectiveCapital)} sub={`Base ${money(r.configuredCapital)}`} />
      <Metric title="Open Positions" value={String(openTrades.length)} sub={`Max ${r.maxConcurrentPositions ?? 0}`} />
      <Metric title="Validated Today P&L" value={money(r.dailyPnl)} sub={`${r.tradesToday || 0} valid trade(s) today`} tone={pnlClass(r.dailyPnl)} />
      <Metric title="Excluded / Invalid P&L" value={money(r.excludedInvalidTodayPnl)} sub={`${r.excludedInvalidTodayCount || 0} invalid trade(s) excluded today`} tone={pnlClass(r.excludedInvalidTodayPnl)} />
      <Metric title="Win Rate" value={percent(p.winRate)} sub={`${p.wins || 0}W / ${p.losses || 0}L`} />
      <Metric title="Risk / Trade" value={money(r.riskPerTrade)} sub={`${r.dynamicLimits ? 'Dynamic' : 'Fixed'} risk limit`} />
      <Metric title="Deployable Cap" value={money(Number(r.effectiveCapital || 0) * Number(r.capitalUsagePct || 0) / 100)} sub={`${r.capitalUsagePct || 0}% maximum usage`} />
      <Metric title="Max Drawdown" value={money(p.maxDrawdown)} sub={`Monthly cap ${money(r.maxMonthlyDrawdown)}`} tone={Number(p.maxDrawdown) > 0 ? 'negative' : 'neutral'} />
    </section>

    <section className="panel chart-intelligence-panel">
      <div className="section-head intelligence-head">
        <div>
          <div className="eyebrow">PRO MARKET CHART</div>
          <h2>SENSEX • TradingView + APEX Analysis</h2>
          <p>TradingView gives the professional market-reference chart and drawing/indicator UI. APEX Markup shows the exact candle/SMC evidence used by the bot.</p>
        </div>
        <div className="phase-stack">
          <span className={`phase-chip ${chartView === 'tradingview' ? 'live' : marketResearchOnly ? 'historical' : String(chartIntel?.freshness?.state || 'unknown').toLowerCase()}`}>
            {chartView === 'tradingview' ? 'TRADINGVIEW' : marketResearchOnly ? 'HISTORICAL / RESEARCH' : (chartIntel?.freshness?.state || 'WAITING')}
          </span>
          <span className="phase-sub">{chartView === 'tradingview' ? 'BSE:SENSEX market reference' : chartIntel?.generatedAt ? `updated ${formatDateTime(chartIntel.generatedAt)}` : 'loading APEX chart intelligence'}</span>
        </div>
      </div>

      <div className="chart-view-tabs">
        <button type="button" className={chartView === 'tradingview' ? 'active' : ''} onClick={() => setChartView('tradingview')}>
          TradingView Live
        </button>
        <button type="button" className={chartView === 'apex' ? 'active' : ''} onClick={() => setChartView('apex')}>
          APEX Markup
        </button>
      </div>

      {chartView === 'tradingview'
        ? <TradingViewAdvancedChart symbol="BSE:SENSEX" />
        : <MarketIntelligenceChart data={chartIntel} />}

      <div className="chart-intel-stats">
        <SystemItem label="1m trend" value={chartIntel?.trends?.['1m']?.trend || '—'} />
        <SystemItem label="5m trend" value={chartIntel?.trends?.['5m']?.trend || '—'} />
        <SystemItem label="15m trend" value={chartIntel?.trends?.['15m']?.trend || '—'} />
        <SystemItem label="Swing structure" value={chartIntel?.currentCandle?.smc?.swingStructure || '—'} />
        <SystemItem label="Day H / L" value={chartIntel?.sessionProfile?.dayHigh ? `${chartIntel.sessionProfile.dayHigh} / ${chartIntel.sessionProfile.dayLow}` : '—'} />
        <SystemItem label="Prev day H / L" value={chartIntel?.sessionProfile?.previousDayHigh ? `${chartIntel.sessionProfile.previousDayHigh} / ${chartIntel.sessionProfile.previousDayLow}` : '—'} />
        <SystemItem label="Gap" value={chartIntel?.sessionProfile?.gapPct != null ? `${Number(chartIntel.sessionProfile.gapPct) >= 0 ? '+' : ''}${Number(chartIntel.sessionProfile.gapPct).toFixed(2)}%` : '—'} />
        <SystemItem label="Plan bias" value={chartIntel?.plan?.marketBias || nextPlan.marketBias || '—'} />
      </div>
      <p className="panel-note">
        TradingView Live is the visual market-reference view. APEX automated scoring/training still uses the backend provider feed and hard freshness gates; switching chart tabs does not change execution data.
      </p>
    </section>

    <section className="panel intelligence-panel">
      <div className="section-head intelligence-head">
        <div>
          <div className="eyebrow">APEX INTELLIGENCE GRAPH</div>
          <h2>Research Memory • Market Structure • Strategy Network</h2>
          <p>Obsidian-style map of the evidence pipeline. Live trade search stops at <b>15:15 IST</b>; after that APEX switches to research, sector study, hypothesis review and next-session planning.</p>
        </div>
        <div className="phase-stack">
          <span className={`phase-chip ${String(worker.researchPhase || '').toLowerCase()}`}>{worker.researchPhase || phase.reason || 'IDLE'}</span>
          <span className="phase-sub">{intelligence.knowledgeCount || 0} persisted sources</span>
        </div>
      </div>

      <div className="intelligence-layout">
        <KnowledgeGraph research={intelligence} worker={worker} marketResearch={marketResearch} neuralModel={neuralModel} learning={learning} />
        <div className="next-plan-card">
          <div className="plan-glow" />
          <small>NEXT SESSION PLAN</small>
          <h3>{nextPlan.planDate || 'Waiting for postmarket research'}</h3>
          <div className="plan-bias">{nextPlan.marketBias || 'UNKNOWN'}</div>
          <div className="plan-levels">
            <span>Support <b>{nextPlan.support ?? '—'}</b></span>
            <span>Resistance <b>{nextPlan.resistance ?? '—'}</b></span>
          </div>
          <div className="playbook">
            <b>SCALP</b>
            <span>{nextPlan.scalpPlaybook?.requires?.slice(0,2).join(' • ') || '1m/5m SMC confirmation'}</span>
          </div>
          <div className="playbook swing">
            <b>SWING</b>
            <span>{nextPlan.swingPlaybook?.requires?.slice(0,2).join(' • ') || '15m trend + 5m pullback'}</span>
          </div>
          <p>{nextPlan.note || 'Postmarket plan will be generated from market structure, sectors, research sources and validated strategy evidence.'}</p>
        </div>
      </div>

      <div className="research-grid">
        <div className="research-box">
          <div className="research-box-head"><b>Source Stream</b><span>{intelligence.knowledgeCount || 0} MEMORY NODES</span></div>
          <div className="source-stream">
            {researchSources.length === 0 ? <div className="empty-mini">Run a deep learning cycle after market hours to collect public research/news.</div> :
              researchSources.slice(0,8).map((s:any, i:number) => <a key={`${s.url}-${i}`} href={s.url} target="_blank" rel="noreferrer" className="source-node">
                <span className="source-dot" />
                <div><b>{s.title || 'Research source'}</b><small>{s.type || 'SOURCE'} • {s.category || 'RESEARCH'}</small></div>
                <span className="source-arrow">↗</span>
              </a>)}
          </div>
        </div>

        <div className="research-box">
          <div className="research-box-head"><b>Strategy Hypotheses</b><span>BACKTEST BEFORE TRUST</span></div>
          <div className="hypothesis-stack">
            {hypotheses.slice(0,8).map((h:any) => {
              const m = h.evidence?.metrics || {}
              return <div className="hypothesis-node" key={h.name}>
                <div><span className={`family ${String(h.family || '').toLowerCase()}`}>{h.family}</span><b>{h.name}</b></div>
                <p>{h.description}</p>
                <small>
                  {h.status} • score {Number(h.score || 0).toFixed(2)}
                  {m.trades != null ? ` • ${m.trades} tests • ${Number(m.winRate || 0).toFixed(1)}% WR • ${Number(m.expectancyR || 0).toFixed(2)}R exp` : ''}
                </small>
              </div>
            })}
          </div>
        </div>

        <div className="research-box">
          <div className="research-box-head"><b>Sector Radar</b><span>{sectors.length} TRACKED</span></div>
          <div className="sector-stack">
            {sectors.length === 0 ? <div className="empty-mini">Sector snapshot is generated during deep research.</div> :
              sectors.slice(0,10).map((s:any) => {
                const pct = Number(s.changePct || 0)
                const width = Math.min(100, Math.max(8, Math.abs(pct) * 28))
                return <div className="sector-row" key={s.sector}>
                  <span>{s.sector}</span>
                  <div className="sector-track"><i className={pct >= 0 ? 'up' : 'down'} style={{width:`${width}%`}} /></div>
                  <b className={pnlClass(pct)}>{pct >= 0 ? '+' : ''}{pct.toFixed(2)}%</b>
                </div>
              })}
          </div>
        </div>
      </div>

      <div className="session-rail">
        <div><b>08:00</b><span>News • Gap scenarios • Day plan</span></div>
        <i />
        <div className="active"><b>09:20–15:15</b><span>Fresh candles • Scalp search • Live memory</span></div>
        <i />
        <div><b>15:15–17:00</b><span>Session review • Backtests</span></div>
        <i />
        <div><b>17:00–08:00</b><span>15h research window • Learning cycles</span></div>
      </div>
    </section>

    <section className="panel research-ops-panel">
      <div className="section-head intelligence-head">
        <div>
          <div className="eyebrow">NIGHT LEARNING CONSOLE</div>
          <h2>What APEX Read • Tested • Learned</h2>
          <p>Persistent activity survives backend restarts. This is evidence collected from public education, news, labeled candle outcomes, strategy backtests and neural evaluation—not a claim of guaranteed profitability.</p>
        </div>
        <div className="phase-stack">
          <span className={`phase-chip ${worker.learningWindowActive ? 'night_research' : String(worker.researchPhase || '').toLowerCase()}`}>{worker.learningWindowActive ? 'LEARNING WINDOW' : (worker.researchPhase || 'IDLE')}</span>
          <span className="phase-sub">{Number(worker.researchHoursToday || 0).toFixed(1)} / {worker.dailyHourBudget ?? 15}h coverage</span>
        </div>
      </div>

      <div className="research-ops-grid">
        <div className="research-console-card">
          <div className="research-console-head"><b>News Monitor</b><span className={String(newsHealth.status || '').toUpperCase()==='OK'?'positive':'negative'}>{newsHealth.status || 'NOT RUN'}</span></div>
          <div className="console-mini-grid">
            <span><small>Fresh today</small><b>{intelligence.todayNewsCount ?? worker.todayNewsCount ?? 0}</b></span>
            <span><small>Last fetch</small><b>{formatDateTime(newsHealth.lastFetchAt)}</b></span>
            <span><small>Fetched last cycle</small><b>{newsHealth.fetchedCount ?? 0}</b></span>
            <span><small>Queries OK</small><b>{newsHealth.successfulQueries ?? 0}/{newsHealth.queryCount ?? 0}</b></span>
          </div>
          <div className="news-stream">
            {latestNews.length===0 ? <div className="empty-mini">No cached headlines yet. Next deep research cycle will retry RSS and keep the exact error state here.</div> :
              latestNews.slice(0,6).map((n:any,i:number)=><a key={`${n.url}-${i}`} href={n.url} target="_blank" rel="noreferrer">
                <b>{n.title}</b><small>{formatDateTime(n.lastCheckedAt)}</small>
              </a>)}
          </div>
          {Array.isArray(newsHealth.errors) && newsHealth.errors.length>0 && <details className="console-detail"><summary>News fetch errors</summary><pre>{JSON.stringify(newsHealth.errors,null,2)}</pre></details>}
        </div>

        <div className="research-console-card">
          <div className="research-console-head"><b>Learning Activity</b><span>{intelligence.todayActivityCount ?? 0} TODAY</span></div>
          <div className="activity-stream">
            {researchActivities.length===0 ? <div className="empty-mini">No persisted learning activity yet.</div> :
              researchActivities.slice(0,10).map((a:any,i:number)=><div className="activity-row" key={`${a.createdAt}-${i}`}>
                <i/><div><b>{a.title}</b><small>{a.stage || a.kind} • {formatDateTime(a.createdAt)}</small></div>
              </div>)}
          </div>
        </div>

        <div className="research-console-card">
          <div className="research-console-head"><b>Learned Pattern Stats</b><span>{datasetModel.datasetSize ?? 0} LABELS</span></div>
          <div className="pattern-insight-stack">
            {patternInsights.length===0 ? <div className="empty-mini">Pattern stats appear after labeled SMC V2 samples accumulate.</div> :
              patternInsights.slice(0,10).map((x:any)=><div className="pattern-insight-row" key={x.pattern}>
                <span>{String(x.pattern).replace(/_/g,' ')}</span>
                <b>{Number(x.winRate||0).toFixed(1)}%</b>
                <small>{x.samples} samples • {x.confidence}</small>
              </div>)}
          </div>
          <p className="console-foot">Win rate here means target-first in the labeled historical setup, not guaranteed live performance.</p>
        </div>

        <div className="research-console-card">
          <div className="research-console-head"><b>Public Reading Queue</b><span>{readingQueue.length} SOURCES</span></div>
          <div className="reading-stack">
            {readingQueue.slice(0,8).map((x:any,i:number)=><a href={x.url} target="_blank" rel="noreferrer" key={`${x.url}-${i}`}>
              <span>{x.category}</span><b>{x.title}</b>
            </a>)}
          </div>
          <p className="console-foot">APEX reads bounded public educational pages; it does not copy paid/copyrighted books.</p>
        </div>
      </div>
    </section>


    <section className="panel">
      <div className="section-head compact">
        <div>
          <div className="eyebrow">MARKET RESEARCH</div>
          <h2>1m / 5m / 15m Structure Snapshot</h2>
          <p>Multi-timeframe market intelligence: trend, HH/HL/LH/LL, support/resistance, BOS/CHOCH, liquidity sweep, FVG, fake breakout, pin bars, engulfing, harami, marubozu, tweezers, stars, soldiers/crows, inside/outside bars and doji/spinning-top context.</p>
        </div>
        <span className="badge subtle">{marketResearch.status || 'waiting'}</span>
      </div>
      <div className="system-strip">
        {['SENSEX'].map(name => {
          const m = researchMarkets[name] || {}
          const f1 = m.frames?.['1m'] || {}
          const f5 = m.frames?.['5m'] || {}
          const f15 = m.frames?.['15m'] || {}
          return <div className="system-item" key={name}>
            <small>{name}</small>
            <b>{marketResearchOnly ? 'HISTORICAL SNAPSHOT' : feedStale ? 'STALE / UNVERIFIED' : (m.state || 'WAITING')}</b>
            <span style={{display:'block',marginTop:6,fontSize:10,color:feedStale?'#ef8d8d':'#71849a'}}>
              {marketResearchOnly
                ? `Last session: 1m ${f1.trend || '—'} • 5m ${f5.trend || '—'} • 15m ${f15.trend || '—'}`
                : feedStale
                  ? `Last verified 1m trend: ${lastVerifiedTrend} • live trend unavailable`
                  : `1m ${f1.trend || '—'} • 5m ${f5.trend || '—'} • 15m ${f15.trend || '—'}`}
            </span>
            <span style={{display:'block',marginTop:4,fontSize:10,color:'#71849a'}}>
              S {f5.structure?.levels?.support ?? '—'} • R {f5.structure?.levels?.resistance ?? '—'}
            </span>
          </div>
        })}
      </div>
      {marketResearch.llm?.summary && <p className="panel-note"><b>LLM summary:</b> {marketResearch.llm.summary}</p>}
      <p className="panel-note">News and LLM research are advisory only. Trade entries, position sizing and hard risk locks remain deterministic and fresh-data gated.</p>
    </section>

    <section className="panel learning-panel">
      <div className="section-head compact">
        <div><div className="eyebrow">APEX LEARNING ENGINE</div><h2>Observe → Label → Research → Backtest → Train → Validate</h2><p>Live session stores fresh candle/SMC moments. Postmarket reviews the day, then 17:00–08:00 runs the 15-hour research window. At 08:00 APEX refreshes news, previous-day levels, gap scenarios and the session plan.</p></div>
        <span className={`badge subtle ${worker.running ? 'worker-live' : ''}`}>{worker.running ? 'LEARNING NOW' : (worker.stage || 'WAITING')}</span>
      </div>
      <div className="learning-grid">
        <SystemItem label="Worker" value={worker.enabled ? 'Enabled' : 'Disabled'} good={worker.enabled} />
        <SystemItem label="Stage" value={worker.stage || 'idle'} />
        <SystemItem label="Cycles today" value={worker.cyclesToday ?? 0} />
        <SystemItem label="Research coverage" value={`${worker.researchHoursToday ?? 0}h / ${worker.dailyHourBudget ?? 15}h`} />
        <SystemItem label="Learning window" value={worker.learningWindow || '17:00-08:00'} good={Boolean(worker.learningWindowActive)} />
        <SystemItem label="Sources reviewed" value={worker.sourcesReviewed ?? 0} />
        <SystemItem label="News reviewed" value={worker.newsReviewed ?? intelligence.todayNewsCount ?? 0} />
        <SystemItem label="Knowledge memory" value={worker.knowledgeCount ?? intelligence.knowledgeCount ?? 0} />
        <SystemItem label="Research phase" value={worker.researchPhase || 'IDLE'} />
        <SystemItem label="Sectors tracked" value={worker.sectorsTracked ?? sectors.length} />
        <SystemItem label="Patterns found" value={worker.patternsDetected ?? 0} />
        <SystemItem label="Hypotheses" value={worker.hypothesesTested ?? 0} />
        <SystemItem label="Backtests" value={worker.backtestsRun ?? 0} />
        <SystemItem label="Strategy-lab setups" value={worker.strategyLabTests ?? 0} />
        <SystemItem label="Dataset" value={worker.datasetSize ?? 0} />
        <SystemItem label="New labels last cycle" value={worker.datasetInsertedLastCycle ?? 0} />
        <SystemItem label="Labeled progress" value={trainingReadiness.minimumSamples ? `${trainingReadiness.eligibleSamples ?? 0} / ${trainingReadiness.minimumSamples}` : String(trainingReadiness.eligibleSamples ?? 0)} good={Boolean(trainingReadiness.sampleThresholdReady)} />
        <SystemItem label="Training state" value={trainingReadiness.status || 'COLLECTING_LABELS'} good={Boolean(trainingReadiness.productionModelReady)} />
        <SystemItem label="Candidate" value={worker.candidateVersion || 'not ready'} />
        <SystemItem label="Candidate score" value={worker.candidateScore != null ? Number(worker.candidateScore).toFixed(2) : '—'} />
        <SystemItem label="LLM provider" value={ollama.configured ? (ollama.provider || 'Connected') : 'Not configured'} good={ollama.configured} />
        <SystemItem label="LLM model" value={ollama.model || '—'} />
      </div>

      <div className="auto-learning-core">
        <div className="learning-core-card neural-core-card">
          <div className="core-card-head">
            <div><small>REAL NEURAL MODEL</small><h3>{activeNeural.version || 'Waiting for labeled data'}</h3></div>
            <span className={`model-role ${String(activeNeural.role || activeNeural.status || 'waiting').toLowerCase()}`}>{activeNeural.role || activeNeural.status || 'WAITING'}</span>
          </div>
          <LearningJar readiness={trainingReadiness} model={activeNeural} />
          <div className="core-stat-grid">
            <span><small>Architecture</small><b>{activeNeural.architecture || '14 → 24 → 1 MLP'}</b></span>
            <span><small>Labeled samples</small><b>{trainingReadiness.eligibleSamples ?? activeNeural.trainedSamples ?? worker.neuralTrainedSamples ?? 0}</b></span>
            <span><small>OOS AUC</small><b>{neuralOos.auc != null ? Number(neuralOos.auc).toFixed(3) : '—'}</b></span>
            <span><small>OOS Brier</small><b>{neuralOos.brier != null ? Number(neuralOos.brier).toFixed(3) : '—'}</b></span>
          </div>
          <div className="training-readiness">
            <div><span>Training readiness</span><b>{Number(trainingReadiness.sampleProgressPct ?? 0).toFixed(0)}%</b></div>
            <div className="training-readiness-track"><i style={{width:`${Math.max(0, Math.min(100, Number(trainingReadiness.sampleProgressPct ?? 0)))}%`}} /></div>
            <small>{trainingReadiness.positiveSamples ?? 0} target-first • {trainingReadiness.negativeSamples ?? 0} stop-first • {trainingReadiness.classBalanceReady ? 'class balance ready' : 'collecting both classes'}</small>
          </div>
          <div className="learning-flow-line">
            <span>SMC V2 labels</span><i>→</i><span>postmarket train</span><i>→</i><span>validation/OOS</span><i>→</i><span>promote or shadow</span>
          </div>
          <p>Weights stay frozen during live candles. A new model is promoted only after chronological validation and out-of-sample gates pass.</p>
        </div>

        <div className="learning-core-card rl-core-card">
          <div className="core-card-head">
            <div><small>REINFORCEMENT BANDIT</small><h3>{learning.bestArm || 'Context learner'}</h3></div>
            <span className="model-role production">AUTO</span>
          </div>
          <div className="core-stat-grid">
            <span><small>Reward updates</small><b>{learning.rewardUpdates ?? 0}</b></span>
            <span><small>Learning rate</small><b>{learning.learningRate != null ? Number(learning.learningRate).toFixed(2) : '—'}</b></span>
            <span><small>Exploration</small><b>{learning.explorationRate != null ? `${Math.round(Number(learning.explorationRate)*100)}%` : '—'}</b></span>
            <span><small>Latest reward</small><b>{rewardEvents[0]?.shapedReward != null ? Number(rewardEvents[0].shapedReward).toFixed(2)+'R' : '—'}</b></span>
          </div>
          <div className="reward-stream">
            {rewardEvents.length === 0 ? <span className="reward-empty">Valid closed paper trades will create automatic reward updates.</span> :
              rewardEvents.slice(0,4).map((ev:any)=><span key={ev.tradeId} className={Number(ev.shapedReward)>=0?'reward-good':'reward-bad'}>
                #T{ev.tradeId} {ev.strategy} <b>{Number(ev.shapedReward)>=0?'+':''}{Number(ev.shapedReward).toFixed(2)}R</b>
              </span>)}
          </div>
          <p>Closed valid trades update contextual strategy preferences immediately; invalid contracts are excluded.</p>
        </div>
      </div>

      <div className="worker-status"><b>{worker.currentTask || 'Waiting for next research cycle'}</b><span>Heartbeat {formatDateTime(worker.lastHeartbeatAt)}</span></div>
      {worker.lastSummary && <p className="panel-note">{worker.lastSummary}</p>}
      {worker.candidateMetrics && <details className="candidate-detail"><summary>Candidate validation metrics</summary><pre>{JSON.stringify(worker.candidateMetrics, null, 2)}</pre></details>}
      {worker.lastError && <p className="panel-note negative"><b>Safe failure:</b> {worker.lastError}</p>}
      <div className="actions"><ActionButton label="Run Learning Cycle" disabled={Boolean(busy || !providerReady)} tip="Run one bounded research, pattern and backtest cycle now." onClick={() => runAction('learning', 'Learning cycle completed.', () => fetchJson('/api/learning/run-once', {method:'POST'}))} /></div>
    </section>

    <section className="panel live-learning-panel">
      <div className="section-head compact">
        <div><div className="eyebrow">LIVE MARKET LEARNING</div><h2>SENSEX 1m Moment Memory</h2><p>09:20–15:15 only. Valid-price SMC/price-action moments are stored before outcomes are known; after cutoff the worker switches to deep research instead of creating after-hours noise.</p></div>
        <span className="badge subtle">{liveLearning.totalObservations ?? 0} OBSERVATIONS</span>
      </div>
      {feedStale && !marketResearchOnly && <div className="live-feed-freeze">
        <b>Live moment memory paused — feed is stale</b>
        <span>Last verified trend: <strong>{lastVerifiedTrend}</strong> • latest verified candle {sensexFreshness.candleTime ? formatDateTime(sensexFreshness.candleTime) : '—'} • no new market moment is stored until a fresh candle arrives.</span>
      </div>}
      <div className="learning-grid">
        <SystemItem label="Mode" value={marketResearchOnly ? 'MARKET_CLOSED_RESEARCH' : feedStale ? 'STALE_FEED_PAUSED' : (liveLearning.mode || 'waiting')} good={Boolean(liveLearning.enabled && !feedStale)} />
        <SystemItem label="Moments stored" value={liveLearning.totalObservations ?? 0} />
        <SystemItem label="Pending outcomes" value={liveLearning.pendingOutcomes ?? 0} />
        <SystemItem label="LLM live review" value={ollama.configured ? `Active • ${ollama.provider || 'LLM'}` : 'Not configured'} good={ollama.configured} />
        <SystemItem label="Standard entry" value={automation.entryPolicy?.standardMinScore != null ? `≥ ${Number(automation.entryPolicy.standardMinScore).toFixed(2)}` : '—'} />
        <SystemItem label="Strong SMC entry" value={automation.entryPolicy?.smcOverrideMinScore != null ? `≥ ${Number(automation.entryPolicy.smcOverrideMinScore).toFixed(2)}` : '—'} />
      </div>
      {liveMoments.length === 0 ? <div className="empty-line">Waiting for the next live SENSEX scan.</div> :
      <div className="table-scroll">
        <table className="trade-table">
          <thead><tr><th>Time</th><th>Market</th><th>Action</th><th>Score</th><th>Strategy</th><th>SMC / Context</th><th>Ollama</th><th>Outcome</th></tr></thead>
          <tbody>{liveMoments.slice(0,10).map((m:any) => <tr key={m.id}>
            <td className="time-cell">{formatDateTime(m.observedAt)}</td>
            <td>{m.instrument}<br/><small>{Number(m.marketPrice || 0).toFixed(2)}</small></td>
            <td><span className={`side ${m.action}`}>{m.action}</span></td>
            <td>{Number(m.signalScore || 0).toFixed(2)}</td>
            <td><small>{m.strategy || '—'}</small></td>
            <td><small>
              {m.context?.key || '—'}<br/>
              BOS {m.context?.smc?.bos || 'NONE'} • CHOCH {m.context?.smc?.choch || 'NONE'}<br/>
              Sweep {m.context?.smc?.liquiditySweep || 'NONE'}
              {m.context?.smcOverride ? <><br/><b>SMC OVERRIDE</b></> : null}
            </small></td>
            <td><small>{m.ollama?.status || (ollama.configured ? 'waiting' : 'not configured')}<br/>{m.ollama?.bias || '—'} {m.ollama?.confidence != null ? `${Math.round(Number(m.ollama.confidence)*100)}%` : ''}</small></td>
            <td><span className={`trade-status ${m.outcome}`}>{m.outcome || 'PENDING'}</span>{m.tradeId ? <small> #T{m.tradeId}</small> : null}</td>
          </tr>)}</tbody>
        </table>
      </div>}
      <p className="panel-note">Normal entries keep the standard score threshold. A lower score is allowed only when the live SMC engine confirms CHOCH/liquidity sweep, or BOS with directional candle confirmation. Primary evidence remains raw candle/SMC data.</p>
    </section>

    <section className="panel positions-panel">
      <div className="section-head">
        <div>
          <div className="eyebrow">LIVE PAPER POSITIONS</div>
          <h2>Open Positions</h2>
          <p>Prices refresh on the dashboard every second. Backend position monitor: <b>{positionMonitor.running ? 'Running' : 'Stopped'}</b> • interval <b>{positionMonitor.intervalSeconds ?? '—'}s</b>.</p>
        </div>
        <div className="live-box">
          <span className="pulse" />
          <div><small>Last dashboard sync</small><b>{lastSync ? lastSync.toLocaleTimeString('en-IN') : '—'}</b></div>
        </div>
      </div>

      {openTrades.length === 0 ? <div className="empty-state">
        <div className="empty-icon">↗</div>
        <b>No open paper position</b>
        <span>When APEX enters a trade, entry, LTP, SL, target and running P&L will appear here automatically.</span>
      </div> :
      <div className="table-scroll">
        <table className="trade-table open-table">
          <thead><tr>
            <th>Contract</th><th>Side</th><th>Qty / Lot</th><th>Entry</th><th>LTP</th><th>SL</th><th>Target</th><th>Price source</th><th>Plan</th><th>Net P&L</th><th>Chart</th><th>Opened</th><th>Manual</th>
          </tr></thead>
          <tbody>{openTrades.map(t => <tr key={t.id}>
            <td>
              <div className="instrument">{t.displayName || instrumentName(t.symbol)}</div>
              <small>{t.expiry ? `Exp ${t.expiry}` : 'Expiry —'} • Strike {t.strike ?? '—'} • #{t.id}</small>
            </td>
            <td><span className={`side ${t.direction}`}>{t.direction}</span></td>
            <td><b>{t.quantity}</b><br/><small>lot {t.lotSize}</small></td>
            <td>{money(t.entry)}</td>
            <td className="ltp">{money(t.currentPrice)}</td>
            <td>{editingTradeId === t.id ? <input className="trade-edit-input" type="number" step="0.05" value={planDraft.stop} onChange={e=>setPlanDraft(v=>({...v,stop:e.target.value}))}/> : money(t.stop)}</td>
            <td>{editingTradeId === t.id ? <input className="trade-edit-input" type="number" step="0.05" value={planDraft.target} onChange={e=>setPlanDraft(v=>({...v,target:e.target.value}))}/> : money(t.target)}</td>
            <td><small className={t.priceSource === 'synthetic_estimate' ? 'negative' : 'positive'}>{t.priceSource === 'synthetic_estimate' ? 'SIMULATED PREMIUM' : (t.priceSource || '—')}</small><br/><small>{formatDateTime(t.quoteTime)}</small></td>
            <td><small>{t.meta?.tradeStyle || '—'} • {t.meta?.strategy || 'APEX'}<br/>T1 {t.meta?.firstTarget ? money(t.meta.firstTarget) : '—'}</small></td>
            <td className={`pnl ${pnlClass(t.pnl)}`}>{Number(t.pnl) > 0 ? '+' : ''}{money(t.pnl)}<br/><small>{tradePnlPct(t) > 0 ? '+' : ''}{percent(tradePnlPct(t))}</small></td>
            <td>{t.chartUrl ? <a className="chart-link" href={t.chartUrl} target="_blank" rel="noreferrer">Open SENSEX Chart ↗</a> : <span>Unavailable</span>}</td>
            <td className="time-cell">{formatDateTime(t.openedAt)}</td>
            <td>
              <div className="trade-actions">
                {editingTradeId === t.id ? <>
                  <button type="button" onClick={()=>void savePlan(t)} disabled={Boolean(busy)}>Save</button>
                  <button type="button" onClick={()=>setEditingTradeId(null)} disabled={Boolean(busy)}>Cancel</button>
                </> : <button type="button" onClick={()=>beginPlanEdit(t)} disabled={Boolean(busy)}>Edit SL/TG</button>}
                <button type="button" className="danger-mini" onClick={()=>void exitTrade(t)} disabled={Boolean(busy)}>Exit</button>
              </div>
            </td>
          </tr>)}</tbody>
        </table>
      </div>}
      <p className="panel-note">Dashboard checks every 1s, but provider freshness is independent. APEX now hard-blocks new entries when the latest decision candle exceeds <b>{automation.entryPolicy?.maxLiveCandleAgeSeconds ?? 180}s</b>; stale Yahoo candles remain visible for audit/research only.</p>
    </section>

    <section className="two-col">
      <section className="panel">
        <div className="section-head compact"><div><div className="eyebrow">CONTROL</div><h2>Automation & Safety</h2></div></div>
        <div className="system-strip">
          <SystemItem label="Strategy loop" value={automation.running ? 'Running' : 'Stopped'} good={automation.running} />
          <SystemItem label="Position monitor" value={positionMonitor.running ? 'Running' : 'Stopped'} good={positionMonitor.running} />
          <SystemItem label="Market data" value={feedStale ? 'STALE — blocked' : feedDelayed ? 'Delayed' : providerReady ? 'Ready' : 'Not ready'} good={providerReady && !feedStale} />
          <SystemItem label="Candle age" value={sensexFreshness.ageSeconds != null ? `${Math.max(0, Number(sensexFreshness.ageSeconds)).toFixed(0)}s / ${sensexFreshness.maxAgeSeconds ?? '—'}s` : 'Waiting'} good={Boolean(sensexFreshness.fresh)} />
          <SystemItem label="Provider" value={market.provider || 'unknown'} />
          <SystemItem label="Paper broker" value={market.paperBroker || 'internal'} />
          <SystemItem label="Phase" value={phase.reason || 'Waiting'} />
        </div>

        <div className="do-trade-card">
          <div className="do-trade-warning">⚠</div>
          <div className="do-trade-copy">
            <div className="do-trade-title">
              <div><small>MANUAL AGGRESSIVE PAPER SCALP</small><h3>DO TRADE</h3></div>
              <span>DANGER</span>
            </div>
            <p>One-click fresh-market scan using 1m/5m/15m momentum, SMC structure, BOS/CHOCH/sweeps/FVG, lower-high/lower-low, EMA/RSI and Fibonacci context. It executes only when deterministic confluence passes.</p>
            <div className="do-trade-grid">
              <span>Target <b>+{manualDoTrade.targetPoints ?? 30} pts</b></span>
              <span>Stop <b>-{manualDoTrade.stopPoints ?? 15} pts</b></span>
              <span>Capital request <b>{manualDoTrade.capitalUsagePct ?? 100}% max</b></span>
              <span>Cutoff <b>{manualDoTrade.cutoffTime ?? '15:20'} IST</b></span>
              <span>Carry forward <b>{manualDoTrade.carryForward ? 'FUTURE-EXPIRY ONLY' : 'OFF'}</b></span>
              <span>Feed <b className={feedStale ? 'negative' : 'positive'}>{feedStale ? 'STALE — BLOCKED' : freshnessState}</b></span>
            </div>
            <small className="do-trade-foot">PAPER ONLY. Quantity targets maximum affordable deployment but is reduced when needed to stay inside remaining daily / weekly / monthly hard loss-lock headroom. Expiry-day contracts do not carry. Kill + Flatten always overrides carry-forward.</small>
          </div>
          <div className="do-trade-action">
            <button type="button" disabled={doTradeDisabled} onClick={() => void doTradeNow()}>
              {busy === 'do-trade' ? 'SCANNING…' : '⚠ DO TRADE'}
            </button>
            <small>{feedStale ? 'Waiting for fresh candle' : !manualDoTrade.timeEligible ? `Available until ${manualDoTrade.cutoffTime ?? '15:20'}` : openTrades.length ? 'Close current position first' : status.mode !== 'PAPER' ? 'Switch to PAPER first' : 'Fresh SMC setup required'}</small>
          </div>
        </div>

        <div className="trade-alert-card">
          <div className="trade-alert-icon">🔔</div>
          <div className="trade-alert-copy">
            <div><small>TRADE EXECUTION ALERTS</small><h3>Browser / Desktop Push</h3></div>
            <p>Get an OS-level notification immediately after APEX commits a new paper trade. Background tabs are supported; delivery with the tab closed depends on the browser/OS keeping Web Push enabled.</p>
            <div className="trade-alert-meta">
              <span>Server <b className={pushConfig?.configured ? 'positive' : 'negative'}>{pushConfig?.configured ? 'READY' : 'NOT CONFIGURED'}</b></span>
              <span>Permission <b>{pushPermission.toUpperCase()}</b></span>
              <span>This device <b className={pushSubscribed ? 'positive' : ''}>{pushSubscribed ? 'SUBSCRIBED' : 'OFF'}</b></span>
              <span>Devices <b>{pushConfig?.subscriberCount ?? 0}</b></span>
            </div>
            {pushError && <div className="trade-alert-error">{pushError}</div>}
          </div>
          <div className="trade-alert-actions">
            {pushSubscribed
              ? <button type="button" className="action-btn danger-mini" disabled={pushBusy} onClick={() => void disableTradeAlerts()}>{pushBusy ? 'Working…' : 'Disable Alerts'}</button>
              : <button type="button" className="action-btn primary" disabled={pushBusy || pushConfig?.supported === false} onClick={() => void enableTradeAlerts()}>{pushBusy ? 'Enabling…' : 'Enable Trade Alerts'}</button>}
            <button type="button" className="action-btn" disabled={!pushSubscribed || pushPermission !== 'granted'} onClick={() => void testTradeAlert()}>Test Alert</button>
          </div>
        </div>

        <div className="actions">
          <ActionButton
            label="PAPER"
            tone="primary"
            disabled={Boolean(busy || killed || status.mode === 'PAPER')}
            tip={killed ? 'Reset kill switch first.' : status.mode === 'PAPER' ? 'PAPER mode is already active.' : 'Enable automated paper trading.'}
            onClick={() => runAction('paper', 'PAPER mode enabled.', () => fetchJson('/api/system/mode', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({mode:'PAPER'})}))}
          />
          <ActionButton
            label="SAFE"
            disabled={Boolean(busy || killed || status.mode === 'SAFE')}
            tip={killed ? 'Reset kill switch first.' : status.mode === 'SAFE' ? 'SAFE mode is already active.' : 'Stop new entries while keeping the system available.'}
            onClick={() => runAction('safe', 'SAFE mode enabled.', () => fetchJson('/api/system/mode', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({mode:'SAFE'})}))}
          />
          <ActionButton
            label="OFF"
            disabled={Boolean(busy || status.mode === 'OFF')}
            tip={status.mode === 'OFF' ? 'System is already OFF.' : 'Turn strategy entries off.'}
            onClick={() => runAction('off', 'Automation mode switched OFF.', () => fetchJson('/api/system/mode', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({mode:'OFF'})}))}
          />
          <ActionButton
            label="Run Scan Now"
            disabled={scanDisabled}
            tip={killed ? 'Kill switch is active.' : status.mode !== 'PAPER' ? 'Switch to PAPER first.' : !providerReady ? 'Market provider is not ready.' : 'Run one strategy scan immediately.'}
            onClick={() => runAction('scan', 'Manual scan completed.', () => fetchJson('/api/automation/run-once', {method:'POST'}))}
          />
          <ActionButton
            label="Run Research"
            disabled={Boolean(busy || !providerReady)}
            tip={!providerReady ? 'Market provider is not ready.' : 'Run the daily research routine now.'}
            onClick={() => runAction('research', 'Research run completed.', () => fetchJson('/api/research/run-once', {method:'POST'}))}
          />
          <ActionButton
            label="Flatten"
            disabled={flattenDisabled}
            tip={openTrades.length === 0 ? 'There is no open position to close.' : 'Close all open paper positions immediately.'}
            onClick={() => runAction('flatten', 'Open paper positions flattened.', () => fetchJson('/api/risk/flatten', {method:'POST'}))}
          />
          <ActionButton
            label="KILL + FLATTEN"
            tone="danger"
            disabled={killDisabled}
            tip={killed ? 'Kill switch is already active.' : 'Emergency stop: kill automation and close open paper positions.'}
            onClick={() => runAction('kill', 'Kill switch activated and positions flattened.', () => fetchJson('/api/risk/kill-switch', {method:'POST'}))}
          />
          <ActionButton
            label="Reset Kill → OFF"
            disabled={resetDisabled}
            tip={!killed ? 'Available only when the kill switch is active.' : 'Reset emergency kill switch. System returns to OFF.'}
            onClick={() => runAction('reset', 'Kill switch reset. Mode is OFF.', () => fetchJson('/api/risk/reset-kill-switch', {method:'POST'}))}
          />
        </div>
        <p className="panel-note">Disabled controls are intentionally faded. Hover any control for its reason/action. Entry window: <b>{automation.tradeWindow}</b> • forced exit: <b>{automation.forceExit}</b> • research: <b>{automation.researchTime}</b>.</p>
      </section>

      <section className="panel">
        <div className="section-head compact"><div><div className="eyebrow">MONTHLY RISK</div><h2>Target & Locks</h2></div><span className={`lock-pill ${r.monthlyTargetLocked ? 'locked' : ''}`}>{r.monthlyTargetLocked ? 'LOCKED / SAFE' : 'ACTIVE'}</span></div>
        <div className="target-amounts">
          <div><small>Month start equity</small><b>{money(mt.monthStartEquity)}</b></div>
          <div><small>Target</small><b>{percent(mt.targetPct)} / {money(mt.targetAmount)}</b></div>
          <div><small>Month P&L</small><b className={pnlClass(mt.monthPnl)}>{money(mt.monthPnl)}</b></div>
          <div><small>Remaining</small><b>{money(mt.remaining)}</b></div>
        </div>
        <div className="progress"><span style={{width: `${targetProgress}%`}} /></div>
        <div className="lock-grid">
          <span>Daily loss lock <b>{r.dailyLocked ? 'LOCKED' : 'Open'}</b></span>
          <span>Weekly loss lock <b>{r.weeklyLocked ? 'LOCKED' : 'Open'}</b></span>
          <span>Monthly loss lock <b>{r.monthlyLossLocked ? 'LOCKED' : 'Open'}</b></span>
          <span>Auto compound <b>{r.autoCompoundProfits ? 'ON' : 'OFF'}</b></span>
        </div>
      </section>
    </section>

    <section className="panel">
      <div className="section-head compact">
        <div><div className="eyebrow">JOURNAL</div><h2>Trade History</h2><p>Closed paper trades with final P&L and timestamps. Invalid contracts stay visible for audit but are excluded from performance, risk and learning.</p></div>
        <span className="badge subtle">{validatedClosedTrades.length} VALIDATED • {invalidClosedTrades.length} EXCLUDED</span>
      </div>
      {closedTrades.length === 0 ? <div className="empty-line">No closed paper trades yet.</div> :
      <div className="table-scroll">
        <table className="trade-table history-table">
          <thead><tr><th>Instrument</th><th>Side</th><th>Qty</th><th>Entry</th><th>Exit</th><th>Status</th><th>Gross</th><th>Costs</th><th>Net P&L</th><th>Opened</th><th>Closed</th></tr></thead>
          <tbody>{closedTrades.map(t => <tr key={t.id}>
            <td><div className="instrument">{instrumentName(t.symbol)}</div><small>#{t.id}</small></td>
            <td><span className={`side ${t.direction}`}>{t.direction}</span></td>
            <td>{t.quantity}</td>
            <td>{money(t.entry)}</td>
            <td>{money(t.currentPrice)}</td>
            <td>
              <span className={`trade-status ${t.status}`}>{t.status}</span>
              {t.status === 'INVALID_CONTRACT' ? <><br/><small className="negative">Excluded from stats / learning</small></> : null}
            </td>
            <td>{money((t as any).grossPnl ?? t.pnl)}</td>
            <td>{money((t as any).estimatedCharges ?? 0)}</td>
            <td className={`pnl ${pnlClass(t.pnl)}`}>{Number(t.pnl) > 0 ? '+' : ''}{money(t.pnl)}{t.status === 'INVALID_CONTRACT' ? <><br/><small>Audit only</small></> : null}</td>
            <td className="time-cell">{formatDateTime(t.openedAt)}</td>
            <td className="time-cell">{formatDateTime(t.closedAt)}</td>
          </tr>)}</tbody>
        </table>
      </div>}
    </section>

    <section className="details-grid">
      <details className="panel detail-panel">
        <summary>Last Strategy Decision</summary>
        <pre>{JSON.stringify(automation.lastDecision || {action:'waiting'}, null, 2)}</pre>
      </details>
      <details className="panel detail-panel">
        <summary>Adaptive Learning</summary>
        <div className="detail-copy">
          <span>Method <b>{learning.method || '—'}</b></span>
          <span>Best arm <b>{learning.bestArm || '—'}</b></span>
          <span>Exploration <b>{Math.round((learning.explorationRate || 0) * 100)}%</b></span>
          <span>Latest research <b>{learning.latestResearch?.runDate || 'not run yet'}</b></span>
        </div>
        <pre>{JSON.stringify({qValues: learning.qValues, researchArms: learning.latestResearch?.arms}, null, 2)}</pre>
      </details>
    </section>

    <footer className="apex-footer">
      <span>APEX Algo AI • Testing dashboard</span>
      <span className="tarun-credit"><i /> Developed by <b>Tarun</b></span>
      <span>NO TRADE is valid • Monthly target is a lock, not a guaranteed return • Real broker orders disabled</span>
    </footer>
  </main>
}

function TradingViewAdvancedChart({symbol}:{symbol:string}) {
  const containerRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    container.innerHTML = ''
    const widget = document.createElement('div')
    widget.className = 'tradingview-widget-container__widget'
    widget.style.height = 'calc(100% - 28px)'
    widget.style.width = '100%'

    const copyright = document.createElement('div')
    copyright.className = 'tradingview-widget-copyright'
    copyright.innerHTML = '<a href="https://www.tradingview.com/" rel="noopener nofollow" target="_blank"><span>Market chart</span></a><span> by TradingView</span>'

    const script = document.createElement('script')
    script.type = 'text/javascript'
    script.src = 'https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js'
    script.async = true
    script.innerHTML = JSON.stringify({
      autosize: true,
      symbol,
      interval: '1',
      timezone: 'Asia/Kolkata',
      theme: 'dark',
      style: '1',
      locale: 'en',
      backgroundColor: 'rgba(6, 19, 27, 1)',
      gridColor: 'rgba(30, 48, 58, 0.45)',
      allow_symbol_change: false,
      calendar: false,
      details: true,
      hide_side_toolbar: false,
      hide_top_toolbar: false,
      hide_legend: false,
      save_image: false,
      withdateranges: true,
      support_host: 'https://www.tradingview.com',
    })

    container.appendChild(widget)
    container.appendChild(copyright)
    container.appendChild(script)

    return () => {
      container.innerHTML = ''
    }
  }, [symbol])

  return <div className="tv-live-shell">
    <div className="tv-live-head">
      <div><small>TRADINGVIEW ADVANCED CHART</small><b>{symbol}</b></div>
      <span>1m • Asia/Kolkata</span>
    </div>
    <div ref={containerRef} className="tradingview-widget-container tv-widget-host" />
  </div>
}

function MarketIntelligenceChart({data}:{data:any}) {
  const all = Array.isArray(data?.candles) ? data.candles : []
  if (all.length < 2) return <div className="chart-empty">Waiting for SENSEX candle intelligence…</div>

  const maxBars = 90
  const offset = Math.max(0, all.length - maxBars)
  const rows = all.slice(offset)
  const width = 1000
  const height = 430
  const pad = {left:58,right:94,top:25,bottom:42}
  const plotW = width - pad.left - pad.right
  const plotH = height - pad.top - pad.bottom
  const levelValues = [
    data?.levels?.support,data?.levels?.resistance,data?.levels?.dayHigh,data?.levels?.dayLow,
    data?.levels?.previousDayHigh,data?.levels?.previousDayLow,
    data?.fibonacci?.fib382,data?.fibonacci?.fib500,data?.fibonacci?.fib618,
  ].map(Number).filter(Number.isFinite)
  const lows = rows.map((x:any)=>Number(x.low))
  const highs = rows.map((x:any)=>Number(x.high))
  let minP = Math.min(...lows,...levelValues)
  let maxP = Math.max(...highs,...levelValues)
  const extra = Math.max(8,(maxP-minP)*0.06)
  minP -= extra; maxP += extra
  const range = Math.max(1,maxP-minP)
  const x = (i:number)=>pad.left + (i/(Math.max(1,rows.length-1)))*plotW
  const y = (p:number)=>pad.top + (maxP-p)/range*plotH
  const candleW = Math.max(2,Math.min(7,plotW/rows.length*0.58))
  const points = (key:string)=>rows.map((r:any,i:number)=>`${x(i).toFixed(1)},${y(Number(r[key])).toFixed(1)}`).join(' ')
  const levelDefs = [
    {key:'dayHigh',label:'DAY HIGH',value:data?.levels?.dayHigh,kind:'day-high'},
    {key:'dayLow',label:'DAY LOW',value:data?.levels?.dayLow,kind:'day-low'},
    {key:'previousDayHigh',label:'PDH',value:data?.levels?.previousDayHigh,kind:'prev'},
    {key:'previousDayLow',label:'PDL',value:data?.levels?.previousDayLow,kind:'prev'},
    {key:'support',label:'SUPPORT',value:data?.levels?.support,kind:'support'},
    {key:'resistance',label:'RESIST',value:data?.levels?.resistance,kind:'resistance'},
    {key:'fib382',label:'FIB 38.2',value:data?.fibonacci?.fib382,kind:'fib'},
    {key:'fib500',label:'FIB 50',value:data?.fibonacci?.fib500,kind:'fib'},
    {key:'fib618',label:'FIB 61.8',value:data?.fibonacci?.fib618,kind:'fib'},
  ].filter((l:any)=>Number.isFinite(Number(l.value)))
  const events = (Array.isArray(data?.events)?data.events:[])
    .filter((e:any)=>Number(e.index)>=offset && Number(e.index)<all.length)
    .map((e:any)=>({...e,index:Number(e.index)-offset}))
  const ticks = Array.from({length:6},(_,i)=>maxP-(range*i/5))

  return <div className="market-intel-chart-wrap">
    <svg className="market-intel-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="SENSEX candlestick chart with APEX SMC and pattern annotations">
      <rect x={pad.left} y={pad.top} width={plotW} height={plotH} className="chart-bg"/>
      {ticks.map((p,i)=><g key={i}>
        <line x1={pad.left} x2={width-pad.right} y1={y(p)} y2={y(p)} className="chart-grid-line"/>
        <text x={pad.left-8} y={y(p)+4} textAnchor="end" className="chart-axis-label">{p.toFixed(0)}</text>
      </g>)}

      {levelDefs.map((l:any)=><g key={l.key} className={`chart-level ${l.kind}`}>
        <line x1={pad.left} x2={width-pad.right} y1={y(Number(l.value))} y2={y(Number(l.value))}/>
        <text x={width-pad.right+6} y={y(Number(l.value))+4}>{l.label} {Number(l.value).toFixed(0)}</text>
      </g>)}

      <polyline points={points('ema21')} className="ema-line ema21"/>
      <polyline points={points('ema9')} className="ema-line ema9"/>

      {rows.map((r:any,i:number)=>{
        const o=Number(r.open), h=Number(r.high), l=Number(r.low), close=Number(r.close)
        const bull=close>=o
        const top=y(Math.max(o,close))
        const bottom=y(Math.min(o,close))
        const bodyH=Math.max(1.5,bottom-top)
        return <g key={`${r.time}-${i}`} className={`candle ${bull?'bull':'bear'}`}>
          <line x1={x(i)} x2={x(i)} y1={y(h)} y2={y(l)} className="wick"/>
          <rect x={x(i)-candleW/2} y={top} width={candleW} height={bodyH} rx="0.8"/>
        </g>
      })}

      {events.map((e:any,i:number)=>{
        const cx=x(e.index)
        const cy=y(Number(e.price))
        const above=String(e.direction).toUpperCase()==='BEAR'
        const labelY=above?cy-15:cy+24
        return <g key={`${e.time}-${e.kind}-${i}`} className={`chart-event ${String(e.direction).toLowerCase()} ${String(e.kind).toLowerCase()}`}>
          <circle cx={cx} cy={cy} r="3.4"/>
          <line x1={cx} x2={cx} y1={cy} y2={above?labelY+4:labelY-10}/>
          <text x={cx} y={labelY} textAnchor="middle">{String(e.label).replace(/_/g,' ').slice(0,20)}</text>
        </g>
      })}

      {rows.filter((_:any,i:number)=>i%15===0 || i===rows.length-1).map((r:any,i:number)=>{
        const index=rows.indexOf(r)
        const d=new Date(r.time)
        const label=Number.isNaN(d.getTime())?'':d.toLocaleTimeString('en-IN',{timeZone:'Asia/Kolkata',hour:'2-digit',minute:'2-digit'})
        return <text key={`t-${index}`} x={x(index)} y={height-15} textAnchor="middle" className="chart-time-label">{label}</text>
      })}
    </svg>
    <div className="chart-legend">
      <span><i className="legend-chip ema9-chip"/>EMA 9</span>
      <span><i className="legend-chip ema21-chip"/>EMA 21</span>
      <span><i className="legend-chip smc-chip"/>SMC / Pattern marker</span>
      <span><b>{data?.currentCandle?.smc?.bos || 'NONE'}</b> BOS</span>
      <span><b>{data?.currentCandle?.smc?.choch || 'NONE'}</b> CHOCH</span>
      <span><b>{data?.currentCandle?.smc?.liquiditySweep || 'NONE'}</b> SWEEP</span>
      <span><b>{data?.currentCandle?.smc?.fairValueGap || 'NONE'}</b> FVG</span>
    </div>
  </div>
}

function LearningJar({readiness, model}:{readiness:any, model:any}) {
  const raw = Number(readiness?.jarLevelPct ?? readiness?.sampleProgressPct ?? 0)
  const level = Math.max(0, Math.min(100, Number.isFinite(raw) ? raw : 0))
  const state = String(readiness?.jarState || readiness?.status || 'COLLECTING_LABELS')
  const productionReady = Boolean(readiness?.productionModelReady || model?.role === 'PRODUCTION')
  const eligible = Number(readiness?.eligibleSamples || 0)
  const minimum = Number(readiness?.minimumSamples || 0)
  const remaining = Number(readiness?.remainingSamples ?? Math.max(0, minimum - eligible))
  const milestone = readiness?.milestoneLabel || (productionReady
    ? 'Promoted neural model active'
    : `${remaining} more clean labeled setups to first training milestone`)

  return <div className="learning-jar-wrap">
    <div className={`learning-jar ${productionReady ? 'ready' : ''}`} aria-label={`APEX learning jar ${level.toFixed(0)} percent`}>
      <div className="jar-cap"><i/><i/><i/></div>
      <div className="jar-neck"/>
      <div className="jar-vessel">
        <div className="jar-liquid" style={{height:`${Math.max(4, level)}%`}}>
          <span className="jar-wave wave-a"/>
          <span className="jar-wave wave-b"/>
          <i className="bubble b1"/><i className="bubble b2"/><i className="bubble b3"/><i className="bubble b4"/>
        </div>
        <div className="jar-glass-shine"/>
        <div className="jar-level-label">
          <strong>{level.toFixed(0)}%</strong>
          <span>{productionReady ? 'MODEL READY' : 'TRAINING DATA'}</span>
        </div>
      </div>
      <div className="jar-base"/>
    </div>
    <div className="jar-copy">
      <div className="jar-copy-top"><span>APEX LEARNING JAR</span><b>{state.replace(/_/g,' ')}</b></div>
      <h4>{eligible.toLocaleString('en-IN')} / {minimum ? minimum.toLocaleString('en-IN') : '—'} clean labels</h4>
      <p>{milestone}</p>
      <div className="jar-mini-stats">
        <span><b>{readiness?.positiveSamples ?? 0}</b> target-first</span>
        <span><b>{readiness?.negativeSamples ?? 0}</b> stop-first</span>
        <span><b>{readiness?.classBalanceReady ? 'YES' : 'NO'}</b> class balance</span>
      </div>
      <small>This jar measures first-training readiness from clean labeled SMC V2 outcomes; it is not a promise of model accuracy or profit.</small>
    </div>
  </div>
}

function KnowledgeGraph({research, worker, marketResearch, neuralModel, learning}:{research:any, worker:any, marketResearch:any, neuralModel:any, learning:any}) {
  const sensex = marketResearch?.analytics?.markets?.SENSEX || {}
  const knowledge = Number(research?.knowledgeCount || 0)
  const patternCount = Number(worker?.patternsDetected || 0)
  const sectorCount = Array.isArray(research?.latestSectors) ? research.latestSectors.length : 0
  const production = neuralModel?.production || {}
  const modelLabel = production?.version ? String(production.version).replace('nn-','NN ') : 'NN SHADOW'
  const rewardUpdates = Number(learning?.rewardUpdates || 0)

  const inputs = [
    {id:'market', label:'SENSEX', sub:sensex?.state || 'MARKET'},
    {id:'smc', label:'SMC', sub:'BOS / CHOCH'},
    {id:'liq', label:'LIQUIDITY', sub:'SWEEP / FVG'},
    {id:'price', label:'PRICE ACTION', sub:'1m / 5m / 15m'},
    {id:'patterns', label:'PATTERNS', sub:`${patternCount} decoded`},
    {id:'sectors', label:'SECTORS', sub:`${sectorCount} tracked`},
    {id:'news', label:'NEWS', sub:'MACRO / INDIA'},
    {id:'memory', label:'MEMORY', sub:`${knowledge} sources`},
  ]

  const hiddenA = [
    {id:'regime', label:'REGIME', sub:'trend / range'},
    {id:'structure', label:'STRUCTURE', sub:'HTF alignment'},
    {id:'momentum', label:'MOMENTUM', sub:'EMA / RSI'},
    {id:'volatility', label:'VOLATILITY', sub:'ATR / range'},
    {id:'patternctx', label:'PATTERN CTX', sub:'location + candle'},
    {id:'research', label:'RESEARCH', sub:'evidence layer'},
  ]

  const hiddenB = [
    {id:'quality', label:'SETUP QUALITY', sub:'score + filters'},
    {id:'timing', label:'TIMING', sub:'entry window'},
    {id:'risk', label:'RISK', sub:'SL / size / lock'},
    {id:'memoryfit', label:'MEMORY FIT', sub:'historical analog'},
    {id:'neural', label:'NEURAL P', sub:modelLabel},
  ]

  const outputs = [
    {id:'scalp', label:'SCALP', sub:'1m / 5m'},
    {id:'swing', label:'SWING', sub:'15m / 5m / 1m'},
    {id:'wait', label:'NO TRADE', sub:'quality gate'},
    {id:'plan', label:'NEXT PLAN', sub:'postmarket'},
    {id:'reward', label:'REWARD', sub:`${rewardUpdates} updates`},
  ]

  const layers = [
    {x:92, nodes:inputs},
    {x:340, nodes:hiddenA},
    {x:590, nodes:hiddenB},
    {x:830, nodes:outputs},
  ]

  const placed:any[] = []
  layers.forEach((layer, layerIndex) => {
    const usableTop = 44
    const usableBottom = 430
    const count = layer.nodes.length
    layer.nodes.forEach((node:any, index:number) => {
      const y = count === 1 ? 235 : usableTop + ((usableBottom - usableTop) * index / (count - 1))
      placed.push({...node, x:layer.x, y, layerIndex})
    })
  })
  const byId:any = Object.fromEntries(placed.map(n => [n.id, n]))

  const connect = (from:string[], to:string[], salt=0) => {
    const rows:any[] = []
    from.forEach((a, ai) => to.forEach((b, bi) => {
      const weight = ((ai * 7 + bi * 11 + salt) % 9) + 1
      const polarity = (ai + bi + salt) % 4 === 0 ? 'negative' : (ai + bi + salt) % 3 === 0 ? 'hot' : 'positive'
      rows.push({a,b,weight,polarity})
    }))
    return rows
  }

  const edges = [
    ...connect(inputs.map(n=>n.id), hiddenA.map(n=>n.id), 1),
    ...connect(hiddenA.map(n=>n.id), hiddenB.map(n=>n.id), 4),
    ...connect(hiddenB.map(n=>n.id), outputs.map(n=>n.id), 7),
  ]

  return <div className="knowledge-graph neural-graph">
    <div className="graph-grid" />
    <div className="neural-layer-labels">
      <span>MARKET INPUTS</span><span>FEATURE ENGINE</span><span>DECISION LAYERS</span><span>OUTPUTS</span>
    </div>
    <svg viewBox="0 0 920 480" role="img" aria-label="APEX neural-style trading intelligence graph">
      <defs>
        <filter id="nodeGlowV2" x="-100%" y="-100%" width="300%" height="300%">
          <feGaussianBlur stdDeviation="4" result="blur"/>
          <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
        </filter>
        <linearGradient id="coreNode" x1="0" x2="1">
          <stop offset="0%" stopColor="#09272c"/><stop offset="100%" stopColor="#0a1c2d"/>
        </linearGradient>
      </defs>

      {edges.map((edge:any, i:number) => {
        const a=byId[edge.a], b=byId[edge.b]
        return <line
          key={`${edge.a}-${edge.b}`}
          className={`nn-edge ${edge.polarity} w${Math.min(4, Math.ceil(edge.weight/2.5))}`}
          x1={a.x} y1={a.y} x2={b.x} y2={b.y}
          style={{animationDelay:`${(i%12)*-0.22}s`}}
        />
      })}

      {placed.map((n:any) => <g key={n.id} className={`nn-node layer-${n.layerIndex} ${n.id}`} transform={`translate(${n.x},${n.y})`}>
        <circle className="nn-orbit orbit-a" r={n.layerIndex===2?29:25}/>
        <circle className="nn-orbit orbit-b" r={n.layerIndex===2?23:20}/>
        <circle className="nn-core" r={n.layerIndex===2?16:14} fill={n.layerIndex===2?'url(#coreNode)':undefined}/>
        <circle className="nn-shine" r="5" cx="-4" cy="-5"/>
        <text className="nn-label" textAnchor="middle" x={n.layerIndex===0 ? 34 : n.layerIndex===3 ? -34 : 0} y="-3">{n.label}</text>
        <text className="nn-sub" textAnchor="middle" x={n.layerIndex===0 ? 34 : n.layerIndex===3 ? -34 : 0} y="10">{n.sub}</text>
      </g>)}

      <g className="apex-brain" transform="translate(590,235)">
        <circle r="38" className="brain-halo"/>
        <circle r="25" className="brain-core"/>
        <text textAnchor="middle" y="-2" className="brain-title">APEX</text>
        <text textAnchor="middle" y="11" className="brain-sub">{production?.version ? 'TRAINED CORE' : 'AI CORE'}</text>
      </g>
    </svg>

    <div className="neural-legend">
      <span><i className="legend-line cyan"/> positive evidence</span>
      <span><i className="legend-line red"/> conflicting evidence</span>
      <span><i className="legend-dot"/> live research memory</span>
    </div>
    <div className="graph-caption"><span className="pulse"/> {production?.version ? `Promoted model ${production.version} is active in paper scoring.` : 'Neural visualization active; trained model remains shadow until validation gates pass.'}</div>
  </div>
}


function Metric({title, value, sub, tone = 'neutral'}:{title:string, value:ReactNode, sub?:ReactNode, tone?:string}) {
  return <div className="metric">
    <small>{title}</small>
    <strong className={tone}>{value}</strong>
    {sub && <span>{sub}</span>}
  </div>
}

function SystemItem({label, value, good}:{label:string, value:ReactNode, good?:boolean}) {
  return <div className="system-item"><small>{label}</small><b>{good !== undefined && <span className={`mini-dot ${good ? 'good' : 'bad'}`} />}{value}</b></div>
}

function ActionButton({label, tip, onClick, disabled, tone = 'default'}:{label:string, tip:string, onClick:()=>void, disabled?:boolean, tone?:string}) {
  return <span className="action-wrap">
    <button type="button" className={`action-btn ${tone}`} onClick={onClick} disabled={disabled}>{label}</button>
    <span className="tooltip">{tip}</span>
  </span>
}
