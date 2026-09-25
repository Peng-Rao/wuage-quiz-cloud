import { httpParseApi } from './http'
import { mockParseApi } from './mock'
import type { ParseApi } from './types'

export * from './types'

/** VITE_PARSE_API=http 时连接后端，默认使用 mock */
export const parseApi: ParseApi = import.meta.env.VITE_PARSE_API === 'http' ? httpParseApi : mockParseApi
