<template>
  <div class="tes-page tes-anim">
    <h2 class="tes-page-title">模型配置（BYOM）</h2>
    <p class="tes-sub">
      列表化管理各租户的自有大模型。未配置的租户，其请求自动回落平台全局模型。
      每条配置含三段：<b>Chat</b>（解题/对话）、<b>Embedding</b>（向量检索）、
      <b>视觉模型</b>（拍照识题）。Embedding 与视觉模型均可缺省复用 Chat 段的凭证与端点，
      视觉模型名（vlm_model）填写后，该租户的学生端拍照识题即走本校配置。
    </p>

    <div class="tes-toolbar">
      <button class="tes-btn-primary" v-has-permission="'system:byom:manage'" @click="openCreate">
        新增配置
      </button>
      <button class="tes-btn" @click="load">刷新</button>
      <span class="tes-tag">{{ rows.length }} 条配置</span>
      <span v-if="loading" class="tes-tag">加载中…</span>
      <span v-if="!loading && !rows.length" class="tes-tag warn">全部回落全局模型</span>
    </div>

    <p v-if="error" class="tes-empty err">{{ error }}</p>
    <div v-else-if="banner" class="tes-empty probe" :class="banner.ok ? 'ok' : 'err'">
      <div class="probe-head">
        <b>{{ banner.title }}</b>
        <span>总判定：{{ banner.ok ? '全部可达 ✅' : '存在不可达 ❌' }}</span>
      </div>
      <div class="probe-line">
        <span class="kv-k">Chat</span>
        <b>{{ verdict(banner.chat) }}</b>
        <span v-if="banner.chat?.status_code">HTTP {{ banner.chat.status_code }}</span>
        <span v-if="banner.chat?.latency_ms != null">· {{ banner.chat.latency_ms }}ms</span>
        · {{ banner.chat?.detail }}
      </div>
      <div class="probe-line" v-if="banner.embedding">
        <span class="kv-k">Embed</span>
        <b>{{ verdict(banner.embedding) }}</b>
        <span v-if="banner.embedding.status_code">HTTP {{ banner.embedding.status_code }}</span>
        <span v-if="banner.embedding.latency_ms != null">· {{ banner.embedding.latency_ms }}ms</span>
        · {{ banner.embedding.detail }}
      </div>
      <div class="probe-line muted" v-else>
        <span class="kv-k">Embed</span>
        未单独配置 embedding 模型（复用 Chat 段），不单独探测。
      </div>
      <div class="probe-line" v-if="banner.vlm">
        <span class="kv-k">VLM</span>
        <b>{{ verdict(banner.vlm) }}</b>
        <span v-if="banner.vlm.status_code">HTTP {{ banner.vlm.status_code }}</span>
        <span v-if="banner.vlm.latency_ms != null">· {{ banner.vlm.latency_ms }}ms</span>
        · {{ banner.vlm.detail }}
      </div>
      <div class="probe-line muted" v-else-if="banner.ok !== null">
        <span class="kv-k">VLM</span>
        未配置视觉模型（复用 Chat 段无法探测），拍照识题回落全局或不可用。
      </div>
    </div>

    <div v-if="rows.length" class="tes-table-card">
      <div class="table-scroll">
        <table class="tes-table byom-table">
          <thead>
            <tr>
              <th v-if="isAdmin">租户</th>
              <th>供应商</th>
              <th>API Base URL</th>
              <th>模型</th>
              <th>API Key</th>
              <th>状态</th>
              <th>更新</th>
              <th class="op">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="row.tenant_id">
              <td v-if="isAdmin"><code class="mid">{{ row.tenant_id }}</code></td>
              <td>
                <b :title="row.provider">{{ row.provider_display || row.provider }}</b>
              </td>
              <td><code class="url" :title="row.base_url">{{ row.base_url }}</code></td>

              <!-- 模型：主模型 + 分级/Embedding 折进副行小字（省列宽，避免操作列被挤掉） -->
              <td>
                <code>{{ row.model }}</code>
                <div v-if="row.model_simple || row.model_hard" class="sub">
                  分级 简单 {{ row.model_simple || '同' }} · 困难 {{ row.model_hard || '同' }}
                </div>
                <div v-if="row.embed_model" class="sub muted">
                  Embed {{ row.embed_model }}·{{ row.embed_dim }}维
                </div>
                <div v-else class="sub muted">Embed 复用 Chat 段</div>
                <div v-if="row.vlm_model" class="sub vlm">
                  VLM 拍照识题 {{ row.vlm_model }}
                </div>
                <div v-else class="sub muted">VLM 未配置（拍照识题回落全局）</div>
              </td>

              <!-- API Key：默认掩码，点「反显」取明文（后端鉴权 + 审计）
                   两把密钥分属两段：Chat（解题/对话）与 Embedding（向量检索），
                   可指向不同厂商，故分别打标签，避免误以为重复配置。 -->
              <td class="key-cell">
                <template v-if="revealed[row.tenant_id]">
                  <div class="kv">
                    <span class="kv-k">Chat</span>
                    <code class="key plain">{{ revealed[row.tenant_id].api_key || '(空)' }}</code>
                  </div>
                  <div class="key-ops">
                    <button class="tes-btn xs" @click="copy(revealed[row.tenant_id].api_key)">复制</button>
                    <button class="tes-btn xs" @click="hideKey(row)">隐藏</button>
                  </div>
                  <div v-if="revealed[row.tenant_id].embed_api_key" class="kv">
                    <span class="kv-k">Embed</span>
                    <code class="key plain">{{ revealed[row.tenant_id].embed_api_key }}</code>
                    <button class="tes-btn xs" @click="copy(revealed[row.tenant_id].embed_api_key)">复制</button>
                  </div>
                </template>
                <template v-else>
                  <div class="kv">
                    <span class="kv-k">Chat</span>
                    <code class="key">{{ row.api_key_masked || '—' }}</code>
                  </div>
                  <div class="key-ops">
                    <button
                      class="tes-btn xs"
                      v-has-permission="'system:byom:manage'"
                      :disabled="revealingId === row.tenant_id"
                      @click="reveal(row)"
                    >{{ revealingId === row.tenant_id ? '读取中…' : '反显' }}</button>
                  </div>
                  <div v-if="row.has_embed_api_key" class="kv">
                    <span class="kv-k">Embed</span>
                    <code class="key">{{ row.embed_api_key_masked }}</code>
                  </div>
                  <div v-else class="sub muted">Embed 复用 Chat 凭证</div>
                </template>
              </td>

              <!-- 状态：点击即切换启停（省一个操作按钮位） -->
              <td>
                <button
                  class="pill-btn"
                  :class="row.enabled ? 'on' : 'off'"
                  v-has-permission="'system:byom:manage'"
                  :disabled="togglingId === row.tenant_id"
                  :title="row.enabled ? '点击停用' : '点击启用'"
                  @click="toggle(row)"
                >{{ togglingId === row.tenant_id ? '切换中…' : (row.enabled ? '启用' : '停用') }}</button>
              </td>
              <td class="mid" :title="row.updated_at">{{ fmtTime(row.updated_at) }}</td>
              <td class="op">
                <button
                  class="tes-btn sm"
                  v-has-permission="'system:byom:manage'"
                  :disabled="testingId === row.tenant_id"
                  @click="test(row)"
                >{{ testingId === row.tenant_id ? '测试中…' : '连通测试' }}</button>
                <button class="tes-btn sm" v-has-permission="'system:byom:manage'" @click="openEdit(row)">编辑</button>
                <button class="tes-btn sm danger" v-has-permission="'system:byom:manage'" @click="remove(row)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
    <p v-else-if="!loading" class="tes-empty">
      尚未配置自有大模型，点击「新增配置」接入首条；未配置时请求回落平台全局模型。
    </p>

    <!-- 新增 / 编辑对话框 -->
    <div v-if="dialog" class="mask" @click.self="dialog = null">
      <div class="tes-card dialog" role="dialog" aria-modal="true">
        <h3>{{ dialogTitle }}</h3>
        <div class="form-grid">
          <label v-if="isAdmin">租户 ID
            <!-- 编辑态锁定回显（黑字必可见，防误改租户）；新增态可手输并下拉已有租户 -->
            <input v-if="dialog.mode === 'edit'" :value="form.tenant_id" disabled title="编辑时租户不可修改" />
            <input v-else v-model.trim="form.tenant_id" list="tenant-options"
              placeholder="如 school_pilot_01（超管可代配）" />
            <span v-if="dialog.mode === 'edit'" class="hint">编辑时租户不可修改；如需改租户请新建配置。</span>
            <datalist id="tenant-options">
              <option v-for="r in rows" :key="r.tenant_id" :value="r.tenant_id" />
            </datalist>
          </label>

          <label>供应商（预设）
            <select v-model="selectedProvider" @change="onSelectPreset">
              <option v-for="p in presets" :key="p.provider" :value="p.provider">{{ p.display_name }}</option>
              <option value="custom">自定义（custom）</option>
            </select>
          </label>

          <label class="wide">API Base URL
            <input v-model.trim="form.base_url" placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1" />
            <span class="hint">仅 https；localhost / 私网 / 云元数据地址会被后端拒绝（SSRF 防护）。</span>
          </label>

          <label class="wide">API Key
            <input v-model="form.api_key" type="password" autocomplete="new-password"
              :placeholder="dialog.mode === 'edit'
                ? '留空保持不变（已存：' + (currentRow?.api_key_masked || '****') + '）'
                : '粘贴厂商 API Key（加密后落库，列表可反显）'" />
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
            <select v-model="form.enabled">
              <option :value="true">启用</option>
              <option :value="false">停用</option>
            </select>
          </label>
          <label>RAG top_k
            <input type="number" v-model.number="form.rag_top_k" min="1" max="50" />
          </label>
        </div>

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

        <details class="adv">
          <summary>高级 · 视觉模型段（拍照识题，可选）</summary>
          <p class="hint" style="margin-top:8px">
            拍照识题的视觉模型。密钥复用上方 Chat 段的 API Key，无需重复填写；
            VLM Base URL 留空即复用 Chat Base URL；视觉模型名（如 qwen-vl-max、gpt-4o）必填方可启用拍照识题。
          </p>
          <div class="form-grid">
            <label class="wide">视觉模型 Base URL
              <input v-model.trim="form.vlm_base_url" placeholder="留空=复用 Chat Base URL" />
            </label>
            <label class="wide">视觉模型名（VLM Model）
              <input v-model.trim="form.vlm_model" placeholder="如 qwen-vl-max / gpt-4o" />
            </label>
          </div>
        </details>

        <p v-if="formError" class="tes-empty err">{{ formError }}</p>
        <div class="dlg-actions">
          <button class="tes-btn" @click="dialog = null">取消</button>
          <button
            class="tes-btn"
            :disabled="testingDraft || !form.api_key"
            @click="testDraft"
          >{{ testingDraft ? '测试中…' : '测试当前填写' }}</button>
          <button class="tes-btn-primary" :disabled="saving" v-has-permission="'system:byom:manage'" @click="save">
            {{ saving ? '保存中…' : '保存配置' }}
          </button>
        </div>
        <p class="hint" v-if="dialog.mode === 'edit' && !form.api_key">
          未填写 API Key 时保存将保留已存密钥；「测试当前填写」需先粘贴 Key，
          或关闭后用列表行内的「连通测试」（直接测已存密钥）。
        </p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  deleteConfig, listConfigs, listPresets, revealKey, saveConfig,
  testConfig, testStoredConfig,
} from '../api/byom'
import { errMsg } from '../utils/errmsg'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const isAdmin = computed(() => auth.user?.role === 'admin' || auth.user?.role === 'region_admin')

