from datetime import datetime, date, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from typing import List, Optional, Dict, Any
from app.database import get_db
from app import models, schemas, auth

recheck_router = APIRouter(prefix="/api/recheck", tags=["批次复检管理"])


def _generate_application_no(db: Session) -> str:
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
    if current_user.role == models.UserRole.HQ_ADMIN.value:
        if not target_store_id:
            raise HTTPException(status_code=400, detail="请指定门店ID")
        return target_store_id
    raise HTTPException(status_code=403, detail="无权限执行此操作")


def _apply_data_scope(query, current_user: models.User):
    if current_user.role == models.UserRole.STORE_STAFF.value:
        query = query.filter(models.RecheckApplication.store_id == current_user.store_id)
    return query


def _sync_batch_status_after_recheck(
    db: Session,
    batch: models.MaterialBatch,
    recheck_result: str,
    disposition_note: Optional[str] = None
):
    if recheck_result == models.RecheckResult.QUALIFIED.value:
        batch.status = models.BatchStatus.READY_FOR_SALE.value
        batch.sale_start_time = datetime.utcnow()
        batch.final_disposition = None
        open_anomalies = db.query(models.AnomalyEvent).filter(
            models.AnomalyEvent.batch_id == batch.id,
            models.AnomalyEvent.is_resolved == False
        ).all()
        for anomaly in open_anomalies:
            if anomaly.anomaly_type != models.AnomalyType.CONCENTRATED_ANOMALY.value:
                anomaly.is_resolved = True
                anomaly.resolved_at = datetime.utcnow()
                anomaly.resolution_note = f"复检合格，状态自动解除: {disposition_note or ''}"
    elif recheck_result == models.RecheckResult.UNQUALIFIED.value:
        batch.status = models.BatchStatus.DISCARDED.value
        batch.discard_time = datetime.utcnow()
        batch.final_disposition = f"复检不合格废弃: {disposition_note or ''}"
        open_anomalies = db.query(models.AnomalyEvent).filter(
            models.AnomalyEvent.batch_id == batch.id,
            models.AnomalyEvent.is_resolved == False
        ).all()
        for anomaly in open_anomalies:
            anomaly.is_resolved = True
            anomaly.resolved_at = datetime.utcnow()
            anomaly.resolution_note = f"批次复检不合格已废弃: {disposition_note or ''}"
    elif recheck_result == models.RecheckResult.FURTHER_RECHECK.value:
        batch.status = models.BatchStatus.ANOMALY_HOLD.value


