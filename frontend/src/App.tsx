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

  async function mode(mode:string) {
    await fetch(`${API}/api/system/mode`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({mode})})
    load()
  }
  async function kill() { await fetch(`${API}/api/risk/kill-switch`, {method:'POST'}); load() }
  async function reset() { await fetch(`${API}/api/risk/reset-kill-switch`, {method:'POST'}); load() }

  if (!status) return <main className="wrap"><h1>APEX Algo AI</h1><p>{error || 'Loading…'}</p></main>
  return <main className="wrap">
    <header><div><h1>APEX Algo AI</h1><p>Risk-first options research & paper trading</p></div><b className={`mode ${status.mode}`}>{status.mode}</b></header>
    <section className="grid">
      <Card title="Capital" value={`₹${status.risk.capital}`} />
      <Card title="Risk / trade" value={`₹${status.risk.maxRiskPerTrade}`} />
      <Card title="Daily loss limit" value={`₹${status.risk.maxDailyLoss}`} />
      <Card title="Open positions" value={status.risk.openPositions} />
      <Card title="Trades today" value={status.risk.tradesToday} />
      <Card title="Broker" value={status.broker.connected ? 'Connected' : 'Not connected'} />
    </section>
    <section className="panel"><h2>Controls</h2><div className="actions">
      <button onClick={()=>mode('OFF')}>OFF</button><button onClick={()=>mode('PAPER')}>PAPER</button>
      <button onClick={()=>mode('SAFE')}>SAFE</button><button className="danger" onClick={kill}>KILL</button>
      <button onClick={reset}>Reset Kill → OFF</button>
    </div><p>Live order execution is not implemented in this MVP and remains disabled.</p></section>
    <section className="panel"><h2>Paper Trades</h2>
      {trades.length===0 ? <p>No paper trades yet. Use POST /api/paper/orders or Swagger at /docs.</p> :
      <div className="table"><div className="row head"><span>Symbol</span><span>Dir</span><span>Qty</span><span>Status</span><span>P&L</span></div>
      {trades.map(t=><div className="row" key={t.id}><span>{t.symbol}</span><span>{t.direction}</span><span>{t.quantity}</span><span>{t.status}</span><span>₹{t.pnl}</span></div>)}</div>}
    </section>
    <footer>APEX Algo AI • NO TRADE is a valid outcome • Live trading disabled</footer>
  </main>
}
function Card({title,value}:{title:string,value:any}) { return <div className="card"><small>{title}</small><strong>{value}</strong></div> }
