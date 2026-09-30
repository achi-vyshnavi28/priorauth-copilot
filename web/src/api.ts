// Typed client for the PriorAuth Copilot API (priorauth/api.py).

export type CriterionResult = 'met' | 'not_met' | 'unknown'

export interface Criterion {
  id: string
  fact: string
  result: CriterionResult
  value: number | boolean | null
  policy_text: string
}

export interface Evidence {
  value: number | boolean | null
  quote: string | null
  verified: boolean
  note: string
}

export interface EngineDetail {
  extractor: 'llm' | 'keyword'
  decision: {
    outcome: 'approve' | 'pend'
    pend_reason: 'missing_documentation' | 'criteria_not_met' | null
    missing_facts: string[]
    criteria: Criterion[]
    ncd: string
    rules_version: string
    summary: string
  }
  evidence: Record<string, Evidence>
}

export interface DecisionRow {
  actor: string
  outcome: 'approve' | 'pend' | 'deny'
  rationale: string
  pend_reason: string | null
  detail: EngineDetail | { review: true }
  created_at: string
}

export interface CaseRow {
  case_id: string
  policy_id: string
  service: string
  member_ref: string
  urgency: 'standard' | 'expedited'
  status: string
  received_at: string
}

export interface CaseDetail extends CaseRow {
  decisions: DecisionRow[]
  audit: { action: string; actor: string; at: string }[]
  documents: { _id: string; text: string }[]
}

export interface Stats {
  cases: number
  by_status: Record<string, number>
  pend_reasons: Record<string, number>
  auto_approval_rate: number | null
}

const BASE = import.meta.env.VITE_API_URL ?? '/api'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init)
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    const detail = typeof body.detail === 'string' ? body.detail : `${res.status} ${res.statusText}`
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

export const api = {
  queue: () => request<CaseRow[]>('/cases?status=pending_review'),
  get: (id: string) => request<CaseDetail>(`/cases/${encodeURIComponent(id)}`),
  stats: () => request<Stats>('/stats'),
  review: (id: string, clinician: string, outcome: 'approve' | 'deny', rationale: string) =>
    request<CaseDetail>(`/cases/${encodeURIComponent(id)}/review`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ clinician, outcome, rationale }),
    }),
}

export const engineDecision = (c: CaseDetail): EngineDetail | null => {
  const d = c.decisions.find((x) => x.actor.startsWith('engine'))
  return d && 'decision' in d.detail ? (d.detail as EngineDetail) : null
}

export const label = (s: string) => s.replace(/_/g, ' ')
