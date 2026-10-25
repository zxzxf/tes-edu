<template>
  <div class="tes-page tes-anim">
    <h2 class="tes-page-title">系统服务（MCP 厂商）</h2>

    <div class="tes-toolbar">
      <!-- T-AM6：写操作统一走 system:vendor:manage 按钮码（后端 require_perm 同步拦截） -->
      <button class="tes-btn-primary" v-has-permission="'system:vendor:manage'" @click="openCreate">注册厂商</button>
      <button class="tes-btn" @click="load">刷新</button>
      <span class="tes-tag">{{ rows.length }} 个厂商</span>
      <span v-if="loading" class="tes-tag">加载中…</span>
    </div>

    <p v-if="error" class="tes-empty err">{{ error }}</p>
    <p v-else-if="testResult" class="tes-empty" :class="testResult.ok ? 'ok' : 'err'">
      连通性测试「{{ testResult.vendor_name }}」：
      {{ testResult.ok ? '可达' : '不可达' }}
      <span v-if="testResult.status_code">HTTP {{ testResult.status_code }}</span>
      <span v-if="testResult.latency_ms != null">{{ testResult.latency_ms }}ms</span>
      —— {{ testResult.detail }}
    </p>

    <div v-if="rows.length" class="tes-table-card">
      <table class="tes-table">
        <thead>
          <tr>
            <th>厂商</th><th>类型</th><th>服务地址</th><th>认证</th>
            <th>凭证</th><th>实例</th><th>状态</th><th class="op">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.vendor_id">
            <td>
              <b>{{ row.vendor_name }}</b>
              <code class="mid">{{ row.vendor_id }}</code>
            </td>
            <td>{{ row.vendor_type }}</td>
            <td><code v-if="row.base_url">{{ row.base_url }}</code><span v-else class="muted">—</span></td>
            <td>{{ row.auth_type || '—' }}</td>
            <!-- 凭证只回显布尔：已存=密文在库，明文任何路径都拿不到 -->
            <td>
              <span v-if="row.has_credential" class="tes-pill">已加密保存</span>
              <span v-else class="tes-pill warn">未保存</span>
            </td>
            <td>
              <button class="tes-btn sm" @click="openInstances(row)">
                实例（{{ row.instance_count }}）
              </button>
            </td>
            <td>
              <span class="tes-pill" :class="row.status === 1 ? '' : 'danger'">
                {{ row.status === 1 ? '启用' : '停用' }}
              </span>
            </td>
            <td class="op">
              <button class="tes-btn sm" v-has-permission="'system:vendor:manage'" :disabled="testingId === row.vendor_id" @click="test(row)">
                {{ testingId === row.vendor_id ? '测试中…' : '连通测试' }}
              </button>
              <button class="tes-btn sm" v-has-permission="'system:vendor:manage'" @click="openEdit(row)">编辑</button>
              <button class="tes-btn sm danger" v-has-permission="'system:vendor:manage'" @click="remove(row)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-else-if="!loading" class="tes-empty">暂无厂商，点击「注册厂商」接入首个 MCP 服务。</p>

    <!-- 新建 / 编辑厂商对话框 -->
    <div v-if="dialog" class="mask" @click.self="dialog = null">
      <div class="tes-card dialog" role="dialog" aria-modal="true">
        <h3>{{ dialog.mode === 'create' ? '注册厂商' : `编辑厂商：${form.vendor_name}` }}</h3>
        <div class="form-grid">
          <label>厂商 ID
            <input v-model="form.vendor_id" :disabled="dialog.mode === 'edit'" placeholder="如 v_aliyun" />
          </label>
          <label>类型
            <select v-model="form.vendor_type">
              <option value="aliyun">阿里云（aliyun）</option>
              <option value="tencent">腾讯云（tencent）</option>
              <option value="ark">火山方舟（ark）</option>
              <option value="deepseek">DeepSeek</option>
              <option value="custom">自定义（custom）</option>
            </select>
          </label>
          <label>厂商名称
            <input v-model="form.vendor_name" placeholder="展示名" />
          </label>
          <label>认证方式
            <select v-model="form.auth_type">
              <option value="apikey">API Key（apikey）</option>
              <option value="oauth">OAuth（oauth）</option>
              <option value="aksk">AK/SK（aksk，暂不做签名）</option>
            </select>
          </label>
          <label class="wide">服务地址
            <input v-model="form.base_url" placeholder="https://dashscope.aliyuncs.com（连通测试目标）" />
          </label>
          <label class="wide">凭证
            <input v-model="form.credential" type="password" autocomplete="new-password"
              :placeholder="dialog.mode === 'edit' ? '留空保持不变（不可回显，仅可覆盖）' : 'API Key / Token（加密后落库，不回显）'" />
          </label>
          <label>状态
            <select v-model.number="form.status">
              <option :value="1">启用</option>
              <option :value="0">停用</option>
            </select>
          </label>
        </div>
        <p class="hint">凭证经 AES-256-GCM 加密后落库，任何接口都不回显；编辑时留空即保持已存凭证。</p>
        <p v-if="formError" class="tes-empty err">{{ formError }}</p>
        <div class="dlg-actions">
          <button class="tes-btn" @click="dialog = null">取消</button>
          <button class="tes-btn-primary" :disabled="saving" @click="save">
            {{ saving ? '保存中…' : '保存' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 实例管理对话框（每厂商独立数据服务） -->
    <div v-if="instVendor" class="mask" @click.self="instVendor = null">
      <div class="tes-card dialog wide-dlg" role="dialog" aria-modal="true">
        <h3>数据服务实例：{{ instVendor.vendor_name }}</h3>
        <div class="inst-toolbar">
          <button class="tes-btn-primary sm" v-has-permission="'system:vendor:manage'" @click="openInstCreate">新建实例</button>
          <span class="tes-tag">{{ instances.length }} 个实例</span>
          <span v-if="instLoading" class="tes-tag">加载中…</span>
        </div>
        <p v-if="instError" class="tes-empty err">{{ instError }}</p>
        <table v-if="instances.length" class="tes-table">
          <thead>
            <tr><th>实例</th><th>服务名</th><th>数据命名空间</th><th>隔离</th><th>连接</th><th class="op">操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="i in instances" :key="i.instance_id">
              <td><code class="mid">{{ i.instance_id }}</code></td>
              <td>{{ i.service_name }}</td>
              <td><code>{{ i.data_namespace }}</code></td>
              <td><span class="tes-pill" :class="i.isolation_mode === 'physical' ? 'info' : ''">{{ i.isolation_mode === 'physical' ? '物理' : '逻辑' }}</span></td>
              <td>
                <span v-if="i.has_connection" class="tes-pill">已加密保存</span>
                <span v-else class="tes-pill warn">未保存</span>
              </td>
              <td class="op">
                <button class="tes-btn sm" v-has-permission="'system:vendor:manage'" @click="openInstEdit(i)">编辑</button>
                <button class="tes-btn sm danger" v-has-permission="'system:vendor:manage'" @click="removeInstance(i)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-else-if="!instLoading" class="tes-empty">该厂商暂无数据服务实例。</p>

        <!-- 新建 / 编辑实例子对话框 -->
        <div v-if="instDialog" class="sub-mask" @click.self="instDialog = null">
          <div class="tes-card sub-dialog" role="dialog" aria-modal="true">
            <h3>{{ instDialog.mode === 'create' ? '新建实例' : `编辑实例：${instForm.service_name}` }}</h3>
            <div class="form-grid">
              <label>实例 ID
                <input v-model="instForm.instance_id" :disabled="instDialog.mode === 'edit'" placeholder="如 inst_aliyun_main" />
              </label>
              <label>服务名
                <input v-model="instForm.service_name" placeholder="如 主数据服务" />
              </label>
              <label>数据命名空间
                <input v-model="instForm.data_namespace" placeholder="schema / keyspace / 存储前缀" />
              </label>
              <label>隔离模式
                <select v-model="instForm.isolation_mode">
                  <option value="logical">逻辑隔离（vendor_id 分区）</option>
                  <option value="physical">物理隔离（独立库/集群）</option>
                </select>
              </label>
              <label class="wide">连接串 / 凭证
                <input v-model="instForm.connection" type="password" autocomplete="new-password"
                  :placeholder="instDialog.mode === 'edit' ? '留空保持不变（不可回显）' : 'postgres://…（加密后落库，不回显）'" />
              </label>
              <label>状态
                <select v-model.number="instForm.status">
                  <option :value="1">启用</option>
                  <option :value="0">停用</option>
                </select>
              </label>
            </div>
            <p v-if="instFormError" class="tes-empty err">{{ instFormError }}</p>
            <div class="dlg-actions">
              <button class="tes-btn" @click="instDialog = null">取消</button>
              <button class="tes-btn-primary" :disabled="instSaving" @click="saveInstance">
                {{ instSaving ? '保存中…' : '保存' }}
              </button>
            </div>
          </div>
        </div>

        <div class="dlg-actions">
          <button class="tes-btn" @click="instVendor = null">关闭</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import {
  createInstance, createVendor, deleteInstance, deleteVendor, listInstances,
  listVendors, testVendor, updateInstance, updateVendor,
} from '../api/vendor'
import { errMsg } from '../utils/errmsg'

const rows = ref([])
const loading = ref(false)
const error = ref('')
const dialog = ref(null)  // { mode: 'create' | 'edit' }
const saving = ref(false)
const formError = ref('')
const form = ref(blankVendorForm())

// 连通性测试：进行中标记 + 结果横幅（ok/detail 由后端实调返回）
const testingId = ref('')
const testResult = ref(null)

// 实例管理对话框状态
const instVendor = ref(null)
const instances = ref([])
const instLoading = ref(false)
const instError = ref('')
const instDialog = ref(null)  // { mode: 'create' | 'edit' }
const instSaving = ref(false)
const instFormError = ref('')
const instForm = ref(blankInstanceForm())

function blankVendorForm() {
  return {
    vendor_id: '', vendor_name: '', vendor_type: 'aliyun',
    base_url: '', auth_type: 'apikey', credential: '', status: 1,
  }
}

function blankInstanceForm() {
  return {
    instance_id: '', service_name: '', data_namespace: '',
    isolation_mode: 'logical', connection: '', status: 1,
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    rows.value = await listVendors()
  } catch (e) {
    error.value = errMsg(e, '厂商加载失败')
  } finally {
    loading.value = false
  }
}

// ---------- 厂商 ----------
function openCreate() {
  form.value = blankVendorForm()
  formError.value = ''
  dialog.value = { mode: 'create' }
}

function openEdit(row) {
  form.value = {
    vendor_id: row.vendor_id,
    vendor_name: row.vendor_name,
    vendor_type: row.vendor_type,
    base_url: row.base_url || '',
    auth_type: row.auth_type || 'apikey',
    credential: '',  // 凭证不回显：编辑态永远从空开始，留空=不覆盖
    status: row.status ?? 1,
  }
  formError.value = ''
  dialog.value = { mode: 'edit' }
}

/** 表单 → VendorIn 载荷。credential 空串时不带该键——后端语义为「不传则不覆盖」。 */
function vendorPayload() {
  const p = {
    vendor_id: form.value.vendor_id.trim(),
    vendor_name: form.value.vendor_name.trim(),
    vendor_type: form.value.vendor_type,
    base_url: form.value.base_url.trim() || null,
    auth_type: form.value.auth_type || null,
    status: Number(form.value.status) || 0,
  }
  const cred = form.value.credential.trim()
  if (cred) p.credential = cred
  return p
}

function validateVendor(payload) {
  if (!payload.vendor_id) return '厂商 ID 不能为空'
  if (!payload.vendor_name) return '厂商名称不能为空'
  // 新建必须给凭证（没有凭证的厂商连不上服务，也过不了连通测试）
  if (dialog.value?.mode === 'create' && !payload.credential) {
    return '注册厂商必须填写凭证（加密后落库，不回显）'
  }
  return ''
}

async function save() {
  const payload = vendorPayload()
  formError.value = validateVendor(payload)
  if (formError.value) return
  saving.value = true
  try {
    if (dialog.value.mode === 'create') {
      await createVendor(payload)
    } else {
      await updateVendor(payload.vendor_id, payload)
    }
    dialog.value = null
    await load()
  } catch (e) {
    formError.value = errMsg(e, '保存失败')
  } finally {
    saving.value = false
  }
}

async function remove(row) {
  // 删除守卫前置提示：名下有实例时后端 400
  const hint = row.instance_count
    ? `该厂商名下还有 ${row.instance_count} 个数据服务实例，删除会被后端拒绝（400）。`
    : ''
  if (!window.confirm(`确定删除厂商「${row.vendor_name}」？${hint}`)) return
  try {
    await deleteVendor(row.vendor_id)
    await load()
  } catch (e) {
    error.value = errMsg(e, '删除失败')
  }
}

async function test(row) {
  testingId.value = row.vendor_id
  testResult.value = null
  try {
    const res = await testVendor(row.vendor_id)
    testResult.value = { ...res, vendor_name: row.vendor_name }
  } catch (e) {
    // 400（缺凭证/缺 base_url）等也走这里：detail 原样展示
    testResult.value = {
      ok: false, status_code: null, latency_ms: null,
      detail: errMsg(e, '测试失败'), vendor_name: row.vendor_name,
    }
  } finally {
    testingId.value = ''
  }
}

// ---------- 实例 ----------
async function openInstances(vendor) {
  instVendor.value = vendor
  instances.value = []
  instError.value = ''
  instLoading.value = true
  try {
    instances.value = await listInstances(vendor.vendor_id)
  } catch (e) {
    instError.value = errMsg(e, '实例加载失败')
  } finally {
    instLoading.value = false
  }
}

function openInstCreate() {
  instForm.value = blankInstanceForm()
  instFormError.value = ''
  instDialog.value = { mode: 'create' }
}

function openInstEdit(i) {
  instForm.value = {
    instance_id: i.instance_id,
    service_name: i.service_name,
    data_namespace: i.data_namespace,
    isolation_mode: i.isolation_mode || 'logical',
    connection: '',  // 不回显，留空=不覆盖
    status: i.status ?? 1,
  }
  instFormError.value = ''
  instDialog.value = { mode: 'edit' }
}

function instancePayload() {
  const p = {
    instance_id: instForm.value.instance_id.trim(),
    service_name: instForm.value.service_name.trim(),
    data_namespace: instForm.value.data_namespace.trim(),
    isolation_mode: instForm.value.isolation_mode,
    status: Number(instForm.value.status) || 0,
  }
  const conn = instForm.value.connection.trim()
  if (conn) p.connection = conn
  return p
}

function validateInstance(payload) {
  if (!payload.instance_id) return '实例 ID 不能为空'
  if (!payload.service_name) return '服务名不能为空'
  if (!payload.data_namespace) return '数据命名空间不能为空（逻辑隔离分区键）'
  return ''
}

async function saveInstance() {
  const payload = instancePayload()
  instFormError.value = validateInstance(payload)
  if (instFormError.value) return
  instSaving.value = true
  try {
    if (instDialog.value.mode === 'create') {
      await createInstance(instVendor.value.vendor_id, payload)
    } else {
      await updateInstance(instVendor.value.vendor_id, payload.instance_id, payload)
    }
    instDialog.value = null
    await openInstances(instVendor.value)
    await load()  // 厂商行上的实例计数同步刷新
  } catch (e) {
    instFormError.value = errMsg(e, '保存失败')
  } finally {
    instSaving.value = false
  }
}

async function removeInstance(i) {
  if (!window.confirm(`确定删除实例「${i.service_name}」？`)) return
  try {
    await deleteInstance(instVendor.value.vendor_id, i.instance_id)
    await openInstances(instVendor.value)
    await load()
  } catch (e) {
    instError.value = errMsg(e, '删除失败')
  }
}

onMounted(load)
</script>

<style scoped>
.mid { margin-left: 8px; font-size: 11px; color: var(--muted); }
.muted { color: var(--muted); }
td.op, th.op { white-space: nowrap; text-align: right; }
.tes-btn.sm { padding: 5px 12px; font-size: 12px; border-radius: var(--r-sm); }
.tes-btn.sm.danger { color: var(--danger); border-color: #ffd6de; }
.tes-btn.sm.danger:hover { background: #fff0f3; color: var(--danger); }
.tes-btn.sm:disabled { opacity: .5; cursor: not-allowed; }
.tes-empty.err { background: #ffe9ee; border-color: #ffd6de; color: var(--danger); text-align: left; }
.tes-empty.ok { background: #e3f9f1; border-color: #b8ecda; color: #00795c; text-align: left; }
.hint { margin: 10px 0 0; font-size: 12px; color: var(--muted); }

/* ---------- 对话框 ---------- */
.mask {
  position: fixed; inset: 0; z-index: 50;
  background: rgba(31, 27, 46, .45);
  display: grid; place-items: center; padding: 24px;
}
.dialog { width: 720px; max-width: 100%; max-height: 88vh; overflow: auto; }
.dialog h3 { margin-bottom: 16px; }
.wide-dlg { width: 860px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 16px; }
.form-grid label { display: flex; flex-direction: column; gap: 6px; font-size: 13px; font-weight: 600; color: var(--ink-2); }
.form-grid .wide { grid-column: 1 / -1; }
.dlg-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; }
.inst-toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }

/* 实例子对话框（叠在实例对话框之上） */
.sub-mask {
  position: fixed; inset: 0; z-index: 60;
  background: rgba(31, 27, 46, .55);
  display: grid; place-items: center; padding: 24px;
}
.sub-dialog { width: 640px; max-width: 100%; max-height: 84vh; overflow: auto; }
</style>
