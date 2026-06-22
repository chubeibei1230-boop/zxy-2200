from datetime import datetime, date, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, or_, and_
from typing import List, Optional, Dict, Any
from app.database import get_db
from app import models, schemas, auth
from app.services import freshness_warning as warning_service

dashboard_router = APIRouter(prefix="/api/dashboard", tags=["异常批次闭环处置看板"])

BATCH_STATUS_LABELS = {
    "pending_acceptance": "待验收",
    "ready_for_production": "待制作",
    "in_production": "制作中",
    "pending_qc": "待抽检",
    "anomaly_hold": "异常留观",
    "ready_for_sale": "可销售",
    "discarded": "已废弃",
}

STAGE_LABELS = {
    "pending_acceptance": "验收环节",
    "ready_for_production": "验收完成",
    "after_wash": "清洗完成",
    "in_production": "制作环节",
    "pending_qc": "待抽检环节",
    "anomaly_hold": "异常留观环节",
    "ready_for_sale": "销售环节",
    "discarded": "已处置",
}

ANOMALY_TYPE_LABELS = {
    "freshness_timeout": "保鲜超时",
    "high_discard_ratio": "废弃比例过高",
    "qc_missing": "品控抽检缺失",
    "temperature_gap": "温度异常",
    "concentrated_anomaly": "集中异常",
}

ROLE_LABELS = {
    "hq_admin": "总部管理员",
    "store_staff": "门店人员",
    "qc_staff": "品控人员",
}

WARNING_LEVEL_LABELS = {
    "normal": "正常",
    "attention": "关注",
    "warning": "预警",
    "urgent": "紧急",
}

DISPOSAL_STATUS_LABELS = {
    "pending": "待处理",
    "processing": "处理中",
    "closed": "已闭环",
    "overdue": "已超时",
}


def _get_batch_stage(batch: models.MaterialBatch) -> str:
    if batch.status == models.BatchStatus.DISCARDED.value:
        return "discarded"
    if batch.status == models.BatchStatus.READY_FOR_SALE.value:
        return "ready_for_sale"
    if batch.status == models.BatchStatus.ANOMALY_HOLD.value:
        return "anomaly_hold"
    if batch.status == models.BatchStatus.PENDING_QC.value:
        return "pending_qc"
    if batch.status == models.BatchStatus.IN_PRODUCTION.value:
        return "in_production"
    if batch.wash_complete_time:
        return "after_wash"
    if batch.status == models.BatchStatus.READY_FOR_PRODUCTION.value:
        return "ready_for_production"
    return "pending_acceptance"


def _get_responsible_role(batch: models.MaterialBatch, has_unresolved_anomaly: bool,
                          has_pending_warning: bool, has_pending_recheck: bool) -> Optional[str]:
    if batch.status == models.BatchStatus.DISCARDED.value or batch.status == models.BatchStatus.READY_FOR_SALE.value:
        return None
    if has_pending_recheck:
        return models.UserRole.QC_STAFF.value
    if batch.status in [models.BatchStatus.PENDING_QC.value, models.BatchStatus.ANOMALY_HOLD.value]:
        return models.UserRole.QC_STAFF.value
    if has_pending_warning:
        return models.UserRole.STORE_STAFF.value
    if has_unresolved_anomaly:
        return models.UserRole.QC_STAFF.value
    if batch.status in [models.BatchStatus.PENDING_ACCEPTANCE.value,
                        models.BatchStatus.READY_FOR_PRODUCTION.value,
                        models.BatchStatus.IN_PRODUCTION.value]:
        return models.UserRole.STORE_STAFF.value
    return None


def _get_remaining_deadline(db: Session, batch: models.MaterialBatch,
                            has_pending_warning: bool, has_pending_recheck: bool) -> Optional[float]:
    now = datetime.utcnow()
    if has_pending_recheck:
        pending_recheck = db.query(models.RecheckApplication).filter(
            models.RecheckApplication.batch_id == batch.id,
            models.RecheckApplication.status.in_([
                models.RecheckStatus.PENDING.value,
                models.RecheckStatus.IN_PROGRESS.value
            ])
        ).order_by(models.RecheckApplication.applied_at.asc()).first()
        if pending_recheck:
            deadline = pending_recheck.applied_at + timedelta(hours=pending_recheck.deadline_hours)
            remaining = (deadline - now).total_seconds() / 3600
            return round(remaining, 1)
    if has_pending_warning:
        pending_warning = db.query(models.FreshnessWarning).filter(
            models.FreshnessWarning.batch_id == batch.id,
            models.FreshnessWarning.status.in_([
                models.WarningStatus.PENDING.value,
                models.WarningStatus.PROCESSING.value
            ])
        ).order_by(models.FreshnessWarning.deadline_time.asc()).first()
        if pending_warning:
            remaining = (pending_warning.deadline_time - now).total_seconds() / 3600
            return round(remaining, 1)
    if batch.status == models.BatchStatus.PENDING_QC.value and batch.production_complete_time:
        batch_rule = db.query(models.BatchRule).filter(
            models.BatchRule.ingredient_category_id == batch.ingredient_category_id,
            models.BatchRule.is_active == True
        ).first()
        if batch_rule:
            deadline = batch.production_complete_time + timedelta(hours=batch_rule.qc_required_within_hours)
            remaining = (deadline - now).total_seconds() / 3600
            return round(remaining, 1)
    return None


def _get_disposal_status(batch: models.MaterialBatch, has_unresolved_anomaly: bool,
                         has_pending_warning: bool, has_pending_recheck: bool,
                         remaining_hours: Optional[float]) -> str:
    if batch.status == models.BatchStatus.DISCARDED.value or batch.final_disposition:
        return "closed"
    if batch.status == models.BatchStatus.READY_FOR_SALE.value and not has_unresolved_anomaly \
            and not has_pending_warning and not has_pending_recheck:
        return "closed"
    if remaining_hours is not None and remaining_hours < 0:
        return "overdue"
    if has_pending_recheck or has_pending_warning or has_unresolved_anomaly:
        return "processing"
    return "pending"


