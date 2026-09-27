import { httpBankApi } from './http'
export * from './types'

/** 登录与数据权限统一由服务端校验。 */
export const bankApi = httpBankApi
