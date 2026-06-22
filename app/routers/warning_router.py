from datetime import datetime, date, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from app.database import get_db
from app import models, schemas, auth
from app.services import freshness_warning as warning_service


warning_router = APIRouter(prefix="/api/warnings", tags=["临期预警"])


def _apply_data_scope(query, current_user: models.User):
    if current_user.role == models.UserRole.STORE_STAFF.value:
        query = query.filter(models.FreshnessWarning.store_id == current_user.store_id)
    return query


@warning_router.post("/run-detection")
def run_detection(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(
        models.UserRole.QC_STAFF.value,
        models.UserRole.HQ_ADMIN.value,
        models.UserRole.STORE_STAFF.value
    ))
):
    result = warning_service.detect_freshness_warnings(db)
    return {"message": "临期预警检测执行完成", "details": result}


@warning_router.get("", response_model=List[schemas.FreshnessWarningDetailResponse])
def list_warnings(
    store_id: Optional[int] = None,
    ingredient_category_id: Optional[int] = None,
    batch_id: Optional[int] = None,
    batch_no: Optional[str] = None,
    stage: Optional[str] = None,
    warning_level: Optional[str] = None,
    status: Optional[str] = None,
    is_overdue: Optional[bool] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_active_user)
):
    warnings = warning_service.get_warning_list(
        db, current_user,
        store_id=store_id,
        ingredient_category_id=ingredient_category_id,
        batch_id=batch_id,
        batch_no=batch_no,
        stage=stage,
        warning_level=warning_level,
        status=status,
        is_overdue=is_overdue,
        date_from=date_from,
        date_to=date_to
    )
    return warnings


@warning_router.get("/my-pending", response_model=List[schemas.FreshnessWarningDetailResponse])
def get_my_pending_warnings(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_active_user)
):
    query = db.query(models.FreshnessWarning).filter(
        models.FreshnessWarning.status.in_([
            models.WarningStatus.PENDING.value,
            models.WarningStatus.PROCESSING.value
        ])
    )
    query = _apply_data_scope(query, current_user)

    if current_user.role == models.UserRole.QC_STAFF.value:
        query = query.filter(
            models.FreshnessWarning.stage.in_([
                models.WarningStage.AFTER_PRODUCTION.value,
                models.WarningStage.PENDING_QC.value
            ])
        )

    return query.order_by(
        models.FreshnessWarning.is_overdue.desc(),
        models.FreshnessWarning.warning_level.desc(),
        models.FreshnessWarning.created_at.asc()
    ).all()


@warning_router.get("/batches/{batch_id}", response_model=List[schemas.FreshnessWarningResponse])
def get_batch_warnings(
    batch_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_active_user)
):
    batch = db.query(models.MaterialBatch).filter(
        models.MaterialBatch.id == batch_id
    ).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")

    if current_user.role == models.UserRole.STORE_STAFF.value:
        if batch.store_id != current_user.store_id:
            raise HTTPException(status_code=403, detail="无权限查看其他门店数据")

    warnings = db.query(models.FreshnessWarning).filter(
        models.FreshnessWarning.batch_id == batch_id
    ).order_by(models.FreshnessWarning.created_at.desc()).all()

    return warnings


@warning_router.get("/statistics/overview", response_model=schemas.WarningStatsOverview)
def get_warning_stats(
    store_id: Optional[int] = None,
    days: int = Query(7, ge=1, le=30, description="统计天数"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_active_user)
):
    stats = warning_service.get_warning_stats_overview(
        db, current_user, days=days, store_id=store_id
    )
    return stats


@warning_router.get("/statistics/trend", response_model=List[Dict[str, Any]])
def get_trend(
    store_id: Optional[int] = None,
    days: int = Query(7, ge=1, le=30, description="统计天数"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_active_user)
):
    trend = warning_service.get_warning_trend(
        db, current_user, days=days, store_id=store_id
    )
    return trend


@warning_router.get("/statistics/store-ranking", response_model=List[Dict[str, Any]])
def get_store_ranking(
    days: int = Query(7, ge=1, le=30, description="统计天数"),
    limit: int = Query(20, ge=1, le=100, description="返回条数"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(
        models.UserRole.HQ_ADMIN.value
    ))
):
    ranking = warning_service.get_store_ranking(
        db, current_user, days=days, limit=limit
    )
    return ranking


@warning_router.get("/statistics/level-distribution", response_model=List[Dict[str, Any]])
def get_level_distribution(
    store_id: Optional[int] = None,
    days: int = Query(7, ge=1, le=30, description="统计天数"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_active_user)
):
    distribution = warning_service.get_warning_level_distribution(
        db, current_user, days=days, store_id=store_id
    )
    return distribution