def _apply_data_scope(query, current_user: models.User, batch_model=models.MaterialBatch):
    if current_user.role == models.UserRole.STORE_STAFF.value:
        query = query.filter(batch_model.store_id == current_user.store_id)
    return query


def _build_anomaly_batch_list_item(db: Session, batch: models.MaterialBatch) -> schemas.AnomalyBatchListItem:
    store = db.query(models.Store).filter(models.Store.id == batch.store_id).first()
    cat = db.query(models.IngredientCategory).filter(
        models.IngredientCategory.id == batch.ingredient_category_id
    ).first()

    latest_anomaly = db.query(models.AnomalyEvent).filter(
        models.AnomalyEvent.batch_id == batch.id
    ).order_by(models.AnomalyEvent.detected_at.desc()).first()

    unresolved_anomalies = db.query(models.AnomalyEvent).filter(
        models.AnomalyEvent.batch_id == batch.id,
        models.AnomalyEvent.is_resolved == False
    ).count()

    pending_warnings = db.query(models.FreshnessWarning).filter(
        models.FreshnessWarning.batch_id == batch.id,
        models.FreshnessWarning.status.in_([
            models.WarningStatus.PENDING.value,
            models.WarningStatus.PROCESSING.value
        ])
    ).count()

    pending_rechecks = db.query(models.RecheckApplication).filter(
        models.RecheckApplication.batch_id == batch.id,
        models.RecheckApplication.status.in_([
            models.RecheckStatus.PENDING.value,
            models.RecheckStatus.IN_PROGRESS.value
        ])
    ).count()

    has_unresolved = unresolved_anomalies > 0
    has_pending_w = pending_warnings > 0
    has_pending_r = pending_rechecks > 0

    current_stage = _get_batch_stage(batch)
    responsible_role = _get_responsible_role(batch, has_unresolved, has_pending_w, has_pending_r)
    remaining_hours = _get_remaining_deadline(db, batch, has_pending_w, has_pending_r)

    return schemas.AnomalyBatchListItem(
        batch_id=batch.id,
        batch_no=batch.batch_no,
        store_id=batch.store_id,
        store_name=store.store_name if store else "未知",
        ingredient_category_id=batch.ingredient_category_id,
        category_name=cat.category_name if cat else "未知",
        quantity=batch.quantity,
        unit=batch.unit,
        batch_status=batch.status,
        batch_status_label=BATCH_STATUS_LABELS.get(batch.status, batch.status),
        current_stage=current_stage,
        current_stage_label=STAGE_LABELS.get(current_stage, current_stage),
        latest_anomaly_type=latest_anomaly.anomaly_type if latest_anomaly else None,
        latest_anomaly_type_label=ANOMALY_TYPE_LABELS.get(latest_anomaly.anomaly_type) if latest_anomaly else None,
        latest_anomaly_reason=latest_anomaly.description if latest_anomaly else None,
        responsible_role=responsible_role,
        responsible_role_label=ROLE_LABELS.get(responsible_role) if responsible_role else None,
        remaining_deadline_hours=remaining_hours,
        final_disposition=batch.final_disposition,
        has_unresolved_anomaly=has_unresolved,
        has_pending_warning=has_pending_w,
        has_pending_recheck=has_pending_r,
        created_at=batch.created_at,
        updated_at=batch.updated_at
    )


@dashboard_router.get("/anomaly-batches", response_model=List[schemas.AnomalyBatchListItem])
def list_anomaly_batches(
    store_id: Optional[int] = None,
    ingredient_category_id: Optional[int] = None,
    batch_no: Optional[str] = None,
    batch_status: Optional[str] = None,
    anomaly_type: Optional[str] = None,
    warning_level: Optional[str] = None,
    disposal_status: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_active_user)
):
    query = db.query(models.MaterialBatch)
    query = _apply_data_scope(query, current_user)

    if store_id and current_user.role != models.UserRole.STORE_STAFF.value:
        query = query.filter(models.MaterialBatch.store_id == store_id)
    if ingredient_category_id:
        query = query.filter(models.MaterialBatch.ingredient_category_id == ingredient_category_id)
    if batch_no:
        query = query.filter(models.MaterialBatch.batch_no.contains(batch_no))
    if batch_status:
        query = query.filter(models.MaterialBatch.status == batch_status)
    if date_from:
        query = query.filter(models.MaterialBatch.created_at >= date_from)
    if date_to:
        date_to_end = datetime.combine(date_to + timedelta(days=1), datetime.min.time())
        query = query.filter(models.MaterialBatch.created_at < date_to_end)

    batch_ids_with_anomaly = None
    if anomaly_type:
        anomaly_batches = db.query(models.AnomalyEvent.batch_id).filter(
            models.AnomalyEvent.anomaly_type == anomaly_type
        ).distinct().all()
        batch_ids_with_anomaly = [row[0] for row in anomaly_batches]
        if not batch_ids_with_anomaly:
            return []
        query = query.filter(models.MaterialBatch.id.in_(batch_ids_with_anomaly))

    batch_ids_with_warning_level = None
    if warning_level:
        warning_batches = db.query(models.FreshnessWarning.batch_id).filter(
            models.FreshnessWarning.warning_level == warning_level,
            models.FreshnessWarning.status.in_([
                models.WarningStatus.PENDING.value,
                models.WarningStatus.PROCESSING.value
            ])
        ).distinct().all()
        batch_ids_with_warning_level = [row[0] for row in warning_batches]
        if not batch_ids_with_warning_level:
            return []
        query = query.filter(models.MaterialBatch.id.in_(batch_ids_with_warning_level))

    query = query.order_by(models.MaterialBatch.updated_at.desc())
    batches = query.all()

    results = []
    now = datetime.utcnow()
    for batch in batches:
        item = _build_anomaly_batch_list_item(db, batch)
        if disposal_status:
            actual_status = _get_disposal_status(
                batch, item.has_unresolved_anomaly,
                item.has_pending_warning, item.has_pending_recheck,
                item.remaining_deadline_hours
            )
            if actual_status != disposal_status:
                continue
        results.append(item)

    return results


