<template>
  <div class="tes-page tes-anim">
    <h2 class="tes-page-title">模型配置（BYOM）</h2>
    <p class="tes-sub">为<span v-if="tenantId" class="mono">「{{ tenantId }}」</span>租户配置自有大模型。未配置时，本校请求回落平台全局模型。</p>

    <div class="tes-toolbar">
      <button class="tes-btn" @click="reload">刷新</button>
      <span class="tes-tag" v-if="currentConfigured">已配置（{{ current.provider }}）</span>
      <span class="tes-tag warn" v-else>未配置（回落全局）</span>
      <span v-if="loading" class="tes-tag">加载中…</span>
    </div>

    <p v-if="error" class="tes-empty err">{{ error }}</p>

    <!-- 连通性测试结果横幅 -->
    <p v-if="testResult" class="tes-empty" :class="testResult.ok ? 'ok' : 'err'">
      连通性测试：
      <b>{{ testResult.ok ? '可达 ✅' : '不可达 ❌' }}</b>
      <span v-if="testResult.status_code">HTTP {{ testResult.status_code }}</span>
      <span v-if="testResult.latency_ms != null">· {{ testResult.latency_ms }}ms</span>
      · {{ testResult.detail }}
    </p>

    <div class="tes-card byom-form">
      <div class="form-grid">
        <!-- 供应商下拉：预设自动填充 base_url + 候选模型 -->
        <label class="wide">供应商（预设）
          <select v-model="selectedProvider" @change="onSelectPreset">
            <option v-for="p in presets" :key="p.provider" :value="p.provider">
              {{ p.display_name }}
            </option>
          </select>
        </label>

        <label class="wide">API Base URL
          <input v-model.trim="form.base_url" placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1" />
          <span class="hint">仅 https；localhost / 私网 / 云元数据地址会被后端拒绝（SSRF 防护）。</span>
        </label>

        <label class="wide">API Key
          <input v-model="form.api_key" type="password" autocomplete="new-password"
            :placeholder="currentConfigured ? '留空保持不变（已存：' + (current.api_key_masked || '****') + '）' : '粘贴厂商 API Key（加密后落库，不回显）'" />
        </label>

        <label class="wide">模型（Chat）
          <input v-model.trim="form.model" placeholder="如 qwen-max" />
          <span class="hint" v-if="presetModels.length">
            预设候选（点击填入）：
            <button v-for="m in presetModels" :key="m" class="chip" @click="form.model = m">{{ m }}</button>
          </span>
          <span class="hint" v-else>自定义端点请填写厂商提供的模型名。</span>
        </label>

        <label>分级·简单题模型
          <input v-model.trim="form.model_simple" placeholder="留空=同 Chat 模型" />
        </label>
        <label>分级·难题模型
          <input v-model.trim="form.model_hard" placeholder="留空=同 Chat 模型" />
        </label>

        <label>启用
          <select v-model="form.enabled" :disabled="!currentConfigured">
            <option :value="true">启用</option>
            <option :value="false">停用</option>
          </select>
        </label>
        <label>RAG top_k
          <input type="number" v-model.number="form.rag_top_k" min="1" max="50" />
        </label>
      </div>

      <!-- 高级：Embedding 段（缺省复用 Chat 段） -->
      <details class="adv">
        <summary>高级 · Embedding 段（可选，缺省复用 Chat 段）</summary>
        <div class="form-grid">
          <label class="wide">Embedding Base URL
            <input v-model.trim="form.embed_base_url" placeholder="留空=复用 Chat Base URL" />
          </label>
          <label class="wide">Embedding API Key
            <input v-model="form.embed_api_key" type="password" autocomplete="new-password"
              placeholder="留空=复用 Chat Key（或保持不变）" />
          </label>
          <label>Embedding 模型
            <input v-model.trim="form.embed_model" placeholder="如 text-embedding-v3" />
          </label>
          <label>向量维度
            <input type="number" v-model.number="form.embed_dim" min="1" />
          </label>
        </div>
      </details>

      <p v-if="formError" class="tes-empty err">{{ formError }}</p>

      <div class="dlg-actions">
        <button class="tes-btn" :disabled="testing || !form.api_key" @click="runTest">
          {{ testing ? '测试中…' : '连通性测试' }}
        </button>
        <button class="tes-btn-primary" :disabled="saving" v-has-permission="'system:byom:manage'" @click="save">
          {{ saving ? '保存中…' : '保存配置' }}
        </button>
        <button class="tes-btn danger" :disabled="!currentConfigured || saving" v-has-permission="'system:byom:manage'" @click="remove">
          删除（回落全局）
        </button>
      </div>
      <p class="hint" v-if="!form.api_key && currentConfigured">
        未填写 API Key 时保存将保留已存密钥；连通性测试需先粘贴 Key。
      </p>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  deleteConfig, getConfig, listPresets, saveConfig, testConfig,
} from '../api/byom'
import { errMsg } from '../utils/errmsg'

const presets = ref([])
const selectedProvider = ref('custom')
const loading = ref(false)
const saving = ref(false)
const testing = ref(false)
const error = ref('')
const formError = ref('')
const testResult = ref(null)
const tenantId = ref('')
const current = ref({})
const currentConfigured = ref(false)

const form = ref(blankForm())

function blankForm() {
  return {
    provider: 'custom',
    base_url: '',
    api_key: '',
    model: '',
    model_simple: '',
    model_hard: '',
    enabled: true,
    rag_top_k: 4,
    embed_base_url: '',
    embed_api_key: '',
    embed_model: '',
    embed_dim: 1024,
  }
}

