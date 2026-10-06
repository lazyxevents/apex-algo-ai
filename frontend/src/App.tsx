import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'

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

  const loadAll = useCallback(async () => {
    await Promise.all([loadStatus(), loadTrades()])
  }, [loadStatus, loadTrades])

  useEffect(() => {
    void loadAll()
    const tradePoll = window.setInterval(() => void loadTrades(true), 1000)
    const statusPoll = window.setInterval(() => void loadStatus(true), 3000)
    const clockPoll = window.setInterval(() => setClock(new Date()), 1000)
    return () => {
      window.clearInterval(tradePoll)
      window.clearInterval(statusPoll)
      window.clearInterval(clockPoll)
    }
  }, [loadAll, loadStatus, loadTrades])

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

  const openTrades = useMemo(() => trades.filter(t => t.status === 'OPEN'), [trades])
  const closedTrades = useMemo(() => trades.filter(t => t.status !== 'OPEN'), [trades])
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
  const providerReady = market.marketDataConfigured !== false
  const killed = Boolean(status.killSwitch || r.killSwitch)
  const phase = automation.lastDecision?.phase || {}
  const targetAmount = Number(mt.targetAmount || 0)
  const monthPnl = Number(mt.monthPnl || 0)
  const targetProgress = targetAmount > 0 ? Math.max(0, Math.min(100, monthPnl / targetAmount * 100)) : 0

  const scanDisabled = Boolean(busy || killed || status.mode !== 'PAPER' || !providerReady)
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
        <span>Yahoo supplies index data; option premium and execution are simulated. This is for forward testing, not proof of real broker fills.</span>
      </div>
      <span className="badge">LIVE ORDERS OFF</span>
    </section>}

    <section className="summary-grid">
      <Metric title="Running P&L" value={money(runningPnl)} sub={openTrades.length ? `${runningPnlPct >= 0 ? '+' : ''}${percent(runningPnlPct)} on open positions` : 'No open position'} tone={pnlClass(runningPnl)} />
      <Metric title="Realized Net P&L" value={money(p.netPnl)} sub={`${p.trades || 0} closed trades`} tone={pnlClass(p.netPnl)} />
      <Metric title="Effective Capital" value={money(r.effectiveCapital)} sub={`Base ${money(r.configuredCapital)}`} />
      <Metric title="Open Positions" value={String(openTrades.length)} sub={`Max ${r.maxConcurrentPositions ?? 0}`} />
      <Metric title="Today P&L" value={money(r.dailyPnl)} sub={`${r.tradesToday || 0} trade(s) today`} tone={pnlClass(r.dailyPnl)} />
      <Metric title="Win Rate" value={percent(p.winRate)} sub={`${p.wins || 0}W / ${p.losses || 0}L`} />
      <Metric title="Risk / Trade" value={money(r.riskPerTrade)} sub={`${r.dynamicLimits ? 'Dynamic' : 'Fixed'} risk limit`} />
      <Metric title="Deployable Cap" value={money(Number(r.effectiveCapital || 0) * Number(r.capitalUsagePct || 0) / 100)} sub={`${r.capitalUsagePct || 0}% maximum usage`} />
      <Metric title="Max Drawdown" value={money(p.maxDrawdown)} sub={`Monthly cap ${money(r.maxMonthlyDrawdown)}`} tone={Number(p.maxDrawdown) > 0 ? 'negative' : 'neutral'} />
    </section>


    <section className="panel">
      <div className="section-head compact">
        <div>
          <div className="eyebrow">MARKET RESEARCH</div>
          <h2>1m / 5m / 15m Structure Snapshot</h2>
          <p>Research-only analytics: trend, support/resistance, BOS/CHOCH proxy, liquidity sweep, FVG and fake-breakout labels. Scheduled from <b>{automation.premarketResearchTime || '08:00'} IST</b>.</p>
        </div>
        <span className="badge subtle">{marketResearch.status || 'waiting'}</span>
      </div>
      <div className="system-strip">
        {['NIFTY','BANKNIFTY','SENSEX'].map(name => {
          const m = researchMarkets[name] || {}
          const f1 = m.frames?.['1m'] || {}
          const f5 = m.frames?.['5m'] || {}
          const f15 = m.frames?.['15m'] || {}
          return <div className="system-item" key={name}>
            <small>{name}</small>
            <b>{m.state || 'WAITING'}</b>
            <span style={{display:'block',marginTop:6,fontSize:10,color:'#71849a'}}>
              1m {f1.trend || '—'} • 5m {f5.trend || '—'} • 15m {f15.trend || '—'}
            </span>
            <span style={{display:'block',marginTop:4,fontSize:10,color:'#71849a'}}>
              S {f5.structure?.levels?.support ?? '—'} • R {f5.structure?.levels?.resistance ?? '—'}
            </span>
          </div>
        })}
      </div>
      {marketResearch.llm?.summary && <p className="panel-note"><b>Ollama summary:</b> {marketResearch.llm.summary}</p>}
      <p className="panel-note">News/LLM research stays optional and does not place orders or override hard risk controls.</p>
    </section>

    <section className="panel learning-panel">
      <div className="section-head compact">
        <div><div className="eyebrow">APEX LEARNING ENGINE</div><h2>Research → Patterns → Backtest → Candidate Model</h2><p>Live worker visibility. Research cannot bypass hard risk controls or place orders directly.</p></div>
        <span className={`badge subtle ${worker.running ? 'worker-live' : ''}`}>{worker.running ? 'LEARNING NOW' : (worker.stage || 'WAITING')}</span>
      </div>
      <div className="learning-grid">
        <SystemItem label="Worker" value={worker.enabled ? 'Enabled' : 'Disabled'} good={worker.enabled} />
        <SystemItem label="Stage" value={worker.stage || 'idle'} />
        <SystemItem label="Cycles today" value={worker.cyclesToday ?? 0} />
        <SystemItem label="Runtime today" value={`${worker.researchHoursToday ?? 0}h / ${worker.dailyHourBudget ?? 15}h`} />
        <SystemItem label="Sources reviewed" value={worker.sourcesReviewed ?? 0} />
        <SystemItem label="Patterns found" value={worker.patternsDetected ?? 0} />
        <SystemItem label="Hypotheses" value={worker.hypothesesTested ?? 0} />
        <SystemItem label="Backtests" value={worker.backtestsRun ?? 0} />
        <SystemItem label="Dataset" value={worker.datasetSize ?? 0} />
        <SystemItem label="Candidate" value={worker.candidateVersion || 'not ready'} />
        <SystemItem label="Candidate score" value={worker.candidateScore != null ? Number(worker.candidateScore).toFixed(2) : '—'} />
        <SystemItem label="Ollama" value={ollama.configured ? 'Connected' : (ollama.enabled ? 'Needs endpoint' : 'Disabled')} good={ollama.configured} />
        <SystemItem label="LLM model" value={ollama.model || '—'} />
      </div>
      <div className="worker-status"><b>{worker.currentTask || 'Waiting for next research cycle'}</b><span>Heartbeat {formatDateTime(worker.lastHeartbeatAt)}</span></div>
      {worker.lastSummary && <p className="panel-note">{worker.lastSummary}</p>}
      {worker.candidateMetrics && <details className="candidate-detail"><summary>Candidate validation metrics</summary><pre>{JSON.stringify(worker.candidateMetrics, null, 2)}</pre></details>}
      {worker.lastError && <p className="panel-note negative"><b>Safe failure:</b> {worker.lastError}</p>}
      <div className="actions"><ActionButton label="Run Learning Cycle" disabled={Boolean(busy || !providerReady)} tip="Run one bounded research, pattern and backtest cycle now." onClick={() => runAction('learning', 'Learning cycle completed.', () => fetchJson('/api/learning/run-once', {method:'POST'}))} /></div>
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
            <th>Instrument</th><th>Side</th><th>Qty</th><th>Entry</th><th>LTP</th><th>Stop</th><th>Target</th><th>Plan</th><th>Gross</th><th>Est. costs</th><th>Net P&L</th><th>P&L %</th><th>Chart</th><th>Opened</th>
          </tr></thead>
          <tbody>{openTrades.map(t => <tr key={t.id}>
            <td>
              <div className="instrument">{instrumentName(t.symbol)}</div>
              <small>{t.meta?.strategy || 'APEX'} • #{t.id}</small>
            </td>
            <td><span className={`side ${t.direction}`}>{t.direction}</span></td>
            <td>{t.quantity}</td>
            <td>{money(t.entry)}</td>
            <td className="ltp">{money(t.currentPrice)}</td>
            <td>{money(t.stop)}</td>
            <td>{money(t.target)}</td>
            <td><small>{t.meta?.tradeStyle || '—'} • T1 {t.meta?.firstTarget ? money(t.meta.firstTarget) : '—'}<br/>{t.meta?.stopModel ? 'Structure SL' : '—'}</small></td>
            <td>{money((t as any).grossPnl ?? t.pnl)}</td>
            <td>{money((t as any).estimatedCharges ?? 0)}</td>
            <td className={`pnl ${pnlClass(t.pnl)}`}>{Number(t.pnl) > 0 ? '+' : ''}{money(t.pnl)}</td>
            <td className={`pnl ${pnlClass(tradePnlPct(t))}`}>{tradePnlPct(t) > 0 ? '+' : ''}{percent(tradePnlPct(t))}</td>
            <td>{t.chartUrl ? <a className="chart-link" href={t.chartUrl} target="_blank" rel="noreferrer">Open Chart ↗</a> : '—'}</td>
            <td className="time-cell">{formatDateTime(t.openedAt)}</td>
          </tr>)}</tbody>
        </table>
      </div>}
      <p className="panel-note">Note: dashboard checks every 1s, but Yahoo/yfinance itself can be delayed and may not publish a new market price every second.</p>
    </section>

    <section className="two-col">
      <section className="panel">
        <div className="section-head compact"><div><div className="eyebrow">CONTROL</div><h2>Automation & Safety</h2></div></div>
        <div className="system-strip">
          <SystemItem label="Strategy loop" value={automation.running ? 'Running' : 'Stopped'} good={automation.running} />
          <SystemItem label="Position monitor" value={positionMonitor.running ? 'Running' : 'Stopped'} good={positionMonitor.running} />
          <SystemItem label="Market data" value={providerReady ? 'Ready' : 'Not ready'} good={providerReady} />
          <SystemItem label="Provider" value={market.provider || 'unknown'} />
          <SystemItem label="Paper broker" value={market.paperBroker || 'internal'} />
          <SystemItem label="Phase" value={phase.reason || 'Waiting'} />
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
        <div><div className="eyebrow">JOURNAL</div><h2>Trade History</h2><p>Closed paper trades with final P&L and timestamps.</p></div>
        <span className="badge subtle">{closedTrades.length} CLOSED</span>
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
            <td><span className={`trade-status ${t.status}`}>{t.status}</span></td>
            <td>{money((t as any).grossPnl ?? t.pnl)}</td>
            <td>{money((t as any).estimatedCharges ?? 0)}</td>
            <td className={`pnl ${pnlClass(t.pnl)}`}>{Number(t.pnl) > 0 ? '+' : ''}{money(t.pnl)}</td>
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

    <footer>
      <span>APEX Algo AI • Testing dashboard</span>
      <span>NO TRADE is valid • Monthly target is a lock, not a guaranteed return • Real broker orders disabled</span>
    </footer>
  </main>
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
