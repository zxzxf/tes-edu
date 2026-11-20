<template>
  <div class="stock-page">
    <h2>自选股</h2>
    <p class="muted">增删、分组、置顶（实时行情来自盯盘 worker 快照）</p>

    <div class="add-bar">
      <input v-model="keyword" placeholder="代码或名称（如 600519 / 茅台），回车搜索"
             class="code-input" @keyup.enter="searchAdd" />
      <button class="btn-add" @click="searchAdd" :disabled="searching">
        {{ searching ? '搜索中…' : '添加' }}
      </button>
      <select v-model="newGroupId" class="group-select">
        <option :value="null">未分组</option>
        <option v-for="g in groups" :key="g.group_id" :value="g.group_id">{{ g.name }}</option>
      </select>
      <button class="btn-add alt" @click="createGroup">+ 新分组</button>
    </div>
    <div v-if="sugOpen && suggestions.length" class="sug-box">
      <div v-for="s in suggestions" :key="s.symbol" class="sug-row" @click="pickSug(s)">
        {{ s.name }} <span class="muted">{{ s.symbol }}</span>
      </div>
    </div>
    <p class="err" v-if="error">{{ error }}</p>

    <div class="filter-bar">
      <button v-for="f in filterTabs" :key="f.key" class="tab"
              :class="{ on: filter === f.key }" @click="filter = f.key">{{ f.label }}</button>
    </div>

    <table class="watch-table">
      <thead>
        <tr><th>代码</th><th>名称</th><th>现价</th><th>涨跌%</th><th>分组</th><th>操作</th></tr>
      </thead>
      <tbody>
        <tr v-for="w in visible" :key="w.id">
          <td>{{ w.symbol }}</td>
          <td>{{ w.name }}</td>
          <td :class="cls(w.symbol)">{{ priceOf(w.symbol) }}</td>
          <td :class="cls(w.symbol)">{{ pctOf(w.symbol) }}</td>
          <td>{{ groupName(w.group_id) }}</td>
          <td>
            <button class="btn-link" @click="togglePin(w)">{{ w.pinned ? '取消置顶' : '置顶' }}</button>
            <button class="btn-link" @click="toggleGroup(w)">{{ w.group_id ? '移出组' : '入组' }}</button>
            <button class="btn-del" @click="remove(w)">删除</button>
          </td>
        </tr>
        <tr v-if="!visible.length"><td colspan="6" class="empty">暂无自选股</td></tr>
      </tbody>
    </table>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  addGroup, addWatch, delWatch, getQuotes, listWatchlist,
  patchWatch, searchStock, delGroup,
} from '../../api/stock'

const rows = ref([])
const groups = ref([])
const quotes = ref({})
const keyword = ref('')
const suggestions = ref([])
const sugOpen = ref(false)
const searching = ref(false)
const error = ref('')
const newGroupId = ref(null)
const filter = ref('all')

const filterTabs = computed(() => [
  { key: 'all', label: '全部' },
  ...groups.value.map((g) => ({ key: `g${g.group_id}`, label: g.name })),
  { key: 'none', label: '未分组' },
])

const visible = computed(() => {
  if (filter.value === 'all') return rows.value
  if (filter.value === 'none') return rows.value.filter((w) => !w.group_id)
  const gid = Number(filter.value.slice(1))
  return rows.value.filter((w) => w.group_id === gid)
})

const groupName = (gid) => groups.value.find((g) => g.group_id === gid)?.name || '未分组'
const priceOf = (s) => quotes.value[s]?.price?.toFixed(2) ?? '—'
const pctOf = (s) => {
  const q = quotes.value[s]
  return q ? `${q.pct > 0 ? '+' : ''}${q.pct.toFixed(2)}%` : '—'
}
const cls = (s) => {
  const p = quotes.value[s]?.pct
  return p > 0 ? 'up' : p < 0 ? 'down' : ''
}

async function load() {
  const [wl, qs] = await Promise.all([listWatchlist(), getQuotes()])
  rows.value = wl.items
  groups.value = wl.groups
  quotes.value = Object.fromEntries((qs.items || []).map((q) => [q.symbol, q]))
}