const rows = ref([])
const presets = ref([])
const selectedProvider = ref('custom')
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const formError = ref('')
const banner = ref(null)

// 行内异步态：连通测试 / 反显 / 启停 / 弹窗内试填
const testingId = ref('')
const revealingId = ref('')
const togglingId = ref('')
const testingDraft = ref(false)
const revealed = ref({}) // { tenant_id: { api_key, embed_api_key } }

const dialog = ref(null) // { mode: 'create' | 'edit' }
const currentRow = ref(null)
const form = ref(blankForm())

function blankForm() {
  return {
    tenant_id: '',
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
    vlm_base_url: '',
    vlm_model: '',
  }
}

const presetModels = computed(() => {
  const p = presets.value.find((x) => x.provider === selectedProvider.value)
  return p ? p.models || [] : []
})

// 弹窗标题：编辑态带租户与供应商，打开即知在改谁；新增态明示"新增"。
const dialogTitle = computed(() => {
  if (!dialog.value) return ''
  if (dialog.value.mode === 'create') return '新增模型配置'
  const p = presets.value.find((x) => x.provider === form.value.provider)
  return `编辑配置：${form.value.tenant_id}${p ? `（${p.display_name}）` : ''}`
})

async function load() {
  loading.value = true
  error.value = ''
  revealed.value = {}
  try {
    const [list, presetRows] = await Promise.all([listConfigs(), listPresets()])
    rows.value = Array.isArray(list) ? list : []
    presets.value = Array.isArray(presetRows) ? presetRows : []
  } catch (e) {
    error.value = errMsg(e, '模型配置列表加载失败')
  } finally {
    loading.value = false
  }
}

