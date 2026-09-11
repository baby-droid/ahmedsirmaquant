/** Non-component pieces the Data Explorer tabs share: the Fields filter store and helpers. */

import { useEffect, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { create } from 'zustand'
import { catalog, type CatalogSyncRequest, type FieldFilter, type FieldSortKey } from '@/api/catalog'
import { errorMessage } from '@/api/http'
import type { Scope } from '@/api/types'
import { useDatasetPick } from '@/lib/dataset-pick'
import { DASH, fmt, isNum } from '@/lib/format'
import type { Sort } from '@/ui/table'

/** Status box in DESIGN.md's brand-secure tint, the same as the top bar's. */
export const STAT = 'inline-flex h-6 items-center gap-1.5 whitespace-nowrap rounded-md border border-brand-secure/35 bg-brand-secure/10 px-2 text-brand-secure'

export type FieldFilterState = Omit<FieldFilter, 'sort_by' | 'sort_desc' | 'limit' | 'offset'>

interface FieldFilterStore {
  filter: FieldFilterState
  sort: Sort
  offset: number
  set: (change: Partial<FieldFilterState>) => void
  replace: (filter: FieldFilterState) => void
  setSort: (sort: Sort) => void
  page: (offset: number) => void
}

/** Lives outside the Fields tab so the filter survives leaving the Data Explorer and coming back. */
export const useFieldFilter = create<FieldFilterStore>()((set) => ({
  filter: {},
  sort: { key: 'alpha_count', desc: true },
  offset: 0,
  set: (change) => set((s) => ({ filter: { ...s.filter, ...change }, offset: 0 })),
  replace: (filter) => set({ filter, offset: 0 }),
  setSort: (sort) => set({ sort: { key: sort.key as FieldSortKey, desc: sort.desc }, offset: 0 }),
  page: (offset) => set({ offset }),
}))

const NONE: string[] = []

/**
 * The datasets the Fields tab filters on, and how to change them: while a lab picks datasets,
 * that pick (kept across a reload); otherwise the Fields filter's own.
 */
export function useDatasetChoice(): [string[], (ids: string[]) => void] {
  const picking = useDatasetPick((s) => s.active)
  const picked = useDatasetPick((s) => s.ids)
  const filtered = useFieldFilter((s) => s.filter.dataset_ids ?? NONE)
  if (picking) {
    return [
      picked,
      (ids) => {
        useDatasetPick.getState().setIds(ids)
        useFieldFilter.getState().page(0)
      },
    ]
  }
  return [filtered, (ids) => useFieldFilter.getState().set({ dataset_ids: ids })]
}

/** Start a catalog download. Calls BRAIN, spends no simulations. */
export function useDownload() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => catalog.syncAll(),
    onSuccess: () => {
      toast.success('Syncing every BRAIN dataset')
      void queryClient.invalidateQueries({ queryKey: ['catalog'] })
    },
    onError: (error) => toast.error(errorMessage(error)),
  })
}

export function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return debounced
}

export const isActive =(v: unknown) => v != null && v !== '' && !(Array.isArray(v) && v.length === 0)

export const syncBody = (scope: Scope): CatalogSyncRequest => ({
  region: scope.region,
  delay: scope.delay,
  universe: scope.universe,
  instrument_type: scope.instrumentType,
})

export const sameScope = (a: Scope, b: Scope) =>
  a.region === b.region && a.delay === b.delay && a.universe === b.universe && a.instrumentType === b.instrumentType

/** `×1.4` */
export const multiplier = (v: number | null | undefined) => (isNum(v) ? `×${fmt.ratio(v, 1)}` : DASH)

/** Client-side sort for tables the backend returns whole. Absent values last. */
export function sortRows<T>(rows: T[], sort: Sort): T[] {
  const key = sort.key as keyof T
  return [...rows].sort((a, b) => {
    const x = a[key]
    const y = b[key]
    if (x == null) return y == null ? 0 : 1
    if (y == null) return -1
    const c = x < y ? -1 : x > y ? 1 : 0
    return sort.desc ? -c : c
  })
}

/** `themes` is JSON array text of strings or `{id,name}` objects. */
export function parseThemes(raw: string | null): string[] {
  if (!raw) return []
  try {
    const list: unknown = JSON.parse(raw)
    if (!Array.isArray(list)) return []
    return list.map((t) => (typeof t === 'string' ? t : ((t as { name?: string; id?: string })?.name ?? (t as { id?: string })?.id ?? JSON.stringify(t))))
  } catch {
    return [raw]
  }
}
