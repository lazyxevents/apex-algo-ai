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
  async function reset() { await fetch(`${API}/api/risk/reset-kill-switch`, {method:'POST'}); load() }
  async function scan() { await fetch(`${API}/api/automation/run-once`, {method:'POST'}); load() }

  if (!status) return <main className="wrap"><h1>APEX Algo AI</h1><p>{error || 'Loading…'}</p></main>
  const p = status.performance || {}
  const r = status.risk || {}
  const learning = status.learning || {}
  return <main className="wrap">
    <header><div><h1>APEX Algo AI</h1><p>Adaptive risk-first Indian index options paper trading</p></div><b className={`mode ${status.mode}`}>{status.mode}</b></header>

    <section className="grid">
      <Card title="Configured Capital" value={`₹${r.configuredCapital}`} />
      <Card title="Effective Capital" value={`₹${r.effectiveCapital}`} />
      <Card title="Risk / Trade" value={`₹${r.riskPerTrade}`} />
      <Card title="Win Rate" value={`${p.winRate || 0}%`} />
      <Card title="Net P&L" value={`₹${p.netPnl || 0}`} />
      <Card title="Profit Factor" value={p.profitFactor || 0} />
      <Card title="Max Drawdown" value={`₹${p.maxDrawdown || 0}`} />
      <Card title="Open Positions" value={r.openPositions} />
    </section>

    <section className="panel"><h2>Automation</h2>
      <p>Window: <b>{status.automation.tradeWindow}</b> • Forced exit: <b>{status.automation.forceExit}</b> • Loop: <b>{status.automation.running ? 'Running' : 'Stopped'}</b></p>
      <p>Market data: <b>{status.market?.marketDataConfigured ? 'Configured' : 'Token missing'}</b> • Paper broker: <b>{status.market?.paperBroker}</b></p>
      <p>Adaptive learner: <b>{learning.enabled ? 'ON' : 'OFF'}</b> • Best arm: <b>{learning.bestArm}</b></p>
      <div className="actions">
        <button onClick={()=>mode('OFF')}>OFF</button><button onClick={()=>mode('PAPER')}>PAPER</button>
        <button onClick={()=>mode('SAFE')}>SAFE</button><button onClick={scan}>Run Scan Now</button>
        <button className="danger" onClick={kill}>KILL</button><button onClick={reset}>Reset Kill → OFF</button>
      </div>
      <p className="muted">No live broker order endpoint exists. Upstox sandbox orders are optional; Zerodha is reserved for a later phase.</p>
    </section>

    <section className="grid">
      <Card title="Today P&L" value={`₹${r.dailyPnl}`} />
      <Card title="Week P&L" value={`₹${r.weeklyPnl}`} />
      <Card title="Month P&L" value={`₹${r.monthlyPnl}`} />
      <Card title="Trades" value={p.trades || 0} />
      <Card title="Expectancy" value={`₹${p.expectancy || 0}`} />
      <Card title="Daily Lock" value={r.dailyLocked ? 'LOCKED' : 'Open'} />
    </section>

    <section className="panel"><h2>Last Decision</h2><pre>{JSON.stringify(status.automation.lastDecision || {action:'waiting'}, null, 2)}</pre></section>

    <section className="panel"><h2>Paper Trades</h2>
      {trades.length===0 ? <p>No paper trades yet. Put the system in PAPER mode after configuring market data.</p> :
      <div className="table"><div className="row head"><span>Instrument</span><span>Dir</span><span>Qty</span><span>Status</span><span>P&L</span></div>
      {trades.map(t=><div className="row" key={t.id}><span>{t.symbol}</span><span>{t.direction}</span><span>{t.quantity}</span><span>{t.status}</span><span>₹{t.pnl}</span></div>)}</div>}
    </section>
    <footer>APEX Algo AI • NO TRADE is valid • Adaptive learning is bounded to pre-approved paper strategy arms</footer>
  </main>
}
function Card({title,value}:{title:string,value:any}) { return <div className="card"><small>{title}</small><strong>{value}</strong></div> }
