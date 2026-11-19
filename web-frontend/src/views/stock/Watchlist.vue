<template>
  <div class="stock-page">
    <h2>自选股</h2>
    <p class="muted">管理你的自选股列表（阶段 3 实现）</p>
    <div class="add-bar">
      <input v-model="newCode" placeholder="输入股票代码（如 600519）" class="code-input" />
      <button class="btn-add" @click="addStock">添加</button>
    </div>
    <table class="watch-table">
      <thead>
        <tr><th>代码</th><th>名称</th><th>现价</th><th>涨跌%</th><th>操作</th></tr>
      </thead>
      <tbody>
        <tr v-for="s in watchlist" :key="s.code">
          <td>{{ s.code }}</td>
          <td>{{ s.name }}</td>
          <td>{{ s.price }}</td>
          <td :class="{ up: s.change > 0, down: s.change < 0 }">
            {{ s.change > 0 ? '+' : '' }}{{ s.change }}%
          </td>
          <td><button class="btn-del" @click="removeStock(s.code)">删除</button></td>
        </tr>
        <tr v-if="!watchlist.length"><td colspan="5" class="empty">暂无自选股</td></tr>
      </tbody>
    </table>
  </div>
</template>

<script setup>
import { ref } from 'vue'
const newCode = ref('')
const watchlist = ref([
  { code: 'SH600519', name: '贵州茅台', price: '1688.00', change: 2.35 },
  { code: 'SZ000858', name: '五粮液', price: '142.50', change: -1.20 },
])
function addStock() {
  if (!newCode.value.trim()) return
  // 阶段 3 调后端 API
  alert(`添加 ${newCode.value}（待后端实现）`)
  newCode.value = ''
}
function removeStock(code) {
  watchlist.value = watchlist.value.filter(s => s.code !== code)
}
</script>

<style scoped>
.stock-page { padding: 20px; }
.muted { color: #888; margin-bottom: 16px; }
.add-bar { display: flex; gap: 8px; margin-bottom: 16px; }
.code-input {
  padding: 6px 12px; border: 1px solid #d0d0d0; border-radius: 4px;
  width: 260px; font-size: 14px;
}
.btn-add {
  padding: 6px 16px; background: #1976d2; color: #fff; border: none;
  border-radius: 4px; cursor: pointer;
}
.watch-table { width: 100%; border-collapse: collapse; }
.watch-table th, .watch-table td {
  padding: 8px 12px; text-align: left; border-bottom: 1px solid #eee;
}
.watch-table th { background: #f5f5f5; font-weight: 600; }
.up { color: #e53935; }
.down { color: #43a047; }
.btn-del { color: #e53935; border: none; background: none; cursor: pointer; }
.empty { text-align: center; color: #999; padding: 24px; }
</style>
