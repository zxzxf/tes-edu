<template>
  <div class="stock-page">
    <h2>行情看板</h2>
    <div class="bar">
      <input v-model="keyword" placeholder="输入代码或名称（如 600519 / 茅台）" class="code-input"
             @keyup.enter="go" />
      <button class="btn-go" @click="go">查询</button>
      <select class="sym-select" v-model="current" @change="loadCharts">
        <option v-for="s in options" :key="s" :value="s">{{ s }}</option>
      </select>
      <div class="seg">
        <button :class="{ on: mode === 'intraday' }" @click="switchMode('intraday')">分时</button>
        <button v-for="p in periods" :key="p" :class="{ on: mode === p }" @click="switchMode(p)">
          {{ p === 'day' ? '日K' : p === 'week' ? '周K' : '月K' }}
        </button>
      </div>
    </div>
    <div v-if="sugOpen && suggestions.length" class="sug-box">
      <div v-for="s in suggestions" :key="s.symbol" class="sug-row" @click="pick(s)">
        {{ s.name }} <span class="muted">{{ s.symbol }}</span>
      </div>
    </div>
    <p class="err" v-if="error">{{ error }}</p>

    <div class="quote-head" v-if="quote">
      <b>{{ quote.name }}</b><span class="muted">{{ quote.symbol }}</span>
      <span class="price" :class="quote.pct > 0 ? 'up' : quote.pct < 0 ? 'down' : ''">
        {{ quote.price.toFixed(2) }}
      </span>
      <span :class="quote.pct > 0 ? 'up' : quote.pct < 0 ? 'down' : ''">
        {{ quote.pct > 0 ? '+' : '' }}{{ quote.pct.toFixed(2) }}%
        （{{ quote.change >= 0 ? '+' : '' }}{{ quote.change.toFixed(2) }}）
      </span>
      <span class="muted">今开 {{ quote.open }}　最高 {{ quote.high }}　最低 {{ quote.low }}　昨收 {{ quote.prev_close }}</span>
    </div>

    <StockChart v-if="kdata" :kline="kdata" :height="440" />
    <StockChart v-else-if="idata && idata.items.length" :intraday="idata" :height="440" />
    <div v-else-if="!loading" class="chart-empty muted">暂无图表数据（行情源不可用或代码无效）</div>
    <div v-else class="chart-empty muted">加载中…</div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import StockChart from './StockChart.vue'
import { getIntraday, getKline, getQuotes, listWatchlist, searchStock } from '../../api/stock'

const periods = ['day', 'week', 'month']
const keyword = ref('')
const current = ref('sh000001')
const options = ref(['sh000001'])
const mode = ref('intraday')
const quote = ref(null)
const kdata = ref(null)
const idata = ref(null)
const suggestions = ref([])
const sugOpen = ref(false)
const loading = ref(false)
const error = ref('')

async function go() {
  const kw = keyword.value.trim()
  if (!kw) return
  error.value = ''
  if (/^\d{6}$/.test(kw) || /^(sh|sz|bj)\d{6}$/.test(kw.toLowerCase())) {
    addOption(kw.toLowerCase())
    current.value = kw.toLowerCase()
    sugOpen.value = false
    await loadCharts()
    return
  }
  try {
    const r = await searchStock(kw)
    suggestions.value = r.items || []
    sugOpen.value = suggestions.value.length > 0
    if (!suggestions.value.length) error.value = '没搜到相关标的（仅支持 A 股）'
  } catch (e) {
    error.value = '搜索失败'
  }
}

function pick(s) {
  sugOpen.value = false
  keyword.value = ''
  addOption(s.symbol)
  current.value = s.symbol
  loadCharts()
}

function addOption(sym) {
  if (!options.value.includes(sym)) options.value = [...options.value, sym]
}

function switchMode(m) {
  mode.value = m
  loadCharts()
}

async function loadCharts() {
  loading.value = true
  error.value = ''
  kdata.value = null
  idata.value = null
  try {
    const [qs] = await Promise.all([getQuotes({ symbols: current.value })])
    quote.value = (qs.items || [])[0] || null
    if (mode.value === 'intraday') {
      idata.value = await getIntraday(current.value)
    } else {
      const r = await getKline(current.value, mode.value, 180)
      kdata.value = r.items || []
    }
  } catch (e) {
    error.value = e.response?.data?.detail || '行情加载失败（源降级）'
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  try {
    const [wl, idx] = await Promise.all([listWatchlist(), getQuotes()])
    options.value = [...new Set([...(wl.items || []).map((w) => w.symbol),
                                ...(idx.items || []).map((q) => q.symbol),
                                'sh000001'])]
    current.value = options.value[0]
  } catch (e) { /* 首次进入失败不阻塞，图表区会提示 */ }
  await loadCharts()
})
</script>

<style scoped>
.stock-page { padding: 20px; position: relative; }
.muted { color: #888; font-size: 13px; }
.bar { display: flex; gap: 8px; align-items: center; margin-bottom: 10px; flex-wrap: wrap; }
.code-input { padding: 6px 12px; border: 1px solid #d0d0d0; border-radius: 4px; width: 240px; }
.btn-go { padding: 6px 16px; background: #1976d2; color: #fff; border: none; border-radius: 4px; cursor: pointer; }
.sym-select { padding: 6px; border: 1px solid #d0d0d0; border-radius: 4px; max-width: 220px; }
.seg { display: flex; margin-left: auto; }
.seg button { padding: 5px 14px; border: 1px solid #d0d0d0; background: #fff; cursor: pointer; font-size: 13px; margin-left: -1px; }
.seg button:first-child { border-radius: 4px 0 0 4px; }
.seg button:last-child { border-radius: 0 4px 4px 0; }
.seg button.on { background: #1976d2; color: #fff; border-color: #1976d2; z-index: 1; position: relative; }
.sug-box { position: absolute; z-index: 9; background: #fff; border: 1px solid #d0d0d0; border-radius: 4px; width: 240px; margin: 4px 0 0 0; }
.sug-row { padding: 6px 12px; cursor: pointer; font-size: 13px; }
.sug-row:hover { background: #f0f6ff; }
.err { color: #e53935; font-size: 13px; }
.quote-head { display: flex; gap: 14px; align-items: baseline; margin: 8px 0 12px; flex-wrap: wrap; font-size: 13px; }
.quote-head .price { font-size: 20px; font-weight: 700; }
.up { color: #e53935; }
.down { color: #43a047; }
.chart-empty { border: 2px dashed #e0e0e0; border-radius: 8px; min-height: 360px; display: flex; align-items: center; justify-content: center; }
</style>
