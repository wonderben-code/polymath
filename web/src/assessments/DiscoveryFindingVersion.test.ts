import { afterEach, describe, expect, it, vi } from 'vitest'
import { DATE, RESULTS, RUN, STAGES, intent, stageDetail } from '../discovery/test-support'
import { getFindingVersion, parseFindingVersion } from './client'
import { head } from './fixtures.test-support'
import type { FindingVersion } from './types'

function version(status: FindingVersion['run']['status'] = 'running'): FindingVersion {
  const detail = stageDetail(0)
  return {
    id: RESULTS[0],
    run_id: RUN,
    task_kind: 'research',
    run: {
      id: RUN,
      task_kind: 'research',
      mode: 'surprise',
      goal: 'Synthetic owned discovery; this is not scientific evidence.',
      status,
      created_at: DATE,
      updated_at: DATE,
      result_id: null,
      artifact_id: null,
      assessment_id: null,
      error_code: status === 'interrupted' ? 'model_call_limit' : null,
      external_outcome_unknown: false,
      discovery_intent: intent(),
    },
    payload: structuredClone(detail.result),
    discovery_stage: detail,
    assessment: head({
      requested_result_id: RESULTS[0],
      root_result_id: RESULTS[0],
      current_result_id: RESULTS[0],
      current_run_id: RUN,
      discovery_stage: {
        stage_id: detail.stage.stage_id,
        ordinal: detail.stage.ordinal,
        depth: detail.stage.depth,
        status: detail.status,
      },
    }),
  }
}

afterEach(() => { vi.unstubAllGlobals() })