@dashboard_router.get("/anomaly-batches/{batch_id}", response_model=schemas.AnomalyBatchDetailResponse)
def get_anomaly_batch_detail(
    batch_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_active_user)
):
    batch = db.query(models.MaterialBatch).filter(models.MaterialBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    if current_user.role == models.UserRole.STORE_STAFF.value:
        if batch.store_id != current_user.store_id:
            raise HTTPException(status_code=403, detail="无权限查看其他门店数据")

    acceptance = db.query(models.MaterialAcceptance).filter(
        models.MaterialAcceptance.batch_id == batch_id
    ).first()

    production_records = db.query(models.ProductionRecord).filter(
        models.ProductionRecord.batch_id == batch_id
    ).order_by(models.ProductionRecord.created_at.asc()).all()

    qc_inspections = db.query(models.QCInspection).filter(
        models.QCInspection.batch_id == batch_id
    ).order_by(models.QCInspection.created_at.asc()).all()

    anomaly_events = db.query(models.AnomalyEvent).filter(
        models.AnomalyEvent.batch_id == batch_id
    ).order_by(models.AnomalyEvent.detected_at.asc()).all()

    freshness_warnings = db.query(models.FreshnessWarning).filter(
        models.FreshnessWarning.batch_id == batch_id
    ).order_by(models.FreshnessWarning.detected_at.asc()).all()

    recheck_applications = db.query(models.RecheckApplication).filter(
        models.RecheckApplication.batch_id == batch_id
    ).order_by(models.RecheckApplication.applied_at.asc()).all()

    warning_ids = [w.id for w in freshness_warnings]
    disposal_records = []
    if warning_ids:
        disposal_records = db.query(models.WarningDisposalRecord).filter(
            models.WarningDisposalRecord.warning_id.in_(warning_ids)
        ).order_by(models.WarningDisposalRecord.operation_time.asc()).all()

    timeline = _build_timeline(
        db, batch, acceptance, production_records, qc_inspections,
        anomaly_events, freshness_warnings, recheck_applications, disposal_records
    )

    return schemas.AnomalyBatchDetailResponse(
        batch=batch,
        acceptance=acceptance,
        production_records=production_records,
        qc_inspections=qc_inspections,
        anomaly_events=anomaly_events,
        freshness_warnings=freshness_warnings,
        recheck_applications=recheck_applications,
        disposal_records=disposal_records,
        timeline=timeline
    )


def _build_timeline(db: Session, batch: models.MaterialBatch,
                    acceptance: Optional[models.MaterialAcceptance],
                    production_records: List[models.ProductionRecord],
                    qc_inspections: List[models.QCInspection],
                    anomaly_events: List[models.AnomalyEvent],
                    freshness_warnings: List[models.FreshnessWarning],
                    recheck_applications: List[models.RecheckApplication],
                    disposal_records: List[models.WarningDisposalRecord]) -> List[schemas.AnomalyBatchTimelineItem]:
    timeline: List[schemas.AnomalyBatchTimelineItem] = []

    def _get_user_name(user_id: Optional[int]) -> Optional[str]:
        if not user_id:
            return None
        user = db.query(models.User).filter(models.User.id == user_id).first()
        return user.full_name if user else None

    timeline.append(schemas.AnomalyBatchTimelineItem(
        event_type="batch_created",
        event_type_label="批次创建",
        event_time=batch.created_at,
        description=f"创建批次 {batch.batch_no}，数量 {batch.quantity}{batch.unit}"
    ))

    if acceptance:
        timeline.append(schemas.AnomalyBatchTimelineItem(
            event_type="acceptance",
            event_type_label="原料验收",
            event_time=acceptance.acceptance_time,
            operator_name=_get_user_name(acceptance.accepted_by),
            description="验收通过" if acceptance.is_accepted else "验收不通过",
            detail={
                "appearance_check": acceptance.appearance_check,
                "temperature_check": acceptance.temperature_check,
                "packaging_check": acceptance.packaging_check,
                "certificate_check": acceptance.certificate_check,
                "accepted_quantity": acceptance.accepted_quantity,
                "abnormal_remark": acceptance.abnormal_remark
            }
        ))

    if batch.wash_complete_time:
        timeline.append(schemas.AnomalyBatchTimelineItem(
            event_type="wash_complete",
            event_type_label="清洗完成",
            event_time=batch.wash_complete_time,
            description="原料清洗完成"
        ))

    if batch.production_start_time:
        timeline.append(schemas.AnomalyBatchTimelineItem(
            event_type="production_start",
            event_type_label="开始制作",
            event_time=batch.production_start_time,
            description="开始制作"
        ))

    for rec in production_records:
        station = db.query(models.ProductionStation).filter(
            models.ProductionStation.id == rec.station_id
        ).first()
        timeline.append(schemas.AnomalyBatchTimelineItem(
            event_type="production_record",
            event_type_label=f"制作记录-{station.station_name if station else '未知岗位'}",
            event_time=rec.start_time,
            operator_name=_get_user_name(rec.operator_id),
            description=f"制作 {rec.cups_produced} 杯，废弃 {rec.cups_discarded} 杯",
            detail={
                "cups_produced": rec.cups_produced,
                "cups_discarded": rec.cups_discarded,
                "discard_reason": rec.discard_reason,
                "abnormal_remark": rec.abnormal_remark,
                "end_time": rec.end_time.isoformat() if rec.end_time else None
            }
        ))

    if batch.production_complete_time:
        timeline.append(schemas.AnomalyBatchTimelineItem(
            event_type="production_complete",
            event_type_label="制作完成",
            event_time=batch.production_complete_time,
            description="制作完成，进入待抽检"
        ))

    for anomaly in anomaly_events:
        timeline.append(schemas.AnomalyBatchTimelineItem(
            event_type="anomaly_event",
            event_type_label=f"异常事件-{ANOMALY_TYPE_LABELS.get(anomaly.anomaly_type, anomaly.anomaly_type)}",
            event_time=anomaly.detected_at,
            operator_name=_get_user_name(anomaly.resolved_by),
            description=anomaly.description or ("异常已解决" if anomaly.is_resolved else "待处理异常"),
            detail={
                "anomaly_type": anomaly.anomaly_type,
                "severity": anomaly.severity,
                "is_resolved": anomaly.is_resolved,
                "resolution_note": anomaly.resolution_note,
                "resolved_at": anomaly.resolved_at.isoformat() if anomaly.resolved_at else None
            }
        ))

    for warning in freshness_warnings:
        timeline.append(schemas.AnomalyBatchTimelineItem(
            event_type="freshness_warning",
            event_type_label=f"临期预警-{WARNING_LEVEL_LABELS.get(warning.warning_level, warning.warning_level)}",
            event_time=warning.detected_at,
            operator_name=_get_user_name(warning.processed_by),
            description=f"{warning.stage} 环节保鲜预警，剩余 {warning.remaining_hours:.1f} 小时",
            detail={
                "stage": warning.stage,
                "warning_level": warning.warning_level,
                "status": warning.status,
                "remaining_hours": warning.remaining_hours,
                "deadline_time": warning.deadline_time.isoformat(),
                "suggested_action": warning.suggested_action,
                "processing_note": warning.processing_note,
                "final_disposition": warning.final_disposition
            }
        ))

    for qc in qc_inspections:
        timeline.append(schemas.AnomalyBatchTimelineItem(
            event_type="qc_inspection",
            event_type_label="品控抽检",
            event_time=qc.inspection_time,
            operator_name=_get_user_name(qc.inspector_id),
            description=f"抽检结论: {qc.disposition or '未填写'}，综合评分: {qc.overall_score if qc.overall_score else '未评分'}",
            detail={
                "appearance_score": qc.appearance_score,
                "taste_score": qc.taste_score,
                "texture_score": qc.texture_score,
                "overall_score": qc.overall_score,
                "taste_deviation": qc.taste_deviation,
                "check_result": qc.check_result,
                "disposition": qc.disposition,
                "disposition_note": qc.disposition_note
            }
        ))

    for recheck in recheck_applications:
        timeline.append(schemas.AnomalyBatchTimelineItem(
            event_type="recheck_application",
            event_type_label="复检申请",
            event_time=recheck.applied_at,
            operator_name=_get_user_name(recheck.applied_by),
            description=f"复检申请: {recheck.recheck_reason}，状态: {recheck.status}",
            detail={
                "application_no": recheck.application_no,
                "recheck_source": recheck.recheck_source,
                "recheck_reason": recheck.recheck_reason,
                "reason_detail": recheck.reason_detail,
                "status": recheck.status,
                "recheck_result": recheck.recheck_result,
                "deadline_hours": recheck.deadline_hours,
                "assigned_to": _get_user_name(recheck.assigned_to),
                "overall_score": recheck.overall_score,
                "recheck_disposition_note": recheck.recheck_disposition_note
            }
        ))

    for dr in disposal_records:
        timeline.append(schemas.AnomalyBatchTimelineItem(
            event_type="disposal_record",
            event_type_label=f"处置记录-{dr.disposal_type}",
            event_time=dr.operation_time,
            operator_name=_get_user_name(dr.operator_id),
            description=dr.disposal_note or "处置操作",
            detail={
                "disposal_type": dr.disposal_type,
                "qc_inspection_id": dr.qc_inspection_id,
                "recheck_application_id": dr.recheck_application_id
            }
        ))

    if batch.sale_start_time:
        timeline.append(schemas.AnomalyBatchTimelineItem(
            event_type="sale_release",
            event_type_label="放行销售",
            event_time=batch.sale_start_time,
            description="批次已放行销售"
        ))

    if batch.discard_time:
        timeline.append(schemas.AnomalyBatchTimelineItem(
            event_type="discard",
            event_type_label="批次废弃",
            event_time=batch.discard_time,
            description=f"废弃原因: {batch.final_disposition or '未填写'}"
        ))

    timeline.sort(key=lambda x: x.event_time)
    return timeline


@dashboard_router.post("/anomaly-batches/{batch_id}/disposal-note")
def add_batch_disposal_note(
    batch_id: int,
    note_in: schemas.BatchDisposalNote,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(
        models.UserRole.STORE_STAFF.value,
        models.UserRole.HQ_ADMIN.value
    ))
):
    batch = db.query(models.MaterialBatch).filter(models.MaterialBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    if current_user.role == models.UserRole.STORE_STAFF.value:
        if batch.store_id != current_user.store_id:
            raise HTTPException(status_code=403, detail="无权限操作其他门店数据")

    pending_warning = db.query(models.FreshnessWarning).filter(
        models.FreshnessWarning.batch_id == batch_id,
        models.FreshnessWarning.status.in_([
            models.WarningStatus.PENDING.value,
            models.WarningStatus.PROCESSING.value
        ])
    ).order_by(models.FreshnessWarning.detected_at.desc()).first()

    if not pending_warning:
        today = datetime.utcnow().strftime("%Y%m%d")
        prefix = f"FW{today}"
        last = db.query(models.FreshnessWarning).filter(
            models.FreshnessWarning.warning_no.like(f"{prefix}%")
        ).order_by(models.FreshnessWarning.warning_no.desc()).first()
        if last:
            seq = int(last.warning_no[-4:]) + 1
        else:
            seq = 1
        warning_no = f"{prefix}{seq:04d}"
        pending_warning = models.FreshnessWarning(
            warning_no=warning_no,
            batch_id=batch_id,
            store_id=batch.store_id,
            ingredient_category_id=batch.ingredient_category_id,
            stage=_get_batch_stage(batch),
            warning_level=models.WarningLevel.ATTENTION.value,
            status=models.WarningStatus.PROCESSING.value,
            reference_time=datetime.utcnow(),
            deadline_time=datetime.utcnow() + timedelta(hours=24),
            remaining_hours=24.0,
            max_hours=24.0,
            elapsed_hours=0.0,
            suggested_action="门店补充处置说明",
            processed_by=current_user.id
        )
        db.add(pending_warning)
        db.flush()

    pending_warning.status = models.WarningStatus.PROCESSING.value
    pending_warning.processing_note = note_in.disposal_note
    if not pending_warning.processed_by:
        pending_warning.processed_by = current_user.id

    warning_service.create_disposal_record(
        db,
        warning_id=pending_warning.id,
        batch_id=batch_id,
        store_id=batch.store_id,
        disposal_type=models.DisposalType.STORE_NOTE.value,
        operator_id=current_user.id,
        disposal_note=note_in.disposal_note
    )

    db.commit()
    db.refresh(pending_warning)
    return {"message": "处置说明已补充", "warning_id": pending_warning.id}


@dashboard_router.get("/qc/tasks", response_model=List[schemas.QCTaskItem])
def get_qc_tasks(
    store_id: Optional[int] = None,
    task_type: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(
        models.UserRole.QC_STAFF.value,
        models.UserRole.HQ_ADMIN.value
    ))
):
    now = datetime.utcnow()
    tasks: List[schemas.QCTaskItem] = []

    pending_qc_batches = db.query(models.MaterialBatch).filter(
        models.MaterialBatch.status.in_([
            models.BatchStatus.PENDING_QC.value,
        ]),
        models.MaterialBatch.production_complete_time != None
    ).all()

    if store_id:
        pending_qc_batches = [b for b in pending_qc_batches if b.store_id == store_id]

    for batch in pending_qc_batches:
        has_qc = db.query(models.QCInspection).filter(
            models.QCInspection.batch_id == batch.id
        ).first()
        if has_qc:
            continue

        store = db.query(models.Store).filter(models.Store.id == batch.store_id).first()
        cat = db.query(models.IngredientCategory).filter(
            models.IngredientCategory.id == batch.ingredient_category_id
        ).first()
        batch_rule = db.query(models.BatchRule).filter(
            models.BatchRule.ingredient_category_id == batch.ingredient_category_id,
            models.BatchRule.is_active == True
        ).first()

        pending_hours = round((now - batch.production_complete_time).total_seconds() / 3600, 1)
        threshold = batch_rule.qc_required_within_hours if batch_rule else 4
        deadline_at = batch.production_complete_time + timedelta(hours=threshold)

        if pending_hours >= threshold * 2:
            priority = "紧急"
        elif pending_hours >= threshold:
            priority = "高"
        elif pending_hours >= threshold / 2:
            priority = "中"
        else:
            priority = "低"

        if task_type and task_type != "qc_inspection":
            continue

        tasks.append(schemas.QCTaskItem(
            task_type="qc_inspection",
            task_type_label="首次抽检",
            batch_id=batch.id,
            batch_no=batch.batch_no,
            store_id=batch.store_id,
            store_name=store.store_name if store else "未知",
            category_name=cat.category_name if cat else "未知",
            batch_status=batch.status,
            pending_hours=pending_hours,
            priority=priority,
            reason="待品控抽检",
            deadline_at=deadline_at
        ))

    pending_rechecks = db.query(models.RecheckApplication).filter(
        models.RecheckApplication.status.in_([
            models.RecheckStatus.PENDING.value,
            models.RecheckStatus.IN_PROGRESS.value
        ])
    ).all()

    if store_id:
        pending_rechecks = [r for r in pending_rechecks if r.store_id == store_id]

    if current_user.role == models.UserRole.QC_STAFF.value:
        pending_rechecks = [
            r for r in pending_rechecks
            if r.assigned_to == current_user.id or r.assigned_to is None
        ]

    for recheck in pending_rechecks:
        batch = db.query(models.MaterialBatch).filter(models.MaterialBatch.id == recheck.batch_id).first()
        if not batch:
            continue
        store = db.query(models.Store).filter(models.Store.id == recheck.store_id).first()
        cat = db.query(models.IngredientCategory).filter(
            models.IngredientCategory.id == batch.ingredient_category_id
        ).first() if batch else None

        pending_hours = round((now - recheck.applied_at).total_seconds() / 3600, 1)
        deadline_at = recheck.applied_at + timedelta(hours=recheck.deadline_hours)

        if pending_hours >= recheck.deadline_hours * 2:
            priority = "紧急"
        elif pending_hours >= recheck.deadline_hours:
            priority = "高"
        elif pending_hours >= recheck.deadline_hours / 2:
            priority = "中"
        else:
            priority = "低"

        if task_type and task_type != "recheck":
            continue

        tasks.append(schemas.QCTaskItem(
            task_id=recheck.id,
            task_type="recheck",
            task_type_label="复检任务",
            batch_id=recheck.batch_id,
            batch_no=batch.batch_no if batch else "未知",
            store_id=recheck.store_id,
            store_name=store.store_name if store else "未知",
            category_name=cat.category_name if cat else "未知",
            batch_status=batch.status if batch else "未知",
            pending_hours=pending_hours,
            priority=priority,
            reason=recheck.reason_detail or recheck.recheck_reason,
            deadline_at=deadline_at,
            assigned_to=recheck.assigned_to
        ))

    anomaly_hold_batches = db.query(models.MaterialBatch).filter(
        models.MaterialBatch.status == models.BatchStatus.ANOMALY_HOLD.value
    ).all()

    if store_id:
        anomaly_hold_batches = [b for b in anomaly_hold_batches if b.store_id == store_id]

    for batch in anomaly_hold_batches:
        has_pending_recheck = db.query(models.RecheckApplication).filter(
            models.RecheckApplication.batch_id == batch.id,
            models.RecheckApplication.status.in_([
                models.RecheckStatus.PENDING.value,
                models.RecheckStatus.IN_PROGRESS.value
            ])
        ).first()
        if has_pending_recheck:
            continue

        store = db.query(models.Store).filter(models.Store.id == batch.store_id).first()
        cat = db.query(models.IngredientCategory).filter(
            models.IngredientCategory.id == batch.ingredient_category_id
        ).first()
        latest_anomaly = db.query(models.AnomalyEvent).filter(
            models.AnomalyEvent.batch_id == batch.id
        ).order_by(models.AnomalyEvent.detected_at.desc()).first()

        pending_hours = round((now - (latest_anomaly.detected_at if latest_anomaly else batch.updated_at)).total_seconds() / 3600, 1)

        if pending_hours >= 48:
            priority = "紧急"
        elif pending_hours >= 24:
            priority = "高"
        elif pending_hours >= 12:
            priority = "中"
        else:
            priority = "低"

        if task_type and task_type != "anomaly_review":
            continue

        tasks.append(schemas.QCTaskItem(
            task_type="anomaly_review",
            task_type_label="异常复核",
            batch_id=batch.id,
            batch_no=batch.batch_no,
            store_id=batch.store_id,
            store_name=store.store_name if store else "未知",
            category_name=cat.category_name if cat else "未知",
            batch_status=batch.status,
            pending_hours=pending_hours,
            priority=priority,
            reason=latest_anomaly.description if latest_anomaly else "异常留观待复核"
        ))

    tasks.sort(key=lambda x: x.pending_hours, reverse=True)
    return tasks


