import { label, type EngineDetail } from '../api'

const ICON = { met: '✓', not_met: '✗', unknown: '?' } as const
const TEXT = { met: 'Met', not_met: 'Not met', unknown: 'Not documented' } as const

export function CriteriaChecklist({ detail }: { detail: EngineDetail }) {
  const { decision, evidence, extractor } = detail
  return (
    <section aria-label="Criteria">
      <p className="muted small">
        CMS NCD {decision.ncd} · rules {decision.rules_version} · evidence by {extractor === 'llm' ? 'LLM agent (citations verified)' : 'keyword extractor'}
      </p>
      <p className={`summary ${decision.outcome}`}>{decision.summary}</p>
      <ul className="criteria">
        {decision.criteria.map((c) => {
          const ev = evidence[c.fact]
          return (
            <li key={c.id} className={`criterion ${c.result}`} data-testid={`criterion-${c.id}`}>
              <div className="crit-head">
                <span className={`mark ${c.result}`} aria-hidden>{ICON[c.result]}</span>
                <strong>{TEXT[c.result]}</strong>
                <span className="muted"> · {label(c.fact)}{c.value !== null ? `: ${String(c.value)}` : ''}</span>
              </div>
              <div className="policy">“{c.policy_text}”</div>
              {ev?.quote && ev.verified && <blockquote>{ev.quote}</blockquote>}
              {ev?.note && <div className="muted small">{ev.note}</div>}
            </li>
          )
        })}
      </ul>
    </section>
  )
}
