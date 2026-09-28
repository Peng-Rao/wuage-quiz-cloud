"""接口数据结构，与前端 src/api/parse/types.ts 一一对应（JSON 字段为 camelCase）。"""

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

QuestionType = Literal["单选题", "多选题", "填空题", "解答题"]
QUESTION_TYPES: tuple[QuestionType, ...] = ("单选题", "多选题", "填空题", "解答题")
ParseStage = Literal["ocr", "classify", "segment", "knowledge", "difficulty", "dedupe"]
PARSE_STAGES: tuple[ParseStage, ...] = ("ocr", "classify", "segment", "knowledge", "difficulty", "dedupe")
StageStatus = Literal["pending", "running", "done", "skipped", "failed"]
JobStatus = Literal["uploading", "queued", "running", "done", "failed", "cancelled"]

REVIEW_CONFIDENCE = 0.8

Bbox = tuple[float, float, float, float]

# 统一输出 UTC：带时区的时间换算为 UTC，不带时区的按 UTC 处理
UtcDatetime = Annotated[datetime, AfterValidator(
    lambda d: d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc))]


class Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


class ParseOptions(Model):
    subject: str = ""
    ocr: bool = True
    answer: bool = True
    dedupe: bool = True
    knowledge: bool = True


class StageState(Model):
    stage: ParseStage
    status: StageStatus
    note: str | None = None


class PaperMeta(Model):
    title: str = ""  # 试卷名称，取自卷首标题
    stage: str = ""
    subject: str = ""
    grade: str = ""
    paper_type: str = ""
    region: str = ""
    school_year: str = ""
    textbook: str = ""


class UsageSummary(Model):
    """AI 用量汇总。cost 为估算费用，未配置任何单价时为 null；priced=false 表示部分调用缺单价。"""

    calls: int = 0
    llm_calls: int = 0
    errors: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0  # 已包含在 completion_tokens 中
    cached_tokens: int = 0     # 已包含在 prompt_tokens 中
    total_tokens: int = 0
    pages: int = 0             # MinerU 解析页数
    duration_ms: int = 0
    estimated: bool = False    # 存在按字符数估算的用量
    cost: float | None = None
    llm_cost: float | None = None
    mineru_cost: float | None = None
    priced: bool = True
    unpriced_models: list[str] = []
    currency: str = "¥"


class UsageCall(Model):
    id: int
    provider: Literal["llm", "mineru"]
    purpose: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    cached_tokens: int
    pages: int
    duration_ms: int
    estimated: bool
    status: Literal["ok", "error"]
    cost: float | None = None
    created_at: UtcDatetime


class JobUsage(Model):
    summary: UsageSummary
    calls: list[UsageCall]


class DailyUsage(Model):
    date: str
    jobs: int
    total_tokens: int
    pages: int
    cost: float | None


class UsageOverview(Model):
    days: int
    jobs: int
    pages: int
    questions: int
    summary: UsageSummary
    cost_per_job: float | None
    cost_per_page: float | None
    cost_per_question: float | None
    tokens_per_job: int | None
    daily: list[DailyUsage]


class AnswerTask(Model):
    """AI 生成答案任务的进度。"""

    status: Literal["queued", "running", "done", "failed"]
    total: int
    done: int = 0
    failed: int = 0
    error: str | None = None
    # 本次要生成答案的题
    question_ids: list[str] = []


class GenerateAnswersRequest(Model):
    # 留空表示本卷所有缺少答案的题
    question_ids: list[str] | None = None
    # true 时覆盖已有答案（原卷或人工填写的答案也会被替换）
    overwrite: bool = False


class ParseJobOut(Model):
    id: str
    batch_id: str | None = None
    file_name: str
    file_count: int
    file_size: int
    file_type: Literal["pdf", "docx", "image"]
    page_count: int | None
    options: ParseOptions
    parser: str | None
    status: JobStatus
    progress: int
    stages: list[StageState]
    meta: PaperMeta | None
    question_count: int = 0
    review_count: int = 0
    saved_count: int = 0
    error: str | None = None
    usage: UsageSummary | None = None
    answer_task: AnswerTask | None = None
    # 已设为评测样本时为样本 id
    eval_sample_id: str | None = None
    created_at: UtcDatetime


class JobListItem(Model):
    """任务列表的精简信息。"""

    id: str
    batch_id: str | None
    file_name: str
    file_type: Literal["pdf", "docx", "image"]
    status: JobStatus
    progress: int
    # 进行中的阶段，用于列表中显示「拆题中…」
    current_stage: ParseStage | None
    question_count: int
    review_count: int
    saved_count: int
    error: str | None
    created_at: UtcDatetime


class JobListPage(Model):
    items: list[JobListItem]
    total: int
    # 排队中 + 解析中的任务数，前端据此决定是否继续轮询
    active: int


class BatchItem(Model):
    file_keys: list[str] = Field(min_length=1, max_length=50)
    file_names: list[str] = Field(min_length=1, max_length=50)


