/**
 * Hot state pushed over the socket, held outside React. Components read a slice with a
 * selector (`useLive((s) => s.simulations)`), so a tick re-renders only what reads it.
 */

import { useMemo } from 'react'
import { create } from 'zustand'
import type { SimulationRow, SyncRun, TasksSummary } from '@/api/types'
import { buildCores } from './matrix'
import { telemetry } from './ws'

/**
 * One stable core assignment shared by every matrix view, so the Simulation Matrix page and
 * the header preview put each batch on the same core, and a cancel never shifts the rest.
 * ponytail: module-level and reset on reload; BRAIN has no slot ids to restore it from anyway.
 */
let coreAssignment: ReadonlyMap<number, number> = new Map()

export function useCores(active: SimulationRow[] | null | undefined, slots: number, maxBatch: number) {
  return useMemo(() => {
    const built = buildCores(active ?? [], slots, maxBatch, coreAssignment)
    coreAssignment = built.assignment
    return built
  }, [active, slots, maxBatch])
}

interface Live {
  connected: boolean
  /** The full active set, replaced on every `simulations` message. Null until the first. */
  simulations: SimulationRow[] | null
  tasks: TasksSummary | null
  sync: SyncRun | null
  /** Set when any call reports BRAIN wants identity verification; cleared on dismiss. */
  verificationUrl: string | null
}

export const useLive = create<Live>(() => ({
  connected: false,
  simulations: null,
  tasks: null,
  sync: null,
  verificationUrl: null,
}))

telemetry.onStatus((connected) => useLive.setState({ connected }))
telemetry.subscribe('simulations', (payload) => {
  // The topic also carries `{alphaId, submittable}` from backfill; only arrays are snapshots.
  if (Array.isArray(payload)) useLive.setState({ simulations: payload as SimulationRow[] })
})
telemetry.subscribe('tasks', (payload) => useLive.setState({ tasks: payload as TasksSummary }))
telemetry.subscribe('sync', (payload) => useLive.setState({ sync: payload as SyncRun }))
