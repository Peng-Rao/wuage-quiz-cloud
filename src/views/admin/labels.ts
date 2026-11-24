/** 管理后台的状态文案与格式化 */
export const EXAM_STATUS: Record<string, { text: string; tone: string }> = {
  queued: { text: '排队中', tone: 'info' },
  running: { text: '解析中', tone: 'info' },
  done: { text: '已完成', tone: 'ok' },
  failed: { text: '失败', tone: 'bad' },
  cancelled: { text: '已取消', tone: '' },
  uploading: { text: '上传中', tone: '' },
}

export const SOURCE_TEXT: Record<string, string> = { console: '后台设置', env: '环境变量', default: '默认值' }

export function fmtTime(s: string | null | undefined): string {
  if (!s) return '—'
  return new Date(s).toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
}

/** 相对时间：刚刚 / N 分钟前 / N 小时前 / N 天前 */
export function ago(s: string | null | undefined, now = Date.now()): string {
  if (!s) return '从未'
  const sec = Math.max(0, Math.floor((now - Date.parse(s)) / 1000))
  if (sec < 60) return '刚刚'
  if (sec < 3600) return `${Math.floor(sec / 60)} 分钟前`
  if (sec < 86400) return `${Math.floor(sec / 3600)} 小时前`
  return `${Math.floor(sec / 86400)} 天前`
}

export function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
