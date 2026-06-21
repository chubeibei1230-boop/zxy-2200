from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_
from typing import List, Dict, Any, Optional
from app import models


def create_anomaly_event(
    db: Session,
    batch_id: int,
    anomaly_type: str,
    severity: str,
    description: str
) -> models.AnomalyEvent:
    existing = db.query(models.AnomalyEvent).filter(
        models.AnomalyEvent.batch_id == batch_id,
        models.AnomalyEvent.anomaly_type == anomaly_type,
        models.AnomalyEvent.is_resolved == False
    ).first()
    if existing:
        return existing
    event = models.AnomalyEvent(
        batch_id=batch_id,
        anomaly_type=anomaly_type,
        severity=severity,
        description=description,
        detected_at=datetime.utcnow()
    )
    db.add(event)
    batch = db.query(models.MaterialBatch).filter(models.MaterialBatch.id == batch_id).first()
    if batch and batch.status not in [
        models.BatchStatus.ANOMALY_HOLD.value,
        models.BatchStatus.DISCARDED.value
    ]:
        batch.status = models.BatchStatus.ANOMALY_HOLD.value
    db.commit()
    db.refresh(event)
    return event


def detect_freshness_timeout(db: Session) -> List[models.AnomalyEvent]:
    results = []
    now = datetime.utcnow()
    freshness_rules = db.query(models.FreshnessRule).all()
    rule_map = {r.ingredient_category_id: {} for r in freshness_rules}
    for r in freshness_rules:
        rule_map[r.ingredient_category_id][r.stage] = r.max_hours

    active_batches = db.query(models.MaterialBatch).filter(
        models.MaterialBatch.status.notin_([
            models.BatchStatus.DISCARDED.value,
            models.BatchStatus.READY_FOR_SALE.value
        ])
    ).all()

    for batch in active_batches:
        cat_rules = rule_map.get(batch.ingredient_category_id, {})
        timeout_hours = None
        stage_name = None
        ref_time = None

        if batch.status == models.BatchStatus.READY_FOR_PRODUCTION.value:
            if batch.wash_complete_time:
                timeout_hours = cat_rules.get("after_wash")
                stage_name = "清洗后"
                ref_time = batch.wash_complete_time
            elif batch.arrival_time:
                timeout_hours = cat_rules.get("after_arrival") or cat_rules.get("acceptance")
                stage_name = "验收后"
                ref_time = batch.arrival_time
        elif batch.status == models.BatchStatus.PENDING_QC.value:
            if batch.production_complete_time:
                timeout_hours = cat_rules.get("after_production")
                stage_name = "制作完成后"
                ref_time = batch.production_complete_time

        if timeout_hours and ref_time:
            elapsed = (now - ref_time).total_seconds() / 3600
            if elapsed > timeout_hours:
                desc = f"[{stage_name}]保鲜超时，已存放{elapsed:.1f}小时，上限{timeout_hours}小时"
                sev = "high" if elapsed > timeout_hours * 1.5 else "medium"
                evt = create_anomaly_event(db, batch.id, models.AnomalyType.FRESHNESS_TIMEOUT.value, sev, desc)
                results.append(evt)
    return results


def detect_high_discard_ratio(db: Session, threshold_override: Optional[float] = None) -> List[models.AnomalyEvent]:
    results = []
    batch_rules = {
        br.ingredient_category_id: br
        for br in db.query(models.BatchRule).filter(models.BatchRule.is_active == True).all()
    }
    production_records = db.query(models.ProductionRecord).filter(
        models.ProductionRecord.end_time != None,
        models.ProductionRecord.cups_discarded > 0
    ).all()

    for rec in production_records:
        batch = db.query(models.MaterialBatch).filter(models.MaterialBatch.id == rec.batch_id).first()
        if not batch:
            continue
        rule = batch_rules.get(batch.ingredient_category_id)
        threshold = threshold_override or (rule.high_discard_threshold if rule else 0.15)
        total = rec.cups_produced + rec.cups_discarded
        if total <= 0:
            continue
        ratio = rec.cups_discarded / total
        if ratio > threshold:
            existing = db.query(models.AnomalyEvent).filter(
                models.AnomalyEvent.batch_id == batch.id,
                models.AnomalyEvent.anomaly_type == models.AnomalyType.HIGH_DISCARD_RATIO.value,
                models.AnomalyEvent.is_resolved == False
            ).first()
            if not existing:
                desc = f"废弃比例{ratio:.2%}超过阈值{threshold:.2%}，生产{total}杯废弃{rec.cups_discarded}杯"
                sev = "high" if ratio > threshold * 1.5 else "medium"
                evt = create_anomaly_event(db, batch.id, models.AnomalyType.HIGH_DISCARD_RATIO.value, sev, desc)
                results.append(evt)
    return results


def detect_qc_missing(db: Session) -> List[models.AnomalyEvent]:
    results = []
    now = datetime.utcnow()
    batch_rules = {
        br.ingredient_category_id: br
        for br in db.query(models.BatchRule).filter(models.BatchRule.is_active == True).all()
    }
    pending_qc_batches = db.query(models.MaterialBatch).filter(
        or_(
            models.MaterialBatch.status == models.BatchStatus.PENDING_QC.value,
            and_(
                models.MaterialBatch.status == models.BatchStatus.READY_FOR_SALE.value,
                models.MaterialBatch.production_complete_time != None
            )
        )
    ).all()

    for batch in pending_qc_batches:
        if not batch.production_complete_time:
            continue
        qc_done = db.query(models.QCInspection).filter(
            models.QCInspection.batch_id == batch.id
        ).first()
        if qc_done:
            continue
        rule = batch_rules.get(batch.ingredient_category_id)
        required_hours = rule.qc_required_within_hours if rule else 4
        elapsed = (now - batch.production_complete_time).total_seconds() / 3600
        if elapsed > required_hours:
            desc = f"抽检遗漏，制作完成后{elapsed:.1f}小时未抽检（要求{required_hours}小时内）"
            sev = "high" if elapsed > required_hours * 2 else "medium"
            evt = create_anomaly_event(db, batch.id, models.AnomalyType.QC_MISSING.value, sev, desc)
            results.append(evt)
    return results


