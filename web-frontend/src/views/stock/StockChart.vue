<template>
  <div ref="el" class="stock-chart" :style="{ height: height + 'px' }"></div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { CandlestickChart, LineChart, BarChart } from 'echarts/charts'
import {
  GridComponent, TooltipComponent, LegendComponent, DataZoomComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

// 红涨绿跌（A 股惯例，计划书 §七测试锁定项）
echarts.use([
  CandlestickChart, LineChart, BarChart,
  GridComponent, TooltipComponent, LegendComponent, DataZoomComponent,
  CanvasRenderer,
])

const UP = '#e53935'
const DOWN = '#43a047'

const props = defineProps({
  /** K线模式：[{date,open,close,high,low,volume}] */
  kline: { type: Array, default: null },
  /** 分时模式：{prevClose, items:[{time,price,avg}]} */
  intraday: { type: Object, default: null },
  height: { type: Number, default: 420 },
})

const el = ref(null)
let chart = null
let ro = null

function render() {
  if (!chart) return
  if (props.kline) return renderKline(props.kline)
  if (props.intraday) return renderIntraday(props.intraday)
  chart.clear()
}

function renderKline(rows) {
  const dates = rows.map((r) => r.date)
  const ohlc = rows.map((r) => [r.open, r.close, r.low, r.high])
  const vols = rows.map((r, i) => ({
    value: r.volume,
    itemStyle: { color: r.close >= r.open ? UP : DOWN },
  }))
  chart.setOption({
    animation: false,
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    grid: [
      { left: 62, right: 16, top: 16, height: '58%' },
      { left: 62, right: 16, top: '78%', height: '14%' },
    ],
    xAxis: [
      { type: 'category', data: dates, boundaryGap: true, axisLine: { lineStyle: { color: '#bbb' } } },
      { type: 'category', gridIndex: 1, data: dates, axisLabel: { show: false } },
    ],
    yAxis: [
      { scale: true, splitLine: { lineStyle: { color: '#f0f0f0' } } },
      { gridIndex: 1, axisLabel: { show: false }, splitLine: { show: false } },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 60, end: 100 },
      { type: 'slider', xAxisIndex: [0, 1], bottom: 0, height: 16 },
    ],
    series: [
      {
        name: 'K线', type: 'candlestick', data: ohlc,
        itemStyle: { color: UP, color0: DOWN, borderColor: UP, borderColor0: DOWN },
      },
      { name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: vols },
    ],
  }, true)
}

function renderIntraday({ prevClose, items }) {
  const times = items.map((r) => r.time)
  const prices = items.map((r) => r.price)
  const avgs = items.map((r) => r.avg)
  chart.setOption({
    animation: false,
    tooltip: { trigger: 'axis' },
    legend: { data: ['价格', '均价'], top: 0, right: 8, itemWidth: 14 },
    grid: { left: 62, right: 16, top: 28, bottom: 30 },
    xAxis: { type: 'category', data: times, axisLabel: { fontSize: 11 } },
    yAxis: {
      scale: true, splitLine: { lineStyle: { color: '#f0f0f0' } },
    },
    series: [
      {
        name: '价格', type: 'line', data: prices, showSymbol: false,
        lineStyle: { color: '#1976d2', width: 1.4 },
        areaStyle: { color: 'rgba(25,118,210,0.06)' },
        markLine: prevClose ? {
          silent: true, symbol: 'none',
          label: { formatter: `昨收 ${prevClose.toFixed(2)}`, position: 'insideStartTop', fontSize: 11 },
          lineStyle: { color: '#999', type: 'dashed' },
          data: [{ yAxis: prevClose }],
        } : undefined,
      },
      { name: '均价', type: 'line', data: avgs, showSymbol: false, lineStyle: { color: '#ff9800', width: 1 } },
    ],
  }, true)
}

onMounted(() => {
  chart = echarts.init(el.value)
  ro = new ResizeObserver(() => chart && chart.resize())
  ro.observe(el.value)
  render()
})
watch(props, render, { deep: true })
onBeforeUnmount(() => {
  ro?.disconnect()
  chart?.dispose()
  chart = null
})
</script>

<style scoped>
.stock-chart { width: 100%; }
</style>
