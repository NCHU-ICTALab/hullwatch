import { useEffect, useRef } from 'react'
import { LineChart, ScatterChart } from 'echarts/charts'
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  MarkPointComponent,
  TooltipComponent,
} from 'echarts/components'
import {
  getInstanceByDom,
  init,
  use as registerECharts,
  type EChartsCoreOption,
} from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'

registerECharts([
  LineChart,
  ScatterChart,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  MarkPointComponent,
  TooltipComponent,
  CanvasRenderer,
])

interface EChartProps {
  option: EChartsCoreOption
  className?: string
  ariaLabel: string
}

export function EChart({ option, className, ariaLabel }: EChartProps) {
  const elementRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<ReturnType<typeof init> | null>(null)

  useEffect(() => {
    const element = elementRef.current
    if (!element) return
    const chart = getInstanceByDom(element) ?? init(element)
    chartRef.current = chart
    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(element)
    return () => {
      observer.disconnect()
      if (chartRef.current === chart) chartRef.current = null
      chart.dispose()
    }
  }, [])

  useEffect(() => {
    chartRef.current?.setOption(option, { notMerge: true })
  }, [option])

  return <div ref={elementRef} className={className} role="img" aria-label={ariaLabel} />
}
