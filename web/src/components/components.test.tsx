import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { EngineDetail } from '../api'
import { CriteriaChecklist } from './CriteriaChecklist'
import { ReviewForm } from './ReviewForm'

const detail: EngineDetail = {
  extractor: 'llm',
  decision: {
    outcome: 'pend', pend_reason: 'missing_documentation', missing_facts: ['prior_medical_treatment_failed'],
    ncd: '100.1', rules_version: '2026.10.1', summary: 'Pended for clinical review: documentation does not state prior_medical_treatment_failed.',
    criteria: [
      { id: 'C1_BMI', fact: 'bmi', result: 'met', value: 41, policy_text: 'body-mass index ≥ 35' },
      { id: 'C3_PRIOR_TREATMENT', fact: 'prior_medical_treatment_failed', result: 'unknown', value: null, policy_text: 'previously unsuccessful with medical treatment for obesity' },
    ],
  },
  evidence: {
    bmi: { value: 41, quote: 'body mass index of 41', verified: true, note: '' },
    prior_medical_treatment_failed: { value: null, quote: null, verified: false, note: '' },
  },
}

describe('CriteriaChecklist', () => {
  it('shows each criterion with its result, the policy text and the verified quote', () => {
    render(<CriteriaChecklist detail={detail} />)
    const bmi = screen.getByTestId('criterion-C1_BMI')
    expect(within(bmi).getByText('Met')).toBeInTheDocument()
    expect(within(bmi).getByText('body mass index of 41')).toBeInTheDocument()
    const prior = screen.getByTestId('criterion-C3_PRIOR_TREATMENT')
    expect(within(prior).getByText('Not documented')).toBeInTheDocument()
    expect(screen.getByText(/CMS NCD 100.1/)).toBeInTheDocument()
  })
})

describe('ReviewForm', () => {
  it('requires a reviewer and a real rationale before any decision', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    render(<ReviewForm onSubmit={onSubmit} />)
    const deny = screen.getByRole('button', { name: 'Deny' })
    expect(deny).toBeDisabled()
    await userEvent.type(screen.getByLabelText('Reviewer'), 'dr.rao')
    await userEvent.type(screen.getByLabelText('Clinical rationale'), 'too short')
    expect(deny).toBeDisabled()
    await userEvent.type(screen.getByLabelText('Clinical rationale'), ' - no comorbidity after peer to peer')
    await userEvent.click(deny)
    expect(onSubmit).toHaveBeenCalledWith('deny', 'dr.rao', 'too short - no comorbidity after peer to peer')
  })

  it('shows the API error when saving fails', async () => {
    const onSubmit = vi.fn().mockRejectedValue(new Error('case is approved, not pending review'))
    render(<ReviewForm onSubmit={onSubmit} />)
    await userEvent.type(screen.getByLabelText('Reviewer'), 'dr.iyer')
    await userEvent.type(screen.getByLabelText('Clinical rationale'), 'Documentation confirmed by phone.')
    await userEvent.click(screen.getByRole('button', { name: 'Approve' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('not pending review')
  })
})