@dashboard_router.get("/statistics/overview", response_model=schemas.DashboardOverviewStats)
def get_dashboard_overview(
    store_id: Optional[int] = None,
    days: int = Query(7, ge=1, le=90, description="统计天数"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_active_user)
):
    cutoff = datetime.utcnow() - timedelta(days=days)

    batch_query = db.query(models.MaterialBatch).filter(
        models.MaterialBatch.created_at >= cutoff
    )
    batch_query = _apply_data_scope(batch_query, current_user)
    if store_id and current_user.role != models.UserRole.STORE_STAFF.value:
        batch_query = batch_query.filter(models.MaterialBatch.store_id == store_id)
    total_batches = batch_query.count()

    anomaly_batch_ids = db.query(models.AnomalyEvent.batch_id).filter(
        models.AnomalyEvent.created_at >= cutoff
    ).distinct().all()
    anomaly_batch_ids = [row[0] for row in anomaly_batch_ids]

    warning_batch_ids = db.query(models.FreshnessWarning.batch_id).filter(
        models.FreshnessWarning.detected_at >= cutoff
    ).distinct().all()
    warning_batch_ids = [row[0] for row in warning_batch_ids]

    recheck_batch_ids = db.query(models.RecheckApplication.batch_id).filter(
        models.RecheckApplication.applied_at >= cutoff
    ).distinct().all()
    recheck_batch_ids = [row[0] for row in recheck_batch_ids]

    all_anomaly_batch_ids = list(set(anomaly_batch_ids + warning_batch_ids + recheck_batch_ids))

    anomaly_batch_query = db.query(models.MaterialBatch).filter(
        models.MaterialBatch.id.in_(all_anomaly_batch_ids) if all_anomaly_batch_ids else False
    )
    anomaly_batch_query = _apply_data_scope(anomaly_batch_query, current_user)
    if store_id and current_user.role != models.UserRole.STORE_STAFF.value:
        anomaly_batch_query = anomaly_batch_query.filter(models.MaterialBatch.store_id == store_id)
    anomaly_batches = anomaly_batch_query.count()

    unresolved_anomaly_query = db.query(models.AnomalyEvent).filter(
        models.AnomalyEvent.is_resolved == False
    ).join(models.MaterialBatch)
    unresolved_anomaly_query = _apply_data_scope(unresolved_anomaly_query, current_user, models.MaterialBatch)
    if store_id and current_user.role != models.UserRole.STORE_STAFF.value:
        unresolved_anomaly_query = unresolved_anomaly_query.filter(models.MaterialBatch.store_id == store_id)
    unresolved_anomaly_count = unresolved_anomaly_query.count()

    pending_warning_query = db.query(models.FreshnessWarning).filter(
        models.FreshnessWarning.status.in_([
            models.WarningStatus.PENDING.value,
            models.WarningStatus.PROCESSING.value
        ])
    )
    pending_warning_query = _apply_data_scope(pending_warning_query, current_user)
    if store_id and current_user.role != models.UserRole.STORE_STAFF.value:
        pending_warning_query = pending_warning_query.filter(models.FreshnessWarning.store_id == store_id)
    pending_warning_count = pending_warning_query.count()

    pending_recheck_query = db.query(models.RecheckApplication).filter(
        models.RecheckApplication.status.in_([
            models.RecheckStatus.PENDING.value,
            models.RecheckStatus.IN_PROGRESS.value
        ])
    )
    pending_recheck_query = _apply_data_scope(pending_recheck_query, current_user)
    if store_id and current_user.role != models.UserRole.STORE_STAFF.value:
        pending_recheck_query = pending_recheck_query.filter(models.RecheckApplication.store_id == store_id)
    pending_recheck_count = pending_recheck_query.count()

    now = datetime.utcnow()
    overdue_count = 0
    all_pending_warnings = pending_warning_query.all()
    for w in all_pending_warnings:
        if now > w.deadline_time:
            overdue_count += 1
    all_pending_rechecks = pending_recheck_query.all()
    for r in all_pending_rechecks:
        deadline = r.applied_at + timedelta(hours=r.deadline_hours)
        if now > deadline:
            overdue_count += 1

    disposed_query = db.query(models.MaterialBatch).filter(
        models.MaterialBatch.created_at >= cutoff,
        or_(
            models.MaterialBatch.status == models.BatchStatus.DISCARDED.value,
            models.MaterialBatch.final_disposition != None
        )
    )
    disposed_query = _apply_data_scope(disposed_query, current_user)
    if store_id and current_user.role != models.UserRole.STORE_STAFF.value:
        disposed_query = disposed_query.filter(models.MaterialBatch.store_id == store_id)
    disposed_count = disposed_query.count()

    closed_loop_query = db.query(models.MaterialBatch).filter(
        models.MaterialBatch.created_at >= cutoff,
        or_(
            models.MaterialBatch.status == models.BatchStatus.DISCARDED.value,
            and_(
                models.MaterialBatch.status == models.BatchStatus.READY_FOR_SALE.value,
                models.MaterialBatch.id.notin_(all_anomaly_batch_ids) if all_anomaly_batch_ids else True
            )
        )
    )
    closed_loop_query = _apply_data_scope(closed_loop_query, current_user)
    if store_id and current_user.role != models.UserRole.STORE_STAFF.value:
        closed_loop_query = closed_loop_query.filter(models.MaterialBatch.store_id == store_id)

    if anomaly_batches > 0:
        resolved_anomaly_batches = 0
        for batch_id in all_anomaly_batch_ids:
            batch = db.query(models.MaterialBatch).filter(models.MaterialBatch.id == batch_id).first()
            if not batch:
                continue
            if current_user.role == models.UserRole.STORE_STAFF.value:
                if batch.store_id != current_user.store_id:
                    continue
            if store_id and current_user.role != models.UserRole.STORE_STAFF.value:
                if batch.store_id != store_id:
                    continue
            if batch.status in [models.BatchStatus.DISCARDED.value, models.BatchStatus.READY_FOR_SALE.value]:
                has_unresolved = db.query(models.AnomalyEvent).filter(
                    models.AnomalyEvent.batch_id == batch_id,
                    models.AnomalyEvent.is_resolved == False
                ).count() > 0
                if not has_unresolved:
                    resolved_anomaly_batches += 1
        closed_loop_rate = round(resolved_anomaly_batches / anomaly_batches, 4) if anomaly_batches > 0 else 0.0
    else:
        closed_loop_rate = 1.0 if total_batches > 0 else 0.0

    anomaly_rate = round(anomaly_batches / total_batches, 4) if total_batches > 0 else 0.0

    return schemas.DashboardOverviewStats(
        period_days=days,
        total_batches=total_batches,
        anomaly_batches=anomaly_batches,
        anomaly_rate=anomaly_rate,
        unresolved_anomaly_count=unresolved_anomaly_count,
        pending_warning_count=pending_warning_count,
        pending_recheck_count=pending_recheck_count,
        overdue_count=overdue_count,
        disposed_count=disposed_count,
        closed_loop_rate=closed_loop_rate
    )