@recheck_router.post("/applications", response_model=schemas.RecheckApplicationResponse)
def create_recheck_application(
    app_in: schemas.RecheckApplicationCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(
        models.UserRole.STORE_STAFF.value,
        models.UserRole.HQ_ADMIN.value
    ))
):
    batch = db.query(models.MaterialBatch).filter(
        models.MaterialBatch.id == app_in.batch_id
    ).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")

    store_id = _resolve_store_id(batch.store_id, current_user)

    valid_statuses_for_recheck = [
        models.BatchStatus.PENDING_QC.value,
        models.BatchStatus.ANOMALY_HOLD.value,
        models.BatchStatus.READY_FOR_SALE.value,
    ]
    if batch.status not in valid_statuses_for_recheck:
        raise HTTPException(
            status_code=400,
            detail=f"当前批次状态[{batch.status}]不可发起复检，仅待抽检、异常留观或可销售状态可申请"
        )

    pending_exists = db.query(models.RecheckApplication).filter(
        models.RecheckApplication.batch_id == batch.id,
        models.RecheckApplication.status.in_([
            models.RecheckStatus.PENDING.value,
            models.RecheckStatus.IN_PROGRESS.value
        ])
    ).first()
    if pending_exists:
        raise HTTPException(status_code=400, detail="该批次已有待处理或进行中的复检申请")

    application_no = _generate_application_no(db)

    recheck_source = app_in.recheck_source
    if app_in.source_qc_inspection_id:
        qc = db.query(models.QCInspection).filter(
            models.QCInspection.id == app_in.source_qc_inspection_id
        ).first()
        if not qc or qc.batch_id != batch.id:
            raise HTTPException(status_code=400, detail="关联的品控抽检记录不存在或不匹配")
        recheck_source = models.RecheckSource.QC_INSPECTION.value
    if app_in.source_anomaly_event_id:
        anomaly = db.query(models.AnomalyEvent).filter(
            models.AnomalyEvent.id == app_in.source_anomaly_event_id
        ).first()
        if not anomaly or anomaly.batch_id != batch.id:
            raise HTTPException(status_code=400, detail="关联的异常事件不存在或不匹配")
        recheck_source = models.RecheckSource.ANOMALY_HOLD.value

    if current_user.role == models.UserRole.HQ_ADMIN.value and recheck_source == models.RecheckSource.STORE_INITIATIVE.value:
        recheck_source = models.RecheckSource.HQ_ASSIGN.value

    application = models.RecheckApplication(
        **app_in.model_dump(exclude={"recheck_source"}),
        application_no=application_no,
        store_id=store_id,
        recheck_source=recheck_source,
        applied_by=current_user.id,
        applied_at=datetime.utcnow()
    )
    db.add(application)

    if batch.status != models.BatchStatus.ANOMALY_HOLD.value:
        batch.status = models.BatchStatus.ANOMALY_HOLD.value

    db.commit()
    db.refresh(application)
    return application


@recheck_router.put("/applications/{app_id}", response_model=schemas.RecheckApplicationResponse)
def update_recheck_application(
    app_id: int,
    update_in: schemas.RecheckApplicationUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(
        models.UserRole.STORE_STAFF.value,
        models.UserRole.HQ_ADMIN.value
    ))
):
    application = db.query(models.RecheckApplication).filter(
        models.RecheckApplication.id == app_id
    ).first()
    if not application:
        raise HTTPException(status_code=404, detail="复检申请不存在")

    if current_user.role == models.UserRole.STORE_STAFF.value:
        if application.store_id != current_user.store_id:
            raise HTTPException(status_code=403, detail="无权限修改其他门店的复检申请")
        if application.applied_by != current_user.id:
            raise HTTPException(status_code=403, detail="仅申请人可修改申请信息")

    if application.status not in [
        models.RecheckStatus.PENDING.value,
    ]:
        raise HTTPException(status_code=400, detail=f"当前状态[{application.status}]不可修改申请信息")

    update_data = update_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(application, field, value)

    db.commit()
    db.refresh(application)
    return application


@recheck_router.post("/applications/{app_id}/assign", response_model=schemas.RecheckApplicationResponse)
def assign_recheck(
    app_id: int,
    assign_in: schemas.RecheckApplicationAssign,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(
        models.UserRole.HQ_ADMIN.value
    ))
):
    application = db.query(models.RecheckApplication).filter(
        models.RecheckApplication.id == app_id
    ).first()
    if not application:
        raise HTTPException(status_code=404, detail="复检申请不存在")

    if application.status != models.RecheckStatus.PENDING.value:
        raise HTTPException(status_code=400, detail=f"当前状态[{application.status}]不可分配")

    assignee = db.query(models.User).filter(
        models.User.id == assign_in.assigned_to,
        models.User.role.in_([
            models.UserRole.QC_STAFF.value,
            models.UserRole.HQ_ADMIN.value
        ]),
        models.User.is_active == True
    ).first()
    if not assignee:
        raise HTTPException(status_code=400, detail="指定的复检人员不存在或无权限")

    application.assigned_to = assign_in.assigned_to
    application.assigned_at = datetime.utcnow()
    application.status = models.RecheckStatus.IN_PROGRESS.value

    db.commit()
    db.refresh(application)
    return application


