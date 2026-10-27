<template>
  <div class="tes-page tes-anim">
    <h2 class="tes-page-title">系统服务（MCP 厂商）</h2>
    <p class="tes-sub">
      列表化管理平台接入的 MCP 服务厂商。每个厂商可挂多个<b>数据服务实例</b>，
      实例之间按 <code>data_namespace</code> 隔离，互不干扰。
      凭证经 AES-256-GCM 加密落库，任何接口都<b>不回显明文</b>，列表只提示「是否已存」。
    </p>

    <div class="tes-toolbar">
      <!-- T-AM6：写操作统一走 system:vendor:manage 按钮码（后端 require_perm 同步拦截） -->
      <button class="tes-btn-primary" v-has-permission="'system:vendor:manage'" @click="openCreate">注册厂商</button>
      <button class="tes-btn" @click="load">刷新</button>
      <span class="tes-tag">{{ rows.length }} 个厂商</span>
      <span v-if="loading" class="tes-tag">加载中…</span>
    </div>

    <p v-if="error" class="tes-empty err">{{ error }}</p>

    <!-- 连通测试结果横幅（ok / 诊断 / HTTP / 耗时 分行展示，口径同 BYOM 探测横幅） -->
    <div v-else-if="testResult" class="tes-empty probe" :class="testResult.ok ? 'ok' : 'err'">
      <div class="probe-head">
        <b>连通性测试「{{ testResult.vendor_name }}」</b>
        <span>判定：{{ testResult.ok ? '可达 ✅' : '不可达 ❌' }}</span>
      </div>
      <div class="probe-line">
        <span class="kv-k">HTTP</span>
        <b>{{ testResult.status_code ?? '—' }}</b>
        <span v-if="testResult.latency_ms != null">· {{ testResult.latency_ms }}ms</span>
        · {{ testResult.detail }}
      </div>
    </div>

    <div v-if="rows.length" class="tes-table-card">
      <div class="table-scroll">
        <table class="tes-table vendor-table">
          <thead>
            <tr>
              <th>厂商</th>
              <th>服务地址</th>
              <th>认证 / 凭证</th>
              <th>实例</th>
              <th>状态</th>
              <th class="op">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="row.vendor_id">
              <!-- 厂商：名称 + ID + 类型/归属折进副行（省列宽，避免操作列被挤掉） -->
              <td>
                <b>{{ row.vendor_name }}</b>
                <code class="mid">{{ row.vendor_id }}</code>
                <div class="sub">{{ typeLabel(row.vendor_type) }}</div>
                <div v-if="row.region_id || row.tenant_id" class="sub muted">
                  归属 {{ row.region_id || '—' }} / {{ row.tenant_id || '—' }}
                </div>
              </td>

              <td>
                <code v-if="row.base_url" class="url" :title="row.base_url">{{ row.base_url }}</code>
                <span v-else class="muted">未配置（无法连通测试）</span>
              </td>

              <!-- 认证方式 + 凭证状态合并一列：凭证只回显布尔，明文任何路径都拿不到 -->
              <td>
                <div>{{ authLabel(row.auth_type) }}</div>
                <div class="sub">
                  <span v-if="row.has_credential" class="tes-pill">已加密保存</span>
                  <span v-else class="tes-pill warn">未保存</span>
                </div>
              </td>

              <td>
                <button class="tes-btn sm" @click="openInstances(row)">
                  实例（{{ row.instance_count }}）
                </button>
              </td>

              <!-- 状态：点击即切换启停（省一个操作按钮位，口径同 BYOM 列表） -->
              <td>
                <button
                  class="pill-btn"
                  :class="row.status === 1 ? 'on' : 'off'"
                  v-has-permission="'system:vendor:manage'"
                  :disabled="togglingId === row.vendor_id"
                  :title="row.status === 1 ? '点击停用' : '点击启用'"
                  @click="toggle(row)"
                >{{ togglingId === row.vendor_id ? '切换中…' : (row.status === 1 ? '启用' : '停用') }}</button>
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
        <div v-if="instances.length" class="table-scroll">
          <table class="tes-table inst-table">
            <thead>
              <tr>
                <th>服务名 / 实例 ID</th><th>数据命名空间</th><th>隔离</th>
                <th>连接</th><th>状态</th><th class="op">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="i in instances" :key="i.instance_id">
                <td>
                  <b>{{ i.service_name }}</b>
                  <code class="mid block">{{ i.instance_id }}</code>
                </td>
                <td><code>{{ i.data_namespace }}</code></td>
                <td>
                  <span class="tes-pill" :class="i.isolation_mode === 'physical' ? 'info' : ''">
                    {{ i.isolation_mode === 'physical' ? '物理' : '逻辑' }}
                  </span>
                </td>
                <td>
                  <span v-if="i.has_connection" class="tes-pill">已加密保存</span>
                  <span v-else class="tes-pill warn">未保存</span>
                </td>
                <td>
                  <button
                    class="pill-btn"
                    :class="i.status === 1 ? 'on' : 'off'"
                    v-has-permission="'system:vendor:manage'"
                    :disabled="instTogglingId === i.instance_id"
                    :title="i.status === 1 ? '点击停用' : '点击启用'"
                    @click="toggleInstance(i)"
                  >{{ instTogglingId === i.instance_id ? '切换中…' : (i.status === 1 ? '启用' : '停用') }}</button>
                </td>
                <td class="op">
                  <button class="tes-btn sm" v-has-permission="'system:vendor:manage'" @click="openInstEdit(i)">编辑</button>
                  <button class="tes-btn sm danger" v-has-permission="'system:vendor:manage'" @click="removeInstance(i)">删除</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
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
// 状态列可点 pill 的启停进行中态
const togglingId = ref('')

