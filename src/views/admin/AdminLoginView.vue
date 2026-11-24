<script setup lang="ts">
import { ref } from 'vue'
import { useRoute } from 'vue-router'
import { useConsoleStore } from '@/stores/console'

const c = useConsoleStore()
const route = useRoute()
const email = ref('')
const password = ref('')
const busy = ref(false)
const error = ref('')

async function submit() {
  busy.value = true
  error.value = ''
  try {
    await c.login(email.value.trim(), password.value)
    const next = typeof route.query.redirect === 'string' ? route.query.redirect : '/admin'
    // 只允许跳回后台内部地址
    window.location.replace(next.startsWith('/admin') && !next.startsWith('/admin/login') && !next.includes('\\') ? next : '/admin')
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
</script>

<template>
  <main class="login">
    <form class="panel" @submit.prevent="submit">
      <div class="brand">
        <svg viewBox="0 0 450 365" class="mark" fill="var(--c-logo)" aria-hidden="true">
          <rect x="0" y="0" width="87" height="365" /><rect x="0" y="0" width="450" height="95" />
          <path d="M175,133 H442 V365 H162 V283 H300 A52.5 52.5 0 0 0 300,178 H175 A22.5 22.5 0 0 1 175,133 Z" />
        </svg>
        <span><b>福格云上题库</b><small>管理后台</small></span>
      </div>
      <h1>登录管理后台</h1>
      <p class="intro">使用后台账号（邮箱）登录。后台账号与题库账号相互独立，由 root 账号授权。</p>
      <label for="email">邮箱</label>
      <input id="email" v-model="email" type="email" autocomplete="username" required maxlength="254" placeholder="name@example.com" autofocus>
      <label for="password">密码</label>
      <input id="password" v-model="password" type="password" autocomplete="current-password" required maxlength="128" placeholder="请输入密码">
      <p v-if="error || c.error" class="error" role="alert">{{ error || c.error }}</p>
      <button class="btn btn-primary submit" :disabled="busy">{{ busy ? '登录中…' : '登 录' }}</button>
      <p class="help">忘记密码请联系 root 账号重置；root 账号可在服务器上运行<br><code>python -m app.create_root</code> 创建。</p>
      <a class="back" href="/">← 返回题库</a>
    </form>
  </main>
</template>

<style scoped>
.login {
  min-height: 100vh; display: grid; place-items: center; padding: 32px 16px;
  background: radial-gradient(70% 50% at 50% 0%, color-mix(in srgb, var(--c-primary) 9%, transparent), transparent 70%), var(--c-paper);
}
.panel { animation: rise-in var(--dur-slow) var(--ease-out) backwards; width: 100%; max-width: 400px; background: var(--c-surface); border: 1px solid var(--c-border); border-radius: var(--r-lg); padding: 32px; box-shadow: var(--shadow-lg); }
@media (prefers-reduced-motion: reduce) { .panel { animation: none; } }
.brand { display: flex; align-items: center; gap: 10px; margin-bottom: 28px; }
.mark { width: 32px; height: 27px; }
.brand b { display: block; color: var(--c-primary); letter-spacing: 1px; }
.brand small { display: block; font-size: 12px; color: var(--c-text-3); margin-top: 2px; }
h1 { font-size: 22px; margin: 0 0 8px; }
.intro, .help { color: var(--c-text-3); font-size: 13px; line-height: 1.7; margin: 0; }
label { display: block; margin: 20px 0 8px; font-size: 14px; }
input { width: 100%; padding: 11px 12px; border: 1px solid var(--c-border); border-radius: var(--r-sm); background: var(--c-paper); color: var(--c-ink); font-size: 14px; }
.error { color: var(--c-danger); font-size: 13px; margin: 14px 0 0; }
.submit { width: 100%; margin-top: 24px; }
.help { margin-top: 18px; font-size: 12px; }
code { font-size: 12px; background: var(--c-paper-2); padding: 1px 5px; border-radius: 4px; }
.back { display: inline-block; margin-top: 18px; font-size: 13px; color: var(--c-text-3); }
@media (max-width: 480px) { .panel { padding: 24px 20px; } }
</style>
