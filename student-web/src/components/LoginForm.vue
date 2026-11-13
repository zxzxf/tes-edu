<template>
  <div class="login-form" :class="{ compact }">
    <div class="lf-tabs">
      <button type="button" :class="{ active: mode === 'pwd' }" @click="mode = 'pwd'">
        账号密码登录
      </button>
      <button type="button" :class="{ active: mode === 'wx' }" @click="mode = 'wx'">
        微信扫码登录
      </button>
    </div>

    <!-- 账号密码登录（本次实现） -->
    <template v-if="mode === 'pwd'">
      <label class="field">
        <span>用户名</span>
        <input v-model="username" placeholder="请输入用户名" :disabled="loading" />
      </label>

      <label class="field">
        <span>密码</span>
        <input
          v-model="password"
          type="password"
          placeholder="请输入密码"
          :disabled="loading"
          @keyup.enter="submit"
        />
      </label>

      <button class="submit" :disabled="loading" @click="submit">
        {{ loading ? '登录中…' : (compact ? '重新登录' : '登 录') }}
      </button>

      <p v-if="error" class="error">⚠️ {{ error }}</p>
    </template>

    <!-- 微信小程序扫码登录（待实现） -->
    <template v-else>
      <div class="lf-wx">
        <div class="lf-qr">
          <span class="lf-qr-icon">📱</span>
          <span class="lf-todo">待实现</span>
        </div>
        <p class="lf-qr-tip">请使用「Tes Edu 微信小程序」扫描二维码登录</p>
        <p class="lf-qr-hint">微信小程序扫码登录正在开发中，敬请期待。</p>
      </div>
    </template>

    <p class="lf-foot">与「学生 App」使用同一套账号</p>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useAuthStore } from '../stores/auth'

const props = defineProps({
  // compact=true 用于弹窗（重新登录）；false 用于整页登录
  compact: { type: Boolean, default: false },
})
const emit = defineEmits(['success'])

const auth = useAuthStore()
const mode = ref('pwd')
const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

async function submit() {
  error.value = ''
  if (!username.value || !password.value) {
    error.value = '请输入用户名和密码'
    return
  }
  loading.value = true
  try {
    if (props.compact) {
      await auth.reauth(username.value, password.value)
    } else {
      await auth.login(username.value, password.value)
    }
    username.value = ''
    password.value = ''
    emit('success')
  } catch (e) {
    error.value = '登录失败：' + (e.response?.data?.detail || e.message)
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-form { width: 100%; }

.lf-tabs { display: flex; gap: 8px; margin-bottom: 22px; }
.lf-tabs button {
  flex: 1;
  padding: 10px;
  border: 1.5px solid var(--line-strong);
  background: #fff;
  color: var(--ink-2);
  border-radius: var(--r-md);
  font-weight: 700;
}
.lf-tabs button.active {
  border-color: var(--brand-2);
  color: var(--brand-2);
  background: var(--brand-grad-soft);
}

.field { display: block; margin-bottom: 16px; }
.field span {
  display: block;
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 7px;
  color: var(--ink-2);
}

.submit {
  width: 100%;
  background: var(--brand-grad);
  color: #fff;
  border: none;
  border-radius: 14px;
  padding: 13px;
  font-size: 16px;
  font-weight: 800;
  letter-spacing: 2px;
  box-shadow: 0 10px 24px rgba(255, 61, 129, .32);
  margin-top: 6px;
  cursor: pointer;
}
.submit:hover { filter: brightness(1.05); }
.submit:disabled { background: #e9e4ef; color: #b3adc4; box-shadow: none; cursor: not-allowed; }

.error { color: var(--danger); font-size: 13px; margin: 14px 0 0; }

.lf-wx { text-align: center; padding: 8px 0 4px; }
.lf-qr {
  width: 160px;
  height: 160px;
  margin: 0 auto 16px;
  border-radius: var(--r-lg);
  border: 2px dashed var(--line-strong);
  background: var(--bg);
  color: var(--muted);
  display: grid;
  place-items: center;
  position: relative;
}
.lf-qr-icon { font-size: 46px; }
.lf-todo {
  position: absolute;
  top: 8px;
  right: 8px;
  background: var(--warning);
  color: #fff;
  font-size: 11px;
  font-weight: 800;
  padding: 2px 8px;
  border-radius: 999px;
}
.lf-qr-tip { font-size: 14px; font-weight: 700; color: var(--ink); margin: 0 0 6px; }
.lf-qr-hint { font-size: 12px; color: var(--muted); margin: 0; }

.lf-foot { color: var(--muted); font-size: 12px; margin: 18px 0 0; text-align: center; }
.compact .lf-foot { margin-top: 14px; }
</style>
