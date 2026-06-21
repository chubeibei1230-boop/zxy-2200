import enum
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, ForeignKey, Text, Boolean, Date, Time
)
from sqlalchemy.orm import relationship
from app.database import Base


class UserRole(str, enum.Enum):
    HQ_ADMIN = "hq_admin"
    STORE_STAFF = "store_staff"
    QC_STAFF = "qc_staff"


class BatchStatus(str, enum.Enum):
    PENDING_ACCEPTANCE = "pending_acceptance"
    READY_FOR_PRODUCTION = "ready_for_production"
    IN_PRODUCTION = "in_production"
    PENDING_QC = "pending_qc"
    ANOMALY_HOLD = "anomaly_hold"
    READY_FOR_SALE = "ready_for_sale"
    DISCARDED = "discarded"


class AnomalyType(str, enum.Enum):
    FRESHNESS_TIMEOUT = "freshness_timeout"
    HIGH_DISCARD_RATIO = "high_discard_ratio"
    QC_MISSING = "qc_missing"
    TEMPERATURE_GAP = "temperature_gap"
    CONCENTRATED_ANOMALY = "concentrated_anomaly"


class RecheckStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    PASSED = "passed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RecheckSource(str, enum.Enum):
    QC_INSPECTION = "qc_inspection"
    ANOMALY_HOLD = "anomaly_hold"
    STORE_INITIATIVE = "store_initiative"
    HQ_ASSIGN = "hq_assign"


class RecheckResult(str, enum.Enum):
    QUALIFIED = "qualified"
    UNQUALIFIED = "unqualified"
    FURTHER_RECHECK = "further_recheck"


class RecheckReason(str, enum.Enum):
    SCORE_BORDERLINE = "score_borderline"
    TASTE_DEVIATION = "taste_deviation"
    APPEARANCE_ISSUE = "appearance_issue"
    TEMPERATURE_ABNORMAL = "temperature_abnormal"
    ANOMALY_EVENT = "anomaly_event"
    CUSTOMER_COMPLAINT = "customer_complaint"
    OTHER = "other"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(100))
    role = Column(String(20), nullable=False, default=UserRole.STORE_STAFF.value)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    store = relationship("Store", back_populates="users")


class Store(Base):
    __tablename__ = "stores"

    id = Column(Integer, primary_key=True, index=True)
    store_code = Column(String(20), unique=True, index=True, nullable=False)
    store_name = Column(String(100), nullable=False)
    address = Column(String(255))
    manager_name = Column(String(50))
    phone = Column(String(20))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    users = relationship("User", back_populates="store")
    batches = relationship("MaterialBatch", back_populates="store")
    acceptance_records = relationship("MaterialAcceptance", back_populates="store")
    production_records = relationship("ProductionRecord", back_populates="store")
    qc_inspections = relationship("QCInspection", back_populates="store")


class IngredientCategory(Base):
    __tablename__ = "ingredient_categories"

    id = Column(Integer, primary_key=True, index=True)
    category_code = Column(String(20), unique=True, index=True, nullable=False)
    category_name = Column(String(100), nullable=False)
    unit = Column(String(20), default="kg")
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    freshness_rules = relationship("FreshnessRule", back_populates="category")
    batches = relationship("MaterialBatch", back_populates="category")


class BatchRule(Base):
    __tablename__ = "batch_rules"

    id = Column(Integer, primary_key=True, index=True)
    rule_name = Column(String(100), nullable=False)
    ingredient_category_id = Column(Integer, ForeignKey("ingredient_categories.id"), nullable=False)
    min_batch_size = Column(Float, nullable=False)
    max_batch_size = Column(Float, nullable=False)
    required_wash_duration_minutes = Column(Integer, default=10)
    high_discard_threshold = Column(Float, default=0.15)
    temperature_min = Column(Float, default=2.0)
    temperature_max = Column(Float, default=8.0)
    temperature_log_interval_minutes = Column(Integer, default=30)
    qc_required_within_hours = Column(Integer, default=4)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    category = relationship("IngredientCategory")


