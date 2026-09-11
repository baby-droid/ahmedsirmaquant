import type { AlphaSettings } from '@/api/pool'
import type { AlphaCheck } from '@/api/types'
import { fmt, isNum } from '@/lib/format'

/** `undefined` for an empty or non-numeric input. */
export const parseNum = (text: string): number | undefined => {
  const v = Number(text)
  return text.trim() === '' || !Number.isFinite(v) ? undefined : v
}

/** The limit BRAIN reported for a check, e.g. LOW_SHARPE → 1.58. */
export const limitOf = (checks: AlphaCheck[], name: string) => {
  const limit = checks.find((c) => c.name === name)?.limit
  return isNum(limit) ? limit : null
}

export const checkFigure = (name: string, v: number | null | undefined) => (name.includes('TURNOVER') ? fmt.pct(v) : fmt.ratio(v))

export const settingsItems = (s: AlphaSettings): [string, string][] => [
  ['Region', s.region ?? '—'],
  ['Universe', s.universe ?? '—'],
  ['Delay', fmt.int(s.delay)],
  ['Neutralization', s.neutralization ?? '—'],
  ['Decay', fmt.int(s.decay)],
  ['Truncation', fmt.ratio(s.truncation)],
]
