/** ECharts 封装：只暴露声明式 option，生命周期与 resize 在这里处理。 */

import * as echarts from 'echarts'
import type { ECharts, EChartsOption } from 'echarts'
import { useEffect, useMemo, useRef } from 'react'
import type { Bar, EquityPoint } from '../api/types'
import { fmtCompact, fmtMoney, fmtPct } from '../utils/format'

const AXIS = '#6b7c94'
const GRID_LINE = 'rgba(35, 48, 68, 0.55)'
const UP = '#ff5d6c'
const DOWN = '#2ecc8f'
const ACCENT = '#5b8cff'

interface ChartProps {
  option: EChartsOption
  height?: number
  className?: string
}

export function Chart({ option, height = 300, className }: ChartProps) {
  const hostRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<ECharts | null>(null)

  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    const chart = echarts.init(host, undefined, { renderer: 'canvas' })
    chartRef.current = chart
    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(host)
    return () => {
      observer.disconnect()
      chart.dispose()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    chartRef.current?.setOption(option, true)
  }, [option])

  return <div ref={hostRef} className={className ?? 'chart'} style={{ height }} />
}

// --------------------------------------------------------------------------- //
// 权益曲线 + 回撤
// --------------------------------------------------------------------------- //

export function EquityChart({
  points,
  height = 320,
  showDrawdown = true,
}: {
  points: EquityPoint[]
  height?: number
  showDrawdown?: boolean
}) {
  const option = useMemo<EChartsOption>(() => {
    const dates = points.map((point) => point.dt.slice(0, 10))
    const equity = points.map((point) => point.total_equity)
    let peak = -Infinity
    const drawdown = points.map((point) => {
      peak = Math.max(peak, point.total_equity)
      return peak > 0 ? point.total_equity / peak - 1 : 0
    })

    return {
      backgroundColor: 'transparent',
      animation: false,
      tooltip: {
        trigger: 'axis',
        backgroundColor: 'rgba(13, 18, 27, 0.95)',
        borderColor: '#2b3b52',
        textStyle: { color: '#e7eef9', fontSize: 12 },
        axisPointer: { type: 'cross', label: { backgroundColor: '#26344a' } },
        valueFormatter: (value) => fmtMoney(Number(value)),
      },
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      grid: showDrawdown
        ? [
            { left: 8, right: 16, top: 18, height: '56%', containLabel: true },
            { left: 8, right: 16, top: '72%', bottom: 8, containLabel: true },
          ]
        : [{ left: 8, right: 16, top: 18, bottom: 10, containLabel: true }],
      xAxis: showDrawdown
        ? [
            {
              type: 'category',
              data: dates,
              boundaryGap: false,
              axisLine: { lineStyle: { color: GRID_LINE } },
              axisLabel: { color: AXIS, fontSize: 10 },
              axisTick: { show: false },
            },
            {
              type: 'category',
              gridIndex: 1,
              data: dates,
              boundaryGap: false,
              axisLine: { lineStyle: { color: GRID_LINE } },
              axisLabel: { color: AXIS, fontSize: 10 },
              axisTick: { show: false },
            },
          ]
        : [
            {
              type: 'category',
              data: dates,
              boundaryGap: false,
              axisLine: { lineStyle: { color: GRID_LINE } },
              axisLabel: { color: AXIS, fontSize: 10 },
              axisTick: { show: false },
            },
          ],
      yAxis: showDrawdown
        ? [
            {
              type: 'value',
              scale: true,
              axisLabel: {
                color: AXIS,
                fontSize: 10,
                formatter: (value: number) => fmtCompact(value),
              },
              splitLine: { lineStyle: { color: GRID_LINE } },
            },
            {
              type: 'value',
              gridIndex: 1,
              max: 0,
              axisLabel: {
                color: AXIS,
                fontSize: 10,
                formatter: (value: number) => `${(value * 100).toFixed(0)}%`,
              },
              splitLine: { lineStyle: { color: GRID_LINE } },
            },
          ]
        : [
            {
              type: 'value',
              scale: true,
              axisLabel: {
                color: AXIS,
                fontSize: 10,
                formatter: (value: number) => fmtCompact(value),
              },
              splitLine: { lineStyle: { color: GRID_LINE } },
            },
          ],
      dataZoom: [{ type: 'inside', xAxisIndex: showDrawdown ? [0, 1] : [0] }],
      series: [
        {
          name: '总权益',
          type: 'line',
          data: equity,
          showSymbol: false,
          smooth: false,
          lineStyle: { width: 1.6, color: ACCENT },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: 'rgba(91, 140, 255, 0.28)' },
              { offset: 1, color: 'rgba(91, 140, 255, 0.0)' },
            ]),
          },
        },
        ...(showDrawdown
          ? [
              {
                name: '回撤',
                type: 'line' as const,
                xAxisIndex: 1,
                yAxisIndex: 1,
                data: drawdown,
                showSymbol: false,
                lineStyle: { width: 1, color: '#ff9a6b' },
                areaStyle: {
                  color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                    { offset: 0, color: 'rgba(255, 154, 107, 0.05)' },
                    { offset: 1, color: 'rgba(255, 154, 107, 0.28)' },
                  ]),
                },
              },
            ]
          : []),
      ],
    }
  }, [points, showDrawdown])

  if (points.length === 0) {
    return <div className="empty">暂无权益数据</div>
  }
  return <Chart option={option} height={height} />
}