// 实例管理对话框状态
const instVendor = ref(null)
const instances = ref([])
const instLoading = ref(false)
const instError = ref('')
const instDialog = ref(null)  // { mode: 'create' | 'edit' }
const instSaving = ref(false)
const instFormError = ref('')
const instForm = ref(blankInstanceForm())
const instTogglingId = ref('')

const TYPE_LABELS = {
  aliyun: '阿里云', tencent: '腾讯云', ark: '火山方舟',
  deepseek: 'DeepSeek', custom: '自定义',
}
const AUTH_LABELS = { apikey: 'API Key', oauth: 'OAuth', aksk: 'AK/SK' }

function typeLabel(t) {
  return TYPE_LABELS[t] || t || '—'
}
function authLabel(a) {
  return AUTH_LABELS[a] || a || '—'
}

function blankVendorForm() {
  return {
    vendor_id: '', vendor_name: '', vendor_type: 'aliyun',
    base_url: '', auth_type: 'apikey', credential: '', status: 1,
    region_id: null, tenant_id: null,
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
    // 归属字段必须带进表单：后端 PUT 是全量覆盖，不传即被置 None（静默解绑）
    region_id: row.region_id ?? null,
    tenant_id: row.tenant_id ?? null,
  }
  formError.value = ''
  dialog.value = { mode: 'edit' }
}

/**
 * 表单 → VendorIn 载荷。
 * - credential 空串时不带该键——后端语义为「不传则不覆盖」；
 * - region_id / tenant_id 恒带上（哪怕 null），因为后端 PUT 走全量
 *   model_dump + setattr，缺键会被当 None 写入，把已有归属静默清掉。
 */
function vendorPayload() {
  const p = {
    vendor_id: form.value.vendor_id.trim(),
    vendor_name: form.value.vendor_name.trim(),
    vendor_type: form.value.vendor_type,
    base_url: form.value.base_url.trim() || null,
    auth_type: form.value.auth_type || null,
    status: Number(form.value.status) || 0,
    region_id: form.value.region_id ?? null,
    tenant_id: form.value.tenant_id ?? null,
  }
  const cred = form.value.credential.trim()
  if (cred) p.credential = cred
  return p
}