class CreateBatchRequest(Model):
    items: list[BatchItem] = Field(min_length=1, max_length=100)
    options: "ParseOptions" = ParseOptions()


class BatchOut(Model):
    id: str
    total: int
    counts: dict[str, int]
    jobs: list[JobListItem]
    created_at: UtcDatetime


class SimilarQuestion(Model):
    id: str
    # bank 校本题库 / draft 其他试卷中尚未入库的题
    source: Literal["bank", "draft"]
    type: str
    stem: str
    options: list[str]
    answer: str | None
    # 综合相似度 0–1；lexical 字面分；semantic 语义余弦（未启用向量时为 null）
    score: float
    lexical: float
    semantic: float | None
    duplicate: bool
    job_id: str | None
    file_name: str | None
    # 出处（与 DraftQuestion.source 同结构）
    origin: "QuestionSource | None" = None
    knowledge_points: list["KnowledgePointRef"] = []


class SimilarSearchRequest(Model):
    text: str = Field(min_length=2, max_length=4000)
    type: str | None = None
    limit: int = Field(default=10, ge=1, le=50)
    # bank 仅校本题库；all 含其他试卷中尚未入库的题
    scope: Literal["bank", "all"] = "bank"


class QuestionSource(Model):
    """题目出处。label 为拼好的展示文本，如「2026—2027 上 · 北京 · 海淀 · 高一 · 期中考试《…》第 3 题」。"""

    title: str
    file_name: str
    school_year: str
    region: str
    grade: str
    paper_type: str
    subject: str
    no: int | None
    page: int | None
    label: str


class SourceRegion(Model):
    page: int
    bbox: Bbox


class KnowledgePointRef(Model):
    id: str
    name: str
    # 知识树中的完整路径；不在知识树中的知识点为 null
    path: str | None = None
    in_tree: bool = False


class DraftQuestionOut(Model):
    id: str
    job_id: str
    no: int
    type: QuestionType
    score: float
    page: int
    stem: str
    options: list[str]
    answer: str | None
    analysis: str | None
    answer_source: Literal["paper", "ai", "manual"] | None = None
    answer_note: str | None = None
    knowledge_points: list[KnowledgePointRef]
    # 难度系数 0–1，越高越难，1 为最难
    coef: float
    # baseline 按题位估算 / ai 大模型评估 / manual 老师调整
    difficulty_source: Literal["baseline", "ai", "manual"] | None = None
    confidence: float
    block_ids: list[str]
    regions: list[SourceRegion]
    # 题目内配图的访问地址（几何图、函数图像等）
    images: list[str] = []
    source: QuestionSource | None = None
    duplicate_of: str | None
    status: Literal["draft", "saved"]


class DraftQuestionPatch(Model):
    type: QuestionType | None = None
    score: float | None = Field(default=None, ge=0, le=200)
    stem: str | None = Field(default=None, min_length=1)
    options: list[str] | None = None
    answer: str | None = None
    analysis: str | None = None
    knowledge_points: list[KnowledgePointRef] | None = None
    coef: float | None = Field(default=None, ge=0, le=1)


class SourceImage(Model):
    page: int
    url: str
    regions: list[SourceRegion]


class RecentUpload(Model):
    job_id: str
    file_name: str
    question_count: int
    review_count: int
    saved_count: int
    created_at: UtcDatetime


class UploadRequest(Model):
    file_name: str = Field(min_length=1, max_length=512)
    file_size: int = Field(ge=1)
    content_type: str = ""


class UploadTicket(Model):
    upload_url: str
    file_key: str


class CreateJobRequest(Model):
    file_keys: list[str] = Field(min_length=1, max_length=50)
    file_names: list[str] = Field(min_length=1, max_length=50)
    options: ParseOptions = ParseOptions()


class CommitRequest(Model):
    question_ids: list[str] = Field(min_length=1)
    # 跳过重复检查，全部保存
    force: bool = False


class CommitSkip(Model):
    """因与校本题库已有题目重复而未保存的题。"""

    question_id: str
    no: int
    duplicate_of: str
    score: float
    # 已有题目的来源
    source: str


class DuplicatePaper(Model):
    """试卷库中已有的同一份试卷。reason：same_file 相同文件 / same_title 同名试卷 / most_questions 多数题目已入库。"""

    id: str
    title: str
    reason: Literal["same_file", "same_title", "most_questions"]


class CommitResult(Model):
    # 本次保存的题数与 id
    saved_count: int
    saved_ids: list[str] = []
    skipped: list[CommitSkip] = []
    # 不为空时本次未保存任何题：整份试卷已在试卷库中，确认后用 force 重新提交
    duplicate_paper: DuplicatePaper | None = None


# ---------------- 知识树 ----------------

class KnowledgeTreeOut(Model):
    id: str
    name: str
    subject: str
    stage: str
    textbook: str
    builtin: bool
    node_count: int
    created_at: UtcDatetime