def detect_temperature_gap(db: Session) -> List[models.AnomalyEvent]:
    results = []
    now = datetime.utcnow()
    batch_rules = {
        br.ingredient_category_id: br
        for br in db.query(models.BatchRule).filter(models.BatchRule.is_active == True).all()
    }
    batches_in_progress = db.query(models.MaterialBatch).filter(
        models.MaterialBatch.status.in_([
            models.BatchStatus.READY_FOR_PRODUCTION.value,
            models.BatchStatus.IN_PRODUCTION.value,
            models.BatchStatus.PENDING_QC.value,
            models.BatchStatus.READY_FOR_SALE.value
        ])
    ).all()

    for batch in batches_in_progress:
        ref_time = batch.wash_complete_time or batch.arrival_time
        if not ref_time:
            continue
        rule = batch_rules.get(batch.ingredient_category_id)
        interval_minutes = rule.temperature_log_interval_minutes if rule else 30
        if rule is None:
            continue
        last_log = db.query(models.TemperatureLog).filter(
            models.TemperatureLog.batch_id == batch.id
        ).order_by(models.TemperatureLog.log_time.desc()).first()

        gap_found = False
        if not last_log:
            elapsed = (now - ref_time).total_seconds() / 60
            if elapsed > interval_minutes * 2:
                gap_found = True
                desc = f"温度记录断档：自批次开始{elapsed:.0f}分钟无任何温度记录（要求每{interval_minutes}分钟记录）"
        else:
            gap = (now - last_log.log_time).total_seconds() / 60
            if gap > interval_minutes * 2:
                gap_found = True
                desc = f"温度记录断档：最近记录于{gap:.0f}分钟前（要求每{interval_minutes}分钟记录）"

        if gap_found:
            sev = "high" if "2倍" in "" else (
                "medium" if "无任何" in desc else "medium"
            )
            evt = create_anomaly_event(db, batch.id, models.AnomalyType.TEMPERATURE_GAP.value, sev, desc)
            results.append(evt)
    return results


def detect_concentrated_anomaly(db: Session, days_window: int = 7, threshold_count: int = 3) -> List[models.AnomalyEvent]:
    results = []
    cutoff = datetime.utcnow() - timedelta(days=days_window)
    subq = db.query(
        models.MaterialBatch.ingredient_category_id,
        models.MaterialBatch.store_id,
        func.count(models.AnomalyEvent.id).label("anomaly_count")
    ).join(
        models.AnomalyEvent, models.MaterialBatch.id == models.AnomalyEvent.batch_id
    ).filter(
        models.AnomalyEvent.created_at >= cutoff
    ).group_by(
        models.MaterialBatch.ingredient_category_id,
        models.MaterialBatch.store_id
    ).having(
        func.count(models.AnomalyEvent.id) >= threshold_count
    ).subquery()

    rows = db.query(
        subq.c.ingredient_category_id,
        subq.c.store_id,
        subq.c.anomaly_count
    ).all()

    for cat_id, store_id, count in rows:
        store_batches = db.query(models.MaterialBatch).filter(
            models.MaterialBatch.ingredient_category_id == cat_id,
            models.MaterialBatch.store_id == store_id,
            models.MaterialBatch.created_at >= cutoff
        ).all()
        batch_ids = [b.id for b in store_batches]
        if not batch_ids:
            continue
        latest_batch = store_batches[-1]
        existing = db.query(models.AnomalyEvent).filter(
            models.AnomalyEvent.batch_id.in_(batch_ids),
            models.AnomalyEvent.anomaly_type == models.AnomalyType.CONCENTRATED_ANOMALY.value,
            models.AnomalyEvent.is_resolved == False
        ).first()
        if existing:
            continue
        cat = db.query(models.IngredientCategory).filter(models.IngredientCategory.id == cat_id).first()
        store = db.query(models.Store).filter(models.Store.id == store_id).first()
        cat_name = cat.category_name if cat else "未知"
        store_name = store.store_name if store else "未知"
        desc = f"{store_name}近{days_window}天[{cat_name}]共发生{count}次异常（阈值{threshold_count}），同原料异常集中"
        sev = "high" if count >= threshold_count * 2 else "medium"
        evt = create_anomaly_event(db, latest_batch.id, models.AnomalyType.CONCENTRATED_ANOMALY.value, sev, desc)
        results.append(evt)
    return results


def run_all_detections(db: Session) -> Dict[str, int]:
    freshness = detect_freshness_timeout(db)
    discard = detect_high_discard_ratio(db)
    qc = detect_qc_missing(db)
    temp = detect_temperature_gap(db)
    concentrate = detect_concentrated_anomaly(db)
    return {
        "freshness_timeout": len(freshness),
        "high_discard_ratio": len(discard),
        "qc_missing": len(qc),
        "temperature_gap": len(temp),
        "concentrated_anomaly": len(concentrate),
        "total_new": len(freshness) + len(discard) + len(qc) + len(temp) + len(concentrate)
    }
