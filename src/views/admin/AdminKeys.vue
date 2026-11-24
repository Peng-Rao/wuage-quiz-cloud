<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { adminApi, type SettingField } from '@/api/admin'
import LoadingState from '@/components/LoadingState.vue'
import { SOURCE_TEXT, fmtTime } from './labels'

const fields = ref<SettingField[] | null>(null)
const error = ref('')
// 每组的编辑草稿：键 → 输入值；密钥留空表示不修改
const draft = reactive<Record<string, string>>({})
const busy = ref('')
const notes = reactive<Record<string, { ok: boolean; text: string }>>({})
const TESTS: Record<string, 'llm' | 'vision' | 'embedding'> = { 大模型: 'llm', 看图模型: 'vision', 向量检索: 'embedding' }

function toText(f: SettingField) {
  if (f.kind === 'secret') return ''
  if (f.kind === 'json') return f.value && Object.keys(f.value as object).length ? JSON.stringify(f.value, null, 2) : ''
  return f.value == null ? '' : String(f.value)
}
function reset(list: SettingField[]) {
  fields.value = list
  for (const f of list) draft[f.key] = toText(f)
}
onMounted(async () => {
  try { reset(await adminApi.settings()) } catch (e) { error.value = (e as Error).message }
})
const groups = computed(() => {
  const out: Record<string, SettingField[]> = {}
  for (const f of fields.value ?? []) (out[f.group] ??= []).push(f)
  return out
})
function changes(list: SettingField[]) {
  const values: Record<string, unknown> = {}
  for (const f of list) {
    // type="number" 的 v-model 会得到数字，统一按文本比较
    const v = String(draft[f.key] ?? '')
    if (f.kind === 'secret') { if (v.trim()) values[f.key] = v.trim() }
    else if (v !== toText(f)) values[f.key] = f.kind === 'number' ? Number(v) : v
  }
  return values
}
async function save(group: string) {
  const values = changes(groups.value[group])
  if (!Object.keys(values).length) { notes[group] = { ok: true, text: '没有修改' }; return }
  busy.value = group; delete notes[group]
  try { reset(await adminApi.saveSettings(values)); notes[group] = { ok: true, text: '已保存，几秒内所有服务进程生效' } }
  catch (e) { notes[group] = { ok: false, text: (e as Error).message } }
  finally { busy.value = '' }
}
async function revert(f: SettingField) {
  if (!window.confirm(`「${f.group} · ${f.label}」恢复为环境变量（server/.env）中的值？`)) return
  busy.value = f.group
  try { reset(await adminApi.saveSettings({ [f.key]: null })); notes[f.group] = { ok: true, text: `${f.label}已恢复环境变量` } }
  catch (e) { notes[f.group] = { ok: false, text: (e as Error).message } }
  finally { busy.value = '' }
}
async function test(group: string) {
  busy.value = group; delete notes[group]
  try {
    const r = await adminApi.testSettings(TESTS[group])
    notes[group] = { ok: r.ok, text: r.ok ? `连接正常 · ${r.model} · ${r.latencyMs} ms` : r.message }
  } catch (e) { notes[group] = { ok: false, text: (e as Error).message } }
  finally { busy.value = '' }
}
</script>

<template>
  <main class="adm-page">
    <div class="adm-head">
      <div><h1>密钥配置</h1><p>大模型、看图模型、向量检索与 MinerU 的地址、API Key 和模型。后台保存的值优先于环境变量，无需重启；密钥只显示掩码。</p></div>
    </div>
    <p v-if="error" class="adm-error" role="alert">{{ error }}</p>
    <LoadingState v-else-if="!fields" />
    <form v-for="(list, group) in groups" :key="group" class="adm-card" @submit.prevent="save(group)">
      <h2>{{ group }}</h2>
      <div class="adm-grid-2">
        <div v-for="f in list" :key="f.key" class="adm-field" :class="{ wide: f.kind === 'json' }">
          <span class="label-row">
            <label :for="f.key">{{ f.label }}</label>
            <span class="adm-badge" :class="f.source === 'console' ? 'brand' : ''" :title="f.updatedBy ? `${f.updatedBy} · ${fmtTime(f.updatedAt)}` : ''">{{ SOURCE_TEXT[f.source] }}</span>
            <button v-if="f.source === 'console'" type="button" class="btn-link" :disabled="!!busy" @click="revert(f)">恢复</button>
          </span>
          <select v-if="f.kind === 'select'" :id="f.key" v-model="draft[f.key]"><option v-for="o in f.options" :key="o" :value="o">{{ o }}</option></select>
          <textarea v-else-if="f.kind === 'json'" :id="f.key" v-model="draft[f.key]" rows="3" spellcheck="false" placeholder="{}"></textarea>
          <input v-else-if="f.kind === 'secret'" :id="f.key" v-model="draft[f.key]" type="password" autocomplete="new-password" spellcheck="false" :placeholder="f.configured ? `已配置 ${f.value} · 留空不修改` : '未配置'">
          <input v-else-if="f.kind === 'number'" :id="f.key" v-model="draft[f.key]" type="number" min="0" step="1">
          <input v-else :id="f.key" v-model="draft[f.key]" spellcheck="false" autocomplete="off">
          <small v-if="f.help" class="adm-hint">{{ f.help }}</small>
        </div>
      </div>
      <div class="adm-actions foot">
        <button class="btn btn-primary btn-sm" :disabled="busy === group">{{ busy === group ? '处理中…' : '保存' }}</button>
        <button v-if="TESTS[group]" type="button" class="btn btn-sm" :disabled="busy === group" @click="test(group)">测试连接</button>
        <span v-if="notes[group]" :class="notes[group].ok ? 'adm-ok' : 'adm-error'" role="status">{{ notes[group].text }}</span>
      </div>
    </form>
    <p v-if="fields" class="adm-hint tail">数据库地址、对象存储密钥等基础设施配置仍只能在服务器的环境变量中修改。</p>
  </main>
</template>

<style scoped>
.label-row { display: flex; align-items: center; gap: 8px; }
.label-row label { font-size: 13px; }
.label-row .btn-link { font-size: 12px; }
.wide { grid-column: 1 / -1; }
.foot { margin-top: 16px; }
.tail { margin-top: 16px; }
</style>
