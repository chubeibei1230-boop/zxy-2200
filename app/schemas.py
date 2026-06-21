from datetime import datetime, date
from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    username: Optional[str] = None


class UserBase(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    full_name: Optional[str] = None
    role: str
    store_id: Optional[int] = None


class UserCreate(UserBase):
    password: str = Field(..., min_length=6)


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    role: Optional[str] = None
    store_id: Optional[int] = None
    is_active: Optional[bool] = None
    password: Optional[str] = None


class UserResponse(UserBase):
    id: int
    is_active: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class StoreBase(BaseModel):
    store_code: str = Field(..., max_length=20)
    store_name: str = Field(..., max_length=100)
    address: Optional[str] = None
    manager_name: Optional[str] = None
    phone: Optional[str] = None


class StoreCreate(StoreBase):
    pass


class StoreUpdate(BaseModel):
    store_code: Optional[str] = None
    store_name: Optional[str] = None
    address: Optional[str] = None
    manager_name: Optional[str] = None
    phone: Optional[str] = None
    is_active: Optional[bool] = None


class StoreResponse(StoreBase):
    id: int
    is_active: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class IngredientCategoryBase(BaseModel):
    category_code: str = Field(..., max_length=20)
    category_name: str = Field(..., max_length=100)
    unit: Optional[str] = "kg"
    description: Optional[str] = None


class IngredientCategoryCreate(IngredientCategoryBase):
    pass


class IngredientCategoryUpdate(BaseModel):
    category_code: Optional[str] = None
    category_name: Optional[str] = None
    unit: Optional[str] = None
    description: Optional[str] = None


class IngredientCategoryResponse(IngredientCategoryBase):
    id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class BatchRuleBase(BaseModel):
    rule_name: str = Field(..., max_length=100)
    ingredient_category_id: int
    min_batch_size: float
    max_batch_size: float
    required_wash_duration_minutes: Optional[int] = 10
    high_discard_threshold: Optional[float] = 0.15
    temperature_min: Optional[float] = 2.0
    temperature_max: Optional[float] = 8.0
    temperature_log_interval_minutes: Optional[int] = 30
    qc_required_within_hours: Optional[int] = 4


class BatchRuleCreate(BatchRuleBase):
    pass


class BatchRuleUpdate(BaseModel):
    rule_name: Optional[str] = None
    ingredient_category_id: Optional[int] = None
    min_batch_size: Optional[float] = None
    max_batch_size: Optional[float] = None
    required_wash_duration_minutes: Optional[int] = None
    high_discard_threshold: Optional[float] = None
    temperature_min: Optional[float] = None
    temperature_max: Optional[float] = None
    temperature_log_interval_minutes: Optional[int] = None
    qc_required_within_hours: Optional[int] = None
    is_active: Optional[bool] = None


class BatchRuleResponse(BatchRuleBase):
    id: int
    is_active: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class FreshnessRuleBase(BaseModel):
    ingredient_category_id: int
    stage: str = Field(..., max_length=50)
    max_hours: float
    description: Optional[str] = None


class FreshnessRuleCreate(FreshnessRuleBase):
    pass


class FreshnessRuleUpdate(BaseModel):
    ingredient_category_id: Optional[int] = None
    stage: Optional[str] = None
    max_hours: Optional[float] = None
    description: Optional[str] = None


class FreshnessRuleResponse(FreshnessRuleBase):
    id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ProductionStationBase(BaseModel):
    station_code: str = Field(..., max_length=20)
    station_name: str = Field(..., max_length=100)
    description: Optional[str] = None
    sort_order: Optional[int] = 0


class ProductionStationCreate(ProductionStationBase):
    pass


class ProductionStationUpdate(BaseModel):
    station_code: Optional[str] = None
    station_name: Optional[str] = None
    description: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


class ProductionStationResponse(ProductionStationBase):
    id: int
    is_active: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class QCTemplateBase(BaseModel):
    template_name: str = Field(..., max_length=100)
    ingredient_category_id: int
    check_items: str
    scoring_standard: Optional[str] = None
    disposition_options: Optional[str] = None


class QCTemplateCreate(QCTemplateBase):
    pass


class QCTemplateUpdate(BaseModel):
    template_name: Optional[str] = None
    ingredient_category_id: Optional[int] = None
    check_items: Optional[str] = None
    scoring_standard: Optional[str] = None
    disposition_options: Optional[str] = None
    is_active: Optional[bool] = None


class QCTemplateResponse(QCTemplateBase):
    id: int
    is_active: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class MaterialBatchBase(BaseModel):
    batch_no: str = Field(..., max_length=50)
    store_id: int
    ingredient_category_id: int
    quantity: float
    unit: Optional[str] = "kg"
    supplier_batch_no: Optional[str] = None
    production_date: Optional[date] = None
    expiry_date: Optional[date] = None


class MaterialBatchCreate(MaterialBatchBase):
    pass


class MaterialBatchUpdateStatus(BaseModel):
    status: str
    abnormal_remark: Optional[str] = None


class MaterialBatchResponse(MaterialBatchBase):
    id: int
    status: str
    arrival_time: Optional[datetime] = None
    wash_complete_time: Optional[datetime] = None
    production_start_time: Optional[datetime] = None
    production_complete_time: Optional[datetime] = None
    sale_start_time: Optional[datetime] = None
    discard_time: Optional[datetime] = None
    final_disposition: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class MaterialBatchDetailResponse(MaterialBatchResponse):
    store: Optional[StoreResponse] = None
    category: Optional[IngredientCategoryResponse] = None
    model_config = ConfigDict(from_attributes=True)


class MaterialAcceptanceBase(BaseModel):
    batch_id: int
    appearance_check: Optional[bool] = True
    temperature_check: Optional[bool] = True
    packaging_check: Optional[bool] = True
    certificate_check: Optional[bool] = True
    accepted_quantity: float
    abnormal_remark: Optional[str] = None
    is_accepted: Optional[bool] = True


class MaterialAcceptanceCreate(MaterialAcceptanceBase):
    pass


class MaterialAcceptanceResponse(MaterialAcceptanceBase):
    id: int
    store_id: int
    accepted_by: int
    acceptance_time: datetime
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ProductionRecordBase(BaseModel):
    batch_id: int
    station_id: int
    cups_produced: Optional[int] = 0
    cups_discarded: Optional[int] = 0
    discard_reason: Optional[str] = None
    abnormal_remark: Optional[str] = None


class ProductionRecordCreate(ProductionRecordBase):
    pass


class ProductionRecordUpdate(BaseModel):
    end_time: Optional[datetime] = None
    cups_produced: Optional[int] = None
    cups_discarded: Optional[int] = None
    discard_reason: Optional[str] = None
    abnormal_remark: Optional[str] = None


class ProductionRecordResponse(ProductionRecordBase):
    id: int
    store_id: int
    operator_id: int
    start_time: datetime
    end_time: Optional[datetime] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class TemperatureLogBase(BaseModel):
    batch_id: int
    temperature: float
    location: Optional[str] = None
    remark: Optional[str] = None


class TemperatureLogCreate(TemperatureLogBase):
    pass


class TemperatureLogResponse(TemperatureLogBase):
    id: int
    log_time: datetime
    recorded_by: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class QCInspectionBase(BaseModel):
    batch_id: int
    template_id: Optional[int] = None
    appearance_score: Optional[float] = None
    taste_score: Optional[float] = None
    texture_score: Optional[float] = None
    overall_score: Optional[float] = None
    taste_deviation: Optional[str] = None
    check_result: Optional[str] = None
    disposition: Optional[str] = None
    disposition_note: Optional[str] = None


class QCInspectionCreate(QCInspectionBase):
    pass


class QCInspectionUpdate(BaseModel):
    appearance_score: Optional[float] = None
    taste_score: Optional[float] = None
    texture_score: Optional[float] = None
    overall_score: Optional[float] = None
    taste_deviation: Optional[str] = None
    check_result: Optional[str] = None
    disposition: Optional[str] = None
    disposition_note: Optional[str] = None
    review_time: Optional[datetime] = None


class QCInspectionResponse(QCInspectionBase):
    id: int
    store_id: int
    inspector_id: int
    inspection_time: datetime
    review_time: Optional[datetime] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class AnomalyEventBase(BaseModel):
    batch_id: int
    anomaly_type: str
    severity: Optional[str] = "medium"
    description: Optional[str] = None


class AnomalyEventResolve(BaseModel):
    resolution_note: str


class AnomalyEventResponse(AnomalyEventBase):
    id: int
    detected_at: datetime
    resolved_at: Optional[datetime] = None
    resolved_by: Optional[int] = None
    is_resolved: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class BatchFilterParams(BaseModel):
    store_id: Optional[int] = None
    ingredient_category_id: Optional[int] = None
    batch_no: Optional[str] = None
    status: Optional[str] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None


class RecordFilterParams(BaseModel):
    store_id: Optional[int] = None
    ingredient_category_id: Optional[int] = None
    batch_no: Optional[str] = None
    station_id: Optional[int] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None


class AbnormalRankingItem(BaseModel):
    ingredient_category_id: int
    category_name: str
    anomaly_count: int
    batch_count: int
    anomaly_rate: float


class QCTodoItem(BaseModel):
    batch_id: int
    batch_no: str
    store_id: int
    store_name: str
    category_name: str
    production_complete_time: Optional[datetime]
    pending_hours: float
    priority: str


class DiscardTrendItem(BaseModel):
    date: date
    store_id: int
    store_name: str
    total_cups: int
    discarded_cups: int
    discard_rate: float


class PaginatedResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List


class RecheckApplicationBase(BaseModel):
    batch_id: int
    source_qc_inspection_id: Optional[int] = None
    source_anomaly_event_id: Optional[int] = None
    recheck_source: str = "store_initiative"
    recheck_reason: str = "other"
    reason_detail: Optional[str] = None
    supplementary_note: Optional[str] = None
    deadline_hours: Optional[int] = 24


class RecheckApplicationCreate(RecheckApplicationBase):
    pass


class RecheckApplicationAssign(BaseModel):
    assigned_to: int


class RecheckApplicationExecute(BaseModel):
    recheck_template_id: Optional[int] = None
    appearance_score: Optional[float] = None
    taste_score: Optional[float] = None
    texture_score: Optional[float] = None
    overall_score: Optional[float] = None
    recheck_check_result: Optional[str] = None
    recheck_disposition_note: Optional[str] = None
    recheck_result: str


class RecheckApplicationCancel(BaseModel):
    cancel_reason: str


class RecheckApplicationUpdate(BaseModel):
    supplementary_note: Optional[str] = None
    deadline_hours: Optional[int] = None


class RecheckApplicationResponse(BaseModel):
    id: int
    application_no: str
    batch_id: int
    store_id: int
    source_qc_inspection_id: Optional[int] = None
    source_anomaly_event_id: Optional[int] = None
    recheck_source: str
    recheck_reason: str
    reason_detail: Optional[str] = None
    supplementary_note: Optional[str] = None
    status: str
    recheck_result: Optional[str] = None
    deadline_hours: int
    applied_by: int
    applied_at: datetime
    assigned_to: Optional[int] = None
    assigned_at: Optional[datetime] = None
    recheck_started_at: Optional[datetime] = None
    recheck_completed_at: Optional[datetime] = None
    recheck_template_id: Optional[int] = None
    appearance_score: Optional[float] = None
    taste_score: Optional[float] = None
    texture_score: Optional[float] = None
    overall_score: Optional[float] = None
    recheck_check_result: Optional[str] = None
    recheck_disposition_note: Optional[str] = None
    rechecked_by: Optional[int] = None
    cancelled_by: Optional[int] = None
    cancelled_at: Optional[datetime] = None
    cancel_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class RecheckApplicationDetailResponse(RecheckApplicationResponse):
    batch: Optional[MaterialBatchDetailResponse] = None
    store: Optional[StoreResponse] = None
    applicant: Optional[UserResponse] = None
    assignee: Optional[UserResponse] = None
    rechecker: Optional[UserResponse] = None
    model_config = ConfigDict(from_attributes=True)


class RecheckFilterParams(BaseModel):
    store_id: Optional[int] = None
    batch_id: Optional[int] = None
    batch_no: Optional[str] = None
    ingredient_category_id: Optional[int] = None
    status: Optional[str] = None
    recheck_result: Optional[str] = None
    recheck_source: Optional[str] = None
    recheck_reason: Optional[str] = None
    assigned_to: Optional[int] = None
    is_overdue: Optional[bool] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None


class RecheckStatsItem(BaseModel):
    status: str
    count: int


class RecheckResultDistributionItem(BaseModel):
    result: str
    count: int
    percentage: float


class RecheckOverdueItem(BaseModel):
    application_id: int
    application_no: str
    batch_id: int
    batch_no: str
    store_id: int
    store_name: str
    category_name: str
    status: str
    applied_at: datetime
    deadline_hours: int
    overdue_hours: float
    priority: str


class RecheckOverviewStats(BaseModel):
    total: int
    pending: int
    in_progress: int
    passed: int
    failed: int
    cancelled: int
    overdue: int
    avg_processing_hours: float