@dashboard_router.get("/statistics/store-ranking", response_model=List[schemas.StoreAnomalyRankingItem])
def get_store_anomaly_ranking(
    days: int = Query(7, ge=1, le=90, description="统计天数"),
    limit: int = Query(20, ge=1, le=100, description="返回条数"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(
        models.UserRole.HQ_ADMIN.value
    ))
):
    cutoff = datetime.utcnow() - timedelta(days=days)
    stores = db.query(models.Store).filter(models.Store.is_active == True).all()

    ranking = []
    for store in stores:
        total_batches = db.query(models.MaterialBatch).filter(
            models.MaterialBatch.store_id == store.id,
            models.MaterialBatch.created_at >= cutoff
        ).count()

        anomaly_batch_ids = set()

        anomaly_events = db.query(models.AnomalyEvent.batch_id).join(
            models.MaterialBatch
        ).filter(
            models.MaterialBatch.store_id == store.id,
            models.AnomalyEvent.created_at >= cutoff
        ).distinct().all()
        for row in anomaly_events:
            anomaly_batch_ids.add(row[0])

        warnings = db.query(models.FreshnessWarning.batch_id).filter(
            models.FreshnessWarning.store_id == store.id,
            models.FreshnessWarning.detected_at >= cutoff
        ).distinct().all()
        for row in warnings:
            anomaly_batch_ids.add(row[0])

        rechecks = db.query(models.RecheckApplication.batch_id).filter(
            models.RecheckApplication.store_id == store.id,
            models.RecheckApplication.applied_at >= cutoff
        ).distinct().all()
        for row in rechecks:
            anomaly_batch_ids.add(row[0])

        anomaly_batches = len(anomaly_batch_ids)

        unresolved_count = db.query(models.AnomalyEvent).join(models.MaterialBatch).filter(
            models.MaterialBatch.store_id == store.id,
            models.AnomalyEvent.is_resolved == False
        ).count()

        now = datetime.utcnow()
        overdue_count = 0
        pending_warnings = db.query(models.FreshnessWarning).filter(
            models.FreshnessWarning.store_id == store.id,
            models.FreshnessWarning.status.in_([
                models.WarningStatus.PENDING.value,
                models.WarningStatus.PROCESSING.value
            ])
        ).all()
        for w in pending_warnings:
            if now > w.deadline_time:
                overdue_count += 1

        pending_rechecks = db.query(models.RecheckApplication).filter(
            models.RecheckApplication.store_id == store.id,
            models.RecheckApplication.status.in_([
                models.RecheckStatus.PENDING.value,
                models.RecheckStatus.IN_PROGRESS.value
            ])
        ).all()
        for r in pending_rechecks:
            deadline = r.applied_at + timedelta(hours=r.deadline_hours)
            if now > deadline:
                overdue_count += 1

        anomaly_rate = round(anomaly_batches / total_batches, 4) if total_batches > 0 else 0.0

        ranking.append({
            "store_id": store.id,
            "store_name": store.store_name,
            "total_batches": total_batches,
            "anomaly_batches": anomaly_batches,
            "anomaly_rate": anomaly_rate,
            "unresolved_count": unresolved_count,
            "overdue_count": overdue_count,
        })

    ranking.sort(key=lambda x: (-x["anomaly_rate"], -x["overdue_count"]))

    result = []
    for i, item in enumerate(ranking[:limit]):
        result.append(schemas.StoreAnomalyRankingItem(
            **item,
            rank=i + 1
        ))
    return result


