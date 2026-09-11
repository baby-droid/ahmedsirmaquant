/**
 * Alphas (spec §4.5): the local store of Alphas, their submission checks and correlations.
 *
 * Every request body here is snake_case ONLY — camelCase keys are silently dropped.
 * Correlation endpoints spend BRAIN's hourly correlation budget: call them on a click.
 * Nothing here submits an alpha, and nothing ever will.
 */

import { http, qs } from './http'
import type { AlphaCheck, Scope } from './types'

export interface VaultOverview {
  counts: { alphas: number; withReturns: number; dailyRows: number; scored: number; missingReturns: number }
  scopes: {
    instrument_type: string | null
    region: string
    delay: number | null
    universe: string | null
    alphas: number
    scored: number
    best_sharpe: number | null
  }[]
  backfilling: boolean
  lastSync: { state: 'done' | 'failed' | 'cancelled'; imported: number; error?: string; finishedAt: string } | null
}

export type AlphaMetricKey = 'sharpe' | 'fitness' | 'turnover' | 'returns' | 'drawdown' | 'margin' | 'operator_count' | 'k_ratio' | 'calmar'
export type AlphaSortKey = AlphaMetricKey | 'date_created' | 'date_submitted'

export interface AlphaPageRequest {
  submitted?: boolean
  sort_by?: AlphaSortKey
  sort_desc?: boolean
  regions?: string[] | null
  delays?: number[] | null
  universes?: string[] | null
  minimum?: Partial<Record<AlphaMetricKey, number>>
  maximum?: Partial<Record<AlphaMetricKey, number>>
  search?: string | null
  /** 1..500 */
  limit?: number
  offset?: number
}

export interface AlphaRow {
  alphaId: string
  name: string | null
  type: string | null
  status: string | null
  region: string | null
  universe: string | null
  delay: number | null
  neutralization: string | null
  decay: number | null
  truncation: number | null
  expression: string | null
  sharpe: number | null
  fitness: number | null
  turnover: number | null
  returns: number | null
  drawdown: number | null
  margin: number | null
  operatorCount: number | null
  kRatio: number | null
  calmar: number | null
  dateCreated: string | null
  dateSubmitted: string | null
  hasPnl: boolean
}

export interface AlphaPage {
  total: number
  results: AlphaRow[]
}

export interface AlphaSettings {
  region: string | null
  universe: string | null
  delay: number | null
  neutralization: string | null
  decay: number | null
  truncation: number | null
}

export interface AlphaDetail {
  alphaId: string
  expression: string | null
  settings: AlphaSettings
  checks: AlphaCheck[]
  /** Cumulative PnL, one value per stored trading day. */
  pnl: number[]
  /** `YYYY-MM-DD` for each value in `pnl`. */
  dates: string[]
  days: number
  kRatio: number | null
  problem: string | null
  brainUrl: string
}

export interface SubmittableAlpha {
  alphaId: string
  expression: string | null
  lab: string | null
  dateCreated: string | null
  settings: AlphaSettings
  sharpe: number | null
  fitness: number | null
  turnover: number | null
  returns: number | null
  drawdown: number | null
  margin: number | null
  checks: AlphaCheck[]
  /** ≤ 120 points */
  pnl: number[]
  brainUrl: string
}

export interface SubmittableResponse {
  alphas: SubmittableAlpha[]
  total: number
  pending: number
  nearMisses: number
}

export interface BrainCorrelation {
  schema?: { name?: string; title?: string; properties: { name: string; title?: string; type?: string }[] }
  records?: unknown[][]
  min?: number
  max?: number
  [k: string]: unknown
}

export const BRAIN_ALPHA_URL = (alphaId: string) => `https://platform.worldquantbrain.com/alpha/${alphaId}`

export const pool = {
  overview: () => http.get<VaultOverview>('/api/vault'),
  /** Raw BRAIN counts by stage and status. One BRAIN read. */
  summary: () => http.get<Record<string, number>>('/api/alphas/summary'),
  sync: () => http.post<{ taskId: string; since: string | null }>('/api/vault/sync'),
  query: (body: AlphaPageRequest) => http.post<AlphaPage>('/api/vault/alphas/query', body),
  detail: (alphaId: string) => http.get<AlphaDetail>(`/api/vault/alphas/${encodeURIComponent(alphaId)}/detail`),
  /** ≤ 100 ids; downloads daily PnL where missing, in the background. */
  kRatio: (alphaIds: string[]) => http.post<{ taskId: string; alphas: number }>('/api/vault/alphas/k-ratio', { alpha_ids: alphaIds }),
  submittable: (scope: Scope, limit = 200) =>
    http.get<SubmittableResponse>(
      `/api/vault/submittable${qs({ region: scope.region, delay: scope.delay, universe: scope.universe, instrument_type: scope.instrumentType, limit })}`,
    ),
  /** Re-runs the submission checks on BRAIN without submitting. */
  check: (alphaId: string) => http.get<{ is?: { checks?: AlphaCheck[] } }>(`/api/alphas/${encodeURIComponent(alphaId)}/check`),
  /** Spends BRAIN's hourly correlation budget. */
  correlations: (alphaId: string, kind: 'self' | 'prod') =>
    http.get<BrainCorrelation>(`/api/alphas/${encodeURIComponent(alphaId)}/correlations/${kind}`),
}
