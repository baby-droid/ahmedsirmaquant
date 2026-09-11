/** Tasks: what the labs added. Only the Tasks tab runs them. */

import { http, qs } from './http'

/** IDLE: not started. QUEUED: run, waiting for its cores to fit in the free slots. */
export type TaskStatus = 'IDLE' | 'QUEUED' | 'RUNNING' | 'PAUSED' | 'COMPLETE' | 'FAILED'

export interface LabTask {
  id: number
  lab: string
  labName: string
  /** Template Lab only: the template's name and its skeleton. */
  templateName: string | null
  template: string | null
  status: TaskStatus
  stopping: boolean
  message: string | null
  region: string
  delay: number
  /** Evolution Lab only: its market's universe, seeds, population and mutation rate. */
  universe: string | null
  seeds: number
  population: number | null
  mutationRate: number | null
  /** Search Lab and Template Lab only. */
  decay: number | null
  cores: number
  datasetIds: string[]
  fields: number
  target: number
  simulated: number
  queued: number
  running: number
  failed: number
  /** The best value of what the task searches for: Sharpe, or Train Fitness for Evolution Lab. */
  best: number | null
  objectiveLabel: string
  createdAt: string | null
  finishedAt: string | null
}

export interface LabTasks {
  slots: number
  tasks: LabTask[]
}

export interface RankedAlpha {
  trialId: number
  alphaId: string | null
  expression: string | null
  settings: { universe?: string; neutralization?: string; [key: string]: unknown } | null
  value: number
  sharpe: number | null
  fitness: number | null
  turnover: number | null
  returns: number | null
  drawdown: number | null
  margin: number | null
  feasible: boolean | null
  failedChecks: string[]
}

const B = '/api/lab-tasks'

export const labTasks = {
  list: () => http.get<LabTasks>(B),
  /** Spends simulation quota. */
  runAll: () => http.post<LabTasks>(`${B}/run-all`),
  /** Spends simulation quota. Also resumes a paused task. */
  run: (id: number) => http.post<LabTask>(`${B}/${id}/run`),
  pause: (id: number) => http.post<LabTask>(`${B}/${id}/pause`),
  stop: (id: number) => http.post<LabTask>(`${B}/${id}/stop`),
  change: (id: number, body: { cores?: number; simulations?: number }) => http.patch<LabTask>(`${B}/${id}`, body),
  remove: (id: number) => http.del<{ removed: number }>(`${B}/${id}`),
  top: (id: number, limit = 50) => http.get<RankedAlpha[]>(`${B}/${id}/top${qs({ limit })}`),
}