@dashboard_router.get("/statistics/anomaly-trend", response_model=List[schemas.AnomalyTrendItem])
def get_anomaly_trend(
    store_id: Optional[int] = None,
    days: int = Query(14, ge=1, le=90, description="统计天数"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_active_user)
):
    start_date = (datetime.utcnow() - timedelta(days=days - 1)).date()
    end_date = datetime.utcnow().date()

    trend = []
    current = start_date
    while current <= end_date:
        day_start = datetime.combine(current, datetime.min.time())
        day_end = datetime.combine(current + timedelta(days=1), datetime.min.time())

        new_anomaly_query = db.query(func.count(models.AnomalyEvent.id)).filter(
            models.AnomalyEvent.detected_at >= day_start,
            models.AnomalyEvent.detected_at < day_end
        ).join(models.MaterialBatch)
        new_anomaly_query = _apply_data_scope(new_anomaly_query, current_user, models.MaterialBatch)
        if store_id and current_user.role != models.UserRole.STORE_STAFF.value:
            new_anomaly_query = new_anomaly_query.filter(models.MaterialBatch.store_id == store_id)
        new_count = new_anomaly_query.scalar() or 0

        resolved_query = db.query(func.count(models.AnomalyEvent.id)).filter(
            models.AnomalyEvent.resolved_at >= day_start,
            models.AnomalyEvent.resolved_at < day_end,
            models.AnomalyEvent.is_resolved == True
        ).join(models.MaterialBatch)
        resolved_query = _apply_data_scope(resolved_query, current_user, models.MaterialBatch)
        if store_id and current_user.role != models.UserRole.STORE_STAFF.value:
            resolved_query = resolved_query.filter(models.MaterialBatch.store_id == store_id)
        resolved_count = resolved_query.scalar() or 0

        now = datetime.utcnow()
        if current < end_date:
            day_deadline_check = day_end
        else:
            day_deadline_check = now

        overdue_count = 0
        warning_query = db.query(models.FreshnessWarning).filter(
            models.FreshnessWarning.status.in_([
                models.WarningStatus.PENDING.value,
                models.WarningStatus.PROCESSING.value
            ]),
            models.FreshnessWarning.deadline_time >= day_start,
            models.FreshnessWarning.deadline_time < day_deadline_check
        )
        warning_query = _apply_data_scope(warning_query, current_user)
        if store_id and current_user.role != models.UserRole.STORE_STAFF.value:
            warning_query = warning_query.filter(models.FreshnessWarning.store_id == store_id)
        for w in warning_query.all():
            if now > w.deadline_time:
                overdue_count += 1

        recheck_query = db.query(models.RecheckApplication).filter(
            models.RecheckApplication.status.in_([
                models.RecheckStatus.PENDING.value,
                models.RecheckStatus.IN_PROGRESS.value
            ])
        )
        recheck_query = _apply_data_scope(recheck_query, current_user)
        if store_id and current_user.role != models.UserRole.STORE_STAFF.value:
            recheck_query = recheck_query.filter(models.RecheckApplication.store_id == store_id)
        for r in recheck_query.all():
            deadline = r.applied_at + timedelta(hours=r.deadline_hours)
            if day_start <= deadline < day_deadline_check and now > deadline:
                overdue_count += 1

        trend.append(schemas.AnomalyTrendItem(
            date=current,
            new_anomaly_count=new_count,
            resolved_count=resolved_count,
            overdue_count=overdue_count
        ))
        current += timedelta(days=1)

    return trend


