import type { JobStatus, ParseStage } from '@/api/parse'

export const STAGE_LABELS: Record<ParseStage, string> = {
  ocr: '版面识别与文字提取（OCR）',
  classify: '试卷分类：学段 / 学科 / 类型',
  segment: '题目切分与题型判断',
  knowledge: '知识点标注',
  difficulty: '难度评估',
  dedupe: '题库查重',
}

/** 任务列表中的简短阶段名 */
export const STAGE_SHORT: Record<ParseStage, string> = {
  ocr: '版面识别', classify: '试卷分类', segment: '拆题', knowledge: '知识点', difficulty: '难度评估', dedupe: '查重',
}

export const STATUS_LABELS: Record<JobStatus, string> = {
  uploading: '上传中', queued: '排队中', running: '解析中', done: '已完成', failed: '失败', cancelled: '已取消',
}
