from datetime import datetime, timedelta, date
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_
from typing import List, Dict, Any, Optional, Tuple
from app import models


STAGE_LABELS = {
    models.WarningStage.AFTER_ARRIVAL.value: "到店后",
    models.WarningStage.AFTER_WASH.value: "清洗后",
    models.WarningStage.AFTER_PRODUCTION.value: "制作完成后",
    models.WarningStage.PENDING_QC.value: "待抽检",
    models.WarningStage.READY_FOR_SALE.value: "可销售",
}

WARNING_LEVEL_LABELS = {
    models.WarningLevel.NORMAL.value: "正常",
    models.WarningLevel.ATTENTION.value: "注意",
    models.WarningLevel.WARNING.value: "警告",
    models.WarningLevel.URGENT.value: "紧急",
}

SUGGESTED_ACTIONS = {
    models.WarningStage.AFTER_ARRIVAL.value: "请尽快安排验收和清洗处理",
    models.WarningStage.AFTER_WASH.value: "请尽快安排制作，避免原料变质",
    models.WarningStage.AFTER_PRODUCTION.value: "请尽快申请品控抽检，完成后可销售",
    models.WarningStage.PENDING_QC.value: "请联系品控人员优先完成抽检",
    models.WarningStage.READY_FOR_SALE.value: "请尽快销售或安排促销，避免产品过期",
}


def _generate_warning_no(db: Session) -> str:
    today = datetime.utcnow().strftime("%Y%m%d")
    prefix = f"FW{today}"
    last = db.query(models.FreshnessWarning).filter(
        models.FreshnessWarning.warning_no.like(f"{prefix}%")
    ).order_by(models.FreshnessWarning.warning_no.desc()).first()
    if last:
        seq = int(last.warning_no[-4:]) + 1
    else:
        seq = 1
    return f"{prefix}{seq:04d}"


def _get_batch_stage_and_ref_time(
    batch: models.MaterialBatch,
    now: datetime
) -> Tuple[Optional[str], Optional[datetime]]:
    stage = None
    ref_time = None

    if batch.status == models.BatchStatus.READY_FOR_PRODUCTION.value:
        if batch.wash_complete_time:
            stage = models.WarningStage.AFTER_WASH.value
            ref_time = batch.wash_complete_time
        elif batch.arrival_time:
            stage = models.WarningStage.AFTER_ARRIVAL.value
            ref_time = batch.arrival_time
    elif batch.status == models.BatchStatus.IN_PRODUCTION.value:
        if batch.production_start_time:
            stage = models.WarningStage.AFTER_WASH.value
            ref_time = batch.wash_complete_time or batch.arrival_time
    elif batch.status == models.BatchStatus.PENDING_QC.value:
        if batch.production_complete_time:
            stage = models.WarningStage.AFTER_PRODUCTION.value
            ref_time = batch.production_complete_time
    elif batch.status == models.BatchStatus.ANOMALY_HOLD.value:
        stage = models.WarningStage.PENDING_QC.value
        ref_time = batch.production_complete_time or batch.wash_complete_time or batch.arrival_time
    elif batch.status == models.BatchStatus.READY_FOR_SALE.value:
        stage = models.WarningStage.READY_FOR_SALE.value
        ref_time = batch.sale_start_time or batch.production_complete_time

    return stage, ref_time


def _calculate_warning_level(
    remaining_hours: float,
    max_hours: float
) -> str:
    ratio = remaining_hours / max_hours if max_hours > 0 else 0

    if ratio <= 0.1:
        return models.WarningLevel.URGENT.value
    elif ratio <= 0.25:
        return models.WarningLevel.WARNING.value
    elif ratio <= 0.5:
        return models.WarningLevel.ATTENTION.value
    else:
        return models.WarningLevel.NORMAL.value


