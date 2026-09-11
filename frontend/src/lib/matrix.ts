/**
 * The 8×10 simulation matrix as data: one core per BRAIN slot, one cell per Alpha inside
 * its multi-simulation. QUEUED work is counted elsewhere, never drawn.
 */

import type { SimulationRow } from '../api/types.ts'

export type CellState = 'RUNNING' | 'PENDING' | 'EMPTY'

export interface Cell {
  row: SimulationRow | null
  state: CellState
}

export interface Core {
  holder: SimulationRow | null
  cells: Cell[]
}

/** Everything a batch child shares with its parent except `submittedAt`. */
const linkKey = (r: SimulationRow) => [r.task, r.region, r.delay, r.instrumentType, r.language, r.simType].join('|')

const cellState = (status: SimulationRow['status']): CellState => (status === 'RUNNING' ? 'RUNNING' : 'PENDING')

const EMPTY: Cell = { row: null, state: 'EMPTY' }

/**
 * Give each holder a stable core. BRAIN exposes no slot numbers, so positions are ours: a
 * holder keeps the core it was first drawn in until it leaves, and a newcomer takes the
 * lowest free core. Without this, cancelling core 1 shifts every other batch up a row.
 * `holderIds` should be in arrival order (record id) so newcomers fill cores in that order.
 */
export function assignCores(holderIds: number[], previous: ReadonlyMap<number, number>, slots: number): Map<number, number> {
  const next = new Map<number, number>()
  const taken = new Set<number>()
  for (const id of holderIds) {
    const core = previous.get(id)
    if (core !== undefined && core < slots && !taken.has(core)) {
      next.set(id, core)
      taken.add(core)
    }
  }
  let free = 0
  for (const id of holderIds) {
    if (next.has(id)) continue
    while (taken.has(free)) free++
    if (free >= slots) break
    next.set(id, free)
    taken.add(free)
  }
  return next
}

/**
 * Group active rows into cores.
 *
 * A child belongs to its batch by `parentId`. When BRAIN finishes a batch, the parent leaves
 * the active set a little before its children are collected; those children no longer hold
 * a core (the engine has already freed the slot), so they are not drawn. Records without a
 * `parentId` (written before it existed) fall back to the same composite key and an equal
 * `submittedAt`.
 *
 * Pass the previous `assignment` to keep every holder on its core between snapshots.
 */
export function buildCores(
  active: SimulationRow[],
  slots: number,
  maxBatch: number,
  previous: ReadonlyMap<number, number> = new Map(),
): { cores: Core[]; overflow: number; assignment: Map<number, number> } {
  const live = active.filter((r) => r.status === 'PENDING' || r.status === 'RUNNING')
  const parents = live.filter((r) => r.isBatch)
  const liveParents = new Set(parents.map((p) => p.id))
  const children = new Map<number, SimulationRow[]>()
  const standalones: SimulationRow[] = []
  const adopt = (parentId: number, row: SimulationRow) => children.set(parentId, [...(children.get(parentId) ?? []), row])

  for (const row of live) {
    if (row.isBatch) continue
    if (row.parentId != null) {
      if (liveParents.has(row.parentId)) adopt(row.parentId, row)
      continue
    }
    const parent = parents.find((p) => p.submittedAt === row.submittedAt && linkKey(p) === linkKey(row))
    if (!parent) standalones.push(row)
    // An unsubmitted batch cannot be linked to its children yet; it is drawn as pending.
    else if (row.submittedAt !== null) adopt(parent.id, row)
  }

  const holders = [...parents, ...standalones].sort((a, b) => a.id - b.id)
  const assignment = assignCores(
    holders.map((h) => h.id),
    previous,
    slots,
  )
  const byCore = new Map(holders.filter((h) => assignment.has(h.id)).map((h) => [assignment.get(h.id)!, h]))

  const cores = Array.from({ length: slots }, (_, i): Core => {
    const holder = byCore.get(i) ?? null
    if (!holder) return { holder, cells: Array.from({ length: maxBatch }, () => EMPTY) }
    if (!holder.isBatch) {
      return { holder, cells: Array.from({ length: maxBatch }, (_, j) => (j === 0 ? { row: holder, state: cellState(holder.status) } : EMPTY)) }
    }
    const kids = (children.get(holder.id) ?? []).sort((a, b) => a.id - b.id)
    const declared = Number.parseInt(holder.expression ?? '', 10)
    const size = Math.min(maxBatch, Math.max(kids.length, Number.isFinite(declared) ? declared : maxBatch))
    return {
      holder,
      cells: Array.from({ length: maxBatch }, (_, j): Cell => {
        if (j < kids.length) return { row: kids[j], state: cellState(kids[j].status) }
        if (j < size) return { row: null, state: cellState(holder.status) }
        return EMPTY
      }),
    }
  })
  return { cores, overflow: Math.max(0, holders.length - assignment.size), assignment }
}
