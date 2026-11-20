<template>
  <div class="stock-page">
    <h2>个股详情</h2>
    <div class="bar">
      <input v-model="keyword" placeholder="输入代码或名称（如 600519 / 茅台）" class="code-input"
             @keyup.enter="go" />
      <button class="btn-go" @click="go">查询</button>
      <select class="sym-select" v-model="current" @change="onPick">
        <option v-for="s in options" :key="s" :value="s">{{ s }}</option>
      </select>
      <span class="muted">{{ stamp }}</span>
    </div>
    <div v-if="sugOpen && suggestions.length" class="sug-box">
      <div v-for="s in suggestions" :key="s.symbol" class="sug-row" @click="pick(s)">
        {{ s.name }} <span class="muted">{{ s.symbol }}</span>
      </div>
    </div>
    <p class="err" v-if="error">{{ error }}</p>

    <template v-if="d">
      <!-- 头部：价格大字 + 涨跌 -->
      <div class="hero">
        <div>
          <div class="hero-name">{{ d.name }} <span class="muted">{{ d.symbol }}</span>
            <span v-if="status === 'partial'" class="tag">盘口源降级</span>
          </div>
          <div class="hero-price" :class="cls">{{ fmt(d.price) }}
            <span class="hero-chg">{{ d.change >= 0 ? '+' : '' }}{{ fmt(d.change) }}
              （{{ d.pct >= 0 ? '+' : '' }}{{ (d.pct || 0).toFixed(2) }}%）</span>
          </div>
        </div>
        <div class="hero-side">
          <div><span class="k">涨停</span><b class="up">{{ fmt(d.limit_up) }}</b></div>
          <div><span class="k">跌停</span><b class="down">{{ fmt(d.limit_down) }}</b></div>
        </div>
      </div>

      <!-- 基本行情 + 估值指标 -->
      <div class="kv-grid">
        <div class="kv"><span>今开</span><b :class="cmpCls(d.open)">{{ fmt(d.open) }}</b></div>
        <div class="kv"><span>昨收</span><b>{{ fmt(d.prev_close) }}</b></div>
        <div class="kv"><span>最高</span><b class="up">{{ fmt(d.high) }}</b></div>
        <div class="kv"><span>最低</span><b class="down">{{ fmt(d.low) }}</b></div>
        <div class="kv"><span>成交量</span><b>{{ fmtVol(d.volume) }}</b></div>
        <div class="kv"><span>成交额</span><b>{{ fmtAmt(d.amount) }}</b></div>
        <div class="kv"><span>换手率</span><b>{{ num(d.turnover_rate) }}%</b></div>
        <div class="kv"><span>量比</span><b>{{ num(d.volume_ratio) }}</b></div>
        <div class="kv"><span>振幅</span><b>{{ num(d.amplitude) }}%</b></div>
        <div class="kv"><span>市盈率(TTM)</span><b>{{ num(d.pe_ttm) }}</b></div>
        <div class="kv"><span>市净率</span><b>{{ num(d.pb) }}</b></div>
        <div class="kv"><span>总市值</span><b>{{ fmtAmt(d.total_mv) }}</b></div>
        <div class="kv"><span>流通市值</span><b>{{ fmtAmt(d.float_mv) }}</b></div>
      </div>

      <!-- 五档盘口 + 走势图 -->
      <div class="two-col">
        <div class="panel">
          <h3>五档盘口</h3>
          <template v-if="d.depth">
            <div v-for="(a, i) in [...d.depth.asks].reverse()" :key="'a'+i" class="depth-row">
              <span class="dp-tag">卖{{ 5 - i }}</span>
              <span class="dp-price down">{{ fmt(a.price) }}</span>
              <span class="dp-vol muted">{{ fmtVol(a.volume) }}</span>
            </div>
            <div class="depth-mid" :class="cls">{{ fmt(d.price) }}</div>
            <div v-for="(b, i) in d.depth.bids" :key="'b'+i" class="depth-row">
              <span class="dp-tag">买{{ i + 1 }}</span>
              <span class="dp-price up">{{ fmt(b.price) }}</span>
              <span class="dp-vol muted">{{ fmtVol(b.volume) }}</span>
            </div>
          </template>
          <p v-else class="muted">五档数据暂不可用（行情源降级）</p>
        </div>
        <div class="panel">
          <div class="panel-head">
            <h3>走势</h3>
            <div class="seg">
              <button :class="{ on: mode === 'intraday' }" @click="switchMode('intraday')">分时</button>
              <button v-for="p in periods" :key="p" :class="{ on: mode === p }" @click="switchMode(p)">
                {{ p === 'day' ? '日K' : p === 'week' ? '周K' : '月K' }}
              </button>
            </div>
          </div>
          <StockChart v-if="kdata" :kline="kdata" :height="360" />
          <StockChart v-else-if="idata && idata.items.length" :intraday="idata" :height="360" />
          <p v-else-if="!loading" class="muted">走势数据加载失败</p>
        </div>
      </div>
    </template>
    <p v-else-if="!loading" class="muted">输入代码查询个股详情。</p>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import StockChart from './StockChart.vue'
