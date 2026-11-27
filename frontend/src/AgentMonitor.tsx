import { useEffect, useMemo, useState } from 'react'
import './AgentMonitor.css'

const API = import.meta.env.VITE_API_URL || (import.meta.env.PROD ? 'https://apex-algo-backend.onrender.com' : 'http://localhost:8000')
const FLEET_TARGET = 100

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
type LlmHealth = {
  configured?: boolean
  provider?: string
  model?: string
  lastStatus?: string
  lastError?: string
  lastCallAt?: string | null
  lastSuccessAt?: string | null
  calls?: number
  successes?: number
  rateLimits?: number
  cooldownRemainingSeconds?: number
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
  llm?: LlmHealth
}

const clock = (value?: string | null) => value ? new Date(value).toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata' }) : '—'
const duration = (value?: number | null) => value == null ? '—' : value < 1000 ? `${value} ms` : `${(value / 1000).toFixed(1)} s`
const pad = (n: number) => String(n).padStart(2, '0')

export default function AgentMonitor() {
  const [data, setData] = useState<AgentSnapshot | null>(null)
  const [error, setError] = useState('')
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null)
  const [now, setNow] = useState(Date.now())

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
    const clockTimer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => { active = false; window.clearInterval(timer); window.clearInterval(clockTimer) }
  }, [])

  const agents = data?.agents || []
  const llm = data?.llm || {}
  const llmPaused = llm.lastStatus === 'rate_limited' && Number(llm.cooldownRemainingSeconds || 0) > 0
  const llmHealthy = llm.configured && llm.lastStatus === 'ok'
  const slots = useMemo(() => Array.from({ length: FLEET_TARGET }, (_, index) => ({
    number: index + 1,
    agent: agents[index] || null,
  })), [agents])
  const running = data?.running || 0
  const provisionedPct = Math.min(100, ((data?.registered || 0) / FLEET_TARGET) * 100)

  return <section className="agent-command" aria-label="APEX agent fleet control center">
    <div className="agent-command-top">
      <div className="agent-command-brand">
        <div className="agent-orbit"><span/><i/><b>A</b></div>
        <div>
          <div className="agent-kicker">AUTONOMOUS INTELLIGENCE NETWORK <span className="agent-version">RUNTIME / 01</span></div>
          <h2>Agent Command Center</h2>
          <p>100-agent architecture overview · live telemetry from the production backend</p>
        </div>
      </div>
      <div className={`agent-connection ${error ? 'offline' : 'online'}`}><i/>{error ? 'BACKEND DISCONNECTED' : 'LIVE TELEMETRY'} <span>· 3 SEC</span></div>
    </div>

    {error && <div className="agent-error">{error}</div>}

    <div className="agent-command-layout">
      <aside className="agent-overview">
        <div className="agent-fleet-number">
          <div><small>REGISTERED WORKERS</small><strong>{data?.registered ?? '—'}<em> / 100</em></strong></div>
          <span className="agent-fleet-target">FLEET<br/>TARGET</span>
        </div>
        <div className="agent-fleet-progress"><span style={{ width: `${provisionedPct}%` }}/></div>
        <div className="agent-fleet-progress-label"><span>{data?.registered ?? 0} provisioned</span><b>{Math.max(0, FLEET_TARGET - (data?.registered || 0))} slots not provisioned</b></div>

        <div className="agent-stat-grid">
          <div className="agent-stat"><small>RUNNING</small><strong className="agent-green">{data?.running ?? '—'}</strong><span>Executing now</span></div>
          <div className="agent-stat"><small>IDLE</small><strong>{data?.idle ?? '—'}</strong><span>Waiting for work</span></div>
          <div className="agent-stat"><small>FAILED</small><strong className={data?.failed ? 'agent-red' : ''}>{data?.failed ?? '—'}</strong><span>Execution errors</span></div>
          <div className="agent-stat"><small>STALE</small><strong className={data?.stale ? 'agent-amber' : ''}>{data?.stale ?? '—'}</strong><span>Heartbeat overdue</span></div>
        </div>

        <div className={`agent-llm-card ${llmPaused ? 'paused' : llmHealthy ? 'healthy' : ''}`}>
          <div className="agent-llm-head"><span className="agent-llm-icon">✳</span><div><small>LLM REASONING LAYER</small><b>{llmPaused ? 'RATE LIMITED' : llmHealthy ? 'ONLINE' : llm.configured ? String(llm.lastStatus || 'STANDBY').replace(/_/g, ' ').toUpperCase() : 'NOT CONFIGURED'}</b></div><i/></div>
          <div className="agent-llm-provider">{llm.provider || 'Provider unknown'} <span>{llm.model || 'No model selected'}</span></div>
          {llmPaused ? <div className="agent-cooldown"><strong>{Math.ceil(Number(llm.cooldownRemainingSeconds || 0))}s</strong><span>cooldown remaining</span></div> : <div className="agent-llm-foot"><span>Calls <b>{llm.calls ?? '—'}</b></span><span>Success <b>{llm.successes ?? '—'}</b></span><span>429 <b>{llm.rateLimits ?? 0}</b></span></div>}
          {llmPaused && <p>LLM calls paused automatically. Market scanning and deterministic risk checks continue.</p>}
          {!llmPaused && llm.lastError && <p>{llm.lastError}</p>}
        </div>

        <div className="agent-safety-line"><i/> <span>Deterministic Risk Engine</span><b>SEPARATE AUTHORITY</b></div>
        <div className="agent-refresh-line">Updated {updatedAt ? updatedAt.toLocaleTimeString('en-IN') : 'waiting for API'}</div>
      </aside>

      <div className="agent-fleet">
        <div className="agent-fleet-head">
          <div><small>FLEET TOPOLOGY</small><h3>100 Agent Slots</h3><p>Live workers are highlighted. Unprovisioned slots are roadmap capacity, not running bots.</p></div>
          <div className="agent-legend"><span><i className="live"/> LIVE / REGISTERED</span><span><i className="planned"/> NOT PROVISIONED</span></div>
        </div>
        <div className="agent-slot-grid" aria-label="100 agent fleet slots">
          {slots.map(slot => <div
            key={slot.number}
            title={slot.agent ? `#${pad(slot.number)} ${slot.agent.name} — ${slot.agent.status}` : `#${pad(slot.number)} Planned slot — not provisioned`}
            className={`agent-slot ${slot.agent ? slot.agent.status.toLowerCase() : 'planned'}`}
          ><span>{pad(slot.number)}</span>{slot.agent && <i/>}</div>)}
        </div>
        <div className="agent-fleet-caption"><b>{data?.registered ?? 0} REAL WORKER ROLES</b><span>·</span><span>{FLEET_TARGET - (data?.registered || 0)} future slots</span><span>·</span><span>{running} executing now</span></div>
      </div>
    </div>

    <div className="agent-worker-section">
      <div className="agent-worker-heading"><div><small>RUNTIME DETAIL</small><h3>Registered Worker Processes</h3></div><span>ACTUAL BACKEND TELEMETRY</span></div>
      <div className="agent-worker-grid">
        {agents.map((agent, index) => <article className="agent-worker-card" key={agent.id}>
          <div className="agent-worker-top"><span>NODE {pad(index + 1)}</span><b className={`agent-status ${agent.status.toLowerCase()}`}><i/>{agent.status}</b></div>
          <h4>{agent.name}</h4>
          <p>{agent.description}</p>
          <div className="agent-worker-task">{agent.currentTask}</div>
          {agent.lastError && <div className="agent-last-error">{agent.lastError}</div>}
          <div className="agent-worker-meta"><span>Runs <b>{agent.runs}</b></span><span>Fails <b>{agent.failures}</b></span><span>Last <b>{duration(agent.lastDurationMs)}</b></span></div>
          <div className="agent-worker-heartbeat">Heartbeat {clock(agent.lastHeartbeatAt)}</div>
        </article>)}
        {!agents.length && <div className="agent-empty">Waiting for real backend worker telemetry…</div>}
      </div>
    </div>
    <div className="agent-command-foot"><span>{data?.note || 'Worker telemetry unavailable.'}</span><b>LAST SYNC {updatedAt ? updatedAt.toLocaleTimeString('en-IN') : '—'}</b></div>
  </section>
}