def _get_max_hours_for_stage(
    db: Session,
    ingredient_category_id: int,
    stage: str
) -> Optional[float]:
    rule_stage_map = {
        models.WarningStage.AFTER_ARRIVAL.value: ["after_arrival", "acceptance"],
        models.WarningStage.AFTER_WASH.value: ["after_wash"],
        models.WarningStage.AFTER_PRODUCTION.value: ["after_production"],
        models.WarningStage.PENDING_QC.value: ["after_production", "qc_required_within_hours"],
        models.WarningStage.READY_FOR_SALE.value: ["after_production"],
    }

    stage_keys = rule_stage_map.get(stage, [])
    for key in stage_keys:
        if key == "qc_required_within_hours":
            batch_rule = db.query(models.BatchRule).filter(
                models.BatchRule.ingredient_category_id == ingredient_category_id,
                models.BatchRule.is_active == True
            ).first()
            if batch_rule:
                return float(batch_rule.qc_required_within_hours)
        else:
            freshness_rule = db.query(models.FreshnessRule).filter(
                models.FreshnessRule.ingredient_category_id == ingredient_category_id,
                models.FreshnessRule.stage == key
            ).first()
            if freshness_rule:
                return freshness_rule.max_hours

    return 8.0


def create_warning(
    db: Session,
    batch: models.MaterialBatch,
    stage: str,
    ref_time: datetime,
    max_hours: float,
    now: Optional[datetime] = None
) -> Optional[models.FreshnessWarning]:
    now = now or datetime.utcnow()

    existing_pending = db.query(models.FreshnessWarning).filter(
        models.FreshnessWarning.batch_id == batch.id,
        models.FreshnessWarning.stage == stage,
        models.FreshnessWarning.status.in_([
            models.WarningStatus.PENDING.value,
            models.WarningStatus.PROCESSING.value
        ])
    ).first()
    if existing_pending:
        return None

    elapsed_hours = (now - ref_time).total_seconds() / 3600
    remaining_hours = max_hours - elapsed_hours
    deadline_time = ref_time + timedelta(hours=max_hours)
    warning_level = _calculate_warning_level(remaining_hours, max_hours)
    is_overdue = remaining_hours <= 0

    warning = models.FreshnessWarning(
        warning_no=_generate_warning_no(db),
        batch_id=batch.id,
        store_id=batch.store_id,
        ingredient_category_id=batch.ingredient_category_id,
        stage=stage,
        warning_level=warning_level,
        status=models.WarningStatus.PENDING.value,
        reference_time=ref_time,
        deadline_time=deadline_time,
        remaining_hours=round(remaining_hours, 2),
        max_hours=max_hours,
        elapsed_hours=round(elapsed_hours, 2),
        suggested_action=SUGGESTED_ACTIONS.get(stage, "请尽快处理"),
        detected_at=now,
        is_overdue=is_overdue
    )
    db.add(warning)
    db.commit()
    db.refresh(warning)
    return warning