class FreshnessRule(Base):
    __tablename__ = "freshness_rules"

    id = Column(Integer, primary_key=True, index=True)
    ingredient_category_id = Column(Integer, ForeignKey("ingredient_categories.id"), nullable=False)
    stage = Column(String(50), nullable=False)
    max_hours = Column(Float, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    category = relationship("IngredientCategory", back_populates="freshness_rules")


class ProductionStation(Base):
    __tablename__ = "production_stations"

    id = Column(Integer, primary_key=True, index=True)
    station_code = Column(String(20), unique=True, index=True, nullable=False)
    station_name = Column(String(100), nullable=False)
    description = Column(Text)
    sort_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    production_records = relationship("ProductionRecord", back_populates="station")


class QCTemplate(Base):
    __tablename__ = "qc_templates"

    id = Column(Integer, primary_key=True, index=True)
    template_name = Column(String(100), nullable=False)
    ingredient_category_id = Column(Integer, ForeignKey("ingredient_categories.id"), nullable=False)
    check_items = Column(Text, nullable=False)
    scoring_standard = Column(Text)
    disposition_options = Column(Text)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    category = relationship("IngredientCategory")


class MaterialBatch(Base):
    __tablename__ = "material_batches"

    id = Column(Integer, primary_key=True, index=True)
    batch_no = Column(String(50), index=True, nullable=False)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False)
    ingredient_category_id = Column(Integer, ForeignKey("ingredient_categories.id"), nullable=False)
    quantity = Column(Float, nullable=False)
    unit = Column(String(20), default="kg")
    supplier_batch_no = Column(String(100))
    production_date = Column(Date)
    expiry_date = Column(Date)
    status = Column(String(30), nullable=False, default=BatchStatus.PENDING_ACCEPTANCE.value)
    arrival_time = Column(DateTime)
    wash_complete_time = Column(DateTime)
    production_start_time = Column(DateTime)
    production_complete_time = Column(DateTime)
    sale_start_time = Column(DateTime)
    discard_time = Column(DateTime)
    final_disposition = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    store = relationship("Store", back_populates="batches")
    category = relationship("IngredientCategory", back_populates="batches")
    acceptance = relationship("MaterialAcceptance", back_populates="batch", uselist=False)
    production_records = relationship("ProductionRecord", back_populates="batch")
    temperature_logs = relationship("TemperatureLog", back_populates="batch")
    qc_inspections = relationship("QCInspection", back_populates="batch")
    anomaly_events = relationship("AnomalyEvent", back_populates="batch")


class MaterialAcceptance(Base):
    __tablename__ = "material_acceptance"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("material_batches.id"), unique=True, nullable=False)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False)
    accepted_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    acceptance_time = Column(DateTime, default=datetime.utcnow)
    appearance_check = Column(Boolean, default=True)
    temperature_check = Column(Boolean, default=True)
    packaging_check = Column(Boolean, default=True)
    certificate_check = Column(Boolean, default=True)
    accepted_quantity = Column(Float, nullable=False)
    abnormal_remark = Column(Text)
    is_accepted = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("MaterialBatch", back_populates="acceptance")
    store = relationship("Store", back_populates="acceptance_records")
    acceptor = relationship("User", foreign_keys=[accepted_by])


class ProductionRecord(Base):
    __tablename__ = "production_records"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("material_batches.id"), nullable=False)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False)
    station_id = Column(Integer, ForeignKey("production_stations.id"), nullable=False)
    operator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    start_time = Column(DateTime, default=datetime.utcnow)
    end_time = Column(DateTime)
    cups_produced = Column(Integer, default=0)
    cups_discarded = Column(Integer, default=0)
    discard_reason = Column(Text)
    abnormal_remark = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("MaterialBatch", back_populates="production_records")
    store = relationship("Store", back_populates="production_records")
    station = relationship("ProductionStation", back_populates="production_records")
    operator = relationship("User", foreign_keys=[operator_id])


