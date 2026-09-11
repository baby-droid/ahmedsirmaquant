/**
 * Endpoints every screen can lean on: the day's numbers, the session, the simulation
 * engine and background tasks.
 */

import { http, qs } from './http'
import type {
  ActivityDay,
  BarStatus,
  EngineStatus,
  Scope,
  Session,
  SimulationRow,
  TasksSummary,
  Today,
} from './types'

export type QuotaResponse =
  | { known: false }
  | { known: true; limit: number | null; remaining: number | null; resetSeconds: number | null; observedAt: string | null }

export interface SettingsField {
  name: string
  label: string
  type: string | null
  required: boolean
  readOnly: boolean
  choices: { value: string | number; label: string }[] | null
  dependsOn: string[]
  blocked: boolean
  min: number | null
  max: number | null
}

export interface SettingsOptions {
  fields: Record<string, SettingsField>
  problems: string[]
  missing: string[]
}

export const today = {
  /** The first screen in one call. Scope defaults to USA / D1 / TOP3000. */
  get: (scope?: Partial<Scope>) =>
    http.get<Today>(
      `/api/today${qs({ region: scope?.region, delay: scope?.delay, universe: scope?.universe, instrument_type: scope?.instrumentType })}`,
    ),
  /** The header clocks. Cheap. */
  bar: () => http.get<BarStatus>('/api/today/bar'),
  /** Simulations and submissions per day as BRAIN counted them, oldest first. 2 BRAIN reads. */
  activity: (days = 90) => http.get<{ days: ActivityDay[] }>(`/api/today/activity${qs({ days })}`),
}

export const auth = {
  status: (refresh = false) => http.get<Session & { storedEmail: string | null }>(`/api/auth/status${qs({ refresh })}`),
  /** Omit both to sign in with the stored credential. */
  login: (email?: string, password?: string) =>
    http.post<Session>('/api/auth/login', { email: email || null, password: password || null }),
  logout: () => http.post<Session>('/api/auth/logout'),
  forget: () => http.del<{ ok: boolean }>('/api/auth/credential'),
  /** Legal values for every settings field, given what is chosen. Keys use BRAIN's names. */
  settingsOptions: (settings: Record<string, unknown>) => http.post<SettingsOptions>('/api/auth/settings-options', { settings }),
}

export const simulations = {
  /** Every QUEUED, PENDING and RUNNING record, oldest first. Can be thousands long. */
  active: () => http.get<SimulationRow[]>('/api/simulations/active'),
  engine: () => http.get<EngineStatus>('/api/simulations/engine'),
  recent: (limit = 100) => http.get<SimulationRow[]>(`/api/simulations/recent${qs({ limit })}`),
  quota: () => http.get<QuotaResponse>('/api/simulations/quota'),
  /** Cancel by record id. Cancel the batch PARENT: a child cannot be cancelled on BRAIN. */
  cancel: (recordId: number) =>
    http.post<{ acknowledged: boolean; simulation: SimulationRow | null }>(`/api/simulations/${recordId}/cancel`),
  /** Drop queued work that has not been sent. Omit `task` to drop everything queued. */
  dropQueue: (task?: string) => http.del<{ dropped: number }>(`/api/simulations/queue${qs({ task })}`),
}

export const tasks = {
  list: () => http.get<TasksSummary>('/api/tasks'),
}
