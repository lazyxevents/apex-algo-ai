import { useEffect, useState } from 'react'
import './AgentMonitor.css'

const API = import.meta.env.VITE_API_URL || (import.meta.env.PROD ? 'https://apex-algo-backend.onrender.com' : 'http://localhost:8000')

type Agent = {
  id: string
  name: string
  group: string
  description: string
  status: 'RUNNING' | 'IDLE' | 'FAILED' | 'STALE' | 'DISABLED'
  currentTask: string
  lastHeartbeatAt?: string | null
  lastCompletedAt?: string | null
  runs: number
  failures: number
  lastDurationMs?: number | null
  lastError?: string | null
}
type AgentSnapshot = {
  registered: number
  running: number
  idle: number
  failed: number
  stale: number
  updatedAt: string
  agents: Agent[]
  note: string
}

const clock = (value?: string | null) => value ? new Date(value).toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata' }) : '—'
const duration = (value?: number | null) => value == null ? '—' : value < 1000 ? `${value} ms` : `${(value / 1000).toFixed(1)} s`

export default function AgentMonitor() {
  const [data, setData] = useState<AgentSnapshot | null>(null)
  const [error, setError] = useState('')
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null)

  useEffect(() => {
    let active = true
    const load = async () => {
      try {
        const response = await fetch(`${API}/api/agents/status`, { cache: 'no-store' })
        if (!response.ok) throw new Error(`Agent monitor API returned ${response.status}`)
        const result = await response.json() as AgentSnapshot
        if (active) {
          setData(result)
          setError('')
          setUpdatedAt(new Date())
        }
      } catch (err) {
        if (active) setError(err instanceof Error ? err.message : 'Unable to load agent status')
      }
    }
    void load()
    const timer = window.setInterval(load, 3000)
    return () => { active = false; window.clearInterval(timer) }
  }, [])

  return <section className="panel agent-monitor">
    <div className="section-head compact">
      <div>
        <div className="eyebrow">APEX RUNTIME OBSERVABILITY</div>
        <h2>Agent Control Center</h2>
        <p>Live status of instrumented backend workers. This panel reports actual runtime activity, not simulated agent counts.</p>
      </div>
      <span className={`agent-live-pill ${error ? 'offline' : 'online'}`}><i />{error ? 'API OFFLINE' : '3S LIVE REFRESH'}</span>
    </div>

    {error && <div className="agent-error">{error}</div>}

    <div className="agent-metrics">
      <div><small>Registered workers</small><strong>{data?.registered ?? '—'}</strong><span>Real worker modules</span></div>
      <div><small>Running now</small><strong className="agent-green">{data?.running ?? '—'}</strong><span>Active execution</span></div>
      <div><small>Idle / waiting</small><strong>{data?.idle ?? '—'}</strong><span>Available / scheduled</span></div>
      <div><small>Failed</small><strong className={data?.failed ? 'agent-red' : ''}>{data?.failed ?? '—'}</strong><span>Last execution errors</span></div>
      <div><small>Stale heartbeat</small><strong className={data?.stale ? 'agent-amber' : ''}>{data?.stale ?? '—'}</strong><span>No heartbeat for 120s</span></div>
    </div>

    <div className="agent-table-wrap">
      <table className="agent-table">
        <thead><tr><th>Worker</th><th>Status</th><th>Current task</th><th>Runs</th><th>Failures</th><th>Last heartbeat</th><th>Last duration</th></tr></thead>
        <tbody>
          {(data?.agents || []).map(agent => <tr key={agent.id}>
            <td><b>{agent.name}</b><small>{agent.group} · {agent.description}</small></td>
            <td><span className={`agent-status ${agent.status.toLowerCase()}`}><i />{agent.status}</span></td>
            <td className="agent-task">{agent.currentTask}{agent.lastError && <small className="agent-last-error">{agent.lastError}</small>}</td>
            <td>{agent.runs}</td>
            <td className={agent.failures ? 'agent-red' : ''}>{agent.failures}</td>
            <td>{clock(agent.lastHeartbeatAt)}</td>
            <td>{duration(agent.lastDurationMs)}</td>
          </tr>)}
          {!data?.agents?.length && <tr><td colSpan={7} className="agent-empty">Waiting for backend agent telemetry…</td></tr>}
        </tbody>
      </table>
    </div>
    <div className="agent-foot">
      <span>{data?.note || 'Waiting for telemetry.'}</span>
      <b>Last dashboard refresh: {updatedAt ? updatedAt.toLocaleTimeString('en-IN') : '—'}</b>
    </div>
  </section>
}
