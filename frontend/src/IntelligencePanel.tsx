import { useEffect, useState } from 'react'
import './IntelligencePanel.css'

const API = import.meta.env.VITE_API_URL || (import.meta.env.PROD ? 'https://apex-algo-backend.onrender.com' : 'http://localhost:8000')
const money = (value: unknown) => new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(Number(value || 0))
const pct = (value: unknown) => `${Number(value || 0).toFixed(1)}%`

type Scorecard = {
  summary?: any
  byIndex?: any[]
  byStrategy?: any[]
  byRegime?: any[]
  dataQuality?: any
}

export default function IntelligencePanel() {
  const [data, setData] = useState<Scorecard | null>(null)
  const [error, setError] = useState('')
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null)

  useEffect(() => {
    let active = true
    const load = async () => {
      try {
        const response = await fetch(`${API}/api/intelligence/scorecard`)
        if (!response.ok) throw new Error(`Intelligence API returned ${response.status}`)
        const result = await response.json()
        if (active) {
          setData(result)
          setError('')
          setUpdatedAt(new Date())
        }
      } catch (err) {
        if (active) setError(err instanceof Error ? err.message : 'Unable to load intelligence data')
      }
    }
    void load()
    const timer = window.setInterval(load, 30000)
    return () => { active = false; window.clearInterval(timer) }
  }, [])

  const summary = data?.summary || {}
  const rows = (items: any[] = []) => items.length
    ? items.slice(0, 5).map(item => <tr key={item.name}><td>{String(item.name).replace(/_/g, ' ')}</td><td>{item.trades}</td><td>{pct(item.winRatePct)}</td><td className={Number(item.netPnl) >= 0 ? 'intel-positive' : 'intel-negative'}>{money(item.netPnl)}</td><td>{money(item.expectancy)}</td></tr>)
    : <tr><td colSpan={5} className="intel-empty">No validated trade history yet.</td></tr>

  return <section className="panel intelligence-panel">
    <div className="section-head compact">
      <div><div className="eyebrow">APEX INTELLIGENCE</div><h2>Performance & Learning Intelligence</h2><p>Evidence from closed paper trades. This panel reports observed performance; it does not invent predictive probabilities.</p></div>
      <span className="badge subtle">{updatedAt ? `UPDATED ${updatedAt.toLocaleTimeString('en-IN')}` : 'CONNECTING'}</span>
    </div>
    {error && <div className="intel-error">{error}</div>}
    <div className="intel-metrics">
      <div><small>Closed Trades</small><strong>{summary.trades ?? '—'}</strong><span>{summary.sampleQuality?.replace(/_/g, ' ') || 'WAITING FOR DATA'}</span></div>
      <div><small>Win Rate</small><strong>{pct(summary.winRatePct)}</strong><span>{summary.wins ?? 0} wins / {summary.losses ?? 0} losses</span></div>
      <div><small>Net P&amp;L</small><strong className={Number(summary.netPnl) >= 0 ? 'intel-positive' : 'intel-negative'}>{money(summary.netPnl)}</strong><span>Recorded paper outcomes</span></div>
      <div><small>Expectancy / Trade</small><strong>{money(summary.expectancy)}</strong><span>Average net result</span></div>
      <div><small>Profit Factor</small><strong>{Number(summary.profitFactor || 0) >= 999 ? '∞' : Number(summary.profitFactor || 0).toFixed(2)}</strong><span>Gross wins / gross losses</span></div>
      <div><small>Max Drawdown</small><strong className="intel-negative">{money(summary.maxDrawdown)}</strong><span>Closed-trade sequence</span></div>
    </div>
    <div className="intel-tables">
      <div className="intel-table-card"><h3>Index Performance</h3><div className="table-scroll"><table><thead><tr><th>Index</th><th>Trades</th><th>Win %</th><th>Net P&amp;L</th><th>Expectancy</th></tr></thead><tbody>{rows(data?.byIndex)}</tbody></table></div></div>
      <div className="intel-table-card"><h3>Strategy Performance</h3><div className="table-scroll"><table><thead><tr><th>Strategy</th><th>Trades</th><th>Win %</th><th>Net P&amp;L</th><th>Expectancy</th></tr></thead><tbody>{rows(data?.byStrategy)}</tbody></table></div></div>
      <div className="intel-table-card"><h3>Market Regime Performance</h3><div className="table-scroll"><table><thead><tr><th>Regime</th><th>Trades</th><th>Win %</th><th>Net P&amp;L</th><th>Expectancy</th></tr></thead><tbody>{rows(data?.byRegime)}</tbody></table></div></div>
    </div>
    <div className="intel-footnote"><b>Data coverage:</b> {data?.dataQuality?.closedValidTrades ?? 0} closed valid trades • {data?.dataQuality?.regimeCoverage ?? 0} trades with regime labels. {data?.dataQuality?.warning || 'Historical performance is not a guarantee of future results.'}</div>
  </section>
}