@warning_router.get("/{warning_id}", response_model=schemas.FreshnessWarningDetailResponse)
def get_warning_detail(
    warning_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_active_user)
):
    warning = db.query(models.FreshnessWarning).filter(
        models.FreshnessWarning.id == warning_id
    ).first()
    if not warning:
        raise HTTPException(status_code=404, detail="预警记录不存在")

    if current_user.role == models.UserRole.STORE_STAFF.value:
        if warning.store_id != current_user.store_id:
            raise HTTPException(status_code=403, detail="无权限查看其他门店数据")

    return warning


@warning_router.post("/{warning_id}/store-note", response_model=schemas.FreshnessWarningDetailResponse)
def add_store_note(
    warning_id: int,
    note_in: schemas.FreshnessWarningStoreNote,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(
        models.UserRole.STORE_STAFF.value,
        models.UserRole.HQ_ADMIN.value
    ))
):
    warning = db.query(models.FreshnessWarning).filter(
        models.FreshnessWarning.id == warning_id
    ).first()
    if not warning:
        raise HTTPException(status_code=404, detail="预警记录不存在")

    if current_user.role == models.UserRole.STORE_STAFF.value:
        if warning.store_id != current_user.store_id:
            raise HTTPException(status_code=403, detail="无权限处理其他门店预警")

    if warning.status not in [
        models.WarningStatus.PENDING.value,
        models.WarningStatus.PROCESSING.value
    ]:
        raise HTTPException(status_code=400, detail=f"当前预警状态[{warning.status}]不可添加处置说明")

    warning.status = models.WarningStatus.PROCESSING.value
    warning.processing_note = note_in.processing_note
    if not warning.processed_by:
        warning.processed_by = current_user.id

    warning_service.create_disposal_record(
        db,
        warning_id=warning.id,
        batch_id=warning.batch_id,
        store_id=warning.store_id,
        disposal_type=models.DisposalType.STORE_NOTE.value,
        operator_id=current_user.id,
        disposal_note=note_in.processing_note
    )

    db.commit()
    db.refresh(warning)
    return warning


@warning_router.post("/{warning_id}/priority-qc", response_model=schemas.FreshnessWarningDetailResponse)
def prioritize_qc_inspection(
    warning_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(
        models.UserRole.QC_STAFF.value,
        models.UserRole.HQ_ADMIN.value
    ))
):
    warning = db.query(models.FreshnessWarning).filter(
        models.FreshnessWarning.id == warning_id
    ).first()
    if not warning:
        raise HTTPException(status_code=404, detail="预警记录不存在")

    if warning.status not in [
        models.WarningStatus.PENDING.value,
        models.WarningStatus.PROCESSING.value
    ]:
        raise HTTPException(status_code=400, detail=f"当前预警状态[{warning.status}]不可标记优先抽检")

    batch = db.query(models.MaterialBatch).filter(
        models.MaterialBatch.id == warning.batch_id
    ).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")

    if batch.status not in [
        models.BatchStatus.PENDING_QC.value,
        models.BatchStatus.ANOMALY_HOLD.value
    ]:
        raise HTTPException(
            status_code=400,
            detail=f"批次当前状态[{batch.status}]不可执行优先抽检，需待抽检或异常留观状态"
        )

    warning.status = models.WarningStatus.PROCESSING.value
    warning.warning_level = models.WarningLevel.URGENT.value
    warning.processed_by = current_user.id

    warning_service.create_disposal_record(
        db,
        warning_id=warning.id,
        batch_id=warning.batch_id,
        store_id=warning.store_id,
        disposal_type=models.DisposalType.QC_INSPECTION.value,
        operator_id=current_user.id,
        disposal_note="品控人员已标记优先抽检"
    )

    db.commit()
    db.refresh(warning)
    return warning


