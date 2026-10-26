<template>
  <div class="tes-page tes-anim">
    <h2 class="tes-page-title">功能开关</h2>

    <div class="tes-toolbar">
      <label class="scope-label">生效范围
        <select v-model="scopeType">
          <option value="global">平台全局 (global)</option>
          <option value="region">区域 (region)</option>
          <option value="tenant">学校 (tenant)</option>
          <option value="class">班级 (class)</option>
        </select>
      </label>
      <input
        v-if="scopeType !== 'global'"
        v-model="scopeId"
        class="scope-input"
        :placeholder="scopeType + ' ID（如 rgn_east / demo）'"
      />
      <button class="tes-btn" @click="load">刷新</button>
      <span class="tes-tag">按所选范围切换学生端 App / 网页端功能显隐</span>
    </div>

    <p v-if="error" class="tes-empty err">{{ error }}</p>

    <div class="tes-table-card" v-if="switches.length">
      <table class="tes-table">
        <thead>
          <tr>
            <th>功能</th><th>平台</th><th>默认</th><th>当前生效</th><th class="op">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in switches" :key="s.feature_key">
            <td>
              <b>{{ s.feature_name }}</b>
              <code class="mid">{{ s.feature_key }}</code>
            </td>
            <td><span class="tes-pill">{{ s.platform }}</span></td>
            <td>{{ s.default_on ? '开' : '关' }}</td>
            <td>
              <span class="tes-pill" :class="effective(s) ? 'on' : 'off'">
                {{ effective(s) ? '开' : '关' }}
              </span>
            </td>
            <td class="op">
              <button
                class="tes-btn sm"
                v-has-permission="'system:feature:manage'"
                @click="toggle(s)"
              >{{ effective(s) ? '关闭' : '开启' }}</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-else-if="!error" class="tes-empty">暂无功能开关定义。</p>

    <h3 class="sub-title">防疲劳提醒配置（#3）</h3>
    <div class="tes-card af-form" v-if="af">
      <label>提醒间隔(分钟)
        <input type="number" v-model.number="af.interval_min" />
      </label>
      <label>建议休息(分钟)
        <input type="number" v-model.number="af.break_min" />
      </label>
      <label>模式
        <select v-model="af.mode">
          <option value="remind">仅提醒</option>
          <option value="enforce">强制休息</option>
        </select>
      </label>
      <label class="wide">提醒文案
        <input v-model="af.message" />
      </label>
      <button
        class="tes-btn-primary"
        v-has-permission="'system:feature:manage'"
        @click="saveAf"
      >保存配置</button>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import client from '../api/client'
import { errMsg } from '../utils/errmsg'

const switches = ref([])
const error = ref('')
const scopeType = ref('global')
const scopeId = ref('')
const af = ref(null)

// 当前所选范围下的生效值：override 命中则取之，否则回退默认
function effective(s) {
  const ov = (s.overrides || []).find(
    (o) => o.scope_type === scopeType.value && (o.scope_id || '') === (scopeId.value || '')
  )
  if (ov) return ov.enabled
  return s.default_on
}

async function load() {
  error.value = ''
  try {
    const f = await client.get('/features')
    switches.value = f || []
    const a = await client.get('/anti-fatigue', {
      params: { scope_type: scopeType.value, scope_id: scopeId.value || '' },
    })
    af.value = a || null
  } catch (e) {
    error.value = errMsg(e)
  }
}

async function toggle(s) {
  const next = !effective(s)
  try {
    await client.put('/features', {
      feature_key: s.feature_key,
      scope_type: scopeType.value,
      scope_id: scopeId.value || '',
      enabled: next,
    })
    await load()
  } catch (e) {
    error.value = errMsg(e)
  }
}

async function saveAf() {
  try {
    await client.put('/anti-fatigue', {
      scope_type: scopeType.value,
      scope_id: scopeId.value || '',
      enabled: true,
      interval_min: af.value.interval_min,
      break_min: af.value.break_min,
      mode: af.value.mode,
      message: af.value.message,
    })
    await load()
  } catch (e) {
    error.value = errMsg(e)
  }
}

onMounted(load)
</script>

<style scoped>
.scope-label { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; color: var(--ink-2); }
.scope-label select, .scope-input {
  border: 1.5px solid var(--line-strong); border-radius: 8px; padding: 6px 10px; font: inherit;
}
.scope-input { width: 220px; }
.sub-title { margin: 26px 0 12px; font-size: 17px; }
.af-form { max-width: 640px; display: grid; gap: 14px; }
.af-form label { display: flex; flex-direction: column; gap: 6px; font-size: 13px; font-weight: 600; color: var(--ink-2); }
.af-form label.wide { grid-column: 1 / -1; }
.af-form input, .af-form select {
  border: 1.5px solid var(--line-strong); border-radius: 8px; padding: 8px 10px; font: inherit;
}
.af-form input:focus, .af-form select:focus { outline: none; border-color: var(--brand-2); }
.tes-pill.on { color: #07a07a; background: #e6f7f1; }
.tes-pill.off { color: var(--muted); background: #f1f1f4; }
</style>