@recheck_router.post("/applications/{app_id}/start", response_model=schemas.RecheckApplicationResponse)
def start_recheck(
    app_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(
        models.UserRole.QC_STAFF.value,
        models.UserRole.HQ_ADMIN.value
    ))
):
    application = db.query(models.RecheckApplication).filter(
        models.RecheckApplication.id == app_id
    ).first()
    if not application:
        raise HTTPException(status_code=404, detail="复检申请不存在")

    if application.status == models.RecheckStatus.PENDING.value:
        if current_user.role == models.UserRole.QC_STAFF.value:
            raise HTTPException(
                status_code=403,
                detail="品控人员不可自行领取待处理任务，需由总部管理员分配后方可开始"
            )
        application.assigned_to = current_user.id
        application.assigned_at = datetime.utcnow()
        application.status = models.RecheckStatus.IN_PROGRESS.value
    elif application.status == models.RecheckStatus.IN_PROGRESS.value:
        if application.assigned_to and application.assigned_to != current_user.id \
                and current_user.role != models.UserRole.HQ_ADMIN.value:
            raise HTTPException(status_code=403, detail="该复检已分配给其他品控人员")
    else:
        raise HTTPException(status_code=400, detail=f"当前状态[{application.status}]不可开始复检")

    if not application.recheck_started_at:
        application.recheck_started_at = datetime.utcnow()

    db.commit()
    db.refresh(application)
    return application


@recheck_router.post("/applications/{app_id}/execute", response_model=schemas.RecheckApplicationResponse)
def execute_recheck(
    app_id: int,
    exec_in: schemas.RecheckApplicationExecute,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(
        models.UserRole.QC_STAFF.value,
        models.UserRole.HQ_ADMIN.value
    ))
):
    application = db.query(models.RecheckApplication).filter(
        models.RecheckApplication.id == app_id
    ).first()
    if not application:
        raise HTTPException(status_code=404, detail="复检申请不存在")

    if application.status not in [
        models.RecheckStatus.IN_PROGRESS.value,
    ]:
        raise HTTPException(status_code=400, detail=f"当前状态[{application.status}]不可执行复检，需先由总部分配后方可执行")

    if application.assigned_to and application.assigned_to != current_user.id \
            and current_user.role != models.UserRole.HQ_ADMIN.value:
        raise HTTPException(status_code=403, detail="该复检已分配给其他品控人员")

    valid_results = [e.value for e in models.RecheckResult]
    if exec_in.recheck_result not in valid_results:
        raise HTTPException(status_code=400, detail=f"复检结果无效，有效值: {valid_results}")

    if exec_in.recheck_template_id:
        tpl = db.query(models.QCTemplate).filter(
            models.QCTemplate.id == exec_in.recheck_template_id
        ).first()
        if not tpl:
            raise HTTPException(status_code=400, detail="品控模板不存在")

    if not application.recheck_started_at:
        application.recheck_started_at = datetime.utcnow()

    application.recheck_template_id = exec_in.recheck_template_id
    application.appearance_score = exec_in.appearance_score
    application.taste_score = exec_in.taste_score
    application.texture_score = exec_in.texture_score
    application.overall_score = exec_in.overall_score
    application.recheck_check_result = exec_in.recheck_check_result
    application.recheck_disposition_note = exec_in.recheck_disposition_note
    application.recheck_result = exec_in.recheck_result
    application.recheck_completed_at = datetime.utcnow()
    application.rechecked_by = current_user.id

    if exec_in.recheck_result == models.RecheckResult.QUALIFIED.value:
        application.status = models.RecheckStatus.PASSED.value
    elif exec_in.recheck_result == models.RecheckResult.UNQUALIFIED.value:
        application.status = models.RecheckStatus.FAILED.value
    elif exec_in.recheck_result == models.RecheckResult.FURTHER_RECHECK.value:
        application.status = models.RecheckStatus.PENDING.value
        application.recheck_result = None
        application.recheck_template_id = None
        application.appearance_score = None
        application.taste_score = None
        application.texture_score = None
        application.overall_score = None
        application.recheck_check_result = None
        application.recheck_disposition_note = None
        application.recheck_started_at = None
        application.assigned_to = None
        application.assigned_at = None
        application.rechecked_by = None
        application.recheck_completed_at = None

    batch = db.query(models.MaterialBatch).filter(
        models.MaterialBatch.id == application.batch_id
    ).first()
    if batch:
        _sync_batch_status_after_recheck(
            db, batch, exec_in.recheck_result, exec_in.recheck_disposition_note
        )

    db.commit()
    db.refresh(application)
    return application


