import { useEffect, useRef } from 'react'
import { LineChart } from 'echarts/charts'
import {
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  MarkPointComponent,
  TooltipComponent,
} from 'echarts/components'
import { init, use as registerECharts, type EChartsCoreOption } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'

registerECharts([
  LineChart,
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

  useEffect(() => {
    if (!elementRef.current) return
    const chart = init(elementRef.current)
    chart.setOption(option)
    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(elementRef.current)
    return () => {
      observer.disconnect()
      chart.dispose()
    }
  }, [option])

  return <div ref={elementRef} className={className} role="img" aria-label={ariaLabel} />
}
