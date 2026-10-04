import { useEffect, useState } from 'react'

type Status = any
const API = import.meta.env.VITE_API_URL || 'http://localhost:8000'

export default function App() {
  const [status, setStatus] = useState<Status>(null)
  const [trades, setTrades] = useState<any[]>([])
  const [error, setError] = useState('')

  async function load() {
    try {
      const [s, t] = await Promise.all([fetch(`${API}/api/system/status`), fetch(`${API}/api/trades`)])
      setStatus(await s.json()); setTrades(await t.json()); setError('')
    } catch (e:any) { setError(e.message) }
  }
  useEffect(() => { load(); const id = setInterval(load, 3000); return () => clearInterval(id) }, [])

  async function mode(value:string) {
    await fetch(`${API}/api/system/mode`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({mode:value})})
    load()
  }
  async function kill() { await fetch(`${API}/api/risk/kill-switch`, {method:'POST'}); load() }
  async function flatten() { await fetch(`${API}/api/risk/flatten`, {method:'POST'}); load() }
  async function reset() { await fetch(`${API}/api/risk/reset-kill-switch`, {method:'POST'}); load() }
  async function scan() { await fetch(`${API}/api/automation/run-once`, {method:'POST'}); load() }
  async function research() { await fetch(`${API}/api/research/run-once`, {method:'POST'}); load() }

  if (!status) return <main className="wrap"><h1>APEX Algo AI</h1><p>{error || 'Loading…'}</p></main>
  const p = status.performance || {}
  const r = status.risk || {}
  const learning = status.learning || {}
  const mt = r.monthlyTarget || {}
  const researchInfo = learning.latestResearch || {}
  const market = status.market || {}
  const providerReady = market.marketDataConfigured !== false

  return <main className="wrap">
    <header><div><h1>APEX Algo AI</h1><p>Context-aware Indian index options paper trading</p></div><b className={`mode ${status.mode}`}>{status.mode}</b></header>

    <section className="grid">
      <Card title="Base Capital" value={`₹${r.configuredCapital ?? 0}`} />
      <Card title="Effective Capital" value={`₹${r.effectiveCapital ?? 0}`} />
      <Card title="Risk / Trade" value={`₹${r.riskPerTrade ?? 0}`} />
      <Card title="Win Rate" value={`${p.winRate || 0}%`} />
      <Card title="Net P&L" value={`₹${p.netPnl || 0}`} />
      <Card title="Profit Factor" value={p.profitFactor || 0} />
      <Card title="Max Drawdown" value={`₹${p.maxDrawdown || 0}`} />
      <Card title="Open Positions" value={r.openPositions || 0} />
    </section>

    <section className="panel"><h2>Monthly Target Lock</h2>
      <p>Month start equity: <b>₹{mt.monthStartEquity || 0}</b> • Target: <b>{mt.targetPct || 0}% / ₹{mt.targetAmount || 0}</b> • Current month P&L: <b>₹{mt.monthPnl || 0}</b></p>
      <p>Remaining: <b>₹{mt.remaining || 0}</b> • Status: <b>{r.monthlyTargetLocked ? 'LOCKED / SAFE' : 'ACTIVE'}</b> • Auto compound: <b>{r.autoCompoundProfits ? 'ON' : 'OFF'}</b></p>
    </section>

    <section className="panel"><h2>Automation & Safety</h2>
      <p>Entry window: <b>{status.automation.tradeWindow}</b> • Forced exit: <b>{status.automation.forceExit}</b> • Research: <b>{status.automation.researchTime}</b></p>
      <p>
        Loop: <b>{status.automation.running ? 'Running' : 'Stopped'}</b>
        {' '}• Provider: <b>{market.provider || 'unknown'}</b>
        {' '}• Market data: <b>{providerReady ? 'Ready' : 'Not ready'}</b>
        {' '}• Token required: <b>{market.tokenRequired ? 'Yes' : 'No'}</b>
        {' '}• Paper broker: <b>{market.paperBroker}</b>
      </p>
      {market.syntheticPaper && <p className="muted"><b>Demo mode:</b> Yahoo supplies index candles; option premium/lot execution is synthetic and is not a real NSE option-chain simulation.</p>}
      <div className="actions">
        <button onClick={()=>mode('OFF')}>OFF</button><button onClick={()=>mode('PAPER')}>PAPER</button>
        <button onClick={()=>mode('SAFE')}>SAFE</button><button onClick={scan}>Run Scan Now</button>
        <button onClick={research}>Run Research</button><button onClick={flatten}>Flatten</button>
        <button className="danger" onClick={kill}>KILL + FLATTEN</button><button onClick={reset}>Reset Kill → OFF</button>
      </div>
      <p className="muted">Kill and scheduled exits close internal paper positions even if fresh quote retrieval fails. Live trading remains disabled.</p>
    </section>

    <section className="grid">
      <Card title="Today P&L" value={`₹${r.dailyPnl || 0}`} />
      <Card title="Week P&L" value={`₹${r.weeklyPnl || 0}`} />
      <Card title="Month P&L" value={`₹${r.monthlyPnl || 0}`} />
      <Card title="Trades" value={p.trades || 0} />
      <Card title="Expectancy" value={`₹${p.expectancy || 0}`} />
      <Card title="Daily Lock" value={r.dailyLocked ? 'LOCKED' : 'Open'} />
    </section>

    <section className="panel"><h2>Adaptive Learning</h2>
      <p>Method: <b>{learning.method}</b></p>
      <p>Global best arm: <b>{learning.bestArm}</b> • Exploration: <b>{Math.round((learning.explorationRate || 0) * 100)}%</b> • Research run: <b>{researchInfo.runDate || 'not run yet'}</b></p>
      <pre>{JSON.stringify({qValues: learning.qValues, researchArms: researchInfo.arms}, null, 2)}</pre>
    </section>

    <section className="panel"><h2>Last Decision</h2><pre>{JSON.stringify(status.automation.lastDecision || {action:'waiting'}, null, 2)}</pre></section>

    <section className="panel"><h2>Paper Trades</h2>
      {trades.length===0 ? <p>No paper trades yet. Switch to PAPER and keep market data available.</p> :
      <div className="table"><div className="row head"><span>Instrument</span><span>Dir</span><span>Qty</span><span>Status</span><span>P&L</span></div>
      {trades.map(t=><div className="row" key={t.id}><span>{t.symbol}</span><span>{t.direction}</span><span>{t.quantity}</span><span>{t.status}</span><span>₹{t.pnl}</span></div>)}</div>}
    </section>
    <footer>APEX Algo AI • NO TRADE is valid • Monthly target is a lock, not a guaranteed return • Live orders disabled</footer>
  </main>
}
function Card({title,value}:{title:string,value:any}) { return <div className="card"><small>{title}</small><strong>{value}</strong></div> }
