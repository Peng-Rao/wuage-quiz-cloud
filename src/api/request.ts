/** 后端接口地址前缀；与页面同源部署时留空 */
export const BASE = import.meta.env.VITE_API_BASE ?? ''

/** JSON 请求；失败时抛出带后端 message 的 Error */
export async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(BASE + path, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: 'include',
  })
  if (!res.ok) {
    const err = await res.json().catch(() => null)
    throw new Error(err?.message ?? `请求失败（HTTP ${res.status}）`)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

/** 由对象生成查询串，跳过空值 */
export function query(params: Record<string, string | number | undefined | null>): string {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') q.set(k, String(v))
  return q.toString()
}