// --------------------------------------------------------------------------- //
// K 线 + 均线 + 成交量
// --------------------------------------------------------------------------- //

function movingAverage(values: number[], window: number): Array<number | null> {
  const result: Array<number | null> = []
  let sum = 0
  for (let i = 0; i < values.length; i += 1) {
    sum += values[i]
    if (i >= window) sum -= values[i - window]
    result.push(i >= window - 1 ? Number((sum / window).toFixed(3)) : null)
  }
  return result
}

export function KLineChart({ bars, height = 480 }: { bars: Bar[]; height?: number }) {
  const option = useMemo<EChartsOption>(() => {
    const dates = bars.map((bar) => bar.date)
    const candle = bars.map((bar) => [bar.open, bar.close, bar.low, bar.high])
    const closes = bars.map((bar) => bar.close)
    const volumes = bars.map((bar, index) => ({
      value: bar.volume,
      itemStyle: { color: bar.close >= bar.open ? 'rgba(255, 93, 108, 0.55)' : 'rgba(46, 204, 143, 0.55)' },
      name: dates[index],
    }))

    return {
      backgroundColor: 'transparent',
      animation: false,
      tooltip: {
        trigger: 'axis',
        backgroundColor: 'rgba(13, 18, 27, 0.95)',
        borderColor: '#2b3b52',
        textStyle: { color: '#e7eef9', fontSize: 12 },
        axisPointer: { type: 'cross', label: { backgroundColor: '#26344a' } },
      },
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      legend: {
        top: 0,
        left: 0,
        textStyle: { color: AXIS, fontSize: 11 },
        itemWidth: 14,
        itemHeight: 2,
        data: ['MA5', 'MA20', 'MA60'],
      },
      grid: [
        { left: 8, right: 52, top: 24, height: '62%', containLabel: true },
        { left: 8, right: 52, top: '76%', bottom: 26, containLabel: true },
      ],
      xAxis: [
        {
          type: 'category',
          data: dates,
          boundaryGap: true,
          axisLine: { lineStyle: { color: GRID_LINE } },
          axisLabel: { color: AXIS, fontSize: 10 },
          axisTick: { show: false },
        },
        {
          type: 'category',
          gridIndex: 1,
          data: dates,
          axisLine: { lineStyle: { color: GRID_LINE } },
          axisLabel: { show: false },
          axisTick: { show: false },
        },
      ],
      yAxis: [
        {
          scale: true,
          position: 'right',
          axisLabel: { color: AXIS, fontSize: 10 },
          splitLine: { lineStyle: { color: GRID_LINE } },
        },
        {
          gridIndex: 1,
          scale: true,
          position: 'right',
          axisLabel: {
            color: AXIS,
            fontSize: 10,
            formatter: (value: number) => fmtCompact(value),
          },
          splitLine: { lineStyle: { color: GRID_LINE } },
        },
      ],
      dataZoom: [
        { type: 'inside', xAxisIndex: [0, 1], start: Math.max(0, 100 - 20000 / Math.max(bars.length, 1)), end: 100 },
        {
          type: 'slider',
          xAxisIndex: [0, 1],
          bottom: 0,
          height: 16,
          borderColor: 'transparent',
          backgroundColor: 'rgba(255,255,255,0.03)',
          fillerColor: 'rgba(91, 140, 255, 0.14)',
          handleStyle: { color: '#3b4f6d' },
          textStyle: { color: AXIS, fontSize: 10 },
        },
      ],
      series: [
        {
          name: 'K线',
          type: 'candlestick',
          data: candle,
          itemStyle: {
            color: UP,
            color0: DOWN,
            borderColor: UP,
            borderColor0: DOWN,
          },
        },
        {
          name: 'MA5',
          type: 'line',
          data: movingAverage(closes, 5),
          showSymbol: false,
          smooth: true,
          lineStyle: { width: 1, color: '#f0b90b' },
        },
        {
          name: 'MA20',
          type: 'line',
          data: movingAverage(closes, 20),
          showSymbol: false,
          smooth: true,
          lineStyle: { width: 1, color: '#5b8cff' },
        },
        {
          name: 'MA60',
          type: 'line',
          data: movingAverage(closes, 60),
          showSymbol: false,
          smooth: true,
          lineStyle: { width: 1, color: '#b07cff' },
        },
        {
          name: '成交量',
          type: 'bar',
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: volumes,
          barMaxWidth: 8,
        },
      ],
    }
  }, [bars])

  if (bars.length === 0) return <div className="empty">暂无行情数据</div>
  return <Chart option={option} height={height} />
}

