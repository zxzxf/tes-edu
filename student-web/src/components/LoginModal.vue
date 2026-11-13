<template>
  <div class="lm-mask">
    <div class="lm-modal">
      <div class="lm-icon">🔒</div>

      <div class="lm-head">
        <div class="lm-title">登录已过期</div>
        <p class="lm-sub">登录状态已失效，重新登录后即可继续刚才的学习，进度不会丢失</p>
      </div>

      <LoginForm compact @success="onSuccess" />

      <p class="lm-secure">🔐 出于账号安全考虑，长时间未操作会自动退出登录</p>

      <button class="lm-cancel" @click="onCancel">暂不登录，返回登录页</button>
    </div>
  </div>
</template>

<script setup>
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { flushUnauthorized, cancelUnauthorized } from '../api/client'
import LoginForm from '../components/LoginForm.vue'

const auth = useAuthStore()
const router = useRouter()

function onSuccess() {
  // reauth 已写入新 token；重放挂起请求并关闭弹窗
  flushUnauthorized()
  auth.sessionExpired = false
}

function onCancel() {
  // 用户放弃重认证：拒绝挂起请求、清空凭证并跳登录页
  cancelUnauthorized()
  auth.cancelReauth()
  router.push('/login')
}
</script>

<style scoped>
.lm-mask {
  position: fixed; inset: 0;
  background: rgba(31, 27, 46, .55);
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
  display: grid; place-items: center;
  padding: 20px;
  z-index: 200; /* 高于防疲劳弹窗，登录优先 */
  animation: lm-fade .25s ease both;
}
@keyframes lm-fade { from { opacity: 0; } to { opacity: 1; } }

.lm-modal {
  width: 380px; max-width: 100%;
  background: #fff; border-radius: var(--r-lg);
  padding: 0 28px 24px;
  box-shadow: 0 28px 80px rgba(31, 27, 46, .35);
  animation: tes-fade-up .35s ease both;
  text-align: center;
}

/* 顶部浮动图标徽标：品牌渐变圆 + 锁，吸引注意 */
.lm-icon {
  width: 64px; height: 64px;
  margin: -32px auto 14px;
  border-radius: 50%;
  background: var(--brand-grad);
  display: grid; place-items: center;
  font-size: 30px;
  color: #fff;
  box-shadow: 0 12px 30px rgba(255, 61, 129, .38);
  border: 4px solid #fff;
  animation: lm-pop .45s cubic-bezier(.2, .8, .3, 1.2) both;
}
@keyframes lm-pop {
  from { transform: scale(.6); opacity: 0; }
  to { transform: scale(1); opacity: 1; }
}

.lm-head { margin-bottom: 18px; }
.lm-title { font-size: 20px; font-weight: 800; margin-bottom: 6px; }
.lm-sub { color: var(--ink-2); font-size: 13px; line-height: 1.6; margin: 0; }

.lm-secure {
  color: var(--muted); font-size: 12px; margin: 16px 0 0;
}

.lm-cancel {
  width: 100%;
  margin-top: 12px;
  background: #fff;
  border: 1.5px solid var(--line-strong);
  color: var(--ink-2);
  border-radius: var(--r-md);
  padding: 11px;
  font-size: 14px;
  font-weight: 700;
  cursor: pointer;
  transition: transform .12s ease, border-color .2s ease, color .2s ease;
}
.lm-cancel:hover {
  border-color: var(--brand-2);
  color: var(--brand-2);
  transform: translateY(-1px);
}

@media (prefers-reduced-motion: reduce) {
  .lm-mask, .lm-modal, .lm-icon { animation: none !important; }
}
</style>
