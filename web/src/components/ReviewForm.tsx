import { useState } from 'react'

interface Props {
  onSubmit: (outcome: 'approve' | 'deny', clinician: string, rationale: string) => Promise<void>
}

const MIN = 10

export function ReviewForm({ onSubmit }: Props) {
  const [clinician, setClinician] = useState('')
  const [rationale, setRationale] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const ready = clinician.trim().length > 0 && rationale.trim().length >= MIN && !busy

  async function send(outcome: 'approve' | 'deny') {
    setBusy(true)
    setError(null)
    try {
      await onSubmit(outcome, clinician.trim(), rationale.trim())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save the decision')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="review" aria-label="Clinical review">
      <h3>Clinical review</h3>
      <label>
        Reviewer
        <input value={clinician} onChange={(e) => setClinician(e.target.value)} placeholder="e.g. dr.rao" />
      </label>
      <label>
        Clinical rationale
        <textarea value={rationale} onChange={(e) => setRationale(e.target.value)} rows={3}
          placeholder="Required for every decision (at least 10 characters)" />
      </label>
      {error && <p role="alert" className="error">{error}</p>}
      <div className="actions">
        <button className="approve" disabled={!ready} onClick={() => void send('approve')}>Approve</button>
        <button className="deny" disabled={!ready} onClick={() => void send('deny')}>Deny</button>
      </div>
    </section>
  )
}