// --------------------------------------------------------------------------- //
// 小型收益柱状图（月度收益）
// --------------------------------------------------------------------------- //

export function MonthlyReturnChart({
  returns,
  height = 180,
}: {
  returns: Record<string, number>
  height?: number
}) {
  const option = useMemo<EChartsOption>(() => {
    const entries = Object.entries(returns).sort(([a], [b]) => a.localeCompare(b))
    return {
      backgroundColor: 'transparent',
      animation: false,
      tooltip: {
        trigger: 'axis',
        backgroundColor: 'rgba(13, 18, 27, 0.95)',
        borderColor: '#2b3b52',
        textStyle: { color: '#e7eef9', fontSize: 12 },
        valueFormatter: (value) => fmtPct(Number(value)),
      },
      grid: { left: 8, right: 8, top: 12, bottom: 8, containLabel: true },
      xAxis: {
        type: 'category',
        data: entries.map(([key]) => key),
        axisLine: { lineStyle: { color: GRID_LINE } },
        axisLabel: { color: AXIS, fontSize: 10, rotate: 40 },
        axisTick: { show: false },
      },
      yAxis: {
        type: 'value',
        axisLabel: { color: AXIS, fontSize: 10, formatter: (value: number) => `${(value * 100).toFixed(0)}%` },
        splitLine: { lineStyle: { color: GRID_LINE } },
      },
      series: [
        {
          type: 'bar',
          data: entries.map(([, value]) => ({
            value,
            itemStyle: { color: value >= 0 ? UP : DOWN, borderRadius: 2 },
          })),
          barMaxWidth: 22,
        },
      ],
    }
  }, [returns])

  if (Object.keys(returns).length === 0) return <div className="empty">暂无月度数据</div>
  return <Chart option={option} height={height} />
}