async function searchAdd() {
  const kw = keyword.value.trim()
  if (!kw) return
  error.value = ''
  searching.value = true
  try {
    // 纯 6 位代码直接加；否则搜索联想
    if (/^\d{6}$/.test(kw) || /^(sh|sz|bj)\d{6}$/.test(kw.toLowerCase())) {
      await addWatch({ symbol: kw.toLowerCase(), group_id: newGroupId.value })
      keyword.value = ''
      await load()
      return
    }
    const r = await searchStock(kw)
    suggestions.value = r.items || []
    sugOpen.value = suggestions.value.length > 0
    if (!suggestions.value.length) error.value = '没搜到相关标的（仅支持 A 股）'
  } catch (e) {
    error.value = e.response?.data?.detail || '添加失败'
  } finally {
    searching.value = false
  }
}

async function pickSug(s) {
  sugOpen.value = false
  keyword.value = ''
  try {
    await addWatch({ symbol: s.symbol, name: s.name, group_id: newGroupId.value })
    await load()
  } catch (e) {
    error.value = e.response?.data?.detail || '添加失败'
  }
}

async function createGroup() {
  const name = window.prompt('新分组名称')
  if (!name?.trim()) return
  try {
    await addGroup(name.trim())
    await load()
  } catch (e) {
    error.value = e.response?.data?.detail || '分组创建失败'
  }
}

async function togglePin(w) {
  await patchWatch(w.id, { pinned: !w.pinned })
  await load()
}

async function toggleGroup(w) {
  let gid = null
  if (!w.group_id) {
    if (!groups.value.length) return (error.value = '请先创建分组')
    const pick = window.prompt(`输入目标分组序号（1~${groups.value.length}）：\n${groups.value.map((g, i) => `${i + 1}. ${g.name}`).join('\n')}`)
    const g = groups.value[Number(pick) - 1]
    if (!g) return
    gid = g.group_id
  }
  try {
    await patchWatch(w.id, { group_id: gid })
    await load()
  } catch (e) {
    error.value = e.response?.data?.detail || '操作失败'
  }
}

async function remove(w) {
  if (!window.confirm(`删除自选 ${w.name || w.symbol}？`)) return
  await delWatch(w.id)
  await load()
}

onMounted(load)
</script>

<style scoped>
.stock-page { padding: 20px; }
.muted { color: #888; margin-bottom: 16px; font-size: 13px; }
.add-bar { display: flex; gap: 8px; margin-bottom: 10px; position: relative; }
.code-input { padding: 6px 12px; border: 1px solid #d0d0d0; border-radius: 4px; width: 300px; font-size: 14px; }
.group-select { padding: 6px; border: 1px solid #d0d0d0; border-radius: 4px; }
.btn-add { padding: 6px 16px; background: #1976d2; color: #fff; border: none; border-radius: 4px; cursor: pointer; }
.btn-add.alt { background: #607d8b; }
.btn-add:disabled { opacity: 0.6; cursor: not-allowed; }
.sug-box { position: absolute; z-index: 9; background: #fff; border: 1px solid #d0d0d0; border-radius: 4px; width: 300px; margin-top: 42px; }
.sug-row { padding: 6px 12px; cursor: pointer; font-size: 13px; }
.sug-row:hover { background: #f0f6ff; }
.err { color: #e53935; font-size: 13px; }
.filter-bar { display: flex; gap: 6px; margin: 10px 0; }
.tab { padding: 4px 12px; border: 1px solid #d0d0d0; background: #fff; border-radius: 14px; cursor: pointer; font-size: 13px; }
.tab.on { background: #1976d2; color: #fff; border-color: #1976d2; }
.watch-table { width: 100%; border-collapse: collapse; }
.watch-table th, .watch-table td { padding: 8px 12px; text-align: left; border-bottom: 1px solid #eee; }
.watch-table th { background: #f5f5f5; font-weight: 600; }
.up { color: #e53935; }
.down { color: #43a047; }
.btn-link { color: #1976d2; border: none; background: none; cursor: pointer; margin-right: 8px; font-size: 13px; }
.btn-del { color: #e53935; border: none; background: none; cursor: pointer; font-size: 13px; }
.empty { text-align: center; color: #999; padding: 24px; }
</style>