@dashboard_router.get("/statistics/anomaly-type-distribution", response_model=List[schemas.AnomalyTypeDistributionItem])
def get_anomaly_type_distribution(
    store_id: Optional[int] = None,
    days: int = Query(30, ge=1, le=365, description="统计天数"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_active_user)
):
    cutoff = datetime.utcnow() - timedelta(days=days)

    query = db.query(
        models.AnomalyEvent.anomaly_type,
        func.count(models.AnomalyEvent.id)
    ).join(models.MaterialBatch).filter(
        models.AnomalyEvent.detected_at >= cutoff
    )
    query = _apply_data_scope(query, current_user, models.MaterialBatch)
    if store_id and current_user.role != models.UserRole.STORE_STAFF.value:
        query = query.filter(models.MaterialBatch.store_id == store_id)
    query = query.group_by(models.AnomalyEvent.anomaly_type)

    results = query.all()
    total = sum(cnt for _, cnt in results)

    distribution = []
    for anomaly_type, count in results:
        percentage = round(count / total * 100, 2) if total > 0 else 0.0
        distribution.append(schemas.AnomalyTypeDistributionItem(
            anomaly_type=anomaly_type,
            anomaly_type_label=ANOMALY_TYPE_LABELS.get(anomaly_type, anomaly_type),
            count=count,
            percentage=percentage
        ))

    distribution.sort(key=lambda x: x.count, reverse=True)
    return distribution
