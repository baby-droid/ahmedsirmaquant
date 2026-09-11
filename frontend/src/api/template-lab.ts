/** Template Lab: the account's blocks, templates, previews and tasks. Request bodies are snake_case. */

import type { BlockInfo, TemplateDoc } from '@/lib/template-tree'
import { http, qs } from './http'
import type { SearchLabRequest } from './search-lab'

export interface TemplateLabOptions {
  operators: { synced: boolean; count: number }
  blocks: BlockInfo[]
  variables: Record<string, (number | string)[]>
  tags: string[]
  dataFields: string[]
  groupFields: string[]
  vector: string[]
  decays: number[]
  truncation: number
  maxCores: number
  maxSimulations: number
  maxBlocks: number
}

export interface TemplateSummary {
  /** `preset:<slug>` for a preset, a number for a saved template. */
  id: string | number
  name: string
  description: string | null
  preset: boolean
  tree: TemplateDoc
  skeleton: string
  /** Operators the template names that the account does not have. */
  missing: string[]
  /** Where a preset comes from, e.g. AIRS; starred on its card. */
  source: string | null
  updatedAt: string | null
}

export interface TemplateLabPreview {
  round: number
  fields: { total: number; matrix: number; vector: number }
  leftOut: { vector: number }
  universes: string[]
  neutralizations: string[]
  skeleton: string
  sample: { expression: string; settings: Record<string, unknown> }[]
  /** Everything blocking a task; `templateProblems` are the ones about the blocks. */
  problems: string[]
  templateProblems: string[]
  warnings: string[]
}

export interface TemplateLabRequest extends SearchLabRequest {
  tree: TemplateDoc
  template_id?: number | null
  template_name: string
}

export interface TemplateBody {
  name: string
  description?: string | null
  tree: TemplateDoc
}

const B = '/api/template-lab'

export const templateLab = {
  /** `refresh` syncs the account's operators from BRAIN first. */
  options: (refresh = false) => http.get<TemplateLabOptions>(`${B}/options${qs({ refresh: refresh || undefined })}`),
  templates: () => http.get<{ templates: TemplateSummary[] }>(`${B}/templates`),
  create: (body: TemplateBody) => http.post<TemplateSummary>(`${B}/templates`, body),
  update: (id: number, body: TemplateBody) => http.put<TemplateSummary>(`${B}/templates/${id}`, body),
  remove: (id: number) => http.del<{ removed: number }>(`${B}/templates/${id}`),
  /** Free; queues nothing. */
  preview: (body: TemplateLabRequest) => http.post<TemplateLabPreview>(`${B}/preview`, body),
  /** Adds the template's search to Tasks, not started. Spends nothing until it is run there. */
  addTask: (body: TemplateLabRequest & { simulations: number }) => http.post<{ id: number; name: string }>(`${B}/tasks`, body),
}