// ---------------- 反显 / 复制 ----------------
async function reveal(row) {
  revealingId.value = row.tenant_id
  try {
    const res = await revealKey(row.tenant_id)
    revealed.value = { ...revealed.value, [row.tenant_id]: res }
  } catch (e) {
    error.value = errMsg(e, '密钥反显失败')
  } finally {
    revealingId.value = ''
  }
}

function hideKey(row) {
  const next = { ...revealed.value }
  delete next[row.tenant_id]
  revealed.value = next
}

async function copy(text) {
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
  } catch (e) {
    // 非 https / 无权限时降级：选中提示即可，不打扰
    error.value = '浏览器拒绝了剪贴板写入，请手动选中复制'
  }
}

// ---------------- 连通测试 ----------------
async function test(row) {
  testingId.value = row.tenant_id
  banner.value = null
  try {
    const res = await testStoredConfig(row.tenant_id)
    banner.value = { ...res, title: `连通性测试「${row.tenant_id}」` }
  } catch (e) {
    banner.value = {
      ok: false, title: `连通性测试「${row.tenant_id}」`,
      chat: { ok: false, status_code: null, latency_ms: null, detail: errMsg(e, '测试失败') },
      embedding: null,
    }
  } finally {
    testingId.value = ''
  }
}

async function testDraft() {
  formError.value = ''
  if (!form.value.base_url || !form.value.model) {
    formError.value = '请先填写 API Base URL 与模型'
    return
  }
  if (!form.value.api_key) {
    formError.value = '测试当前填写需先粘贴 API Key'
    return
  }
  testingDraft.value = true
  try {
    const res = await testConfig({
      base_url: form.value.base_url,
      api_key: form.value.api_key,
      model: form.value.model,
      // 填了 Embedding 模型就一并探测该段；密钥留空 = 复用 Chat Key
      embed_base_url: form.value.embed_base_url || undefined,
      embed_api_key: form.value.embed_api_key || undefined,
      embed_model: form.value.embed_model || undefined,
      // 填了视觉模型就一并探测 VLM 段；vlm_base_url 留空 = 复用 Chat Base URL
      vlm_base_url: form.value.vlm_base_url || undefined,
      vlm_model: form.value.vlm_model || undefined,
    })
    banner.value = { ...res, title: '连通性测试（未保存的填写值）' }
  } catch (e) {
    banner.value = {
      ok: false, title: '连通性测试（未保存的填写值）',
      chat: { ok: false, status_code: null, latency_ms: null, detail: errMsg(e, '测试失败') },
      embedding: null,
    }
  } finally {
    testingDraft.value = false
  }
}

