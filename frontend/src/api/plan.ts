/**
 * The day's plan: drawn tracks, the PM's allocation across labs, per-lab yield, and
 * stopping queued work. /plan/suggest takes plain keys; /plan/lucky and /plan/start
 * accept camelCase.
 */

import { http, qs } from './http'
import type { EngineStatus, LLMUsage, SimStatus } from './types'

export type SpeedKey = 'fast' | 'slow' | 'mixed'
export type ShapeKey = 'unusual' | 'position' | 'movement' | 'reversal' | 'size'
export type CrowdingKey = 'untouched' | 'proven' | 'open'
export type ComparedKey = 'industry' | 'subindustry' | 'sector' | 'market'

export interface PlanLeverChoice {
  speed: SpeedKey
  shape: ShapeKey
  crowding: CrowdingKey
  compared: ComparedKey
  depth: 0 | 1 | 2 | 3
}

/** snake_case on the wire; sent back to /plan/start verbatim. */
export interface PlanRecipe {
  patterns: string[]
  windows: number[]
  group: ComparedKey
  neutralization: string
  skip: number
  min_alphas?: number
  max_alphas?: number
}

export interface PlanTrack {
  task: string
  name: string
  explains: string
  why: string | null
  levers: PlanLeverChoice
  lab: 'explore'
  target: number
  cores: number
  recipe: PlanRecipe
}

export interface SuggestPlanRequest {
  /** 1..4, default 3 */
  tracks?: number
  /** 1..50000 */
  target?: number
  neutralization?: string
  seed?: number | null
}

export interface SuggestPlanResponse {
  tracks: PlanTrack[]
  slots: number
  target: number
}

export interface StartPlanRequest {
  region: string
  delay: number
  universe: string
  instrumentType?: string
  tracks?: PlanTrack[]
  trackCount?: number
  target?: number
  neutralization?: string
  replace?: boolean
}

interface WireScope {
  instrument_type: string
  region: string
  delay: number
  universe: string
}

export type StartedPlanTrack = PlanTrack & { task: string; day: string; queued: number; skipped: number } & (
    | { empty: true }
    | { empty: false; short: boolean; fields: number }
  )

export interface StartPlanResponse {
  day: string
  scope: WireScope
  tracks: StartedPlanTrack[]
  queued: number
  skipped: number
  status: EngineStatus
}

export interface ActivePlanTrack {
  task: string
  day: string
  fromEarlier: boolean
  name: string
  explains: string
  lab: string
  levers: Partial<PlanLeverChoice>
  target: number
  cores: number
  status: 'running' | 'stopped'
  done: number
  waiting: number
  inFlight: number
  byStatus: Partial<Record<SimStatus, number>>
}

export interface PlanStatusCore {
  day: string
  active: boolean
  tracks: ActivePlanTrack[]
  done: number
  waiting: number
  inFlight: number
  target: number
  remaining: number
}

export interface PlanStatusResponse extends PlanStatusCore {
  engine: EngineStatus
}

export interface PlanStopResponse extends PlanStatusCore {
  dropped: number
  task?: string
}

export interface LabYield {
  lab: string
  simulated: number
  finished: number
  alphas: number
  submittable: number
  yieldRate: number
  meetsTarget: boolean
  bestSharpe: number | null
  /** False below 200 finished simulations. */
  confident: boolean
}

export interface PlanYieldResponse {
  days: number
  labs: LabYield[]
  simulated: number
  finished: number
  submittable: number
  yieldRate: number
  targetYield: number
  meetsTarget: boolean
  plainly: string
}

export interface PlanLuckyRequest {
  region: string
  delay: number
  universe: string
  instrumentType?: string
  target?: number
  model?: string | null
  /** 1..90 */
  days?: number
  /** Defaults to TRUE on the backend. */
  dryRun?: boolean
}

export interface PMAllocation {
  lab: string
  name: string
  cores: number
  why: string
}

export interface DispatchedDesk {
  lab: string
  task: string
  queued: number
  started: boolean
  skipped?: number
  asked?: number
  short?: boolean
  note?: string | null
  topUp?: true
  cores: number
  name?: string
  why?: string
}

export interface DispatchResult {
  day: string
  scope: WireScope
  started: DispatchedDesk[]
  queued: number
  skipped: number
  desksStarted: number
  desksAllocated: number
  toppedUp: number
  short: boolean
}

export interface PlanLuckyResponse {
  briefing: string
  allocations: PMAllocation[]
  fromPM: boolean
  fund: PlanYieldResponse
  rejected?: string[]
  usage?: LLMUsage
  model?: string
  started: DispatchResult | null
  status?: EngineStatus
}

export interface PlanAdviseRequest {
  region: string
  delay: number
  universe: string
  instrumentType?: string
  /** 1..4, default 3 */
  tracks?: number
  target?: number
  /** What they want out of today, in their own words. */
  note?: string
  model?: string | null
  neutralization?: string
}

export interface PlanAdviseResponse {
  opening: string
  tracks: PlanTrack[]
  /** False when the assistant's answer was unusable and a drawn plan stands in. */
  fromAssistant: boolean
  /** How many proposed tracks were thrown away (invented levers or duplicates). */
  rejected: number
  usage?: LLMUsage
  model?: string
}

export const plan = {
  status: () => http.get<PlanStatusResponse>('/api/plan'),
  yield: (days = 14) => http.get<PlanYieldResponse>(`/api/plan/yield${qs({ days })}`),
  /** Free: draws tracks, writes nothing. */
  suggest: (body: SuggestPlanRequest) => http.post<SuggestPlanResponse>('/api/plan/suggest', body),
  /** Queues simulations. `replace` stops today's plan first. */
  start: (body: StartPlanRequest) => http.post<StartPlanResponse>('/api/plan/start', body),
  /** One LLM request; `dryRun: false` also queues simulations. */
  lucky: (body: PlanLuckyRequest) => http.post<PlanLuckyResponse>('/api/plan/lucky', body),
  /** One LLM request: the assistant picks the tracks and says why. Queues nothing. */
  advise: (body: PlanAdviseRequest) => http.post<PlanAdviseResponse>('/api/plan/advise', body),
  stopAll: () => http.post<PlanStopResponse>('/api/plan/stop'),
  stop: (task: string) => http.post<PlanStopResponse>(`/api/plan/stop/${encodeURIComponent(task)}`),
}