class KnowledgeTreeDetail(KnowledgeTreeOut):
    # 嵌套节点：{id, name, aliases, children}
    nodes: list[dict]


class KnowledgeTreeImport(Model):
    format: Literal["json", "csv"]
    content: str = Field(min_length=1, max_length=5_000_000)
    # 覆盖 JSON 中的同名字段；CSV 必须提供学科与学段
    name: str | None = None
    subject: str | None = None
    stage: str | None = None
    textbook: str | None = None


class KnowledgeNodeHit(Model):
    id: str
    name: str
    path: str
    score: float


# ---------------- 校本题库与试卷库 ----------------

class BankQuestionOut(Model):
    owner_id: str | None = None
    reviewed_by: str | None = None
    reviewed_at: UtcDatetime | None = None
    id: str
    type: QuestionType
    score: float
    stem: str
    options: list[str]
    answer: str | None
    analysis: str | None
    answer_source: Literal["paper", "ai", "manual"] | None = None
    knowledge_points: list[KnowledgePointRef]
    # 难度系数 0–1，越高越难
    coef: float
    images: list[str] = []
    source: QuestionSource | None = None
    # 所属试卷（即来源解析任务 id）
    paper_id: str
    created_at: UtcDatetime


class BankQuestionPage(Model):
    items: list[BankQuestionOut]
    total: int


class FacetCount(Model):
    name: str
    count: int


class PaperSummary(Model):
    """试卷库中的一份试卷：由同一份原卷入库的题组成，id 为来源解析任务 id。"""

    id: str
    title: str
    meta: PaperMeta
    file_name: str
    # 已入库题数；原卷拆出的题数（部分入库时大于前者）
    question_count: int
    source_question_count: int
    total_score: float
    type_counts: dict[str, int]
    # 按分值加权的平均难度系数
    avg_coef: float | None
    # 最近一次入库时间
    updated_at: UtcDatetime


class PaperFacets(Model):
    stages: list[FacetCount]
    grades: list[FacetCount]
    subjects: list[FacetCount]
    paper_types: list[FacetCount]
    textbooks: list[FacetCount] = []


class PaperPage(Model):
    items: list[PaperSummary]
    total: int
    # 各维度在其余筛选条件下的试卷数
    facets: PaperFacets


class QuestionFacets(Model):
    """选题「更多」筛选的可选值。"""

    regions: list[FacetCount]
    grades: list[FacetCount]
    years: list[FacetCount]


class ChapterSection(Model):
    id: str
    name: str
    # 对应的知识点名称
    knowledge: list[str]


class ChapterItem(Model):
    id: str
    name: str
    sections: list[ChapterSection]


class TextbookBook(Model):
    id: str
    name: str
    grade: str
    # 平台标注的新教材 / 旧教材（新教材目录未上线时退用旧教材）
    edition: str = ""
    chapters: list[ChapterItem]


class TextbookVersion(Model):
    name: str
    # 适用地区，如「厦门」（该地区现用版本）
    region: str = ""
    books: list[TextbookBook]


class PaperDetail(PaperSummary):
    questions: list[BankQuestionOut]


# ---------------- 评测 ----------------

class EvalSampleOut(Model):
    id: str
    job_id: str
    file_name: str
    question_count: int
    created_at: UtcDatetime


class EvalRunCreate(Model):
    # 留空表示全部样本
    sample_ids: list[str] | None = None


class EvalRunOut(Model):
    id: str
    status: Literal["queued", "running", "done", "failed"]
    total: int
    done: int
    config: dict
    metrics: dict | None
    details: list[dict]
    error: str | None
    created_at: UtcDatetime


# 前向引用（SimilarQuestion 定义在 QuestionSource、KnowledgePointRef 之前）
SimilarQuestion.model_rebuild()


# ---------------- AI 组卷（Demo） ----------------

class ComposeMessage(Model):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class ComposeRequest(Model):
    stage: str
    subject: str
    # 老师当前的设置；对话中要求改变时以大模型整理的蓝图为准
    total: int = Field(default=100, ge=10, le=300)
    difficulty: float = Field(default=0.45, ge=0.05, le=0.95)
    # 完整对话，最后一条为老师本次的要求
    messages: list[ComposeMessage] = Field(min_length=1, max_length=20)


class ComposeItem(Model):
    question: BankQuestionOut
    score: int


class ComposeSection(Model):
    type: QuestionType
    score: int
    items: list[ComposeItem]


class ComposeFocus(Model):
    """重点考查的知识点及选入的题数。"""

    name: str
    path: str | None = None
    weight: int
    count: int


class ComposeResult(Model):
    # 给老师的组卷说明
    reply: str
    title: str
    total: int
    # 目标平均难度
    difficulty: float
    # 实际平均难度（按分值加权）
    actual_difficulty: float
    sections: list[ComposeSection]
    focus: list[ComposeFocus]
    # 题库不足等提示
    gaps: list[str]
    # 需求是否由大模型理解；未配置或调用失败时为 false（按关键词匹配知识点）
    ai: bool