def detect_freshness_warnings(db: Session) -> Dict[str, Any]:
    now = datetime.utcnow()
    results = {
        "new_warnings": [],
        "updated_warnings": [],
        "expired_warnings": []
    }

    freshness_rules = db.query(models.FreshnessRule).all()
    rule_map = {}
    for r in freshness_rules:
        if r.ingredient_category_id not in rule_map:
            rule_map[r.ingredient_category_id] = {}
        rule_map[r.ingredient_category_id][r.stage] = r.max_hours

    active_batches = db.query(models.MaterialBatch).filter(
        models.MaterialBatch.status.notin_([
            models.BatchStatus.DISCARDED.value,
            models.BatchStatus.PENDING_ACCEPTANCE.value
        ])
    ).all()

    for batch in active_batches:
        stage, ref_time = _get_batch_stage_and_ref_time(batch, now)
        if not stage or not ref_time:
            continue

        max_hours = _get_max_hours_for_stage(db, batch.ingredient_category_id, stage)
        if not max_hours:
            continue

        elapsed_hours = (now - ref_time).total_seconds() / 3600
        remaining_hours = max_hours - elapsed_hours

        if remaining_hours <= max_hours * 0.75:
            warning = create_warning(db, batch, stage, ref_time, max_hours, now)
            if warning:
                results["new_warnings"].append(warning)

        existing_warnings = db.query(models.FreshnessWarning).filter(
            models.FreshnessWarning.batch_id == batch.id,
            models.FreshnessWarning.status.in_([
                models.WarningStatus.PENDING.value,
                models.WarningStatus.PROCESSING.value
            ])
        ).all()

        for w in existing_warnings:
            elapsed_hours = (now - w.reference_time).total_seconds() / 3600
            remaining_hours = w.max_hours - elapsed_hours
            new_level = _calculate_warning_level(remaining_hours, w.max_hours)
            is_overdue = remaining_hours <= 0

            if w.remaining_hours != round(remaining_hours, 2) or w.warning_level != new_level or w.is_overdue != is_overdue:
                w.remaining_hours = round(remaining_hours, 2)
                w.elapsed_hours = round(elapsed_hours, 2)
                w.warning_level = new_level
                w.is_overdue = is_overdue
                if is_overdue and w.status != models.WarningStatus.EXPIRED.value:
                    w.status = models.WarningStatus.EXPIRED.value
                    w.processed_at = now
                    results["expired_warnings"].append(w)
                else:
                    results["updated_warnings"].append(w)
                db.commit()
                db.refresh(w)

    return {
        "new_count": len(results["new_warnings"]),
        "updated_count": len(results["updated_warnings"]),
        "expired_count": len(results["expired_warnings"]),
        "total": len(results["new_warnings"]) + len(results["updated_warnings"]) + len(results["expired_warnings"])
    }


def close_warning_for_batch(
    db: Session,
    batch_id: int,
    disposition: str,
    processed_by: Optional[int] = None,
    processing_note: Optional[str] = None
) -> List[models.FreshnessWarning]:
    now = datetime.utcnow()
    warnings = db.query(models.FreshnessWarning).filter(
        models.FreshnessWarning.batch_id == batch_id,
        models.FreshnessWarning.status.in_([
            models.WarningStatus.PENDING.value,
            models.WarningStatus.PROCESSING.value
        ])
    ).all()

    for w in warnings:
        w.status = models.WarningStatus.RESOLVED.value
        w.processed_at = now
        w.processed_by = processed_by
        w.processing_note = processing_note
        w.final_disposition = disposition
        db.commit()
        db.refresh(w)

    return warnings


