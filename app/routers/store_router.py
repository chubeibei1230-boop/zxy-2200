from datetime import datetime, date, timedelta
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from app.database import get_db
from app import models, schemas, auth
from app.services import anomaly_detector

store_router = APIRouter(prefix="/api/store", tags=["门店操作"])


def _resolve_store_id(
    target_store_id: Optional[int],
    current_user: models.User
) -> int:
    if current_user.role == models.UserRole.STORE_STAFF.value:
        if not current_user.store_id:
            raise HTTPException(status_code=400, detail="当前用户未关联门店")
        if target_store_id and target_store_id != current_user.store_id:
            raise HTTPException(status_code=403, detail="无权限操作其他门店数据")
        return current_user.store_id
    if current_user.role in (models.UserRole.HQ_ADMIN.value, models.UserRole.QC_STAFF.value):
        if not target_store_id:
            raise HTTPException(status_code=400, detail="请指定门店ID")
        return target_store_id
    raise HTTPException(status_code=403, detail="无权限执行此操作")


@store_router.post("/batches", response_model=schemas.MaterialBatchResponse)
def register_batch(
    batch_in: schemas.MaterialBatchCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(
        models.UserRole.STORE_STAFF.value,
        models.UserRole.HQ_ADMIN.value
    ))
):
    store_id = _resolve_store_id(batch_in.store_id, current_user)
    cat = db.query(models.IngredientCategory).filter(
        models.IngredientCategory.id == batch_in.ingredient_category_id
    ).first()
    if not cat:
        raise HTTPException(status_code=400, detail="原料类别不存在")
    batch = models.MaterialBatch(
        **batch_in.model_dump(exclude={"store_id"}),
        store_id=store_id,
        arrival_time=datetime.utcnow()
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return batch


@store_router.post("/acceptance", response_model=schemas.MaterialAcceptanceResponse)
def accept_material(
    acc_in: schemas.MaterialAcceptanceCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(
        models.UserRole.STORE_STAFF.value,
        models.UserRole.HQ_ADMIN.value
    ))
):
    batch = db.query(models.MaterialBatch).filter(
        models.MaterialBatch.id == acc_in.batch_id
    ).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    store_id = _resolve_store_id(batch.store_id, current_user)
    existing_acc = db.query(models.MaterialAcceptance).filter(
        models.MaterialAcceptance.batch_id == acc_in.batch_id
    ).first()
    if existing_acc:
        raise HTTPException(status_code=400, detail="该批次已完成验收，不可重复验收")
    duplicate = db.query(models.MaterialBatch).filter(
        models.MaterialBatch.batch_no == batch.batch_no,
        models.MaterialBatch.store_id == store_id,
        models.MaterialBatch.id != batch.id
    ).first()
    if duplicate:
        dup_acc = db.query(models.MaterialAcceptance).filter(
            models.MaterialAcceptance.batch_id == duplicate.id
        ).first()
        if dup_acc:
            raise HTTPException(status_code=400, detail="同一原料批号在同门店不可重复验收")
    acc = models.MaterialAcceptance(
        **acc_in.model_dump(),
        store_id=store_id,
        accepted_by=current_user.id,
        acceptance_time=datetime.utcnow()
    )
    db.add(acc)
    if acc.is_accepted:
        batch.status = models.BatchStatus.READY_FOR_PRODUCTION.value
    else:
        batch.status = models.BatchStatus.DISCARDED.value
        batch.discard_time = datetime.utcnow()
        batch.final_disposition = "验收不通过废弃"
    db.commit()
    db.refresh(acc)
    return acc


@store_router.post("/batches/{batch_id}/wash-complete", response_model=schemas.MaterialBatchResponse)
def mark_wash_complete(
    batch_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(
        models.UserRole.STORE_STAFF.value,
        models.UserRole.HQ_ADMIN.value
    ))
):
    batch = db.query(models.MaterialBatch).filter(models.MaterialBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    _resolve_store_id(batch.store_id, current_user)
    if batch.status != models.BatchStatus.READY_FOR_PRODUCTION.value:
        raise HTTPException(status_code=400, detail=f"当前状态[{batch.status}]不可标记清洗完成，需为可制作状态")
    batch.wash_complete_time = datetime.utcnow()
    db.commit()
    db.refresh(batch)
    return batch


@store_router.post("/batches/{batch_id}/start-production", response_model=schemas.MaterialBatchResponse)
def start_production(
    batch_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(
        models.UserRole.STORE_STAFF.value,
        models.UserRole.HQ_ADMIN.value
    ))
):
    batch = db.query(models.MaterialBatch).filter(models.MaterialBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    _resolve_store_id(batch.store_id, current_user)
    valid_statuses = [
        models.BatchStatus.READY_FOR_PRODUCTION.value,
    ]
    if batch.status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"当前状态[{batch.status}]不可开始制作，需先通过验收")
    if not batch.wash_complete_time:
        raise HTTPException(status_code=400, detail="清洗尚未完成，不可开始制作，请先标记清洗完成")
    batch.status = models.BatchStatus.IN_PRODUCTION.value
    batch.production_start_time = datetime.utcnow()
    db.commit()
    db.refresh(batch)
    return batch


