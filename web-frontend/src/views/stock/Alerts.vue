<template>
  <div class="stock-page">
    <h2>涨跌预警</h2>
    <p class="muted">设置价格/涨跌幅预警规则，触发时推送通知（阶段 3 实现）</p>
    <table class="alert-table">
      <thead>
        <tr><th>股票</th><th>条件</th><th>阈值</th><th>推送渠道</th><th>状态</th><th>操作</th></tr>
      </thead>
      <tbody>
        <tr v-for="a in alerts" :key="a.id">
          <td>{{ a.stockName }} ({{ a.stockCode }})</td>
          <td>{{ a.condition }}</td>
          <td>{{ a.threshold }}</td>
          <td>{{ a.channel }}</td>
          <td><span :class="'badge ' + a.status">{{ a.statusText }}</span></td>
          <td>
            <button class="btn-toggle" @click="toggleAlert(a)">{{ a.status === 'active' ? '停用' : '启用' }}</button>
            <button class="btn-del" @click="deleteAlert(a.id)">删除</button>
          </td>
        </tr>
        <tr v-if="!alerts.length"><td colspan="6" class="empty">暂无预警规则</td></tr>
      </tbody>
    </table>
    <button class="btn-add" @click="showAdd = true">+ 新增预警</button>
  </div>
</template>

<script setup>
import { ref } from 'vue'
const alerts = ref([
  { id: 1, stockCode: 'SH600519', stockName: '贵州茅台', condition: '涨幅超过', threshold: '5%', channel: 'PushPlus', status: 'active', statusText: '监控中' },
  { id: 2, stockCode: 'SZ300750', stockName: '宁德时代', condition: '跌幅超过', threshold: '3%', channel: '邮件', status: 'paused', statusText: '已停用' },
])
const showAdd = ref(false)
function toggleAlert(a) {
  a.status = a.status === 'active' ? 'paused' : 'active'
  a.statusText = a.status === 'active' ? '监控中' : '已停用'
}
function deleteAlert(id) {
  alerts.value = alerts.value.filter(a => a.id !== id)
}
</script>

<style scoped>
.stock-page { padding: 20px; }
.muted { color: #888; margin-bottom: 16px; }
.alert-table { width: 100%; border-collapse: collapse; margin-bottom: 16px; }
.alert-table th, .alert-table td {
  padding: 8px 12px; text-align: left; border-bottom: 1px solid #eee;
}
.alert-table th { background: #f5f5f5; font-weight: 600; }
.badge { padding: 2px 8px; border-radius: 10px; font-size: 12px; }
.badge.active { background: #e8f5e9; color: #2e7d32; }
.badge.paused { background: #fff3e0; color: #e65100; }
.btn-add {
  padding: 8px 20px; background: #1976d2; color: #fff; border: none;
  border-radius: 4px; cursor: pointer;
}
.btn-toggle { color: #1976d2; border: none; background: none; cursor: pointer; margin-right: 8px; }
.btn-del { color: #e53935; border: none; background: none; cursor: pointer; }
.empty { text-align: center; color: #999; padding: 24px; }
</style>
