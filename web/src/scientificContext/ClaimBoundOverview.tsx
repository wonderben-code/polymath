import type { AnswerFinding } from '../research/types'
import type { AskOverviewBinding } from './types'

export function ClaimBoundOverview({ finding, binding, claimAnchor }: { finding: AnswerFinding; binding: AskOverviewBinding; claimAnchor: (id: string) => string }) {
  return <section className="claim-bound-overview" aria-label="Overview linked to saved claims"><h4>Overview · selected saved claims</h4><p className="research-scope-note">Each passage below is the exact text of a saved claim, with its original classification and limits. These classifications are model assessments.</p><ol>{binding.claim_ids.map((id) => {
    const claim = finding.claims.find((candidate) => candidate.id === id)!
    const ordinal = finding.claims.indexOf(claim) + 1
    return <li key={id}><span className={`research-claim-label label-${claim.label}`}>{claim.label}</span><p>{claim.text}</p>{claim.derivation && <p><strong>Derivation:</strong> {claim.derivation}</p>}{claim.caveats.length > 0 && <ul aria-label={`Overview caveats for claim ${ordinal}`}>{claim.caveats.map((caveat, index) => <li key={index}>{caveat}</li>)}</ul>}<ul className="overview-citations" aria-label={`Overview citations for claim ${ordinal}`}>{claim.evidence.map((citation, index) => <li key={index}><blockquote>{citation.quote}</blockquote><p>Recorded support: {citation.supports}</p></li>)}</ul><a href={`#${claimAnchor(id)}`}>Inspect claim {ordinal} and its saved source evidence</a></li>
  })}</ol></section>
}
