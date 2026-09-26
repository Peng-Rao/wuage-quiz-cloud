import { httpBankApi } from './http'
import { mockBankApi } from './mock'
import type { BankApi } from './types'

export * from './types'

/** 与解析服务同一开关：VITE_PARSE_API=http 时连接后端，默认使用演示数据 */
export const bankApi: BankApi = import.meta.env.VITE_PARSE_API === 'http' ? httpBankApi : mockBankApi
