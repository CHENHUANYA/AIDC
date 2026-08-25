"""Stable response contracts for externally consumed API endpoints."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiErrorResponse(BaseModel):
    status: Literal["error"]
    message: str


class StatusOkResponse(BaseModel):
    status: Literal["ok"]


class PublicUserResponse(BaseModel):
    user_id: str
    name: str
    role: str
    line_scope: list[str]
    team: str
    active: bool
    created_at: str
    updated_at: str


class BootstrapUserResponse(BaseModel):
    user_id: str
    name: str
    role: str
    line_scope: list[str]
    team: str


class LoginSuccessResponse(StatusOkResponse):
    token: str
    expires_at: str
    user: PublicUserResponse


class LoginConfigResponse(StatusOkResponse):
    production: bool
    initial_password_configured: bool
    bootstrap_users: list[BootstrapUserResponse]


class CurrentUserSuccessResponse(StatusOkResponse):
    user: PublicUserResponse


class UsersResponse(BaseModel):
    users: list[PublicUserResponse]


class UserCreatedResponse(StatusOkResponse):
    user: PublicUserResponse


class UserUpdatedResponse(UserCreatedResponse):
    sessions_revoked: int


class PasswordResetResponse(UserCreatedResponse):
    sessions_revoked: bool


class SessionResponse(BaseModel):
    token_prefix: str
    user_id: str
    role: str
    created_at: str
    expires_at: str


class SessionsResponse(StatusOkResponse):
    total: int
    sessions: list[SessionResponse]


class SessionsRevokedResponse(StatusOkResponse):
    revoked: int


class HistoryChangeResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    field: str
    from_value: Any = Field(alias="from")
    to_value: Any = Field(alias="to")


class HistoryEventResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    action: str
    user_id: str
    fields: list[str]
    created_at: str
    from_status: str = ""
    to_status: str = ""
    changes: list[HistoryChangeResponse] = Field(default_factory=list)
    request_id: str = Field(default="", alias="_request_id")


class OperatorNoteResponse(BaseModel):
    note: str
    created_by: str
    created_at: str


class IssueResponse(BaseModel):
    issue_id: str
    source: str
    manual: str
    machine_id: str
    line_id: str
    alarm_code: str
    description: str
    original_description: str
    severity: Literal["info", "low", "medium", "high", "critical"]
    status: Literal["open", "assigned", "in_progress", "completed", "verified", "cancelled"]
    created_by: str
    updated_by: str
    assigned_to: str
    work_order_id: str
    rag_suggestion: str
    rag_answer_id: str
    operator_notes: list[OperatorNoteResponse]
    issue_history: list[HistoryEventResponse]
    resolution_summary: str
    created_at: str
    updated_at: str
    completed_at: str
    version: int


class IssueSuccessResponse(StatusOkResponse):
    issue: IssueResponse


class IssueMutationResponse(IssueSuccessResponse):
    work_order: dict[str, Any] | None


class IssueEscalatedResponse(IssueSuccessResponse):
    created: bool
    work_order: dict[str, Any] | None = None
    work_order_id: str = ""


class IssuesResponse(BaseModel):
    total: int
    issues: list[IssueResponse]


class IssuesPageResponse(StatusOkResponse):
    issues: list[IssueResponse]
    total: int
    limit: int
    next_cursor: str
    has_more: bool


class MetricCountResponse(BaseModel):
    count: int


class MachineCountResponse(MetricCountResponse):
    machine_id: str


class DailyCountResponse(MetricCountResponse):
    date: str


class IssueStatsResponse(BaseModel):
    total: int
    unresolved: int
    by_status: dict[str, int]
    by_source: dict[str, int]
    by_line: dict[str, int]
    top_machines: list[MachineCountResponse]
    daily_created: list[DailyCountResponse]


class IssueHistoryResponse(StatusOkResponse):
    issue_id: str
    issue_history: list[HistoryEventResponse]
    work_order_id: str
    work_order_history: list[HistoryEventResponse]


class WorkOrderResponse(BaseModel):
    id: str
    issue_id: str
    alarm_code: str
    manual: str
    machine_id: str
    status: Literal["pending", "assigned", "in_progress", "completed", "verified", "cancelled"]
    priority: Literal["low", "medium", "high", "critical"]
    assigned_to: str
    created_by: str
    updated_by: str
    accepted_by: str
    completed_by: str
    verified_by: str
    description: str
    resolution: str
    notes: str
    root_cause: str
    repair_action: str
    failure_category: str
    llm_correctness: str
    llm_coverage: str
    llm_missing_info: str
    llm_expected_fix: str
    llm_answer_used: bool
    kb_candidate: bool
    kb_review_status: Literal[
        "not_ready",
        "pending_review",
        "needs_revision",
        "rejected",
        "ingested",
        "validation_failed",
    ]
    kb_review_note: str
    kb_reviewed_by: str
    kb_reviewed_at: str
    kb_ingested_at: str
    kb_ingest_result: dict[str, Any] | None
    kb_duplicate_of: str
    rag_suggestion: str
    rag_answer_id: str
    source: str
    created_at: str
    updated_at: str
    completed_at: str
    version: int
    work_order_history: list[HistoryEventResponse]
    deleted_at: str = ""
    archive_file: str = ""


class WorkOrderSuccessResponse(StatusOkResponse):
    order: WorkOrderResponse


class KnowledgeReviewSummaryResponse(BaseModel):
    candidate: bool
    review_status: str


class WorkOrderMutationResponse(WorkOrderSuccessResponse):
    knowledge_review: KnowledgeReviewSummaryResponse
    issue: IssueResponse | None


class WorkOrdersResponse(BaseModel):
    total: int
    orders: list[WorkOrderResponse]


class WorkOrdersPageResponse(StatusOkResponse):
    orders: list[WorkOrderResponse]
    total: int
    limit: int
    next_cursor: str
    has_more: bool


class WorkOrderStatsResponse(BaseModel):
    total: int
    by_status: dict[str, int]
    by_priority: dict[str, int]
    by_manual: dict[str, int]
    by_source: dict[str, int]
    avg_hours: float
    median_hours: float
    today_created: int
    today_completed: int
    open_orders: int
    assigned_orders: int
    unassigned_open: int
    overdue_open: int
    closed_orders: int
    pending_verification: int
    completion_rate: float
    daily_created: list[DailyCountResponse]
    daily_completed: list[DailyCountResponse]
    top_machines: list[MachineCountResponse]
    by_kb_review_status: dict[str, int]
    pending_knowledge_review: int
    status_labels: dict[str, str]
    priority_labels: dict[str, str]


class WorkOrderArchiveFileResponse(BaseModel):
    file: str
    count: int
    updated_at: str


class WorkOrderArchiveResponse(StatusOkResponse):
    archives: list[WorkOrderArchiveFileResponse]
    orders: list[WorkOrderResponse]
    total: int


class WorkOrderHistoryResponse(StatusOkResponse):
    work_order_id: str
    work_order_history: list[HistoryEventResponse]
    issue_id: str
    issue_history: list[HistoryEventResponse]


class WorkOrderDeleteResponse(StatusOkResponse):
    deleted: str
    soft_deleted: bool


class KnowledgeReviewResponse(WorkOrderSuccessResponse):
    ingest: dict[str, Any] | None


class KnowledgeReviewErrorResponse(ApiErrorResponse):
    order: WorkOrderResponse | None = None
    ingest: dict[str, Any] | None = None
    duplicate_of: str = ""


class WorkOrderImportResponse(StatusOkResponse):
    filename: str
    imported: int
    skipped: int
    errors: list[str]
    candidate_count: int
    feedback_count: int


class AlarmEntryResponse(BaseModel):
    alarm_code: str
    manual: str = "808d"
    machine_id: str | None = ""
    line_id: str = ""
    source: str | None = ""
    severity: str = "info"
    description: str = ""
    external_event_id: str = ""
    time: str = ""
    date: str = ""
    alarm_type: str = ""
    category: str = ""
    rag_preview: str = ""
    issue_id: str = ""
    work_order_id: str = ""


class AlarmTriggerResponse(StatusOkResponse):
    duplicate: bool
    external_event_id: str
    alarm: AlarmEntryResponse
    issue: IssueResponse | None
    work_order: WorkOrderResponse | None


class PendingAlarmsResponse(BaseModel):
    alarms: list[AlarmEntryResponse]


class AlarmStatsResponse(BaseModel):
    total: int
    today: int
    by_manual: dict[str, int]
    by_source: dict[str, int]
    daily: list[DailyCountResponse]
    recent: list[AlarmEntryResponse]


class FeedbackEntryResponse(BaseModel):
    time: str = ""
    query: str = ""
    collection: str = ""
    alarm_code: str | None = ""
    feedback: str = ""
    answer_id: str | None = ""
    issue_id: str | None = ""
    work_order_id: str | None = ""
    user_id: str | None = ""
    role: str | None = ""
    correctness: str | None = ""
    coverage: str | None = ""
    missing_info: str | None = ""
    expected_fix: str | None = ""
    kb_candidate: bool | None = False


class FeedbackStatsResponse(BaseModel):
    total: int
    good: int
    bad: int
    rate: str
    correctness_total: int
    correct: int
    correctness_rate: str
    coverage_total: int
    complete: int
    coverage_rate: str
    technician_feedback: int
    entries: list[FeedbackEntryResponse]


class QueryLogEntryResponse(BaseModel):
    time: str = ""
    date: str = ""
    collection: str = ""
    query: str | None = ""
    source: str = ""
    elapsed_ms: int = 0


class QueryStatsResponse(BaseModel):
    total: int
    today: int
    avg_ms: int
    p95_ms: int
    p99_ms: int
    top_codes: list[tuple[str, int]]
    by_collection: dict[str, int]
    recent: list[QueryLogEntryResponse]


class ErrorLogEntryResponse(BaseModel):
    time: str = ""
    collection: str = ""
    query: str = ""
    error: str = ""
    rag_preview: str = ""


class ErrorStatsResponse(BaseModel):
    recent: list[ErrorLogEntryResponse]
    total: int


class RuntimeRouteMetricsResponse(BaseModel):
    method: str
    route: str
    count: int
    errors: int
    avg_ms: float
    max_ms: float


class RuntimeHttpMetricsResponse(BaseModel):
    requests: int
    errors: int
    server_errors: int
    timeouts: int
    slow_requests: int
    slow_request_ms: int
    avg_ms: float
    max_ms: float
    duration_buckets: dict[str, int]
    routes: list[RuntimeRouteMetricsResponse]


class RuntimeAuthMetricsResponse(BaseModel):
    login_attempts: int
    login_successes: int
    login_failures: int
    throttle_triggers: int


class RuntimeRagMetricsResponse(BaseModel):
    requests: int
    errors: int
    streaming_requests: int
    avg_retrieval_ms: float
    avg_model_ms: float
    avg_total_ms: float
    max_total_ms: float
    providers: dict[str, int]
    outcomes: dict[str, int]


class RuntimePostgresMetricsResponse(BaseModel):
    enabled: bool
    status: Literal["ok", "not-required", "unavailable"]
    pool_size: int | None = None
    checked_in: int | None = None
    checked_out: int | None = None
    overflow: int | None = None


class RuntimeMetricsResponse(StatusOkResponse):
    generated_at: str
    uptime_seconds: float
    http: RuntimeHttpMetricsResponse
    auth: RuntimeAuthMetricsResponse
    rag: RuntimeRagMetricsResponse
    postgres: RuntimePostgresMetricsResponse


class SystemSettingsResponse(BaseModel):
    default_manual: str
    session_hours: int
    allow_operator_reopen: bool
    updated_by: str
    updated_at: str
    revision: str


class SystemSettingsEnvelope(StatusOkResponse):
    settings: SystemSettingsResponse


class ActionNumberResponse(BaseModel):
    action_number: str
    reaction: str
    effect: str
    recovery: str
    severity: str
    note: str


class ErrorCodeResponse(BaseModel):
    hex: str
    code: str
    meaning: str
    cause: str
    remedy: str
    severity: str


class ActionNumbersResponse(BaseModel):
    collection: str
    total: int
    entries: list[ActionNumberResponse]


class ErrorCodesResponse(BaseModel):
    collection: str
    total: int
    entries: list[ErrorCodeResponse]


class CitationResponse(BaseModel):
    id: str
    rank: int = 0
    code: str = ""
    title: str = ""
    page: Any = ""
    source: str = ""
    source_file: str = ""
    source_hash: str = ""
    doc_id: str = ""
    source_id: str = ""
    section_id: str = ""
    locator: str = ""
    official_source: bool = False
    publisher: str = ""
    document_title: str = ""
    edition: str = ""
    kind: str = ""
    excerpt: str = ""


class RagMetadataResponse(BaseModel):
    collection: str
    query: str
    citation_count: int
    citations: list[CitationResponse]
    answer_id: str = ""


class ChatMessageResponse(BaseModel):
    role: Literal["assistant"]
    content: str


class ChatChoiceResponse(BaseModel):
    index: int
    message: ChatMessageResponse
    finish_reason: str


class TokenUsageResponse(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class OpenAIChatResponse(BaseModel):
    id: str
    object: Literal["chat.completion"]
    created: int
    model: str
    choices: list[ChatChoiceResponse]
    usage: TokenUsageResponse
    rag: RagMetadataResponse | None = None


class RetrievalResultResponse(CitationResponse):
    text: str


class RetrieveResponse(BaseModel):
    collection: str
    query: str
    ready: bool
    tokenizer_version: str
    results: list[RetrievalResultResponse]
    result_count: int = 0
    error: str = ""


class RagAnswerResponse(BaseModel):
    answer_id: str
    query: str
    collection: str
    answer: str
    answer_state: Literal["complete", "fallback", "unavailable"]
    citations: list[CitationResponse]
    provider: str
    model: str
    tokenizer_version: str
    retrieval_version: str
    elapsed_ms: int
    created_by: str
    created_at: str


class RagAnswerEnvelope(StatusOkResponse):
    answer: RagAnswerResponse


class LookupMetadataResponse(BaseModel):
    collection: str
    code: str
    page: Any = ""
    title: str
    source: str
    source_file: str
    doc_id: str
    kind: str
    imported_at: str


class LookupResponse(BaseModel):
    found: bool
    error: str = ""
    code: str = ""
    page: Any = ""
    title: str = ""
    text: str = ""
    metadata: LookupMetadataResponse | None = None
    alarm_type: str = ""
    category: str = ""
    severity: str = ""


class ModelEntryResponse(BaseModel):
    id: str
    object: Literal["model"]
    owned_by: str


class ModelsResponse(BaseModel):
    object: Literal["list"]
    data: list[ModelEntryResponse]


class CollectionHealthResponse(BaseModel):
    ready: bool
    alarms_indexed: int
    retrieval_runtime: dict[str, Any]
    traceability: dict[str, Any]


class HealthResponse(StatusOkResponse):
    llm_provider: str
    ollama_model: str
    school_api_model: str
    school_api_fallback_to_ollama: bool
    last_llm_source: str
    model_cache: dict[str, Any]
    collections: dict[str, CollectionHealthResponse]


class ReadyChecksResponse(BaseModel):
    database: Literal["ok", "not-required"]
    vector_store: Literal["ok", "not-required"]


class ReadyResponse(StatusOkResponse):
    checks: ReadyChecksResponse


class ReadyUnavailableChecksResponse(BaseModel):
    database: Literal["ok", "not-required", "unavailable"]
    vector_store: Literal["ok", "not-required", "unavailable"]


class ReadyUnavailableResponse(BaseModel):
    status: Literal["unavailable"]
    checks: ReadyUnavailableChecksResponse


class DuplicateResponse(BaseModel):
    status: Literal["duplicate"]
    message: str
    doc_id: str | None = None
    source_hash: str = ""


class IngestPdfResponse(StatusOkResponse):
    collection: str
    filename: str
    doc_id: str
    source_hash: str
    alarms_added: int
    general_added: int
    total_added: int
    total_in_collection: int


class IngestTextResponse(StatusOkResponse):
    collection: str
    doc_id: str
    sections_added: int
    total_in_collection: int


class IngestLogResponse(BaseModel):
    collection: str | None = None
    entries: list[dict[str, Any]]


class CollectionsResponse(BaseModel):
    collections: list[dict[str, Any]]


class DocumentsResponse(BaseModel):
    collection: str
    summary: dict[str, Any]
    documents: list[dict[str, Any]]


class DocumentDeleteResponse(StatusOkResponse):
    removed_sections: int
    remaining: int


class RebuildSyncResponse(StatusOkResponse):
    sections: int


class RebuildJobResponse(BaseModel):
    status: Literal["ok", "accepted"]
    job_id: str
    collection: str
    state: str
    phase: str
    processed_sections: int
    total_sections: int
    sections: int
    percent: float
    error: str
    created_at: str
    updated_at: str
    finished_at: str


API_ERROR_RESPONSES = {
    400: {"model": ApiErrorResponse, "description": "Invalid request"},
    401: {"model": ApiErrorResponse, "description": "Authentication required"},
    403: {"model": ApiErrorResponse, "description": "Permission denied"},
    404: {"model": ApiErrorResponse, "description": "Resource not found"},
    409: {"model": ApiErrorResponse, "description": "Duplicate or concurrent update"},
    410: {"model": ApiErrorResponse, "description": "Resource was deleted"},
    503: {"model": ApiErrorResponse, "description": "Dependency not ready"},
}