@warning_router.post("/{warning_id}/priority-recheck", response_model=schemas.FreshnessWarningDetailResponse)
def prioritize_recheck(
    warning_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(
        models.UserRole.QC_STAFF.value,
        models.UserRole.HQ_ADMIN.value
    ))
):
    warning = db.query(models.FreshnessWarning).filter(
        models.FreshnessWarning.id == warning_id
    ).first()
    if not warning:
        raise HTTPException(status_code=404, detail="预警记录不存在")

    if warning.status not in [
        models.WarningStatus.PENDING.value,
        models.WarningStatus.PROCESSING.value
    ]:
        raise HTTPException(status_code=400, detail=f"当前预警状态[{warning.status}]不可标记优先复检")

    batch = db.query(models.MaterialBatch).filter(
        models.MaterialBatch.id == warning.batch_id
    ).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")

    pending_recheck = db.query(models.RecheckApplication).filter(
        models.RecheckApplication.batch_id == batch.id,
        models.RecheckApplication.status.in_([
            models.RecheckStatus.PENDING.value,
            models.RecheckStatus.IN_PROGRESS.value
        ])
    ).first()

    if not pending_recheck:
        raise HTTPException(status_code=400, detail="该批次暂无待处理的复检申请，请先发起复检")

    warning.status = models.WarningStatus.PROCESSING.value
    warning.warning_level = models.WarningLevel.URGENT.value
    warning.processed_by = current_user.id

    warning_service.create_disposal_record(
        db,
        warning_id=warning.id,
        batch_id=warning.batch_id,
        store_id=warning.store_id,
        disposal_type=models.DisposalType.QC_RECHECK.value,
        operator_id=current_user.id,
        disposal_note="品控人员已标记优先复检",
        recheck_application_id=pending_recheck.id
    )

    db.commit()
    db.refresh(warning)
    return warning


@warning_router.post("/{warning_id}/close", response_model=schemas.FreshnessWarningDetailResponse)
def close_warning(
    warning_id: int,
    process_in: schemas.FreshnessWarningProcess,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(
        models.UserRole.HQ_ADMIN.value,
        models.UserRole.QC_STAFF.value,
        models.UserRole.STORE_STAFF.value
    ))
):
    warning = db.query(models.FreshnessWarning).filter(
        models.FreshnessWarning.id == warning_id
    ).first()
    if not warning:
        raise HTTPException(status_code=404, detail="预警记录不存在")

    if current_user.role == models.UserRole.STORE_STAFF.value:
        if warning.store_id != current_user.store_id:
            raise HTTPException(status_code=403, detail="无权限处理其他门店预警")

    if warning.status not in [
        models.WarningStatus.PENDING.value,
        models.WarningStatus.PROCESSING.value
    ]:
        raise HTTPException(status_code=400, detail=f"当前预警状态[{warning.status}]不可关闭")

    warning.status = models.WarningStatus.RESOLVED.value
    warning.processed_at = datetime.utcnow()
    warning.processed_by = current_user.id
    warning.processing_note = process_in.processing_note
    warning.final_disposition = process_in.final_disposition

    warning_service.create_disposal_record(
        db,
        warning_id=warning.id,
        batch_id=warning.batch_id,
        store_id=warning.store_id,
        disposal_type=models.DisposalType.HQ_REVIEW.value,
        operator_id=current_user.id,
        disposal_note=process_in.processing_note
    )

    db.commit()
    db.refresh(warning)
    return warning


@warning_router.get("/{warning_id}/disposal-records", response_model=List[schemas.WarningDisposalRecordResponse])
def get_warning_disposal_records(
    warning_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_active_user)
):
    warning = db.query(models.FreshnessWarning).filter(
        models.FreshnessWarning.id == warning_id
    ).first()
    if not warning:
        raise HTTPException(status_code=404, detail="预警记录不存在")

    if current_user.role == models.UserRole.STORE_STAFF.value:
        if warning.store_id != current_user.store_id:
            raise HTTPException(status_code=403, detail="无权限查看其他门店数据")

    records = db.query(models.WarningDisposalRecord).filter(
        models.WarningDisposalRecord.warning_id == warning_id
    ).order_by(models.WarningDisposalRecord.created_at.desc()).all()

    return records


@warning_router.post("/{warning_id}/hq-review", response_model=schemas.FreshnessWarningDetailResponse)
def hq_review_warning(
    warning_id: int,
    process_in: schemas.FreshnessWarningProcess,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(
        models.UserRole.HQ_ADMIN.value
    ))
):
    warning = db.query(models.FreshnessWarning).filter(
        models.FreshnessWarning.id == warning_id
    ).first()
    if not warning:
        raise HTTPException(status_code=404, detail="预警记录不存在")

    warning.status = models.WarningStatus.RESOLVED.value
    warning.processed_at = datetime.utcnow()
    warning.processed_by = current_user.id
    warning.processing_note = process_in.processing_note
    warning.final_disposition = process_in.final_disposition

    warning_service.create_disposal_record(
        db,
        warning_id=warning.id,
        batch_id=warning.batch_id,
        store_id=warning.store_id,
        disposal_type=models.DisposalType.HQ_REVIEW.value,
        operator_id=current_user.id,
        disposal_note=f"总部审核：{process_in.processing_note}"
    )

    db.commit()
    db.refresh(warning)
    return warning