// 当前选中预设的候选模型（用于芯片填充）
const presetModels = computed(() => {
  const p = presets.value.find((x) => x.provider === selectedProvider.value)
  return p ? p.models || [] : []
})

async function loadPresets() {
  try {
    presets.value = await listPresets()
  } catch (e) {
    error.value = errMsg(e, '预设清单加载失败')
  }
}

async function loadConfig() {
  loading.value = true
  error.value = ''
  try {
    const c = await getConfig()
    tenantId.value = c.tenant_id || ''
    currentConfigured.value = !!c.configured
    current.value = c || {}
    if (c.configured) {
      // 回填可见字段；api_key 留空（脱敏，不回显）
      form.value.provider = c.provider || 'custom'
      form.value.base_url = c.base_url || ''
      form.value.model = c.model || ''
      form.value.model_simple = c.model_simple || ''
      form.value.model_hard = c.model_hard || ''
      form.value.enabled = c.enabled !== false
      form.value.rag_top_k = c.rag_top_k || 4
      form.value.embed_base_url = c.embed_base_url || ''
      form.value.embed_model = c.embed_model || ''
      form.value.embed_dim = c.embed_dim || 1024
      selectedProvider.value = c.provider || 'custom'
    } else {
      form.value = blankForm()
    }
  } catch (e) {
    error.value = errMsg(e, '配置加载失败')
  } finally {
    loading.value = false
  }
}

function onSelectPreset() {
  const p = presets.value.find((x) => x.provider === selectedProvider.value)
  form.value.provider = selectedProvider.value
  if (p) {
    form.value.base_url = p.base_url || ''
    // 自定义：base_url 由用户填；其它预设预填，仍允许修改
  }
}

async function runTest() {
  formError.value = ''
  if (!form.value.base_url || !form.value.model) {
    formError.value = '请先填写 API Base URL 与模型'
    return
  }
  if (!form.value.api_key) {
    formError.value = '连通性测试需先粘贴 API Key'
    return
  }
  testing.value = true
  testResult.value = null
  try {
    testResult.value = await testConfig({
      base_url: form.value.base_url,
      api_key: form.value.api_key,
      model: form.value.model,
      embed_base_url: form.value.embed_base_url || undefined,
    })
  } catch (e) {
    testResult.value = {
      ok: false, status_code: null, latency_ms: null, detail: errMsg(e, '测试失败'),
    }
  } finally {
    testing.value = false
  }
}

async function save() {
  formError.value = ''
  if (!form.value.base_url || !form.value.model) {
    formError.value = 'API Base URL 与模型为必填'
    return
  }
  if (!currentConfigured.value && !form.value.api_key) {
    formError.value = '首次配置必须填写 API Key'
    return
  }
  saving.value = true
  try {
    await saveConfig({
      provider: form.value.provider,
      base_url: form.value.base_url,
      api_key: form.value.api_key || undefined,
      model: form.value.model,
      model_simple: form.value.model_simple || undefined,
      model_hard: form.value.model_hard || undefined,
      enabled: form.value.enabled,
      rag_top_k: form.value.rag_top_k,
      embed_base_url: form.value.embed_base_url || undefined,
      embed_api_key: form.value.embed_api_key || undefined,
      embed_model: form.value.embed_model || undefined,
      embed_dim: form.value.embed_dim,
    })
    await loadConfig()
  } catch (e) {
    formError.value = errMsg(e, '保存失败')
  } finally {
    saving.value = false
  }
}

async function remove() {
  if (!window.confirm('确定删除本校模型配置？删除后本校请求回落平台全局模型。')) return
  try {
    await deleteConfig()
    await loadConfig()
  } catch (e) {
    error.value = errMsg(e, '删除失败')
  }
}

function reload() {
  loadPresets()
  loadConfig()
}

onMounted(reload)
</script>

<style scoped>
.tes-sub { margin: 0 0 14px; color: var(--ink-2); font-size: 13px; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px 16px; }
.form-grid label { display: flex; flex-direction: column; gap: 6px; font-size: 13px; font-weight: 600; color: var(--ink-2); }
.form-grid .wide { grid-column: 1 / -1; }
.form-grid input, .form-grid select {
  border: 1.5px solid var(--line-strong); border-radius: 8px; padding: 8px 10px; font: inherit;
}
.form-grid input:focus, .form-grid select:focus { outline: none; border-color: var(--brand-2); }
.hint { font-weight: 400; font-size: 12px; color: var(--muted); line-height: 1.5; }
.chip {
  margin: 2px 4px 2px 0; padding: 2px 8px; border: 1px solid var(--line-strong);
  border-radius: 999px; background: #f6f6fb; font-size: 12px; cursor: pointer; color: var(--ink-2);
}
.chip:hover { border-color: var(--brand-2); color: var(--brand-2); }
.adv { margin-top: 16px; border-top: 1px dashed var(--line-strong); padding-top: 12px; }
.adv summary { cursor: pointer; font-size: 13px; font-weight: 600; color: var(--ink-2); }
.dlg-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; }
.tes-btn.danger { color: var(--danger); border-color: #ffd6de; }
.tes-btn.danger:hover:not(:disabled) { background: #fff0f3; color: var(--danger); }
.tes-btn:disabled { opacity: .5; cursor: not-allowed; }
</style>
