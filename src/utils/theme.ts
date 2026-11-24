import { ref, watch } from 'vue'

/** 主题偏好：跟随系统 / 浅色 / 暗色。index.html 里有一段同逻辑的内联脚本，在首屏渲染前先设好，避免闪白 */
export type ThemePref = 'system' | 'light' | 'dark'

const KEY = 'theme'
const media = window.matchMedia('(prefers-color-scheme: dark)')

function load(): ThemePref {
  try {
    const v = localStorage.getItem(KEY)
    if (v === 'light' || v === 'dark') return v
  } catch { /* 隐私模式等读不到存储时跟随系统 */ }
  return 'system'
}

export const themePref = ref<ThemePref>(load())

function apply() {
  const dark = themePref.value === 'dark' || (themePref.value === 'system' && media.matches)
  document.documentElement.dataset.theme = dark ? 'dark' : 'light'
}

watch(themePref, v => {
  try {
    if (v === 'system') localStorage.removeItem(KEY)
    else localStorage.setItem(KEY, v)
  } catch { /* 忽略 */ }
  apply()
})
media.addEventListener('change', apply)
apply()

const ORDER: ThemePref[] = ['system', 'light', 'dark']
export function cycleTheme() {
  const next = ORDER[(ORDER.indexOf(themePref.value) + 1) % ORDER.length]
  // 支持 View Transitions 的浏览器里新旧主题交叉淡入，不再整页瞬间换色；系统要求减少动态效果时直接切换
  if (!('startViewTransition' in document) || matchMedia('(prefers-reduced-motion: reduce)').matches) {
    themePref.value = next
    return
  }
  document.startViewTransition(() => {
    themePref.value = next
    apply()
  })
}
export const THEME_LABELS: Record<ThemePref, string> = { system: '跟随系统', light: '浅色', dark: '暗色' }
