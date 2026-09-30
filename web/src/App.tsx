import { useCallback, useEffect, useState } from 'react'
import { api, engineDecision, label, type CaseDetail, type CaseRow, type Stats } from './api'
import { CriteriaChecklist } from './components/CriteriaChecklist'
import { ReviewForm } from './components/ReviewForm'
import './App.css'

export default function App() {
  const [queue, setQueue] = useState<CaseRow[]>([])
  const [stats, setStats] = useState<Stats | null>(null)
  const [selected, setSelected] = useState<CaseDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [showNote, setShowNote] = useState(false)

  const load = useCallback(async () => {
    setError(null)
    try {
      const [q, s] = await Promise.all([api.queue(), api.stats()])
      setQueue(q)
      setStats(s)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not reach the API')
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function open(id: string) {
    setShowNote(false)
    try {
      setSelected(await api.get(id))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the case')
    }
  }

  async function decide(outcome: 'approve' | 'deny', clinician: string, rationale: string) {
    if (!selected) return
    await api.review(selected.case_id, clinician, outcome, rationale)
    setSelected(null)
    await load()
  }

  const detail = selected ? engineDecision(selected) : null

  return (
    <main>
      <header>
        <div>
          <h1>PriorAuth Copilot</h1>
          <p className="muted">Clinician review queue · CMS coverage criteria · the engine approves or pends, only clinicians deny</p>
        </div>
        {stats && (
          <dl className="stats">
            <div><dt>Cases</dt><dd>{stats.cases}</dd></div>
            <div><dt>Auto-approved</dt><dd>{stats.auto_approval_rate == null ? '-' : `${Math.round(stats.auto_approval_rate * 100)}%`}</dd></div>
            <div><dt>Awaiting review</dt><dd>{stats.by_status.pending_review ?? 0}</dd></div>
          </dl>
        )}
      </header>
      {error && <p role="alert" className="error">{error}. Start the API with: uvicorn priorauth.api:app --port 8920</p>}
      <div className="layout">
        <section className="card" aria-label="Review queue">
          <h2>Pending review <span className="muted">{queue.length}</span></h2>
          <ul className="queue">
            {queue.map((c) => (
              <li key={c.case_id}>
                <button className={`row ${selected?.case_id === c.case_id ? 'on' : ''}`} onClick={() => void open(c.case_id)}>
                  <strong>{c.case_id}</strong>
                  <span className={`tag ${c.urgency}`}>{c.urgency}</span>
                  <div className="muted small">{c.service}</div>
                </button>
              </li>
            ))}
            {queue.length === 0 && <li className="muted">Nothing waiting for review.</li>}
          </ul>
        </section>
        <section className="card" aria-label="Case">
          {!selected && <p className="muted">Select a case to see the criteria, the evidence and the decision form.</p>}
          {selected && (
            <>
              <h2>{selected.case_id} <span className="muted">· {label(selected.policy_id)}</span></h2>
              {detail && <CriteriaChecklist detail={detail} />}
              <button className="link" onClick={() => setShowNote((v) => !v)}>
                {showNote ? 'Hide' : 'Show'} clinical note
              </button>
              {showNote && selected.documents.map((d) => <pre key={d._id} className="note">{d.text}</pre>)}
              <ReviewForm onSubmit={decide} />
            </>
          )}
        </section>
      </div>
    </main>
  )
}
