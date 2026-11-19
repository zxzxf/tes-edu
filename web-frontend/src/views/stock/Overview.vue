<template>
  <div class="stock-page">
    <h2>盯盘总览</h2>
    <p class="muted">自选股实时行情概览（阶段 3 实现）</p>
    <div class="stock-grid">
      <div v-for="s in sampleStocks" :key="s.code" class="stock-card"
           :class="{ up: s.change > 0, down: s.change < 0 }">
        <div class="code">{{ s.code }}</div>
        <div class="name">{{ s.name }}</div>
        <div class="price">{{ s.price }}</div>
        <div class="change">{{ s.change > 0 ? '+' : '' }}{{ s.change }}%</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
// 阶段 3 接入真实行情数据前，先放占位数据
const sampleStocks = ref([
  { code: 'SH600519', name: '贵州茅台', price: '1688.00', change: 2.35 },
  { code: 'SZ000858', name: '五粮液', price: '142.50', change: -1.20 },
  { code: 'SH601318', name: '中国平安', price: '48.60', change: 0.85 },
  { code: 'SZ300750', name: '宁德时代', price: '198.00', change: -0.50 },
])
</script>

<style scoped>
.stock-page { padding: 20px; }
.muted { color: #888; margin-bottom: 16px; }
.stock-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 12px; }
.stock-card {
  border: 1px solid #e0e0e0; border-radius: 8px; padding: 12px;
  transition: box-shadow 0.2s;
}
.stock-card:hover { box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
.stock-card.up { border-left: 3px solid #e53935; }
.stock-card.down { border-left: 3px solid #43a047; }
.code { font-size: 12px; color: #999; }
.name { font-weight: 600; margin: 4px 0; }
.price { font-size: 18px; font-weight: 700; }
.up .price, .up .change { color: #e53935; }
.down .price, .down .change { color: #43a047; }
.change { font-size: 13px; margin-top: 4px; }
</style>
