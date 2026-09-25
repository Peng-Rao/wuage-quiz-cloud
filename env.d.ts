/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 试卷解析服务：mock（默认）| http */
  readonly VITE_PARSE_API?: 'mock' | 'http'
  readonly VITE_API_BASE?: string
}