import { getDetail, getIntraday, getKline, listWatchlist, searchStock } from '../../api/stock'

const periods = ['day', 'week', 'month']
const route = useRoute()
const keyword = ref('')
const current = ref('')
const options = ref([])
const mode = ref('intraday')
const d = ref(null)
const status = ref('ok')
const kdata = ref(null)
const idata = ref(null)
const suggestions = ref([])
const sugOpen = ref(false)
const loading = ref(false)
const error = ref('')
const stamp = ref('')
let timer = null

const fmt = (v) => (v == null || !Number.isFinite(+v) ? '—' : (+v).toFixed(2))
const num = (v) => (v == null || !Number.isFinite(+v) ? '—' : +v)
const fmtVol = (v) => (v == null ? '—' : v >= 1e8 ? `${(v / 1e8).toFixed(2)}亿手` : v >= 1e4 ? `${(v / 1e4).toFixed(2)}万手` : `${Math.round(v)}手`)
const fmtAmt = (v) => (v == null || !v ? '—' : v >= 1e8 ? `${(v / 1e8).toFixed(2)}亿` : v >= 1e4 ? `${(v / 1e4).toFixed(2)}万` : `${v.toFixed(0)}`)
const cls = computed(() => ((d.value?.pct || 0) > 0 ? 'up' : (d.value?.pct || 0) < 0 ? 'down' : ''))
const cmpCls = (v) => (v > d.value?.prev_close ? 'up' : v < d.value?.prev_close ? 'down' : '')

// 后端已统一量单位（东财 f47=手；新浪五档股→手换算），前端只管格式化。
async function loadDetail() {
  if (!current.value) return
  loading.value = true
  error.value = ''
  try {
    const r = await getDetail(current.value)
    status.value = r.source_status
    d.value = r.data
    stamp.value = r.data ? `更新于 ${new Date().toLocaleTimeString('zh-CN')}` : ''
    await loadChart()
  } catch (e) {
    error.value = e.response?.data?.detail || '详情加载失败'
  } finally {
    loading.value = false
  }
}

async function loadChart() {
  try {
    if (mode.value === 'intraday') {
      kdata.value = null
      idata.value = await getIntraday(current.value)
    } else {
      idata.value = null
      const r = await getKline(current.value, mode.value, 180)
      kdata.value = r.items || []
    }
  } catch (e) { /* 图表失败不影响头部数据展示 */ }
}

function switchMode(m) {
  mode.value = m
  loadChart()
}

function onPick() { mode.value = 'intraday'; loadDetail() }

