/**
 * The token hex values for canvas renderers (ECharts, Lightweight Charts), which cannot read
 * CSS variables. Keep in step with the @theme block in index.css.
 */

export const C = {
  canvas: '#010102',
  surface1: '#0f1011',
  surface2: '#141516',
  surface3: '#18191a',
  surface4: '#191a1b',
  hairline: '#23252a',
  hairlineStrong: '#34343a',
  hairlineTertiary: '#3e3e44',
  ink: '#f7f8f8',
  inkMuted: '#d0d6e0',
  inkSubtle: '#8a8f98',
  inkTertiary: '#62666d',
  primary: '#5e6ad2',
  primaryHover: '#828fff',
  profit: '#2ec971',
  loss: '#f15b5b',
  warn: '#e0a84e',
} as const

export const SANS = "'Geist Variable', -apple-system, BlinkMacSystemFont, sans-serif"
export const MONO = "'Geist Mono Variable', 'JetBrains Mono', ui-monospace, monospace"

/** Series colours: a grey ramp. Chromatic colour is reserved for profit/loss meaning. */
export const SERIES = [C.inkMuted, C.inkTertiary, C.hairlineTertiary, C.inkSubtle] as const

/** Sequential ramp for heat grids (coverage, |correlation|, pyramid crowding). */
export const HEAT = [C.surface3, C.hairlineTertiary, C.inkTertiary, C.inkSubtle, C.inkMuted, C.ink] as const

/** Pyramid Multiplier ×1.0 → HEAT step 0 … ×2.0 → step 5. */
export const multiplierHeat = (multiplier: number | null) =>
  multiplier == null ? 0 : Math.max(0, Math.min(HEAT.length - 1, Math.round((multiplier - 1) / 0.2)))

export const echartsTheme = {
  backgroundColor: 'transparent',
  color: [...SERIES],
  textStyle: { fontFamily: MONO, color: C.inkSubtle, fontSize: 11 },
  categoryAxis: {
    axisLine: { lineStyle: { color: C.hairline } },
    axisTick: { show: false },
    axisLabel: { color: C.inkSubtle },
    splitLine: { show: false },
  },
  valueAxis: {
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: C.inkSubtle },
    splitLine: { lineStyle: { color: C.hairline } },
  },
  tooltip: {
    backgroundColor: C.surface3,
    borderColor: C.hairlineStrong,
    textStyle: { color: C.ink, fontFamily: MONO, fontSize: 12 },
    axisPointer: { lineStyle: { color: C.inkTertiary }, crossStyle: { color: C.inkTertiary } },
  },
  legend: { textStyle: { color: C.inkSubtle, fontFamily: SANS } },
}
