/**
 * Evolution Lab: choose seed Alphas, cores and simulations, then add the breeding to Tasks,
 * where it runs. Request bodies are snake_case.
 */

import { http } from './http'

/** A market holding unsubmitted Alphas, and how many. */
export interface EvolutionMarket {
  region: string
  delay: number
  universe: string
  alphas: number
}

export interface EvolutionOptions {
  operators: { synced: boolean; count: number }
  markets: EvolutionMarket[]
  populations: number[]
  mutationRates: number[]
  defaults: { population: number | null; mutationRate: number }
  maxCores: number
  maxSimulations: number
  maxSeeds: number
}

export interface EvolutionRequest {
  region: string
  delay: number
  universe: string
  alpha_ids: string[]
  cores: number
  /** `null`: sized from the simulations. */
  population: number | null
  mutation_rate: number
  /** Needed to add a task; a preview uses it to size the population. */
  simulations?: number
}

export interface SeedRow {
  alphaId: string
  expression: string
  sharpe: number | null
  fitness: number | null
  turnover: number | null
  returns: number | null
  drawdown: number | null
  margin: number | null
  score: number | null
}

export interface SeedReason {
  alphaId: string
  reason: string
}

export interface EvolutionPreview {
  seeds: SeedRow[]
  skipped: SeedReason[]
  population: number
  generations: number
  sample: { expression: string; settings: Record<string, unknown> }[]
  problems: string[]
  warnings: string[]
}

export interface AutoSeeds {
  seeds: SeedRow[]
  wanted: number
  pool: number
  examined: number
  reasons: SeedReason[]
}

export interface AutoSeedsJob {
  state: 'running' | 'done' | 'failed' | 'cancelled'
  progress: number | null
  detail: string
  error: string | null
  result: AutoSeeds | null
}

const B = '/api/evolution-lab'

export const evolutionLab = {
  /** Reads the account's operators, syncing them from BRAIN when missing, and the markets holding Alphas. */
  options: () => http.get<EvolutionOptions>(`${B}/options`),
  /** Free; queues nothing. */
  preview: (body: EvolutionRequest) => http.post<EvolutionPreview>(`${B}/preview`, body),
  /** Chooses seeds in the background. Downloads daily PnL where missing; never simulates. */
  autoSeeds: (body: { region: string; delay: number; universe: string; count: number }) => http.post<{ jobId: string }>(`${B}/seeds/auto`, body),
  autoSeedsJob: (jobId: string) => http.get<AutoSeedsJob>(`${B}/seeds/auto/${encodeURIComponent(jobId)}`),
  /** Adds the breeding to Tasks, not started. Spends nothing until it is run there. */
  addTask: (body: EvolutionRequest & { simulations: number }) => http.post<{ id: number; name: string }>(`${B}/tasks`, body),
}