async function go() {
  const kw = keyword.value.trim()
  if (!kw) return
  error.value = ''
  if (/^\d{6}$/.test(kw) || /^(sh|sz|bj)\d{6}$/.test(kw.toLowerCase())) {
    const sym = kw.toLowerCase()
    if (!options.value.includes(sym)) options.value = [sym, ...options.value]
    current.value = sym
    sugOpen.value = false
    await loadDetail()
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
  if (!options.value.includes(s.symbol)) options.value = [s.symbol, ...options.value]
  current.value = s.symbol
  loadDetail()
}

onMounted(async () => {
  try {
    const wl = await listWatchlist()
    options.value = (wl.items || []).map((w) => w.symbol)
  } catch (e) { /* 选项加载失败不阻塞 */ }
  const q = route.query.symbol
  current.value = typeof q === 'string' && q ? q : (options.value[0] || '')
  if (current.value) loadDetail()
  timer = setInterval(() => { if (document.visibilityState === 'visible') loadDetail() }, 15000)
})
onBeforeUnmount(() => clearInterval(timer))
</script>

<style scoped>
.stock-page { padding: 20px; position: relative; }
h2 { margin: 0 0 10px; }
.muted { color: #888; font-size: 13px; }
.err { color: #e53935; font-size: 13px; }
.up { color: #e53935; }
.down { color: #43a047; }
.bar { display: flex; gap: 8px; align-items: center; margin-bottom: 10px; flex-wrap: wrap; }
.code-input { padding: 6px 12px; border: 1px solid #d0d0d0; border-radius: 4px; width: 240px; }
.btn-go { padding: 6px 16px; background: #1976d2; color: #fff; border: none; border-radius: 4px; cursor: pointer; }
.sym-select { padding: 6px; border: 1px solid #d0d0d0; border-radius: 4px; max-width: 200px; }
.sug-box { position: absolute; z-index: 9; background: #fff; border: 1px solid #d0d0d0; border-radius: 4px; width: 240px; }
.sug-row { padding: 6px 12px; cursor: pointer; font-size: 13px; }
.sug-row:hover { background: #f0f6ff; }
.hero { display: flex; justify-content: space-between; align-items: flex-start; background: #fff; border: 1px solid #e8e8e8; border-radius: 10px; padding: 16px 20px; margin-bottom: 12px; }
.hero-name { font-size: 15px; font-weight: 600; }
.tag { font-size: 11px; color: #e65100; background: #fff3e0; border-radius: 8px; padding: 1px 8px; margin-left: 6px; }
.hero-price { font-size: 30px; font-weight: 700; margin-top: 6px; }
.hero-price.up { color: #e53935; }
.hero-price.down { color: #43a047; }
.hero-chg { font-size: 15px; font-weight: 500; margin-left: 8px; }
.hero-side div { margin-bottom: 6px; font-size: 13px; text-align: right; }
.hero-side .k { color: #888; margin-right: 8px; }
.kv-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 8px; margin-bottom: 12px; }
.kv { background: #fff; border: 1px solid #eee; border-radius: 8px; padding: 8px 12px; font-size: 13px; display: flex; justify-content: space-between; }
.kv span { color: #888; }
.two-col { display: grid; grid-template-columns: 260px 1fr; gap: 12px; }
.panel { background: #fff; border: 1px solid #e8e8e8; border-radius: 10px; padding: 12px 16px; }
.panel h3 { margin: 0 0 10px; font-size: 14px; }
.panel-head { display: flex; justify-content: space-between; align-items: center; }
.depth-row { display: flex; justify-content: space-between; font-size: 13px; padding: 3px 4px; }
.dp-tag { color: #888; width: 40px; }
.dp-price { width: 70px; }
.depth-mid { text-align: center; font-weight: 700; font-size: 16px; padding: 4px 0; }
.seg { display: flex; }
.seg button { padding: 3px 12px; border: 1px solid #d0d0d0; background: #fff; cursor: pointer; font-size: 12px; margin-left: -1px; }
.seg button:first-child { border-radius: 4px 0 0 4px; }
.seg button:last-child { border-radius: 0 4px 4px 0; }
.seg button.on { background: #1976d2; color: #fff; border-color: #1976d2; z-index: 1; position: relative; }
@media (max-width: 900px) { .two-col { grid-template-columns: 1fr; } }
</style>