def create_disposal_record(
    db: Session,
    warning_id: int,
    batch_id: int,
    store_id: int,
    disposal_type: str,
    operator_id: int,
    disposal_note: Optional[str] = None,
    qc_inspection_id: Optional[int] = None,
    recheck_application_id: Optional[int] = None
) -> models.WarningDisposalRecord:
    record = models.WarningDisposalRecord(
        warning_id=warning_id,
        batch_id=batch_id,
        store_id=store_id,
        disposal_type=disposal_type,
        operator_id=operator_id,
        operation_time=datetime.utcnow(),
        disposal_note=disposal_note,
        qc_inspection_id=qc_inspection_id,
        recheck_application_id=recheck_application_id
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def get_warning_list(
    db: Session,
    current_user: models.User,
    store_id: Optional[int] = None,
    ingredient_category_id: Optional[int] = None,
    batch_id: Optional[int] = None,
    batch_no: Optional[str] = None,
    stage: Optional[str] = None,
    warning_level: Optional[str] = None,
    status: Optional[str] = None,
    is_overdue: Optional[bool] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None
) -> List[models.FreshnessWarning]:
    query = db.query(models.FreshnessWarning)

    if current_user.role == models.UserRole.STORE_STAFF.value:
        query = query.filter(models.FreshnessWarning.store_id == current_user.store_id)
    elif store_id:
        query = query.filter(models.FreshnessWarning.store_id == store_id)

    if ingredient_category_id:
        query = query.filter(models.FreshnessWarning.ingredient_category_id == ingredient_category_id)
    if batch_id:
        query = query.filter(models.FreshnessWarning.batch_id == batch_id)
    if stage:
        query = query.filter(models.FreshnessWarning.stage == stage)
    if warning_level:
        query = query.filter(models.FreshnessWarning.warning_level == warning_level)
    if status:
        query = query.filter(models.FreshnessWarning.status == status)
    if is_overdue is not None:
        query = query.filter(models.FreshnessWarning.is_overdue == is_overdue)
    if date_from:
        query = query.filter(models.FreshnessWarning.created_at >= date_from)
    if date_to:
        date_to_end = datetime.combine(date_to + timedelta(days=1), datetime.min.time())
        query = query.filter(models.FreshnessWarning.created_at < date_to_end)
    if batch_no:
        query = query.join(models.MaterialBatch).filter(
            models.MaterialBatch.batch_no.contains(batch_no)
        )

    return query.order_by(
        models.FreshnessWarning.is_overdue.desc(),
        models.FreshnessWarning.warning_level.desc(),
        models.FreshnessWarning.created_at.desc()
    ).all()


def get_warning_stats_overview(
    db: Session,
    current_user: models.User,
    days: int = 7,
    store_id: Optional[int] = None
) -> Dict[str, Any]:
    now = datetime.utcnow()
    cutoff = now - timedelta(days=days)
    today_start = datetime.combine(now.date(), datetime.min.time())

    query = db.query(models.FreshnessWarning).filter(
        models.FreshnessWarning.created_at >= cutoff
    )

    if current_user.role == models.UserRole.STORE_STAFF.value:
        query = query.filter(models.FreshnessWarning.store_id == current_user.store_id)
    elif store_id:
        query = query.filter(models.FreshnessWarning.store_id == store_id)

    all_warnings = query.all()

    status_counts = {
        models.WarningStatus.PENDING.value: 0,
        models.WarningStatus.PROCESSING.value: 0,
        models.WarningStatus.RESOLVED.value: 0,
        models.WarningStatus.EXPIRED.value: 0,
        models.WarningStatus.CANCELLED.value: 0,
    }
    overdue_count = 0
    new_today = 0

    for w in all_warnings:
        if w.status in status_counts:
            status_counts[w.status] += 1
        if w.is_overdue and w.status in [
            models.WarningStatus.PENDING.value,
            models.WarningStatus.PROCESSING.value
        ]:
            overdue_count += 1
        if w.created_at >= today_start:
            new_today += 1

    total = len(all_warnings)
    processed = status_counts[models.WarningStatus.RESOLVED.value] + \
        status_counts[models.WarningStatus.EXPIRED.value] + \
        status_counts[models.WarningStatus.CANCELLED.value]
    processed_rate = round(processed / total * 100, 2) if total > 0 else 0.0

    return {
        "period_days": days,
        "total_warnings": total,
        "pending_count": status_counts[models.WarningStatus.PENDING.value],
        "processing_count": status_counts[models.WarningStatus.PROCESSING.value],
        "resolved_count": status_counts[models.WarningStatus.RESOLVED.value],
        "expired_count": status_counts[models.WarningStatus.EXPIRED.value],
        "cancelled_count": status_counts[models.WarningStatus.CANCELLED.value],
        "processed_rate": processed_rate,
        "overdue_count": overdue_count,
        "new_today": new_today
    }


def get_warning_trend(
    db: Session,
    current_user: models.User,
    days: int = 7,
    store_id: Optional[int] = None
) -> List[Dict[str, Any]]:
    now = datetime.utcnow()
    cutoff = now - timedelta(days=days)

    query = db.query(models.FreshnessWarning).filter(
        models.FreshnessWarning.created_at >= cutoff
    )

    if current_user.role == models.UserRole.STORE_STAFF.value:
        query = query.filter(models.FreshnessWarning.store_id == current_user.store_id)
    elif store_id:
        query = query.filter(models.FreshnessWarning.store_id == store_id)

    all_warnings = query.all()

    daily_data = {}
    for i in range(days):
        day_date = (now - timedelta(days=days - 1 - i)).date()
        daily_data[day_date] = {"new_count": 0, "resolved_count": 0, "overdue_count": 0}

    for w in all_warnings:
        create_date = w.created_at.date()
        if create_date in daily_data:
            daily_data[create_date]["new_count"] += 1
        if w.status == models.WarningStatus.RESOLVED.value and w.processed_at:
            resolve_date = w.processed_at.date()
            if resolve_date in daily_data:
                daily_data[resolve_date]["resolved_count"] += 1
        if w.is_overdue:
            create_date = w.created_at.date()
            if create_date in daily_data:
                daily_data[create_date]["overdue_count"] += 1

    results = []
    for d in sorted(daily_data.keys()):
        results.append({
            "date": d,
            "new_count": daily_data[d]["new_count"],
            "resolved_count": daily_data[d]["resolved_count"],
            "overdue_count": daily_data[d]["overdue_count"]
        })

    return results


def get_store_ranking(
    db: Session,
    current_user: models.User,
    days: int = 7,
    limit: int = 20
) -> List[Dict[str, Any]]:
    now = datetime.utcnow()
    cutoff = now - timedelta(days=days)

    if current_user.role != models.UserRole.HQ_ADMIN.value:
        return []

    stores = db.query(models.Store).filter(models.Store.is_active == True).all()
    rankings = []

    for store in stores:
        warnings = db.query(models.FreshnessWarning).filter(
            models.FreshnessWarning.store_id == store.id,
            models.FreshnessWarning.created_at >= cutoff
        ).all()

        total = len(warnings)
        resolved = sum(1 for w in warnings if w.status == models.WarningStatus.RESOLVED.value)
        expired = sum(1 for w in warnings if w.status == models.WarningStatus.EXPIRED.value)
        overdue = sum(1 for w in warnings if w.is_overdue and w.status in [
            models.WarningStatus.PENDING.value,
            models.WarningStatus.PROCESSING.value
        ])
        processed_rate = round((resolved + expired) / total * 100, 2) if total > 0 else 0.0

        rankings.append({
            "store_id": store.id,
            "store_name": store.store_name,
            "warning_count": total,
            "resolved_count": resolved,
            "processed_rate": processed_rate,
            "overdue_count": overdue
        })

    rankings.sort(key=lambda x: (-x["warning_count"], x["processed_rate"]))

    for i, r in enumerate(rankings):
        r["rank"] = i + 1

    return rankings[:limit]


def get_warning_level_distribution(
    db: Session,
    current_user: models.User,
    days: int = 7,
    store_id: Optional[int] = None
) -> List[Dict[str, Any]]:
    now = datetime.utcnow()
    cutoff = now - timedelta(days=days)

    query = db.query(models.FreshnessWarning).filter(
        models.FreshnessWarning.created_at >= cutoff
    )

    if current_user.role == models.UserRole.STORE_STAFF.value:
        query = query.filter(models.FreshnessWarning.store_id == current_user.store_id)
    elif store_id:
        query = query.filter(models.FreshnessWarning.store_id == store_id)

    all_warnings = query.all()
    total = len(all_warnings)

    level_counts = {}
    for w in all_warnings:
        level_counts[w.warning_level] = level_counts.get(w.warning_level, 0) + 1

    results = []
    for level in [
        models.WarningLevel.URGENT.value,
        models.WarningLevel.WARNING.value,
        models.WarningLevel.ATTENTION.value,
        models.WarningLevel.NORMAL.value
    ]:
        count = level_counts.get(level, 0)
        percentage = round(count / total * 100, 2) if total > 0 else 0.0
        results.append({
            "warning_level": level,
            "level_label": WARNING_LEVEL_LABELS.get(level, level),
            "count": count,
            "percentage": percentage
        })

    return results
