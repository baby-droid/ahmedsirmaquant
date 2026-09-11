/**
 * Formatting for a quant interface: every figure is read against the one above it, so
 * precision is fixed per measure. A genuinely absent value is an em dash, never 0.
 */

export const DASH = '—'

export const isNum = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)

export const fmt = {
  /** 5,000 */
  int: (v: number | null | undefined) => (isNum(v) ? Math.round(v).toLocaleString('en-US') : DASH),
  /** 1.58 — Sharpe, Fitness, K-Ratio. */
  ratio: (v: number | null | undefined, digits = 2) => (isNum(v) ? v.toFixed(digits) : DASH),
  /** A fraction as a percent: 0.643 → 64.3% */
  pct: (v: number | null | undefined, digits = 1) => (isNum(v) ? `${(v * 100).toFixed(digits)}%` : DASH),
  /** A fraction as basis points: 0.0005 → 5.0 bps */
  bps: (v: number | null | undefined, digits = 1) => (isNum(v) ? `${(v * 10_000).toFixed(digits)} bps` : DASH),
  /** 12.4K */
  compact: (v: number | null | undefined) =>
    isNum(v) ? new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(v) : DASH,
  /** +1.25 / −0.40 */
  signed: (v: number | null | undefined, digits = 2) =>
    isNum(v) ? `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(digits)}` : DASH,
  /** 2h 05m, 4m 12s, 9s */
  duration: (seconds: number | null | undefined) => {
    if (!isNum(seconds) || seconds < 0) return DASH
    const s = Math.floor(seconds)
    const h = Math.floor(s / 3600)
    const m = Math.floor((s % 3600) / 60)
    if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m`
    if (m > 0) return `${m}m ${String(s % 60).padStart(2, '0')}s`
    return `${s}s`
  },
  /** 7h 21m — for countdowns to a reset. */
  countdown: (seconds: number | null | undefined) => {
    if (!isNum(seconds)) return DASH
    const s = Math.max(0, Math.floor(seconds))
    return `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}m`
  },
  /** Sep 10, 2026 */
  date: (iso: string | null | undefined) => (iso ? new Date(normaliseIso(iso)).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : DASH),
  /** Sep 10, 14:05 */
  dateTime: (iso: string | null | undefined) =>
    iso
      ? new Date(normaliseIso(iso)).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })
      : DASH,
  /** 3 min ago */
  ago: (iso: string | null | undefined) => {
    if (!iso) return DASH
    const seconds = (Date.now() - new Date(normaliseIso(iso)).getTime()) / 1000
    if (seconds < 60) return 'just now'
    if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`
    if (seconds < 86_400) return `${Math.floor(seconds / 3600)} h ago`
    return `${Math.floor(seconds / 86_400)} d ago`
  },
}

/** Seconds elapsed since an ISO timestamp. */
export function secondsSince(iso: string | null | undefined, now = Date.now()): number | null {
  return iso ? Math.max(0, (now - new Date(normaliseIso(iso)).getTime()) / 1000) : null
}

/** Some backend timestamps carry no offset; they are UTC. */
function normaliseIso(iso: string): string {
  return /[zZ]|[+-]\d\d:\d\d$/.test(iso) || iso.length <= 10 ? iso : `${iso}Z`
}
