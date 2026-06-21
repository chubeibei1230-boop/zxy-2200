from datetime import datetime, date, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from app.database import get_db
from app import models, schemas, auth
from app.services import anomaly_detector

qc_router = APIRouter(prefix="/api/qc", tags=["品控操作"])


def _generate_recheck_app_no(db: Session) -> str:
    today = datetime.utcnow().strftime("%Y%m%d")
    prefix = f"RC{today}"
    last = db.query(models.RecheckApplication).filter(
        models.RecheckApplication.application_no.like(f"{prefix}%")
    ).order_by(models.RecheckApplication.application_no.desc()).first()
    if last:
        seq = int(last.application_no[-4:]) + 1
    else:
        seq = 1
    return f"{prefix}{seq:04d}"


def _auto_create_recheck_from_qc(
    db: Session,
    inspection: models.QCInspection,
    batch: models.MaterialBatch,
    current_user: models.User
):
    pending_exists = db.query(models.RecheckApplication).filter(
        models.RecheckApplication.batch_id == batch.id,
        models.RecheckApplication.status.in_([
            models.RecheckStatus.PENDING.value,
            models.RecheckStatus.IN_PROGRESS.value
        ])
    ).first()
    if pending_exists:
        return

    recheck_reason = models.RecheckReason.SCORE_BORDERLINE.value
    reason_detail = None
    if inspection.taste_deviation:
        recheck_reason = models.RecheckReason.TASTE_DEVIATION.value
        reason_detail = inspection.taste_deviation
    elif inspection.overall_score and inspection.overall_score < 60:
        recheck_reason = models.RecheckReason.APPEARANCE_ISSUE.value

    application = models.RecheckApplication(
        application_no=_generate_recheck_app_no(db),
        batch_id=batch.id,
        store_id=batch.store_id,
        source_qc_inspection_id=inspection.id,
        recheck_source=models.RecheckSource.QC_INSPECTION.value,
        recheck_reason=recheck_reason,
        reason_detail=reason_detail,
        supplementary_note=f"品控抽检结论: {inspection.disposition}. 说明: {inspection.disposition_note or ''}",
        status=models.RecheckStatus.PENDING.value,
        deadline_hours=24,
        applied_by=current_user.id,
        applied_at=datetime.utcnow()
    )
    db.add(application)


@qc_router.post("/inspections", response_model=schemas.QCInspectionResponse)
def create_qc_inspection(
    qc_in: schemas.QCInspectionCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(
        models.UserRole.QC_STAFF.value,
        models.UserRole.HQ_ADMIN.value
    ))
):
    batch = db.query(models.MaterialBatch).filter(models.MaterialBatch.id == qc_in.batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    valid_statuses = [
        models.BatchStatus.PENDING_QC.value,
        models.BatchStatus.ANOMALY_HOLD.value,
    ]
    if batch.status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"当前批次状态[{batch.status}]不可执行抽检，需待抽检或异常留观状态"
        )
    if qc_in.template_id:
        tpl = db.query(models.QCTemplate).filter(models.QCTemplate.id == qc_in.template_id).first()
        if not tpl:
            raise HTTPException(status_code=400, detail="品控模板不存在")
    inspection = models.QCInspection(
        **qc_in.model_dump(),
        store_id=batch.store_id,
        inspector_id=current_user.id,
        inspection_time=datetime.utcnow()
    )
    db.add(inspection)
    db.flush()
    if qc_in.disposition:
        if "放行" in qc_in.disposition or "通过" in qc_in.disposition or "合格" in qc_in.disposition:
            if batch.status in [
                models.BatchStatus.PENDING_QC.value,
                models.BatchStatus.ANOMALY_HOLD.value
            ]:
                batch.status = models.BatchStatus.READY_FOR_SALE.value
                batch.sale_start_time = datetime.utcnow()
        elif "废弃" in qc_in.disposition or "报废" in qc_in.disposition:
            batch.status = models.BatchStatus.DISCARDED.value
            batch.discard_time = datetime.utcnow()
            batch.final_disposition = qc_in.disposition_note or qc_in.disposition
        elif "留观" in qc_in.disposition or "复检" in qc_in.disposition:
            batch.status = models.BatchStatus.ANOMALY_HOLD.value
            _auto_create_recheck_from_qc(db, inspection, batch, current_user)
    db.commit()
    db.refresh(inspection)
    return inspection


@qc_router.put("/inspections/{inspection_id}", response_model=schemas.QCInspectionResponse)
def update_qc_inspection(
    inspection_id: int,
    qc_in: schemas.QCInspectionUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(
        models.UserRole.QC_STAFF.value,
        models.UserRole.HQ_ADMIN.value
    ))
):
    inspection = db.query(models.QCInspection).filter(models.QCInspection.id == inspection_id).first()
    if not inspection:
        raise HTTPException(status_code=404, detail="品控抽检记录不存在")
    if inspection.inspector_id != current_user.id and current_user.role != models.UserRole.HQ_ADMIN.value:
        raise HTTPException(status_code=403, detail="无权限修改他人的抽检记录")
    update_data = qc_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(inspection, field, value)
    db.flush()
    batch = db.query(models.MaterialBatch).filter(models.MaterialBatch.id == inspection.batch_id).first()
    if batch and qc_in.disposition:
        if "放行" in qc_in.disposition or "通过" in qc_in.disposition or "合格" in qc_in.disposition:
            if batch.status in [
                models.BatchStatus.PENDING_QC.value,
                models.BatchStatus.ANOMALY_HOLD.value
            ]:
                batch.status = models.BatchStatus.READY_FOR_SALE.value
                batch.sale_start_time = datetime.utcnow()
        elif "废弃" in qc_in.disposition or "报废" in qc_in.disposition:
            batch.status = models.BatchStatus.DISCARDED.value
            batch.discard_time = datetime.utcnow()
            batch.final_disposition = qc_in.disposition_note or qc_in.disposition
        elif "留观" in qc_in.disposition or "复检" in qc_in.disposition:
            batch.status = models.BatchStatus.ANOMALY_HOLD.value
            _auto_create_recheck_from_qc(db, inspection, batch, current_user)
    db.commit()
    db.refresh(inspection)
    return inspection