@recheck_router.post("/applications/{app_id}/cancel", response_model=schemas.RecheckApplicationResponse)
def cancel_recheck(
    app_id: int,
    cancel_in: schemas.RecheckApplicationCancel,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(
        models.UserRole.STORE_STAFF.value,
        models.UserRole.HQ_ADMIN.value
    ))
):
    application = db.query(models.RecheckApplication).filter(
        models.RecheckApplication.id == app_id
    ).first()
    if not application:
        raise HTTPException(status_code=404, detail="复检申请不存在")

    if application.status not in [
        models.RecheckStatus.PENDING.value,
        models.RecheckStatus.IN_PROGRESS.value
    ]:
        raise HTTPException(status_code=400, detail=f"当前状态[{application.status}]不可取消")

    if current_user.role == models.UserRole.STORE_STAFF.value:
        if application.store_id != current_user.store_id:
            raise HTTPException(status_code=403, detail="无权限取消其他门店的复检申请")
        if application.applied_by != current_user.id:
            raise HTTPException(status_code=403, detail="仅申请人可取消复检申请")

    application.status = models.RecheckStatus.CANCELLED.value
    application.cancelled_by = current_user.id
    application.cancelled_at = datetime.utcnow()
    application.cancel_reason = cancel_in.cancel_reason

    batch = db.query(models.MaterialBatch).filter(
        models.MaterialBatch.id == application.batch_id
    ).first()
    if batch and batch.status == models.BatchStatus.ANOMALY_HOLD.value:
        has_other_pending = db.query(models.RecheckApplication).filter(
            models.RecheckApplication.batch_id == batch.id,
            models.RecheckApplication.id != application.id,
            models.RecheckApplication.status.in_([
                models.RecheckStatus.PENDING.value,
                models.RecheckStatus.IN_PROGRESS.value
            ])
        ).first()
        has_open_anomaly = db.query(models.AnomalyEvent).filter(
            models.AnomalyEvent.batch_id == batch.id,
            models.AnomalyEvent.is_resolved == False
        ).first()
        has_qc_pending = db.query(models.QCInspection).filter(
            models.QCInspection.batch_id == batch.id
        ).first()
        if not has_other_pending and not has_open_anomaly:
            if has_qc_pending:
                batch.status = models.BatchStatus.PENDING_QC.value
            else:
                batch.status = models.BatchStatus.PENDING_QC.value

    db.commit()
    db.refresh(application)
    return application