// ---------------- 新增 / 编辑 ----------------
function openCreate() {
  form.value = blankForm()
  if (!isAdmin.value) form.value.tenant_id = ''
  selectedProvider.value = 'custom'
  currentRow.value = null
  formError.value = ''
  dialog.value = { mode: 'create' }
}

function openEdit(row) {
  currentRow.value = row
  form.value = {
    tenant_id: row.tenant_id,
    provider: row.provider || 'custom',
    base_url: row.base_url || '',
    api_key: '', // 明文永不回填；留空=不覆盖
    model: row.model || '',
    model_simple: row.model_simple || '',
    model_hard: row.model_hard || '',
    enabled: row.enabled !== false,
    rag_top_k: row.rag_top_k || 4,
    embed_base_url: row.embed_base_url || '',
    embed_api_key: '', // 同上
    embed_model: row.embed_model || '',
    embed_dim: row.embed_dim || 1024,
    vlm_base_url: row.vlm_base_url || '',
    vlm_model: row.vlm_model || '',
  }
  selectedProvider.value = row.provider || 'custom'
  formError.value = ''
  dialog.value = { mode: 'edit' }
}

function onSelectPreset() {
  const p = presets.value.find((x) => x.provider === selectedProvider.value)
  form.value.provider = selectedProvider.value
  if (p) form.value.base_url = p.base_url || ''
}

