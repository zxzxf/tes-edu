<template>
  <div class="stock-page">
    <h2>行情看板</h2>
    <p class="muted">K 线图 + 分时图（阶段 3：ECharts 接入）</p>
    <div class="chart-placeholder">
      <div class="placeholder-inner">
        📊 ECharts K 线图占位<br/>
        <small>阶段 3 接入真实行情数据后渲染</small>
      </div>
    </div>
  </div>
</template>

<script setup>
// 阶段 3：引入 ECharts 渲染 K 线图
</script>

<style scoped>
.stock-page { padding: 20px; }
.muted { color: #888; margin-bottom: 16px; }
.chart-placeholder {
  border: 2px dashed #d0d0d0; border-radius: 8px;
  min-height: 400px; display: flex; align-items: center; justify-content: center;
}
.placeholder-inner { text-align: center; color: #999; }
</style>
