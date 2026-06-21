from datetime import datetime, date, timedelta
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from typing import List, Optional, Dict, Any
from app.database import get_db
from app import models, schemas, auth
from app.services import anomaly_detector

stats_router = APIRouter(prefix="/api/stats", tags=["统计分析"])


@stats_router.get("/abnormal-ranking", response_model=List[Dict[str, Any]])
def get_abnormal_ranking(
    store_id: Optional[int] = None,
    days: int = Query(7, ge=1, le=90, description="统计天数"),
    limit: int = Query(20, ge=1, le=100, description="返回条数"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_active_user)
):
    if current_user.role == models.UserRole.STORE_STAFF.value:
        store_id = current_user.store_id
    cutoff = datetime.utcnow() - timedelta(days=days)

    batch_query = db.query(
        models.MaterialBatch.ingredient_category_id,
        func.count(models.MaterialBatch.id).label("batch_count")
    )
    if store_id:
        batch_query = batch_query.filter(models.MaterialBatch.store_id == store_id)
    batch_query = batch_query.filter(
        models.MaterialBatch.created_at >= cutoff
    ).group_by(
        models.MaterialBatch.ingredient_category_id
    )
    batch_counts = {row[0]: row[1] for row in batch_query.all()}

    anomaly_query = db.query(
        models.MaterialBatch.ingredient_category_id,
        func.count(models.AnomalyEvent.id).label("anomaly_count")
    ).join(
        models.AnomalyEvent, models.MaterialBatch.id == models.AnomalyEvent.batch_id
    )
    if store_id:
        anomaly_query = anomaly_query.filter(models.MaterialBatch.store_id == store_id)
    anomaly_query = anomaly_query.filter(
        models.AnomalyEvent.created_at >= cutoff
    ).group_by(
        models.MaterialBatch.ingredient_category_id
    ).order_by(
        func.count(models.AnomalyEvent.id).desc()
    ).limit(limit)

    results = []
    for cat_id, anomaly_count in anomaly_query.all():
        cat = db.query(models.IngredientCategory).filter(
            models.IngredientCategory.id == cat_id
        ).first()
        batch_count = batch_counts.get(cat_id, 0)
        anomaly_rate = round(anomaly_count / batch_count, 4) if batch_count > 0 else 0
        results.append({
            "ingredient_category_id": cat_id,
            "category_name": cat.category_name if cat else "未知",
            "anomaly_count": anomaly_count,
            "batch_count": batch_count,
            "anomaly_rate": anomaly_rate
        })
    results.sort(key=lambda x: x["anomaly_count"], reverse=True)
    return results


@stats_router.get("/qc-todos", response_model=List[Dict[str, Any]])
def get_qc_todos(
    store_id: Optional[int] = None,
    hours_threshold: int = Query(4, ge=1, le=72, description="超时时长阈值(小时)"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(
        models.UserRole.QC_STAFF.value,
        models.UserRole.HQ_ADMIN.value,
        models.UserRole.STORE_STAFF.value
    ))
):
    if current_user.role == models.UserRole.STORE_STAFF.value:
        store_id = current_user.store_id
    now = datetime.utcnow()

    pending_batches = db.query(models.MaterialBatch).filter(
        models.MaterialBatch.status.in_([
            models.BatchStatus.PENDING_QC.value,
            models.BatchStatus.ANOMALY_HOLD.value
        ]),
        models.MaterialBatch.production_complete_time != None
    ).all()

    if store_id:
        pending_batches = [b for b in pending_batches if b.store_id == store_id]

    todo_list = []
    for batch in pending_batches:
        has_qc = db.query(models.QCInspection).filter(
            models.QCInspection.batch_id == batch.id
        ).first()
        if has_qc and batch.status != models.BatchStatus.ANOMALY_HOLD.value:
            continue
        if batch.production_complete_time:
            pending_hours = round((now - batch.production_complete_time).total_seconds() / 3600, 1)
        else:
            pending_hours = 0.0
        if pending_hours >= hours_threshold * 2:
            priority = "紧急"
        elif pending_hours >= hours_threshold:
            priority = "高"
        elif pending_hours >= hours_threshold / 2:
            priority = "中"
        else:
            priority = "低"
        store = db.query(models.Store).filter(models.Store.id == batch.store_id).first()
        cat = db.query(models.IngredientCategory).filter(
            models.IngredientCategory.id == batch.ingredient_category_id
        ).first()
        todo_list.append({
            "batch_id": batch.id,
            "batch_no": batch.batch_no,
            "store_id": batch.store_id,
            "store_name": store.store_name if store else "未知",
            "category_name": cat.category_name if cat else "未知",
            "status": batch.status,
            "production_complete_time": batch.production_complete_time,
            "pending_hours": pending_hours,
            "priority": priority
        })
    todo_list.sort(key=lambda x: x["pending_hours"], reverse=True)
    return todo_list