class TemperatureLog(Base):
    __tablename__ = "temperature_logs"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("material_batches.id"), nullable=False)
    temperature = Column(Float, nullable=False)
    log_time = Column(DateTime, default=datetime.utcnow)
    recorded_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    location = Column(String(100))
    remark = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("MaterialBatch", back_populates="temperature_logs")
    recorder = relationship("User", foreign_keys=[recorded_by])


class QCInspection(Base):
    __tablename__ = "qc_inspections"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("material_batches.id"), nullable=False)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False)
    template_id = Column(Integer, ForeignKey("qc_templates.id"))
    inspector_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    inspection_time = Column(DateTime, default=datetime.utcnow)
    review_time = Column(DateTime)
    appearance_score = Column(Float)
    taste_score = Column(Float)
    texture_score = Column(Float)
    overall_score = Column(Float)
    taste_deviation = Column(Text)
    check_result = Column(Text)
    disposition = Column(String(100))
    disposition_note = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("MaterialBatch", back_populates="qc_inspections")
    store = relationship("Store", back_populates="qc_inspections")
    template = relationship("QCTemplate")
    inspector = relationship("User", foreign_keys=[inspector_id])


class AnomalyEvent(Base):
    __tablename__ = "anomaly_events"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("material_batches.id"), nullable=False)
    anomaly_type = Column(String(50), nullable=False)
    severity = Column(String(20), default="medium")
    description = Column(Text)
    detected_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime)
    resolved_by = Column(Integer, ForeignKey("users.id"))
    resolution_note = Column(Text)
    is_resolved = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("MaterialBatch", back_populates="anomaly_events")
    resolver = relationship("User", foreign_keys=[resolved_by])


class RecheckApplication(Base):
    __tablename__ = "recheck_applications"

    id = Column(Integer, primary_key=True, index=True)
    application_no = Column(String(50), unique=True, index=True, nullable=False)
    batch_id = Column(Integer, ForeignKey("material_batches.id"), nullable=False)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False)
    source_qc_inspection_id = Column(Integer, ForeignKey("qc_inspections.id"), nullable=True)
    source_anomaly_event_id = Column(Integer, ForeignKey("anomaly_events.id"), nullable=True)
    recheck_source = Column(String(30), nullable=False, default=RecheckSource.STORE_INITIATIVE.value)
    recheck_reason = Column(String(50), nullable=False, default=RecheckReason.OTHER.value)
    reason_detail = Column(Text)
    supplementary_note = Column(Text)
    status = Column(String(30), nullable=False, default=RecheckStatus.PENDING.value)
    recheck_result = Column(String(30), nullable=True)
    deadline_hours = Column(Integer, default=24)
    applied_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    applied_at = Column(DateTime, default=datetime.utcnow)
    assigned_to = Column(Integer, ForeignKey("users.id"), nullable=True)
    assigned_at = Column(DateTime, nullable=True)
    recheck_started_at = Column(DateTime, nullable=True)
    recheck_completed_at = Column(DateTime, nullable=True)
    recheck_template_id = Column(Integer, ForeignKey("qc_templates.id"), nullable=True)
    appearance_score = Column(Float, nullable=True)
    taste_score = Column(Float, nullable=True)
    texture_score = Column(Float, nullable=True)
    overall_score = Column(Float, nullable=True)
    recheck_check_result = Column(Text, nullable=True)
    recheck_disposition_note = Column(Text, nullable=True)
    rechecked_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    cancelled_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    cancel_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    batch = relationship("MaterialBatch")
    store = relationship("Store")
    source_qc_inspection = relationship("QCInspection", foreign_keys=[source_qc_inspection_id])
    source_anomaly_event = relationship("AnomalyEvent", foreign_keys=[source_anomaly_event_id])
    applicant = relationship("User", foreign_keys=[applied_by])
    assignee = relationship("User", foreign_keys=[assigned_to])
    rechecker = relationship("User", foreign_keys=[rechecked_by])
    canceller = relationship("User", foreign_keys=[cancelled_by])
    recheck_template = relationship("QCTemplate", foreign_keys=[recheck_template_id])
