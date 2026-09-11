/**
 * The local data-field catalog (Data Explorer). Rows from DuckDB stay snake_case; request
 * bodies are snake_case. `/counts /stats /facets /tree /fields /datasets` read the scope
 * from the query string with `instrumentType`.
 */

import { http, qs } from './http'
import type { Scope, SyncRun } from './types'

/** A market a full sync covers (`GET /api/catalog/markets`). */
export interface Market {
  instrumentType: string
  region: string
  delay: number
  universe: string
}

export interface CatalogSyncRequest {
  region: string
  delay: number
  universe: string
  instrument_type?: string
  restart?: boolean
}

export interface CatalogScopeRow {
  instrument_type: string
  region: string
  delay: number
  universe: string
  fields: number
  synced_at: string | null
}

export interface CatalogCounts {
  fields: number
  datasets: number
  categories: number
  subcategories: number
}

export interface CatalogStats {
  coverage_min: number | null
  coverage_max: number | null
  coverage_median: number | null
  alpha_count_max: number | null
  user_count_max: number | null
  pyramid_multiplier_max: number | null
}

export interface CatalogFacets {
  categories: { id: string; name: string; n: number }[]
  subcategories: { id: string; name: string; n: number; category_id: string | null }[]
  datasets: { id: string; n: number; category_id: string | null; subcategory_id: string | null }[]
  types: { id: string; n: number }[]
}

export interface CategoryNode {
  id: string
  name: string
  datasets: number
  fields: number
  subcategories: { id: string; name: string; datasets: number; fields: number }[]
}

/** Unknown keys sort by alpha_count on the backend. */
export type FieldSortKey =
  | 'field_id'
  | 'dataset_id'
  | 'category_id'
  | 'coverage'
  | 'user_count'
  | 'alpha_count'
  | 'pyramid_multiplier'
  | 'field_type'

export interface FieldFilter {
  search?: string | null
  dataset_ids?: string[]
  category_ids?: string[]
  subcategory_ids?: string[]
  field_types?: string[]
  coverage_min?: number | null
  coverage_max?: number | null
  alpha_count_min?: number | null
  alpha_count_max?: number | null
  user_count_min?: number | null
  user_count_max?: number | null
  pyramid_multiplier_min?: number | null
  sort_by?: FieldSortKey
  sort_desc?: boolean
  limit?: number
  offset?: number
}

export interface DataFieldRow {
  field_id: string
  dataset_id: string | null
  category_id: string | null
  category_name: string | null
  subcategory_id: string | null
  subcategory_name: string | null
  description: string | null
  field_type: string | null
  coverage: number | null
  user_count: number | null
  alpha_count: number | null
  pyramid_multiplier: number | null
  /** JSON array text. */
  themes: string | null
}

export interface FieldPage {
  total: number
  limit: number
  offset: number
  results: DataFieldRow[]
}

export interface DataFieldDetail extends DataFieldRow {
  instrument_type: string
  region: string
  delay: number
  universe: string
  synced_at: string | null
}

export interface FieldAvailabilityRow {
  instrument_type: string
  region: string
  delay: number
  universe: string
  coverage: number | null
  alpha_count: number | null
  field_type: string | null
}

export interface DatasetRow {
  dataset_id: string
  name: string | null
  description: string | null
  category_id: string | null
  category_name: string | null
  subcategory_id: string | null
  subcategory_name: string | null
  coverage: number | null
  value_score: number | null
  user_count: number | null
  alpha_count: number | null
  field_count: number | null
  pyramid_multiplier: number | null
}

export interface PyramidCell {
  categoryId: string
  region: string
  delay: number
  multiplier: number | null
  alphaCount: number
  /** 3+ alphas this quarter. */
  lit: boolean
  synced: boolean
}

export interface PyramidGridData {
  columns: { region: string; delay: number }[]
  categories: { id: string; name: string }[]
  cells: PyramidCell[]
  /** Calendar quarter in platform time; `end` is the next quarter's first day. */
  quarter: { start: string; end: string; today: string }
}

export interface CoverageMatrixRow {
  field_id: string
  region: string
  delay: number
  universe: string
  instrument_type: string
  coverage: number | null
}

const B = '/api/catalog'

/** Scope in the query string, camelCase instrumentType. */
const scopeQs = (s: Scope, extra: Record<string, string | number | undefined> = {}) =>
  qs({ region: s.region, delay: s.delay, universe: s.universe, instrumentType: s.instrumentType, ...extra })

export const catalog = {
  scopes: () => http.get<CatalogScopeRow[]>(`${B}/scopes`),
  sync: (body: CatalogSyncRequest) => http.post<SyncRun>(`${B}/sync`, body),
  syncAll: () => http.post<SyncRun>(`${B}/sync-all`),
  markets: () => http.get<Market[]>(`${B}/markets`),
  runs: (limit = 20) => http.get<SyncRun[]>(`${B}/sync/runs${qs({ limit })}`),
  run: (id: number) => http.get<SyncRun>(`${B}/sync/runs/${id}`),
  cancel: (id: number) => http.post<{ cancelled: boolean }>(`${B}/sync/runs/${id}/cancel`),
  last: () => http.get<SyncRun[]>(`${B}/sync/last`),

  counts: (s: Scope) => http.get<CatalogCounts>(`${B}/counts${scopeQs(s)}`),
  stats: (s: Scope) => http.get<CatalogStats>(`${B}/stats${scopeQs(s)}`),
  /** Counts under every other active filter; each facet ignores its own selection. */
  facets: (s: Scope, filter: FieldFilter) => http.post<CatalogFacets>(`${B}/facets${scopeQs(s)}`, filter),
  tree: (s: Scope) => http.get<CategoryNode[]>(`${B}/tree${scopeQs(s)}`),
  fields: (s: Scope, filter: FieldFilter) => http.post<FieldPage>(`${B}/fields${scopeQs(s)}`, filter),
  field: (s: Scope, id: string) => http.get<DataFieldDetail>(`${B}/fields/${encodeURIComponent(id)}${scopeQs(s)}`),
  availability: (id: string) => http.get<FieldAvailabilityRow[]>(`${B}/fields/${encodeURIComponent(id)}/availability`),
  datasets: (s: Scope, search?: string) => http.get<DatasetRow[]>(`${B}/datasets${scopeQs(s, { search })}`),

  pyramids: () => http.get<PyramidGridData>(`${B}/pyramids`),
}
