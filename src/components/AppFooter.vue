<script setup lang="ts">
import { SITE } from '@/data/site'

const year = new Date().getFullYear()
const years = SITE.since < year ? `${SITE.since}-${year}` : `${year}`
const policeCode = SITE.police.match(/\d+/)?.[0]
</script>

<template>
  <footer class="footer">
    <div class="container inner">
      <p class="line">
        <span>©{{ years }} {{ SITE.owner }}（{{ SITE.name }}）版权所有</span>
        <span>题库自主建设</span>
        <a v-if="SITE.icp" href="https://beian.miit.gov.cn/" target="_blank" rel="noopener">{{ SITE.icp }}</a>
        <span v-else class="todo">ICP 备案号（待填写）</span>
        <a
          v-if="SITE.police" class="police" target="_blank" rel="noopener"
          :href="`https://beian.mps.gov.cn/#/query/webSearch?code=${policeCode}`"
        >{{ SITE.police }}</a>
        <span v-else class="todo">公安备案号（待填写）</span>
      </p>
      <p class="line">
        <span>举报电话：<template v-if="SITE.phone">{{ SITE.phone }}</template><span v-else class="todo">待填写</span></span>
        <span>举报邮箱：<a v-if="SITE.email" :href="`mailto:${SITE.email}`">{{ SITE.email }}</a><span v-else class="todo">待填写</span></span>
        <a href="#" @click.prevent>版权异议</a>
        <a href="#" @click.prevent>用户协议</a>
        <a href="#" @click.prevent>隐私政策</a>
      </p>
      <p class="line sub">
        {{ SITE.name }}拥有「{{ SITE.name }}」名称及商标，若您发现其他网站违规使用，欢迎向我们举报
      </p>
      <p class="line sub">
        <span>技术支持：<span class="studio">@{{ SITE.studio }}</span></span>
      </p>
    </div>
  </footer>
</template>

<style scoped>
.footer { border-top: 1px solid var(--c-border); background: #F3F1EB; }
.inner { padding-top: 28px; padding-bottom: 28px; display: flex; flex-direction: column; align-items: center; gap: 10px; text-align: center; }
.line {
  margin: 0; display: flex; flex-wrap: wrap; justify-content: center; gap: 6px 22px;
  font-size: 13px; line-height: 1.6; color: var(--c-text-2);
}
.line.sub { font-size: 12px; color: var(--c-text-3); }
.line a { color: inherit; }
.line a:hover { color: var(--c-primary); }
.todo { color: var(--c-text-4); }
.studio { color: var(--c-primary); font-weight: 600; }
</style>
