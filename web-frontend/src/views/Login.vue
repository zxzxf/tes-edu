<template>
  <div class="login-wrap">
    <div class="login-card">
      <h2>Tes Edu 管理后台</h2>
      <input v-model="username" placeholder="用户名" />
      <input v-model="password" type="password" placeholder="密码" @keyup.enter="onLogin" />
      <button @click="onLogin">登录</button>
      <p v-if="error" class="error">{{ error }}</p>
      <p class="hint">演示账号：admin@tes.edu / demo1234</p>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const username = ref('')
const password = ref('')
const error = ref('')
const router = useRouter()
const auth = useAuthStore()

async function onLogin() {
  error.value = ''
  try {
    await auth.login(username.value, password.value)
    router.push('/dashboard')
  } catch (e) {
    error.value = '登录失败：' + (e.response?.data?.detail || e.message)
  }
}
</script>

<style scoped>
.login-wrap { display: flex; justify-content: center; align-items: center; height: 100vh; background: #f5f7fa; }
.login-card { background: #fff; padding: 32px; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,.08); width: 320px; display: flex; flex-direction: column; gap: 12px; }
input { padding: 10px; border: 1px solid #dcdfe6; border-radius: 6px; }
button { padding: 10px; background: #409eff; color: #fff; border: none; border-radius: 6px; cursor: pointer; }
.error { color: #f56c6c; font-size: 13px; }
.hint { color: #909399; font-size: 12px; }
</style>
