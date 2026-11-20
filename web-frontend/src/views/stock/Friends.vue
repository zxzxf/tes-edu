<template>
  <div class="stock-page">
    <h2>推送好友</h2>
    <p class="muted">
      这里的"好友"是 PushPlus 用户（非微信通讯录）。添加为双向：渠道账号在 PushPlus
      生成好友二维码 → 对方微信扫码成为好友 → 在 PushPlus「我的好友」列表拿到好友令牌（32 位），
      在此登记后即可作为预警接收方。
    </p>

    <div class="add-bar">
      <input v-model="alias" placeholder="别名（如 老王）" class="in" style="width:160px" />
      <input v-model="token" placeholder="好友令牌（32 位十六进制）" class="in" style="width:280px" />
      <input v-model="note" placeholder="备注（可选）" class="in" style="width:160px" />
      <button class="btn-add" @click="add" :disabled="!alias || !token">添加</button>
    </div>
    <p class="err" v-if="error">{{ error }}</p>

    <table class="fr-table">
      <thead>
        <tr><th>别名</th><th>令牌（脱敏）</th><th>备注</th><th>操作</th></tr>
      </thead>
      <tbody>
        <tr v-for="f in items" :key="f.friend_id">
          <td>{{ f.alias }}</td>
          <td class="mono">{{ f.token_mask }}</td>
          <td class="muted">{{ f.note || '—' }}</td>
          <td>
            <button class="btn-link" @click="testOne(f)">发测试</button>
            <button class="btn-del" @click="remove(f)">删除</button>
          </td>
        </tr>
        <tr v-if="!items.length"><td colspan="4" class="empty">暂无好友</td></tr>
      </tbody>
    </table>

    <div class="test-line">
      <button class="btn-add alt" @click="testSelf">给我自己发测试消息</button>
      <span class="muted">{{ testResult }}</span>
    </div>
    <p class="muted tip">
      提示：测试消息走推送中心真实链路（PushPlus 微信渠道），若返回"未关联"类错误，
      请先确认渠道账号与对方已互加 PushPlus 好友、且渠道账号已实名认证。
    </p>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { addFriend, delFriend, listFriends, pushTest } from '../../api/stock'

const items = ref([])
const alias = ref('')
const token = ref('')
const note = ref('')
const error = ref('')
const testResult = ref('')

async function load() {
  items.value = (await listFriends()).items
}

async function add() {
  error.value = ''
  try {
    await addFriend({ alias: alias.value.trim(), token: token.value.trim(), note: note.value.trim() })
    alias.value = token.value = note.value = ''
    await load()
  } catch (e) {
    error.value = e.response?.data?.detail || '添加失败（令牌须为 32 位十六进制）'
  }
}

async function remove(f) {
  if (!window.confirm(`删除好友「${f.alias}」？已配置该接收方的预警将失效。`)) return
  await delFriend(f.friend_id)
  await load()
}

async function testOne(f) {
  testResult.value = `正在向 ${f.alias} 发送…`
  try {
    const r = await pushTest(f.token)
    const res = (r.results || [])[0] || {}
    testResult.value = res.msg_id
      ? `已发起（msg ${String(res.msg_id).slice(0, 8)}…），请到对方微信确认查收（好友须已与渠道账号互加）`
      : `发送失败：${res.status || '未知'}`
  } catch (e) {
    testResult.value = `发送失败：${e.response?.data?.detail || e.message}`
  }
}

async function testSelf() {
  testResult.value = '正在向渠道账号本人发送…'
  try {
    const r = await pushTest('SELF')
    const res = (r.results || [])[0] || {}
    testResult.value = res.msg_id ? `已发起（msg ${String(res.msg_id).slice(0, 8)}…），查推送中心消息状态` : `失败：${res.status}`
  } catch (e) {
    testResult.value = `发送失败：${e.response?.data?.detail || e.message}`
  }
}

onMounted(load)
</script>

<style scoped>
.stock-page { padding: 20px; }
.muted { color: #888; font-size: 13px; }
.err { color: #e53935; font-size: 13px; }
.tip { margin-top: 10px; }
.add-bar { display: flex; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; }
.in { padding: 6px 12px; border: 1px solid #d0d0d0; border-radius: 4px; font-size: 13px; }
.btn-add { padding: 6px 16px; background: #1976d2; color: #fff; border: none; border-radius: 4px; cursor: pointer; }
.btn-add.alt { background: #607d8b; }
.btn-add:disabled { opacity: 0.5; cursor: not-allowed; }
.fr-table { width: 100%; border-collapse: collapse; }
.fr-table th, .fr-table td { padding: 8px 12px; text-align: left; border-bottom: 1px solid #eee; font-size: 13px; }
.fr-table th { background: #f5f5f5; font-weight: 600; }
.mono { font-family: Consolas, monospace; }
.btn-link { color: #1976d2; border: none; background: none; cursor: pointer; margin-right: 8px; font-size: 13px; }
.btn-del { color: #e53935; border: none; background: none; cursor: pointer; font-size: 13px; }
.empty { text-align: center; color: #999; padding: 24px; }
.test-line { margin-top: 16px; display: flex; gap: 12px; align-items: center; }
</style>