@store_router.post("/production-records", response_model=schemas.ProductionRecordResponse)
def create_production_record(
    rec_in: schemas.ProductionRecordCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(
        models.UserRole.STORE_STAFF.value,
        models.UserRole.HQ_ADMIN.value
    ))
):
    batch = db.query(models.MaterialBatch).filter(models.MaterialBatch.id == rec_in.batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    if batch.status != models.BatchStatus.IN_PRODUCTION.value:
        raise HTTPException(status_code=400, detail=f"当前批次状态[{batch.status}]不可创建制作记录，需先开始制作")
    store_id = _resolve_store_id(batch.store_id, current_user)
    station = db.query(models.ProductionStation).filter(
        models.ProductionStation.id == rec_in.station_id
    ).first()
    if not station:
        raise HTTPException(status_code=400, detail="制作岗位不存在")
    rec = models.ProductionRecord(
        **rec_in.model_dump(),
        store_id=store_id,
        operator_id=current_user.id,
        start_time=datetime.utcnow()
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return rec


@store_router.put("/production-records/{record_id}", response_model=schemas.ProductionRecordResponse)
def update_production_record(
    record_id: int,
    rec_in: schemas.ProductionRecordUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(
        models.UserRole.STORE_STAFF.value,
        models.UserRole.HQ_ADMIN.value
    ))
):
    rec = db.query(models.ProductionRecord).filter(models.ProductionRecord.id == record_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="制作记录不存在")
    _resolve_store_id(rec.store_id, current_user)
    update_data = rec_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(rec, field, value)
    if rec_in.end_time:
        batch = db.query(models.MaterialBatch).filter(models.MaterialBatch.id == rec.batch_id).first()
        if batch and batch.status == models.BatchStatus.IN_PRODUCTION.value:
            batch.status = models.BatchStatus.PENDING_QC.value
            batch.production_complete_time = rec_in.end_time
            batch_rule = db.query(models.BatchRule).filter(
                models.BatchRule.ingredient_category_id == batch.ingredient_category_id,
                models.BatchRule.is_active == True
            ).first()
            if batch_rule and rec.cups_produced + rec.cups_discarded > 0:
                ratio = rec.cups_discarded / (rec.cups_produced + rec.cups_discarded)
                if ratio > batch_rule.high_discard_threshold:
                    anomaly_detector.create_anomaly_event(
                        db, batch.id,
                        models.AnomalyType.HIGH_DISCARD_RATIO.value,
                        "high",
                        f"废弃比例{ratio:.2%}超过阈值{batch_rule.high_discard_threshold:.2%}"
                    )
    db.commit()
    db.refresh(rec)
    return rec


@store_router.post("/temperature-logs", response_model=schemas.TemperatureLogResponse)
def add_temperature_log(
    log_in: schemas.TemperatureLogCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(
        models.UserRole.STORE_STAFF.value,
        models.UserRole.HQ_ADMIN.value
    ))
):
    batch = db.query(models.MaterialBatch).filter(models.MaterialBatch.id == log_in.batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    _resolve_store_id(batch.store_id, current_user)
    log = models.TemperatureLog(
        **log_in.model_dump(),
        log_time=datetime.utcnow(),
        recorded_by=current_user.id
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


@store_router.post("/batches/{batch_id}/mark-saleable", response_model=schemas.MaterialBatchResponse)
def mark_saleable(
    batch_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(
        models.UserRole.STORE_STAFF.value,
        models.UserRole.HQ_ADMIN.value
    ))
):
    batch = db.query(models.MaterialBatch).filter(models.MaterialBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    _resolve_store_id(batch.store_id, current_user)
    valid_statuses = [
        models.BatchStatus.PENDING_QC.value,
        models.BatchStatus.ANOMALY_HOLD.value
    ]
    if batch.status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"当前状态[{batch.status}]不可标记为可销售")
    if current_user.role == models.UserRole.STORE_STAFF.value:
        qc_done = db.query(models.QCInspection).filter(
            models.QCInspection.batch_id == batch_id
        ).first()
        if not qc_done:
            raise HTTPException(status_code=403, detail="门店人员不可直接放行，请先由品控人员完成抽检")
    batch.status = models.BatchStatus.READY_FOR_SALE.value
    batch.sale_start_time = datetime.utcnow()
    db.commit()
    db.refresh(batch)
    return batch


@store_router.post("/batches/{batch_id}/discard", response_model=schemas.MaterialBatchResponse)
def discard_batch(
    batch_id: int,
    reason: str = Query(..., description="废弃原因"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(
        models.UserRole.STORE_STAFF.value,
        models.UserRole.HQ_ADMIN.value
    ))
):
    batch = db.query(models.MaterialBatch).filter(models.MaterialBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    _resolve_store_id(batch.store_id, current_user)
    batch.status = models.BatchStatus.DISCARDED.value
    batch.discard_time = datetime.utcnow()
    batch.final_disposition = reason
    db.commit()
    db.refresh(batch)
    return batch


@store_router.get("/batches", response_model=List[schemas.MaterialBatchDetailResponse])
def list_batches(
    store_id: Optional[int] = None,
    ingredient_category_id: Optional[int] = None,
    batch_no: Optional[str] = None,
    status: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_active_user)
):
    if current_user.role == models.UserRole.STORE_STAFF.value:
        store_id = current_user.store_id
    query = db.query(models.MaterialBatch)
    if store_id:
        query = query.filter(models.MaterialBatch.store_id == store_id)
    if ingredient_category_id:
        query = query.filter(models.MaterialBatch.ingredient_category_id == ingredient_category_id)
    if batch_no:
        query = query.filter(models.MaterialBatch.batch_no.contains(batch_no))
    if status:
        query = query.filter(models.MaterialBatch.status == status)
    if date_from:
        query = query.filter(models.MaterialBatch.created_at >= date_from)
    if date_to:
        date_to_end = datetime.combine(date_to + timedelta(days=1), datetime.min.time())
        query = query.filter(models.MaterialBatch.created_at < date_to_end)
    return query.order_by(models.MaterialBatch.created_at.desc()).all()


@store_router.get("/batches/{batch_id}", response_model=schemas.MaterialBatchDetailResponse)
def get_batch_detail(
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
    return batch


@store_router.get("/batches/{batch_id}/production-records", response_model=List[schemas.ProductionRecordResponse])
def get_batch_production_records(
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
    return db.query(models.ProductionRecord).filter(
        models.ProductionRecord.batch_id == batch_id
    ).order_by(models.ProductionRecord.created_at.desc()).all()


@store_router.get("/batches/{batch_id}/temperature-logs", response_model=List[schemas.TemperatureLogResponse])
def get_batch_temperature_logs(
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
    return db.query(models.TemperatureLog).filter(
        models.TemperatureLog.batch_id == batch_id
    ).order_by(models.TemperatureLog.log_time.desc()).all()


@store_router.get("/production-records", response_model=List[schemas.ProductionRecordResponse])
def list_production_records(
    store_id: Optional[int] = None,
    ingredient_category_id: Optional[int] = None,
    batch_no: Optional[str] = None,
    station_id: Optional[int] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_active_user)
):
    if current_user.role == models.UserRole.STORE_STAFF.value:
        store_id = current_user.store_id
    query = db.query(models.ProductionRecord)
    if store_id:
        query = query.filter(models.ProductionRecord.store_id == store_id)
    if station_id:
        query = query.filter(models.ProductionRecord.station_id == station_id)
    if batch_no or ingredient_category_id:
        query = query.join(models.MaterialBatch)
        if batch_no:
            query = query.filter(models.MaterialBatch.batch_no.contains(batch_no))
        if ingredient_category_id:
            query = query.filter(models.MaterialBatch.ingredient_category_id == ingredient_category_id)
    if date_from:
        query = query.filter(models.ProductionRecord.created_at >= date_from)
    if date_to:
        date_to_end = datetime.combine(date_to + timedelta(days=1), datetime.min.time())
        query = query.filter(models.ProductionRecord.created_at < date_to_end)
    return query.order_by(models.ProductionRecord.created_at.desc()).all()