async function save() {
  formError.value = ''
  if (!form.value.base_url || !form.value.model) {
    formError.value = 'API Base URL 与模型为必填'
    return
  }
  if (dialog.value.mode === 'create' && !form.value.api_key) {
    formError.value = '新增配置必须填写 API Key'
    return
  }
  saving.value = true
  try {
    await saveConfig({
      tenant_id: form.value.tenant_id || undefined,
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
      vlm_base_url: form.value.vlm_base_url || undefined,
      vlm_model: form.value.vlm_model || undefined,
    })
    dialog.value = null
    await load()
  } catch (e) {
    formError.value = errMsg(e, '保存失败')
  } finally {
    saving.value = false
  }
}

// ---------------- 启停 / 删除 ----------------
async function toggle(row) {
  togglingId.value = row.tenant_id
  try {
    await saveConfig({
      tenant_id: row.tenant_id,
      provider: row.provider,
      base_url: row.base_url,
      model: row.model,
      model_simple: row.model_simple || undefined,
      model_hard: row.model_hard || undefined,
      enabled: !row.enabled,
      rag_top_k: row.rag_top_k || 4,
      // embed 段原样回填：不传会被后端按「清空」处理
      embed_base_url: row.embed_base_url || undefined,
      embed_model: row.embed_model || undefined,
      embed_dim: row.embed_dim || 1024,
      // vlm 段原样回填
      vlm_base_url: row.vlm_base_url || undefined,
      vlm_model: row.vlm_model || undefined,
    })
    hideKey(row)
    await load()
  } catch (e) {
    error.value = errMsg(e, '状态切换失败')
  } finally {
    togglingId.value = ''
  }
}

async function remove(row) {
  if (!window.confirm(`确定删除「${row.tenant_id}」的模型配置？删除后该租户请求回落平台全局模型。`)) return
  try {
    await deleteConfig(row.tenant_id)
    hideKey(row)
    await load()
  } catch (e) {
    error.value = errMsg(e, '删除失败')
  }
}

// 429 是「被限流」而非不可达：地址与密钥有效，别误判成配置错误
function verdict(seg) {
  if (!seg) return '—'
  if (seg.ok) return '可达 ✅'
  if (seg.status_code === 429) return '可达（被限流）⚠️'
  return '不可达 ❌'
}

function fmtTime(iso) {
  if (!iso) return '—'
  // 只留 MM-DD HH:mm，避免长时间戳把表格撑宽挤掉操作列
  return String(iso).slice(5, 16).replace('T', ' ')
}

onMounted(load)
</script>

