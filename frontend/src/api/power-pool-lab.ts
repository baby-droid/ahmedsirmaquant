/** LLM Power Pool Lab: datasets, a model, cores and simulations, then add the task to Tasks. */

import { http } from './http'

export interface PowerPoolModel {
  id: string
  label: string
  provider: string
  tpm: number
  remainingToday: number
}

export interface PowerPoolOptions {
  models: PowerPoolModel[]
  defaultModel: string | null
  maxSimulations: number
}

export interface PowerPoolRequest {
  region: string
  delay: number
  universe: string
  dataset_ids: string[]
  model: string | null
  cores: number
  simulations: number
}

export interface PowerPoolPreview {
  fields: number
  universes: string[]
  neutralizations: string[]
  llmCalls: number
  prompt: { system: string; user: string; tokens: number } | null
  problems: string[]
  warnings: string[]
}

const B = '/api/power-pool-lab'

export const powerPoolLab = {
  options: () => http.get<PowerPoolOptions>(`${B}/options`),
  /** Free: no LLM call, no simulation. */
  preview: (body: PowerPoolRequest) => http.post<PowerPoolPreview>(`${B}/preview`, body),
  addTask: (body: PowerPoolRequest) => http.post<{ id: number; name: string }>(`${B}/tasks`, body),
}
