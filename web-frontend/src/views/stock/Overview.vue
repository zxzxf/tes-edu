<template>
  <div class="stock-page">
    <div class="head">
      <h2>盯盘总览</h2>
      <span class="ts muted" v-if="stamp">{{ stamp }}</span>
      <button class="btn-refresh" @click="load">刷新</button>
    </div>
    <p class="muted" v-if="degraded">行情源暂不可用，以下为最近一次快照（{{ stamp || '无' }}）</p>

    <h3 class="sec">大盘指数</h3>
    <div class="stock-grid">
      <div v-for="q in indexQuotes" :key="q.symbol" class="stock-card"
           :class="{ up: q.pct > 0, down: q.pct < 0 }">
        <div class="name">{{ q.name }}</div>
        <div class="price">{{ q.price.toFixed(2) }}</div>
        <div class="change">{{ fmtPct(q.pct) }}</div>
      </div>
      <div v-if="!indexQuotes.length && !loading" class="empty-h">暂无指数数据</div>
    </div>

    <h3 class="sec">自选股涨跌榜</h3>
    <div class="board">
      <div class="board-col">
        <div class="board-title up-t">领涨 TOP5</div>
        <div v-for="q in topGainers" :key="q.symbol" class="board-row" :class="pctCls(q)">
          <span class="nm">{{ q.name || q.symbol }}</span>
          <span>{{ fmtPct(q.pct) }}</span>
        </div>
        <div v-if="!topGainers.length" class="empty-h muted">—</div>
      </div>
      <div class="board-col">
        <div class="board-title down-t">领跌 TOP5</div>
        <div v-for="q in topLosers" :key="q.symbol" class="board-row" :class="pctCls(q)">
          <span class="nm">{{ q.name || q.symbol }}</span>
          <span>{{ fmtPct(q.pct) }}</span>
        </div>
        <div v-if="!topLosers.length" class="empty-h muted">—</div>
      </div>
    </div>

    <div class="grid-list" v-if="watchQuotes.length">
      <div v-for="q in watchQuotes" :key="q.symbol" class="stock-card" :class="pctCls(q)">
        <div class="code">{{ q.symbol }}</div>
        <div class="name">{{ q.name }}</div>
        <div class="price">{{ q.price.toFixed(2) }}</div>
        <div class="change">{{ fmtPct(q.pct) }}　{{ q.change >= 0 ? '+' : '' }}{{ q.change.toFixed(2) }}</div>
      </div>
    </div>
    <p class="muted" v-else-if="!loading">自选股为空——去「自选股」页添加吧。</p>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getQuotes, listWatchlist } from '../../api/stock'

const loading = ref(true)
const degraded = ref(false)
const stamp = ref('')
const items = ref([])
const watchSymbols = ref([])
let timer = null

const fmtPct = (v) => `${v > 0 ? '+' : ''}${(v || 0).toFixed(2)}%`
const pctCls = (q) => (q.pct > 0 ? 'up' : q.pct < 0 ? 'down' : '')

const watchSet = computed(() => new Set(watchSymbols.value))
const indexQuotes = computed(() => items.value.filter((q) => !watchSet.value.has(q.symbol)))
const watchQuotes = computed(() => items.value.filter((q) => watchSet.value.has(q.symbol)))
const sorted = computed(() => [...watchQuotes.value].sort((a, b) => b.pct - a.pct))
const topGainers = computed(() => sorted.value.slice(0, 5).filter((q) => q.pct > 0))
const topLosers = computed(() => sorted.value.slice(-5).reverse().filter((q) => q.pct < 0))

async function load() {
  try {
    const [wl, qs] = await Promise.all([listWatchlist(), getQuotes()])
    watchSymbols.value = (wl.items || []).map((w) => w.symbol)
    items.value = qs.items || []
    degraded.value = qs.source_status === 'degraded'
    if (qs.last_fetched_at) {
      stamp.value = `更新于 ${new Date(qs.last_fetched_at * 1000).toLocaleTimeString('zh-CN')}`
    }
  } catch (e) {
    degraded.value = true
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  load()
  timer = setInterval(load, 10000) // 快照 10s 轮询（后端 Redis 已是缓存面）
})
onBeforeUnmount(() => clearInterval(timer))
</script>

<style scoped>
.stock-page { padding: 20px; }
.head { display: flex; align-items: baseline; gap: 12px; }
.head h2 { margin: 0; }
.sec { margin: 18px 0 10px; font-size: 15px; }
.muted { color: #888; margin: 6px 0; font-size: 13px; }
.btn-refresh { margin-left: auto; padding: 4px 14px; border: 1px solid #d0d0d0; border-radius: 4px; background: #fff; cursor: pointer; font-size: 13px; }
.stock-grid, .grid-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 12px; }
.stock-card { border: 1px solid #e8e8e8; border-radius: 8px; padding: 12px; background: #fff; }
.stock-card.up { border-left: 3px solid #e53935; }
.stock-card.down { border-left: 3px solid #43a047; }
.code { font-size: 12px; color: #999; }
.name { font-weight: 600; margin: 4px 0; }
.price { font-size: 18px; font-weight: 700; }
.up .price, .up .change { color: #e53935; }
.down .price, .down .change { color: #43a047; }
.change { font-size: 13px; margin-top: 4px; }
.board { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.board-col { border: 1px solid #e8e8e8; border-radius: 8px; padding: 10px 14px; background: #fff; }
.board-title { font-size: 13px; font-weight: 600; margin-bottom: 6px; }
.up-t { color: #e53935; }
.down-t { color: #43a047; }
.board-row { display: flex; justify-content: space-between; font-size: 13px; padding: 3px 0; }
.board-row.up span:last-child { color: #e53935; }
.board-row.down span:last-child { color: #43a047; }
.empty-h { grid-column: 1 / -1; color: #999; text-align: center; padding: 12px; }
</style>