@recheck_router.get("/applications", response_model=List[schemas.RecheckApplicationDetailResponse])
def list_recheck_applications(
    store_id: Optional[int] = None,
    batch_id: Optional[int] = None,
    batch_no: Optional[str] = None,
    ingredient_category_id: Optional[int] = None,
    status: Optional[str] = None,
    recheck_result: Optional[str] = None,
    recheck_source: Optional[str] = None,
    recheck_reason: Optional[str] = None,
    assigned_to: Optional[int] = None,
    is_overdue: Optional[bool] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_active_user)
):
    query = db.query(models.RecheckApplication)
    query = _apply_data_scope(query, current_user)

    if store_id and current_user.role != models.UserRole.STORE_STAFF.value:
        query = query.filter(models.RecheckApplication.store_id == store_id)
    if batch_id:
        query = query.filter(models.RecheckApplication.batch_id == batch_id)
    if status:
        query = query.filter(models.RecheckApplication.status == status)
    if recheck_result:
        query = query.filter(models.RecheckApplication.recheck_result == recheck_result)
    if recheck_source:
        query = query.filter(models.RecheckApplication.recheck_source == recheck_source)
    if recheck_reason:
        query = query.filter(models.RecheckApplication.recheck_reason == recheck_reason)
    if assigned_to:
        query = query.filter(models.RecheckApplication.assigned_to == assigned_to)
    if date_from:
        query = query.filter(models.RecheckApplication.applied_at >= date_from)
    if date_to:
        date_to_end = datetime.combine(date_to + timedelta(days=1), datetime.min.time())
        query = query.filter(models.RecheckApplication.applied_at < date_to_end)
    if batch_no or ingredient_category_id:
        query = query.join(models.MaterialBatch)
        if batch_no:
            query = query.filter(models.MaterialBatch.batch_no.contains(batch_no))
        if ingredient_category_id:
            query = query.filter(models.MaterialBatch.ingredient_category_id == ingredient_category_id)

    results = query.order_by(models.RecheckApplication.created_at.desc()).all()

    if is_overdue is not None:
        now = datetime.utcnow()
        filtered = []
        for app in results:
            deadline = app.applied_at + timedelta(hours=app.deadline_hours)
            is_app_overdue = (
                app.status in [models.RecheckStatus.PENDING.value, models.RecheckStatus.IN_PROGRESS.value]
                and now > deadline
            )
            if is_app_overdue == is_overdue:
                filtered.append(app)
        results = filtered

    return results


@recheck_router.get("/applications/{app_id}", response_model=schemas.RecheckApplicationDetailResponse)
def get_recheck_application_detail(
    app_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_active_user)
):
    application = db.query(models.RecheckApplication).filter(
        models.RecheckApplication.id == app_id
    ).first()
    if not application:
        raise HTTPException(status_code=404, detail="复检申请不存在")

    if current_user.role == models.UserRole.STORE_STAFF.value:
        if application.store_id != current_user.store_id:
            raise HTTPException(status_code=403, detail="无权限查看其他门店数据")

    return application


