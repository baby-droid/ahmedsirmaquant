/**
 * Canvas charts only. ECharts (tree-shaken core, canvas renderer) for bars, scatter and
 * heat grids; Lightweight Charts for PnL series. Both are themed from lib/theme.ts.
 */

import { useEffect, useRef } from 'react'
import * as echarts from 'echarts/core'
import { BarChart, HeatmapChart, LineChart, ScatterChart } from 'echarts/charts'
import { DataZoomComponent, GridComponent, LegendComponent, MarkLineComponent, TooltipComponent, VisualMapComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { ColorType, createChart, LineSeries, LineStyle, type Time, type UTCTimestamp } from 'lightweight-charts'
import { C, echartsTheme, MONO } from '@/lib/theme'
import { cx } from './kit'

echarts.use([BarChart, LineChart, ScatterChart, HeatmapChart, GridComponent, TooltipComponent, LegendComponent, VisualMapComponent, MarkLineComponent, DataZoomComponent, CanvasRenderer])
echarts.registerTheme('harness', echartsTheme)

export type EChartOption = echarts.EChartsCoreOption

export interface ChartClick {
  dataIndex: number
  seriesIndex?: number
  data: unknown
  name: string
}

/** `option` should be memoised; a new object re-renders the chart (notMerge). */
export function EChart({ option, className, label, onClick }: { option: EChartOption; className?: string; label: string; onClick?: (params: ChartClick) => void }) {
  const element = useRef<HTMLDivElement>(null)
  const chart = useRef<ReturnType<typeof echarts.init> | null>(null)
  const click = useRef(onClick)
  useEffect(() => {
    click.current = onClick
  })

  useEffect(() => {
    const node = element.current!
    const instance = echarts.init(node, 'harness', { renderer: 'canvas' })
    chart.current = instance
    instance.on('click', (params) => click.current?.(params as unknown as ChartClick))
    const observer = new ResizeObserver(() => instance.resize())
    observer.observe(node)
    return () => {
      observer.disconnect()
      instance.dispose()
      chart.current = null
    }
  }, [])

  useEffect(() => {
    chart.current?.setOption(option, { notMerge: true })
  }, [option])

  return <div ref={element} role="img" aria-label={label} className={cx('h-64 w-full min-w-0', className)} />
}

/**
 * Cumulative PnL. With `dates` (one `YYYY-MM-DD` per value) the axis shows real trading days;
 * without them, as in compact sparklines, it just counts points.
 */
export function PnlChart({
  values,
  dates,
  className,
  label = 'Cumulative PnL',
  compact,
}: {
  values: number[]
  dates?: string[]
  className?: string
  label?: string
  compact?: boolean
}) {
  const element = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const node = element.current
    if (!node || values.length < 2) return
    const times = dates && dates.length === values.length ? dates : null
    const up = values[values.length - 1] >= values[0]
    const chart = createChart(node, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: C.inkSubtle, fontFamily: MONO, fontSize: 11, attributionLogo: false },
      grid: { vertLines: { visible: false }, horzLines: { color: compact ? 'transparent' : C.hairline } },
      rightPriceScale: { borderVisible: false, visible: !compact },
      // Real dates use the library's own date labels; a bare count needs spelling out.
      timeScale: { borderVisible: false, visible: !compact, ...(times ? {} : { tickMarkFormatter: (time: Time) => String(time) }) },
      localization: {
        ...(times ? {} : { timeFormatter: (time: Time) => `Day ${Number(time).toLocaleString('en-US')}` }),
        priceFormatter: (price: number) => new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 2 }).format(price),
      },
      crosshair: {
        vertLine: { color: C.inkTertiary, style: LineStyle.Dashed, labelBackgroundColor: C.surface4 },
        horzLine: { color: C.inkTertiary, style: LineStyle.Dashed, labelBackgroundColor: C.surface4, visible: !compact },
      },
      handleScroll: false,
      handleScale: false,
    })
    const series = chart.addSeries(LineSeries, { color: up ? C.profit : C.loss, lineWidth: compact ? 1 : 2, priceLineVisible: false, lastValueVisible: !compact })
    series.setData(values.map((value, index) => ({ time: times ? times[index] : ((index + 1) as UTCTimestamp), value })))
    if (!compact) series.createPriceLine({ price: 0, color: C.inkTertiary, lineStyle: LineStyle.Dotted, lineWidth: 1, axisLabelVisible: false })
    chart.timeScale().fitContent()
    return () => chart.remove()
  }, [values, dates, compact])

  return <div ref={element} role="img" aria-label={label} className={cx(compact ? 'h-12' : 'h-60', 'w-full min-w-0', className)} />
}