/** 行内启停专用载荷：表单没打开，字段全部从列表行原样回填。 */
function togglePayload(row, nextStatus) {
  return {
    vendor_id: row.vendor_id,
    vendor_name: row.vendor_name,
    vendor_type: row.vendor_type,
    base_url: row.base_url || null,
    auth_type: row.auth_type || null,
    status: nextStatus,
    region_id: row.region_id ?? null,
    tenant_id: row.tenant_id ?? null,
  }
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

/** 状态列 pill 直接启停（等价于编辑弹窗只改 status 再保存） */
async function toggle(row) {
  togglingId.value = row.vendor_id
  try {
    await updateVendor(row.vendor_id, togglePayload(row, row.status === 1 ? 0 : 1))
    await load()
  } catch (e) {
    error.value = errMsg(e, '状态切换失败')
  } finally {
    togglingId.value = ''
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
  error.value = ''
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

/** 实例状态列 pill 直接启停（InstanceIn 无可选归属字段，全量回填即可） */
async function toggleInstance(i) {
  instTogglingId.value = i.instance_id
  try {
    await updateInstance(instVendor.value.vendor_id, i.instance_id, {
      instance_id: i.instance_id,
      service_name: i.service_name,
      data_namespace: i.data_namespace,
      isolation_mode: i.isolation_mode || 'logical',
      status: i.status === 1 ? 0 : 1,
    })
    await openInstances(instVendor.value)
  } catch (e) {
    instError.value = errMsg(e, '状态切换失败')
  } finally {
    instTogglingId.value = ''
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
/* 页头说明段（.tes-sub 全局未定义，这里补样式，口径同 BYOM 页头） */
.tes-sub { margin: 4px 0 14px; font-size: 13px; line-height: 1.7; color: var(--muted); }
.tes-sub code { font-size: 12px; background: #f6f6fb; padding: 1px 5px; border-radius: 5px; }

.mid { margin-left: 8px; font-size: 11px; color: var(--muted); }
.mid.block { display: block; margin: 3px 0 0; }
.muted { color: var(--muted); }
.sub { margin-top: 3px; font-size: 11px; color: var(--muted); line-height: 1.4; }
td.op, th.op { white-space: nowrap; text-align: right; }

/* ⚠️ .tes-table-card 是 overflow:hidden（全局样式），列一多就会把最右的操作列
   整列裁掉——用户看到的就是「编辑/删除按钮不见了」（BYOM 列表 275c272 同款坑）。
   两层兜底：内层 .table-scroll 提供横向滚动 + 表格 min-width 让浏览器出滚动条
   而不是压缩/裁剪；再把「类型/归属」折进厂商副行、「认证/凭证」并一列精简列宽。 */
.table-scroll { overflow-x: auto; }
.vendor-table { min-width: 860px; }
.inst-table { min-width: 720px; }
code.url {
  display: inline-block; max-width: 260px; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; vertical-align: bottom;
}

/* 状态 pill：可点击切换启停（不用 .tes-btn.sm，避免打乱行内按钮序号） */
.pill-btn {
  display: inline-flex; align-items: center; padding: 2px 9px;
  border-radius: 999px; font-size: 11px; font-weight: 700; cursor: pointer;
  border: 1px solid transparent; background: #eef9f3; color: var(--success);
}
.pill-btn.off { background: #ffe9ee; color: var(--danger); }
.pill-btn:hover:not(:disabled) { filter: brightness(.95); border-color: currentColor; }
.pill-btn:disabled { opacity: .55; cursor: not-allowed; }

.tes-btn.sm { padding: 5px 12px; font-size: 12px; border-radius: var(--r-sm); }
.tes-btn.sm.danger { color: var(--danger); border-color: #ffd6de; }
.tes-btn.sm.danger:hover { background: #fff0f3; color: var(--danger); }
.tes-btn.sm:disabled { opacity: .5; cursor: not-allowed; }
.tes-empty.err { background: #ffe9ee; border-color: #ffd6de; color: var(--danger); text-align: left; }
.tes-empty.ok { background: #e3f9f1; border-color: #b8ecda; color: #00795c; text-align: left; }
.hint { margin: 10px 0 0; font-size: 12px; color: var(--muted); }

/* 连通性结果：判定 + HTTP/耗时/诊断 分行展示 */
.probe { display: flex; flex-direction: column; gap: 6px; }
.probe-head { display: flex; gap: 12px; align-items: baseline; flex-wrap: wrap; }
.probe-line { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; font-size: 12px; }
.kv-k {
  flex: none; padding: 1px 6px; border-radius: 5px; font-size: 10px; font-weight: 700;
  background: #eef4ff; color: var(--accent);
}

/* ---------- 对话框 ---------- */
.mask {
  position: fixed; inset: 0; z-index: 50;
  background: rgba(31, 27, 46, .45);
  display: grid; place-items: center; padding: 24px;
}
.dialog { width: 720px; max-width: 100%; max-height: 88vh; overflow: auto; }
.dialog h3 { margin-bottom: 16px; }
.wide-dlg { width: 900px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 16px; }
.form-grid label { display: flex; flex-direction: column; gap: 6px; font-size: 13px; font-weight: 600; color: var(--ink-2); }
.form-grid .wide { grid-column: 1 / -1; }
.form-grid input, .form-grid select {
  border: 1.5px solid var(--line-strong); border-radius: 8px; padding: 8px 10px; font: inherit;
}
.form-grid input:focus, .form-grid select:focus { outline: none; border-color: var(--brand-2, #ff7a7a); }
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