@recheck_router.get("/batches/{batch_id}/applications", response_model=List[schemas.RecheckApplicationResponse])
def get_batch_recheck_history(
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

    return db.query(models.RecheckApplication).filter(
        models.RecheckApplication.batch_id == batch_id
    ).order_by(models.RecheckApplication.created_at.desc()).all()


@recheck_router.get("/statistics/overview", response_model=Dict[str, Any])
def get_recheck_overview_stats(
    store_id: Optional[int] = None,
    days: int = Query(30, ge=1, le=365, description="统计天数"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(
        models.UserRole.HQ_ADMIN.value,
        models.UserRole.QC_STAFF.value,
        models.UserRole.STORE_STAFF.value
    ))
):
    cutoff = datetime.utcnow() - timedelta(days=days)
    query = db.query(models.RecheckApplication).filter(
        models.RecheckApplication.applied_at >= cutoff
    )
    query = _apply_data_scope(query, current_user)
    if store_id and current_user.role != models.UserRole.STORE_STAFF.value:
        query = query.filter(models.RecheckApplication.store_id == store_id)

    all_apps = query.all()
    total = len(all_apps)

    status_counts = {
        models.RecheckStatus.PENDING.value: 0,
        models.RecheckStatus.IN_PROGRESS.value: 0,
        models.RecheckStatus.PASSED.value: 0,
        models.RecheckStatus.FAILED.value: 0,
        models.RecheckStatus.CANCELLED.value: 0,
    }
    now = datetime.utcnow()
    overdue_count = 0
    processing_hours_list = []

    for app in all_apps:
        if app.status in status_counts:
            status_counts[app.status] += 1
        if app.status in [models.RecheckStatus.PENDING.value, models.RecheckStatus.IN_PROGRESS.value]:
            deadline = app.applied_at + timedelta(hours=app.deadline_hours)
            if now > deadline:
                overdue_count += 1
        if app.recheck_started_at and app.recheck_completed_at:
            hours = (app.recheck_completed_at - app.recheck_started_at).total_seconds() / 3600
            processing_hours_list.append(hours)

    avg_processing = round(sum(processing_hours_list) / len(processing_hours_list), 2) if processing_hours_list else 0.0

    return {
        "period_days": days,
        "store_id": store_id,
        "total": total,
        "pending": status_counts[models.RecheckStatus.PENDING.value],
        "in_progress": status_counts[models.RecheckStatus.IN_PROGRESS.value],
        "passed": status_counts[models.RecheckStatus.PASSED.value],
        "failed": status_counts[models.RecheckStatus.FAILED.value],
        "cancelled": status_counts[models.RecheckStatus.CANCELLED.value],
        "overdue": overdue_count,
        "avg_processing_hours": avg_processing
    }


@recheck_router.get("/statistics/result-distribution", response_model=List[Dict[str, Any]])
def get_recheck_result_distribution(
    store_id: Optional[int] = None,
    days: int = Query(30, ge=1, le=365, description="统计天数"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(
        models.UserRole.HQ_ADMIN.value,
        models.UserRole.QC_STAFF.value,
        models.UserRole.STORE_STAFF.value
    ))
):
    cutoff = datetime.utcnow() - timedelta(days=days)
    query = db.query(models.RecheckApplication).filter(
        models.RecheckApplication.applied_at >= cutoff,
        models.RecheckApplication.recheck_result != None
    )
    query = _apply_data_scope(query, current_user)
    if store_id and current_user.role != models.UserRole.STORE_STAFF.value:
        query = query.filter(models.RecheckApplication.store_id == store_id)

    result_query = db.query(
        models.RecheckApplication.recheck_result,
        func.count(models.RecheckApplication.id)
    ).filter(
        models.RecheckApplication.id.in_([a.id for a in query.all()])
    ).group_by(models.RecheckApplication.recheck_result)

    total = sum(cnt for _, cnt in result_query.all())
    results = []
    result_labels = {
        models.RecheckResult.QUALIFIED.value: "复检合格",
        models.RecheckResult.UNQUALIFIED.value: "复检不合格",
        models.RecheckResult.FURTHER_RECHECK.value: "需再次复检",
    }
    for result, count in result_query.all():
        percentage = round(count / total * 100, 2) if total > 0 else 0.0
        results.append({
            "result": result,
            "result_label": result_labels.get(result, result),
            "count": count,
            "percentage": percentage
        })
    results.sort(key=lambda x: x["count"], reverse=True)
    return results


@recheck_router.get("/statistics/overdue-list", response_model=List[Dict[str, Any]])
def get_recheck_overdue_list(
    store_id: Optional[int] = None,
    hours_threshold: int = Query(24, ge=0, le=720, description="超时时长阈值(小时)"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(
        models.UserRole.HQ_ADMIN.value,
        models.UserRole.QC_STAFF.value,
        models.UserRole.STORE_STAFF.value
    ))
):
    now = datetime.utcnow()
    query = db.query(models.RecheckApplication).filter(
        models.RecheckApplication.status.in_([
            models.RecheckStatus.PENDING.value,
            models.RecheckStatus.IN_PROGRESS.value
        ])
    )
    query = _apply_data_scope(query, current_user)
    if store_id and current_user.role != models.UserRole.STORE_STAFF.value:
        query = query.filter(models.RecheckApplication.store_id == store_id)

    overdue_list = []
    for app in query.all():
        deadline = app.applied_at + timedelta(hours=app.deadline_hours)
        overdue_hours = round((now - deadline).total_seconds() / 3600, 1)
        if overdue_hours < hours_threshold:
            continue
        batch = db.query(models.MaterialBatch).filter(
            models.MaterialBatch.id == app.batch_id
        ).first()
        store = db.query(models.Store).filter(
            models.Store.id == app.store_id
        ).first()
        cat = db.query(models.IngredientCategory).filter(
            models.IngredientCategory.id == batch.ingredient_category_id
        ).first() if batch else None

        if overdue_hours >= hours_threshold * 3:
            priority = "特急"
        elif overdue_hours >= hours_threshold * 2:
            priority = "紧急"
        elif overdue_hours >= hours_threshold:
            priority = "高"
        else:
            priority = "中"

        overdue_list.append({
            "application_id": app.id,
            "application_no": app.application_no,
            "batch_id": app.batch_id,
            "batch_no": batch.batch_no if batch else "未知",
            "store_id": app.store_id,
            "store_name": store.store_name if store else "未知",
            "category_name": cat.category_name if cat else "未知",
            "status": app.status,
            "applied_at": app.applied_at,
            "deadline_hours": app.deadline_hours,
            "deadline_at": deadline,
            "overdue_hours": overdue_hours,
            "assigned_to": app.assigned_to,
            "priority": priority
        })
    overdue_list.sort(key=lambda x: x["overdue_hours"], reverse=True)
    return overdue_list


@recheck_router.get("/statistics/trend", response_model=List[Dict[str, Any]])
def get_recheck_trend(
    store_id: Optional[int] = None,
    days: int = Query(30, ge=1, le=365, description="统计天数"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(
        models.UserRole.HQ_ADMIN.value,
        models.UserRole.QC_STAFF.value,
        models.UserRole.STORE_STAFF.value
    ))
):
    cutoff = datetime.utcnow() - timedelta(days=days)
    query = db.query(models.RecheckApplication).filter(
        models.RecheckApplication.applied_at >= cutoff
    )
    query = _apply_data_scope(query, current_user)
    if store_id and current_user.role != models.UserRole.STORE_STAFF.value:
        query = query.filter(models.RecheckApplication.store_id == store_id)

    trend_query = db.query(
        func.date(models.RecheckApplication.applied_at).label("apply_date"),
        func.count(models.RecheckApplication.id).label("total"),
        func.sum(
            models.RecheckApplication.status == models.RecheckStatus.PASSED.value
        ).label("passed_count"),
        func.sum(
            models.RecheckApplication.status == models.RecheckStatus.FAILED.value
        ).label("failed_count"),
    ).filter(
        models.RecheckApplication.id.in_([a.id for a in query.all()])
    ).group_by(
        func.date(models.RecheckApplication.applied_at)
    ).order_by("apply_date")

    results = []
    for row in trend_query.all():
        apply_date, total, passed, failed = row
        results.append({
            "date": apply_date,
            "total": total or 0,
            "passed": passed or 0,
            "failed": failed or 0,
            "pass_rate": round((passed or 0) / (total or 1) * 100, 2)
        })
    return results


@recheck_router.get("/my-todos", response_model=List[schemas.RecheckApplicationDetailResponse])
def get_my_recheck_todos(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(
        models.UserRole.QC_STAFF.value,
        models.UserRole.HQ_ADMIN.value
    ))
):
    query = db.query(models.RecheckApplication).filter(
        models.RecheckApplication.status.in_([
            models.RecheckStatus.PENDING.value,
            models.RecheckStatus.IN_PROGRESS.value
        ])
    )
    if current_user.role == models.UserRole.QC_STAFF.value:
        query = query.filter(
            (models.RecheckApplication.assigned_to == current_user.id) |
            (models.RecheckApplication.assigned_to == None)
        )
    return query.order_by(models.RecheckApplication.applied_at.asc()).all()
