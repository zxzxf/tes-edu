<template>
  <div class="login">
    <aside class="hero">
      <div class="hero-glow"></div>
      <div class="hero-inner">
        <div class="hero-logo">
          <span class="mark">T</span> Tes Edu
        </div>
        <h1 class="hero-title">你的专属<br />逆袭学习舱</h1>
        <p class="hero-sub">步骤级归因 · 精准查漏 · 个性化提分</p>

        <div class="hero-chips">
          <span class="chip">🎯 精准定位错因</span>
          <span class="chip">🚀 越练越强</span>
          <span class="chip">🔥 过程可溯</span>
        </div>

        <div class="hero-stats">
          <div><b>100%</b><i>过程可溯</i></div>
          <div><b>∞</b><i>个性练习</i></div>
          <div><b>24h</b><i>即时反馈</i></div>
        </div>
      </div>
    </aside>

    <main class="form-side">
      <div class="form-card tes-anim">
        <h2>同学，你好 👋</h2>
        <p class="form-tip">登录以进入你的学习工作台</p>

        <label class="field">
          <span>用户名</span>
          <input v-model="username" placeholder="请输入用户名" />
        </label>

        <label class="field">
          <span>密码</span>
          <input v-model="password" type="password" placeholder="请输入密码" @keyup.enter="onLogin" />
        </label>

        <button class="submit" :disabled="loading" @click="onLogin">
          {{ loading ? '登录中…' : '登 录' }}
        </button>

        <p v-if="error" class="error">⚠️ {{ error }}</p>
        <p class="hint">与「学生 App」使用同一套账号</p>
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)
const router = useRouter()
const auth = useAuthStore()

async function onLogin() {
  error.value = ''
  if (!username.value || !password.value) {
    error.value = '请输入用户名和密码'
    return
  }
  loading.value = true
  try {
    await auth.login(username.value, password.value)
    router.push('/')
  } catch (e) {
    error.value = '登录失败：' + (e.response?.data?.detail || e.message)
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login { display: flex; height: 100vh; }

.hero {
  position: relative;
  width: 46%;
  flex-shrink: 0;
  background: linear-gradient(135deg, #ff7a45 0%, #ff3d81 55%, #7c4dff 120%);
  background-size: 200% 200%;
  animation: tes-grad-shift 14s ease infinite;
  color: #fff;
  overflow: hidden;
  display: flex; align-items: center;
}
.hero-glow {
  position: absolute;
  width: 380px; height: 380px;
  top: -80px; right: -60px;
  background: radial-gradient(circle, rgba(255,255,255,.35), transparent 70%);
  filter: blur(6px);
}
.hero-inner { position: relative; padding: 0 56px; z-index: 1; }
.hero-logo { display: flex; align-items: center; gap: 10px; font-size: 20px; font-weight: 800; margin-bottom: 40px; }
.hero-logo .mark {
  width: 36px; height: 36px; border-radius: 10px;
  background: #fff; color: #ff3d81;
  display: grid; place-items: center; font-weight: 900;
}
.hero-title { font-size: 40px; line-height: 1.25; font-weight: 900; letter-spacing: 1px; }
.hero-sub { margin-top: 18px; font-size: 15px; color: rgba(255,255,255,.9); }
.hero-chips { margin-top: 28px; display: flex; flex-wrap: wrap; gap: 10px; }
.chip {
  background: rgba(255,255,255,.16);
  border: 1px solid rgba(255,255,255,.28);
  border-radius: 999px;
  padding: 7px 14px; font-size: 13px; font-weight: 600;
  backdrop-filter: blur(4px);
}
.hero-stats { margin-top: 46px; display: flex; gap: 34px; }
.hero-stats div { display: flex; flex-direction: column; }
.hero-stats b { font-size: 28px; font-weight: 900; }
.hero-stats i { font-style: normal; font-size: 12px; color: rgba(255,255,255,.8); margin-top: 4px; }

.form-side { flex: 1; display: grid; place-items: center; padding: 24px; }
.form-card {
  width: 360px; max-width: 100%;
  background: #fff; border-radius: 22px;
  padding: 38px 34px;
  box-shadow: 0 20px 60px rgba(255, 61, 129, .14);
}
.form-card h2 { font-size: 24px; }
.form-tip { color: var(--muted); margin: 8px 0 26px; }
.field { display: block; margin-bottom: 18px; }
.field span { display: block; font-size: 13px; font-weight: 600; margin-bottom: 7px; color: var(--ink-2); }
.field input {
  width: 100%; box-sizing: border-box;
  border: 1.5px solid var(--line-strong); border-radius: var(--r-md);
  padding: 12px 14px; font-size: 15px; outline: none;
  transition: border-color .15s;
}
.field input:focus { border-color: var(--brand-2); }
.submit {
  width: 100%;
  background: var(--brand-grad);
  color: #fff; border: none; border-radius: 14px;
  padding: 13px; font-size: 16px; font-weight: 800; letter-spacing: 2px;
  box-shadow: 0 10px 24px rgba(255, 61, 129, .32);
  margin-top: 6px; cursor: pointer;
}
.submit:hover { filter: brightness(1.05); }
.submit:disabled { background: #e9e4ef; color: #b3adc4; box-shadow: none; cursor: not-allowed; }
.error { color: var(--danger); font-size: 13px; margin: 14px 0 0; }
.hint { color: var(--muted); font-size: 12px; margin: 16px 0 0; text-align: center; }

@media (max-width: 820px) {
  .hero { display: none; }
  .form-side { background: var(--brand-grad-soft); }
}
</style>
