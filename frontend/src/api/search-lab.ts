/**
 * Search Lab: choose datasets, cores and simulations, then add the search to Tasks, where it
 * runs. Request bodies are snake_case.
 */

import { http } from './http'

export interface SearchLabOptions {
  operators: { synced: boolean; count: number }
  crossSectional: string[]
  timeSeries: string[]
  group: string[]
  vector: string[]
  lookbacks: number[]
  groups: string[]
  decays: number[]
  truncation: number
  maxCores: number
  maxSimulations: number
}

export interface SearchLabRequest {
  region: string
  delay: number
  universe?: string | null
  dataset_ids: string[]
  vector_operators: string[]
  decay: number
  cores: number
  /** Needed to add a task; a preview ignores it. */
  simulations?: number
}

export interface SearchLabPreview {
  round: number
  fields: { total: number; matrix: number; vector: number }
  leftOut: { vector: number }
  universes: string[]
  neutralizations: string[]
  families: string[]
  sample: { expression: string; settings: Record<string, unknown> }[]
  problems: string[]
  warnings: string[]
}

const B = '/api/search-lab'

export const searchLab = {
  /** Reads the account's operators, syncing them from BRAIN when missing. */
  options: () => http.get<SearchLabOptions>(`${B}/options`),
  /** Free; queues nothing. */
  preview: (body: SearchLabRequest) => http.post<SearchLabPreview>(`${B}/preview`, body),
  /** Adds the search to Tasks, not started. Spends nothing until it is run there. */
  addTask: (body: SearchLabRequest & { simulations: number }) => http.post<{ id: number; name: string }>(`${B}/tasks`, body),
}