@qc_router.get("/inspections", response_model=List[schemas.QCInspectionResponse])
def list_qc_inspections(
    store_id: Optional[int] = None,
    ingredient_category_id: Optional[int] = None,
    batch_no: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_active_user)
):
    if current_user.role == models.UserRole.STORE_STAFF.value:
        store_id = current_user.store_id
    query = db.query(models.QCInspection)
    if store_id:
        query = query.filter(models.QCInspection.store_id == store_id)
    if batch_no or ingredient_category_id:
        query = query.join(models.MaterialBatch)
        if batch_no:
            query = query.filter(models.MaterialBatch.batch_no.contains(batch_no))
        if ingredient_category_id:
            query = query.filter(models.MaterialBatch.ingredient_category_id == ingredient_category_id)
    if date_from:
        query = query.filter(models.QCInspection.created_at >= date_from)
    if date_to:
        date_to_end = datetime.combine(date_to + timedelta(days=1), datetime.min.time())
        query = query.filter(models.QCInspection.created_at < date_to_end)
    return query.order_by(models.QCInspection.created_at.desc()).all()


@qc_router.get("/inspections/{inspection_id}", response_model=schemas.QCInspectionResponse)
def get_qc_inspection(
    inspection_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_active_user)
):
    inspection = db.query(models.QCInspection).filter(models.QCInspection.id == inspection_id).first()
    if not inspection:
        raise HTTPException(status_code=404, detail="品控抽检记录不存在")
    if current_user.role == models.UserRole.STORE_STAFF.value:
        if inspection.store_id != current_user.store_id:
            raise HTTPException(status_code=403, detail="无权限查看其他门店数据")
    return inspection


@qc_router.get("/anomalies", response_model=List[schemas.AnomalyEventResponse])
def list_anomalies(
    store_id: Optional[int] = None,
    anomaly_type: Optional[str] = None,
    is_resolved: Optional[bool] = False,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_active_user)
):
    if current_user.role == models.UserRole.STORE_STAFF.value:
        store_id = current_user.store_id
    query = db.query(models.AnomalyEvent)
    if store_id:
        query = query.join(models.MaterialBatch).filter(
            models.MaterialBatch.store_id == store_id
        )
    if anomaly_type:
        query = query.filter(models.AnomalyEvent.anomaly_type == anomaly_type)
    if is_resolved is not None:
        query = query.filter(models.AnomalyEvent.is_resolved == is_resolved)
    if date_from:
        query = query.filter(models.AnomalyEvent.created_at >= date_from)
    if date_to:
        date_to_end = datetime.combine(date_to + timedelta(days=1), datetime.min.time())
        query = query.filter(models.AnomalyEvent.created_at < date_to_end)
    return query.order_by(models.AnomalyEvent.detected_at.desc()).all()


@qc_router.post("/anomalies/{anomaly_id}/resolve", response_model=schemas.AnomalyEventResponse)
def resolve_anomaly(
    anomaly_id: int,
    resolve_in: schemas.AnomalyEventResolve,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(
        models.UserRole.QC_STAFF.value,
        models.UserRole.HQ_ADMIN.value,
        models.UserRole.STORE_STAFF.value
    ))
):
    anomaly = db.query(models.AnomalyEvent).filter(models.AnomalyEvent.id == anomaly_id).first()
    if not anomaly:
        raise HTTPException(status_code=404, detail="异常事件不存在")
    if current_user.role == models.UserRole.STORE_STAFF.value:
        batch = db.query(models.MaterialBatch).filter(models.MaterialBatch.id == anomaly.batch_id).first()
        if batch and batch.store_id != current_user.store_id:
            raise HTTPException(status_code=403, detail="无权限处理其他门店异常")
    anomaly.is_resolved = True
    anomaly.resolved_at = datetime.utcnow()
    anomaly.resolved_by = current_user.id
    anomaly.resolution_note = resolve_in.resolution_note
    db.commit()
    db.refresh(anomaly)
    return anomaly


@qc_router.post("/run-detection")
def run_detection(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(
        models.UserRole.QC_STAFF.value,
        models.UserRole.HQ_ADMIN.value
    ))
):
    result = anomaly_detector.run_all_detections(db)
    return {"message": "异常检测执行完成", "details": result}


@qc_router.get("/batches/{batch_id}/inspections", response_model=List[schemas.QCInspectionResponse])
def get_batch_qc_inspections(
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
    return db.query(models.QCInspection).filter(
        models.QCInspection.batch_id == batch_id
    ).order_by(models.QCInspection.created_at.desc()).all()


@qc_router.get("/batches/{batch_id}/anomalies", response_model=List[schemas.AnomalyEventResponse])
def get_batch_anomalies(
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
    return db.query(models.AnomalyEvent).filter(
        models.AnomalyEvent.batch_id == batch_id
    ).order_by(models.AnomalyEvent.detected_at.desc()).all()
