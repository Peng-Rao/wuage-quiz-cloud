<script setup lang="ts">
import { ref } from 'vue'
import { useRoute } from 'vue-router'
import BrandLogo from '@/components/BrandLogo.vue'
import { useAuthStore } from '@/stores/auth'
const auth = useAuthStore()
const route = useRoute()
const username = ref('')
const password = ref('')
const busy = ref(false)
const error = ref('')
async function submit() {
  busy.value = true
  error.value = ''
  try {
    await auth.login(username.value.trim(), password.value)
    const next = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
    window.location.replace(next.startsWith('/') && !next.startsWith('//') && !next.includes('\\') && !next.startsWith('/login') ? next : '/')
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
</script>

<template>
  <main class="login-page">
    <section class="welcome">
      <BrandLogo />
      <div><span class="eyebrow">教研协作 · 校本题库</span><h1 class="serif">好题共享，<br>各有所用。</h1><p>让每一份教学积累，<br>成为下一堂课的从容准备。</p></div>
      <p class="welcome-foot">按学科协作 · 审核后共享 · 自由组卷</p>
    </section>
    <section class="login-panel">
      <form @submit.prevent="submit">
        <span class="eyebrow">欢迎回来</span><h2>登录云上题库</h2><p class="intro">使用管理员为你分配的账号登录。</p>
        <label for="username">账号</label><input id="username" v-model="username" autocomplete="username" required maxlength="64" placeholder="请输入账号" autofocus>
        <label for="password">密码</label><input id="password" v-model="password" type="password" autocomplete="current-password" required maxlength="128" placeholder="请输入密码">
        <p v-if="error || auth.error" class="error" role="alert">{{ error || auth.error }}</p>
        <button class="btn btn-primary login-button" :disabled="busy">{{ busy ? '登录中…' : '登 录' }}</button>
        <p class="help">没有账号或忘记密码？请联系管理员。</p>
        <div class="roles"><span>管理员<small>全站管理</small></span><span>学科组长<small>AI 教研与审核</small></span><span>普通用户<small>选题与下载</small></span></div>
      </form>
    </section>
  </main>
</template>

<style scoped>
.login-page { min-height: calc(100vh - 110px); display: grid; grid-template-columns: 1fr 1fr; }
.welcome { background: #f5ede2; padding: 54px max(32px, 8vw); display: flex; flex-direction: column; justify-content: space-between; gap: 56px; }
.eyebrow { color: var(--c-primary); font-size: 13px; letter-spacing: 2px; }
h1 { font-size: clamp(38px, 4vw, 64px); font-weight: 500; line-height: 1.3; margin: 24px 0; }
.welcome p { color: var(--c-text-3); line-height: 1.9; }.welcome-foot { font-size: 12px; }
.login-panel { display: grid; place-items: center; padding: 48px 24px; background: #fff; }
form { width: 100%; max-width: 360px; }h2 { font-size: 28px; margin: 12px 0; }.intro,.help { color: var(--c-text-3); font-size: 13px; line-height: 1.7; }
label { display: block; margin: 24px 0 8px; font-size: 14px; }input { width: 100%; padding: 12px 14px; border: 1px solid var(--c-border); border-radius: 6px; background: var(--c-paper); }
.login-button { width: 100%; margin-top: 28px; height: 46px; }.help { text-align: center; margin: 18px 0 36px; }.error { color: #a0301f; font-size: 13px; }
.roles { display: flex; justify-content: space-between; padding-top: 24px; border-top: 1px solid var(--c-border); font-size: 12px; }.roles small { display: block; color: var(--c-text-4); margin-top: 6px; }
@media(max-width:720px) { .login-page { grid-template-columns: 1fr; }.welcome { padding: 24px; gap: 20px; }.welcome h1 { font-size: 32px; margin: 12px 0; }.welcome p,.welcome-foot { display: none; }.welcome h1 br { display: none; }.login-panel { padding: 32px 24px; } }
</style>