@stats_router.get("/discard-trend", response_model=List[Dict[str, Any]])
def get_discard_trend(
    store_id: Optional[int] = None,
    days: int = Query(14, ge=1, le=90, description="统计天数"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_active_user)
):
    if current_user.role == models.UserRole.STORE_STAFF.value:
        store_id = current_user.store_id
    cutoff = datetime.utcnow() - timedelta(days=days)
    start_date = cutoff.date()

    query = db.query(
        func.date(models.ProductionRecord.end_time).label("record_date"),
        models.ProductionRecord.store_id,
        func.sum(models.ProductionRecord.cups_produced).label("total_cups"),
        func.sum(models.ProductionRecord.cups_discarded).label("discarded_cups")
    ).filter(
        models.ProductionRecord.end_time != None,
        models.ProductionRecord.end_time >= cutoff
    )
    if store_id:
        query = query.filter(models.ProductionRecord.store_id == store_id)
    query = query.group_by(
        func.date(models.ProductionRecord.end_time),
        models.ProductionRecord.store_id
    ).order_by(
        "record_date",
        models.ProductionRecord.store_id
    )

    results = []
    for row in query.all():
        record_date, sid, total, discarded = row
        store = db.query(models.Store).filter(models.Store.id == sid).first()
        total = total or 0
        discarded = discarded or 0
        discard_rate = round(discarded / (total + discarded), 4) if (total + discarded) > 0 else 0
        results.append({
            "date": record_date,
            "store_id": sid,
            "store_name": store.store_name if store else "未知",
            "total_cups": total,
            "discarded_cups": discarded,
            "discard_rate": discard_rate
        })
    return results


@stats_router.get("/overview", response_model=Dict[str, Any])
def get_overview(
    store_id: Optional[int] = None,
    days: int = Query(7, ge=1, le=30, description="统计天数"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_active_user)
):
    if current_user.role == models.UserRole.STORE_STAFF.value:
        store_id = current_user.store_id
    cutoff = datetime.utcnow() - timedelta(days=days)

    batch_query = db.query(func.count(models.MaterialBatch.id)).filter(
        models.MaterialBatch.created_at >= cutoff
    )
    if store_id:
        batch_query = batch_query.filter(models.MaterialBatch.store_id == store_id)
    total_batches = batch_query.scalar() or 0

    status_counts = {}
    status_query = db.query(
        models.MaterialBatch.status,
        func.count(models.MaterialBatch.id)
    )
    if store_id:
        status_query = status_query.filter(models.MaterialBatch.store_id == store_id)
    status_query = status_query.group_by(models.MaterialBatch.status)
    for status, cnt in status_query.all():
        status_counts[status] = cnt

    anomaly_query = db.query(func.count(models.AnomalyEvent.id)).join(
        models.MaterialBatch, models.MaterialBatch.id == models.AnomalyEvent.batch_id
    ).filter(
        models.AnomalyEvent.created_at >= cutoff
    )
    if store_id:
        anomaly_query = anomaly_query.filter(models.MaterialBatch.store_id == store_id)
    total_anomalies = anomaly_query.scalar() or 0

    unresolved_anomaly_query = db.query(func.count(models.AnomalyEvent.id)).join(
        models.MaterialBatch, models.MaterialBatch.id == models.AnomalyEvent.batch_id
    ).filter(
        models.AnomalyEvent.is_resolved == False
    )
    if store_id:
        unresolved_anomaly_query = unresolved_anomaly_query.filter(
            models.MaterialBatch.store_id == store_id
        )
    unresolved_anomalies = unresolved_anomaly_query.scalar() or 0

    prod_query = db.query(
        func.sum(models.ProductionRecord.cups_produced),
        func.sum(models.ProductionRecord.cups_discarded)
    ).filter(
        models.ProductionRecord.created_at >= cutoff
    )
    if store_id:
        prod_query = prod_query.filter(models.ProductionRecord.store_id == store_id)
    total_cups, discarded_cups = prod_query.first()
    total_cups = total_cups or 0
    discarded_cups = discarded_cups or 0
    overall_discard_rate = round(discarded_cups / (total_cups + discarded_cups), 4) if (total_cups + discarded_cups) > 0 else 0

    qc_query = db.query(func.count(models.QCInspection.id)).filter(
        models.QCInspection.created_at >= cutoff
    )
    if store_id:
        qc_query = qc_query.filter(models.QCInspection.store_id == store_id)
    total_qc_done = qc_query.scalar() or 0

    return {
        "period_days": days,
        "store_id": store_id,
        "total_batches": total_batches,
        "status_distribution": status_counts,
        "total_anomalies": total_anomalies,
        "unresolved_anomalies": unresolved_anomalies,
        "total_cups_produced": total_cups,
        "total_cups_discarded": discarded_cups,
        "overall_discard_rate": overall_discard_rate,
        "total_qc_inspections": total_qc_done
    }


@stats_router.post("/run-detection")
def run_all_detections(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(
        models.UserRole.QC_STAFF.value,
        models.UserRole.HQ_ADMIN.value
    ))
):
    result = anomaly_detector.run_all_detections(db)
    return result
