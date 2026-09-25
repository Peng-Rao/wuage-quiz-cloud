"""接口数据结构，与前端 src/api/parse/types.ts 一一对应（JSON 字段为 camelCase）。"""

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

QuestionType = Literal["单选题", "多选题", "填空题", "解答题"]
QUESTION_TYPES: tuple[QuestionType, ...] = ("单选题", "多选题", "填空题", "解答题")
ParseStage = Literal["ocr", "classify", "segment", "knowledge", "difficulty"]
PARSE_STAGES: tuple[ParseStage, ...] = ("ocr", "classify", "segment", "knowledge", "difficulty")
StageStatus = Literal["pending", "running", "done", "skipped", "failed"]
JobStatus = Literal["uploading", "queued", "running", "done", "failed"]

REVIEW_CONFIDENCE = 0.8

Bbox = tuple[float, float, float, float]

# SQLite 不保存时区，读出的是 UTC 的 naive 时间；补上时区，前端才能正确换算为本地时间
UtcDatetime = Annotated[datetime, AfterValidator(lambda d: d if d.tzinfo else d.replace(tzinfo=timezone.utc))]


class Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


class ParseOptions(Model):
    ocr: bool = True
    answer: bool = True
    dedupe: bool = True
    knowledge: bool = True


class StageState(Model):
    stage: ParseStage
    status: StageStatus
    note: str | None = None


class PaperMeta(Model):
    stage: str = ""
    subject: str = ""
    grade: str = ""
    paper_type: str = ""
    region: str = ""
    school_year: str = ""
    textbook: str = ""


class ParseJobOut(Model):
    id: str
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
    created_at: UtcDatetime


class SourceRegion(Model):
    page: int
    bbox: Bbox


class KnowledgePointRef(Model):
    id: str
    name: str


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
    knowledge_points: list[KnowledgePointRef]
    coef: float
    confidence: float
    block_ids: list[str]
    regions: list[SourceRegion]
    # 题目内配图的访问地址（几何图、函数图像等）
    images: list[str] = []
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


class CommitResult(Model):
    saved_count: int