<style scoped>
.mid { font-size: 11px; color: var(--muted); }
.muted { color: var(--muted); }
.sub { margin-top: 3px; font-size: 11px; color: var(--muted); line-height: 1.4; }
.sub.vlm { color: #c2611a; font-weight: 600; }
td.op, th.op { white-space: nowrap; text-align: right; }

/* ⚠️ .tes-table-card 是 overflow:hidden（全局样式），列一多就会把最右的操作列
   整列裁掉——用户看到的就是「编辑/删除按钮不见了」。这里两层兜底：
   1) 内层 .table-scroll 提供横向滚动；2) 表格设 min-width 让浏览器出滚动条
   而不是压缩/裁剪。配合上面把分级模型、Embedding 折进副行精简列宽。 */
.table-scroll { overflow-x: auto; }
.byom-table { min-width: 860px; }
code.url {
  display: inline-block; max-width: 220px; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; vertical-align: bottom;
}

/* 状态 pill：可点击切换启停 */
.pill-btn {
  display: inline-flex; align-items: center; padding: 2px 9px;
  border-radius: 999px; font-size: 11px; font-weight: 700; cursor: pointer;
  border: 1px solid transparent; background: #eef9f3; color: var(--success);
}
.pill-btn.off { background: #ffe9ee; color: var(--danger); }
.pill-btn:hover:not(:disabled) { filter: brightness(.95); border-color: currentColor; }
.pill-btn:disabled { opacity: .55; cursor: not-allowed; }

/* API Key 单元格：默认掩码，反显后可复制 */
.key-cell { min-width: 200px; }
/* 两段密钥分标签：Chat / Embed，避免误读为重复配置 */
.kv { display: flex; align-items: center; gap: 6px; margin-top: 2px; flex-wrap: wrap; }
.kv-k {
  flex: none; padding: 1px 6px; border-radius: 5px; font-size: 10px; font-weight: 700;
  background: #eef4ff; color: var(--accent);
}
.kv + .kv { margin-top: 6px; }
.key { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
.key.plain {
  display: inline-block; max-width: 230px; word-break: break-all;
  background: #fff6f0; padding: 2px 6px; border-radius: 6px; color: #a34b00;
}
.key-ops { display: flex; align-items: center; gap: 6px; margin-top: 6px; flex-wrap: wrap; }
.tes-btn.xs { padding: 2px 8px; font-size: 11px; border-radius: var(--r-sm, 8px); }
.tes-btn.xs:disabled { opacity: .5; cursor: not-allowed; }

.tes-btn.sm { padding: 5px 12px; font-size: 12px; border-radius: var(--r-sm, 8px); }
.tes-btn.sm.danger { color: var(--danger); border-color: #ffd6de; }
.tes-btn.sm.danger:hover { background: #fff0f3; color: var(--danger); }
.tes-btn.sm:disabled { opacity: .5; cursor: not-allowed; }
.tes-empty.err { background: #ffe9ee; border-color: #ffd6de; color: var(--danger); text-align: left; }
.tes-empty.ok { background: #e3f9f1; border-color: #b8ecda; color: #00795c; text-align: left; }
/* 连通性结果：Chat / Embedding 两段分列展示 */
.probe { display: flex; flex-direction: column; gap: 6px; }
.probe-head { display: flex; gap: 12px; align-items: baseline; flex-wrap: wrap; }
.probe-line { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; font-size: 12px; }

.hint { font-weight: 400; font-size: 12px; color: var(--muted); line-height: 1.5; }
.chip {
  margin: 2px 4px 2px 0; padding: 2px 8px; border: 1px solid var(--line-strong);
  border-radius: 999px; background: #f6f6fb; font-size: 12px; cursor: pointer; color: var(--ink-2);
}
.chip:hover { border-color: var(--brand-2, #ff7a7a); color: var(--brand-2, #ff7a7a); }

/* ---------- 对话框 ---------- */
.mask {
  position: fixed; inset: 0; z-index: 50;
  background: rgba(31, 27, 46, .45);
  display: grid; place-items: center; padding: 24px;
}
.dialog { width: 760px; max-width: 100%; max-height: 88vh; overflow: auto; }
.dialog h3 { margin-bottom: 16px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 16px; }
.form-grid label { display: flex; flex-direction: column; gap: 6px; font-size: 13px; font-weight: 600; color: var(--ink-2); }
.form-grid .wide { grid-column: 1 / -1; }
.form-grid input, .form-grid select {
  border: 1.5px solid var(--line-strong); border-radius: 8px; padding: 8px 10px; font: inherit;
}
.form-grid input:focus, .form-grid select:focus { outline: none; border-color: var(--brand-2, #ff7a7a); }
/* 禁用态（编辑时锁定的租户 ID）也要看清内容：黑字，不做灰化 */
.form-grid input:disabled { color: var(--ink, #1f1b2e); background: #fafafc; cursor: not-allowed; }
.adv { margin-top: 16px; border-top: 1px dashed var(--line-strong); padding-top: 12px; }
.adv summary { cursor: pointer; font-size: 13px; font-weight: 600; color: var(--ink-2); }
.dlg-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; }
</style>