describe('owned discovery finding-version reader', () => {
  it.each(['queued', 'running', 'cancelled', 'interrupted', 'failed', 'succeeded'] as const)(
    'reads an independently committed unselected stage while containing run is %s',
    (status) => {
      const parsed = parseFindingVersion(version(status))
      expect(parsed.run.status).toBe(status)
      expect(parsed.run.result_id).toBeNull()
      expect(parsed.discovery_stage?.stage.stage_id).toBe(STAGES[0])
      expect(parsed.payload).toEqual(stageDetail(0).result)
      expect(parsed.assessment.can_generate).toBe(true)
    },
  )

  it('requires the exact stage proof even when the containing run later succeeds', () => {
    for (const status of ['running', 'succeeded'] as const) {
      const value = version(status)
      value.run.result_id = value.id
      delete value.discovery_stage
      expect(() => parseFindingVersion(value)).toThrow('invalid_response')
    }
  })

  it('cannot fall back to an ordinary succeeded finding when only minimal stage metadata remains', () => {
    const value = version('succeeded')
    value.run.result_id = value.id
    delete value.discovery_stage
    delete value.run.discovery_intent
    expect(() => parseFindingVersion(value)).toThrow('invalid_response')
  })

  it.each([
    ['outer run', (value: FindingVersion) => { value.run_id = RESULTS[1] }],
    ['run metadata', (value: FindingVersion) => { value.run.id = RESULTS[1] }],
    ['outer result', (value: FindingVersion) => { value.id = RESULTS[1] }],
    ['stage run', (value: FindingVersion) => { value.discovery_stage!.stage.run_id = RESULTS[1] }],
    ['stage result', (value: FindingVersion) => { value.discovery_stage!.stage.result_id = RESULTS[1] }],
    ['stage hash', (value: FindingVersion) => { value.discovery_stage!.stage.result_sha256 = 'bad' }],
    ['head root', (value: FindingVersion) => { value.assessment.root_result_id = RESULTS[1] }],
    ['task kind', (value: FindingVersion) => { value.task_kind = value.run.task_kind = 'reassess_finding' }],
    ['mode', (value: FindingVersion) => { value.run.mode = 'explore' }],
    ['missing intent', (value: FindingVersion) => { delete value.run.discovery_intent }],
    ['missing head proof', (value: FindingVersion) => { delete value.assessment.discovery_stage }],
    ['stage bound', (value: FindingVersion) => { value.discovery_stage!.stage.ordinal = 3 }],
    ['depth bound', (value: FindingVersion) => { value.discovery_stage!.stage.depth = 3 }],
  ])('rejects mismatched %s bindings', (_label, mutate) => {
    const value = version()
    mutate(value)
    expect(() => parseFindingVersion(value)).toThrow('invalid_response')
  })

  it.each([
    ['finding', (value: FindingVersion) => {
      if (value.payload.finding?.outcome !== 'bridge') throw new Error('fixture changed')
      value.payload.finding.title = 'Changed immutable bridge body'
    }],
    ['quote hash', (value: FindingVersion) => { value.payload.quotes[0].source_sha256 = 'f'.repeat(64) }],
    ['source metadata', (value: FindingVersion) => { value.payload.sources[0].title = 'Changed metadata' }],
    ['missing source', (value: FindingVersion) => { value.payload.sources.shift() }],
    ['exact quote', (value: FindingVersion) => {
      value.payload.quotes[0].quote = 'A different passage'
      value.discovery_stage!.result.quotes[0].quote = 'A different passage'
    }],
    ['source body', (value: FindingVersion) => {
      value.payload.sources[0].text = 'Changed original body'
      value.discovery_stage!.result.sources[0].text = 'Changed original body'
    }],
  ])('rejects changed %s between normal result and committed stage', (_label, mutate) => {
    const value = version()
    mutate(value)
    expect(() => parseFindingVersion(value)).toThrow('invalid_response')
  })

  it('preserves a real unsupported head without discarding its immutable historical stage', () => {
    const value = version('cancelled')
    value.discovery_stage!.status = 'unsupported'
    Object.assign(value.assessment, {
      state: 'unsupported', revision: 1, last_assessment_id: RESULTS[2],
      is_supported: false, is_historical: true, can_generate: false,
    })
    value.assessment.discovery_stage!.status = 'unsupported'
    const parsed = parseFindingVersion(value)
    expect(parsed.discovery_stage?.status).toBe('unsupported')
    expect(parsed.assessment.can_generate).toBe(false)
    expect(parsed.payload).toEqual(stageDetail(0).result)
  })

  it.each(['needs_review', 'unsupported', 'superseded'] as const)(
    'rejects stage status %s paired with an ordinary current-generatable head',
    (status) => {
      const value = version()
      value.discovery_stage!.status = status
      expect(() => parseFindingVersion(value)).toThrow('invalid_response')
    },
  )

  it('keeps bibliographic needs-review distinct from an unsupported scientific judgement', () => {
    const value = version('interrupted')
    value.discovery_stage!.status = 'needs_review'
    value.discovery_stage!.primary_paper_groups = [{
      paper_id: 'paper-1', source_ids: value.payload.sources.map((source) => source.source_id),
    }]
    Object.assign(value.assessment, { is_supported: false, can_generate: false })
    value.assessment.discovery_stage!.status = 'needs_review'
    const parsed = parseFindingVersion(value)
    expect(parsed.discovery_stage?.status).toBe('needs_review')
    expect(parsed.assessment.state).toBe('supported')
    expect(parsed.assessment.is_historical).toBe(false)
    expect(parsed.assessment.can_generate).toBe(false)
    expect(parsed.payload).toEqual(stageDetail(0).result)
  })

  it.each([
    ['stage identity', (value: FindingVersion) => { value.assessment.discovery_stage!.stage_id = STAGES[1] }],
    ['ordinal', (value: FindingVersion) => { value.assessment.discovery_stage!.ordinal = 1 }],
    ['depth', (value: FindingVersion) => { value.assessment.discovery_stage!.depth = 2 }],
    ['unknown status', (value: FindingVersion) => { value.assessment.discovery_stage!.status = 'scientifically_proven' as never }],
    ['extra authority', (value: FindingVersion) => { Object.assign(value.assessment.discovery_stage!, { can_generate: true }) }],
  ])('rejects forged minimal head %s proof', (_label, mutate) => {
    const value = version()
    mutate(value)
    expect(() => parseFindingVersion(value)).toThrow('invalid_response')
  })

  it('rejects a current unsupported eligibility flag without explicit structural-review proof', () => {
    const value = version()
    value.discovery_stage!.status = 'needs_review'
    Object.assign(value.assessment, { is_supported: false, can_generate: false })
    delete value.assessment.discovery_stage
    expect(() => parseFindingVersion(value)).toThrow('invalid_response')
  })

  it('fetches only the requested owned finding using the normal private read boundary', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(version('cancelled'))))
    vi.stubGlobal('fetch', fetcher)
    const parsed = await getFindingVersion(RESULTS[0])
    expect(parsed.discovery_stage?.stage.result_id).toBe(RESULTS[0])
    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(fetcher.mock.calls[0][0]).toBe(`/api/v1/assessments/findings/${RESULTS[0]}`)
    expect(fetcher.mock.calls[0][1]).toMatchObject({ credentials: 'same-origin', cache: 'no-store' })
  })

  it('rejects a valid committed stage substituted for a different requested result', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(version()))))
    await expect(getFindingVersion(RESULTS[1])).rejects.toThrow('invalid_response')
  })
})
