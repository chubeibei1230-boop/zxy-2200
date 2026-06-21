from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from app.database import get_db
from app import models, schemas, auth

hq_router = APIRouter(prefix="/api/hq", tags=["总部管理"])


@hq_router.post("/stores", response_model=schemas.StoreResponse)
def create_store(
    store_in: schemas.StoreCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(models.UserRole.HQ_ADMIN.value))
):
    existing = db.query(models.Store).filter(models.Store.store_code == store_in.store_code).first()
    if existing:
        raise HTTPException(status_code=400, detail="门店编码已存在")
    store = models.Store(**store_in.model_dump())
    db.add(store)
    db.commit()
    db.refresh(store)
    return store


@hq_router.get("/stores", response_model=List[schemas.StoreResponse])
def list_stores(
    is_active: Optional[bool] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_active_user)
):
    query = db.query(models.Store)
    if is_active is not None:
        query = query.filter(models.Store.is_active == is_active)
    return query.order_by(models.Store.store_code).all()


@hq_router.get("/stores/{store_id}", response_model=schemas.StoreResponse)
def get_store(
    store_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_active_user)
):
    store = db.query(models.Store).filter(models.Store.id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="门店不存在")
    return store


@hq_router.put("/stores/{store_id}", response_model=schemas.StoreResponse)
def update_store(
    store_id: int,
    store_in: schemas.StoreUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(models.UserRole.HQ_ADMIN.value))
):
    store = db.query(models.Store).filter(models.Store.id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="门店不存在")
    update_data = store_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(store, field, value)
    db.commit()
    db.refresh(store)
    return store


@hq_router.post("/ingredient-categories", response_model=schemas.IngredientCategoryResponse)
def create_ingredient_category(
    cat_in: schemas.IngredientCategoryCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(models.UserRole.HQ_ADMIN.value))
):
    existing = db.query(models.IngredientCategory).filter(
        models.IngredientCategory.category_code == cat_in.category_code
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="原料类别编码已存在")
    cat = models.IngredientCategory(**cat_in.model_dump())
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat


@hq_router.get("/ingredient-categories", response_model=List[schemas.IngredientCategoryResponse])
def list_ingredient_categories(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_active_user)
):
    return db.query(models.IngredientCategory).order_by(models.IngredientCategory.category_code).all()


@hq_router.put("/ingredient-categories/{cat_id}", response_model=schemas.IngredientCategoryResponse)
def update_ingredient_category(
    cat_id: int,
    cat_in: schemas.IngredientCategoryUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(models.UserRole.HQ_ADMIN.value))
):
    cat = db.query(models.IngredientCategory).filter(models.IngredientCategory.id == cat_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="原料类别不存在")
    update_data = cat_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(cat, field, value)
    db.commit()
    db.refresh(cat)
    return cat


@hq_router.post("/batch-rules", response_model=schemas.BatchRuleResponse)
def create_batch_rule(
    rule_in: schemas.BatchRuleCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(models.UserRole.HQ_ADMIN.value))
):
    cat = db.query(models.IngredientCategory).filter(
        models.IngredientCategory.id == rule_in.ingredient_category_id
    ).first()
    if not cat:
        raise HTTPException(status_code=400, detail="原料类别不存在")
    rule = models.BatchRule(**rule_in.model_dump())
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@hq_router.get("/batch-rules", response_model=List[schemas.BatchRuleResponse])
def list_batch_rules(
    ingredient_category_id: Optional[int] = None,
    is_active: Optional[bool] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_active_user)
):
    query = db.query(models.BatchRule)
    if ingredient_category_id:
        query = query.filter(models.BatchRule.ingredient_category_id == ingredient_category_id)
    if is_active is not None:
        query = query.filter(models.BatchRule.is_active == is_active)
    return query.all()


@hq_router.put("/batch-rules/{rule_id}", response_model=schemas.BatchRuleResponse)
def update_batch_rule(
    rule_id: int,
    rule_in: schemas.BatchRuleUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(models.UserRole.HQ_ADMIN.value))
):
    rule = db.query(models.BatchRule).filter(models.BatchRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="批次规则不存在")
    update_data = rule_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(rule, field, value)
    db.commit()
    db.refresh(rule)
    return rule


@hq_router.post("/freshness-rules", response_model=schemas.FreshnessRuleResponse)
def create_freshness_rule(
    rule_in: schemas.FreshnessRuleCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(models.UserRole.HQ_ADMIN.value))
):
    cat = db.query(models.IngredientCategory).filter(
        models.IngredientCategory.id == rule_in.ingredient_category_id
    ).first()
    if not cat:
        raise HTTPException(status_code=400, detail="原料类别不存在")
    rule = models.FreshnessRule(**rule_in.model_dump())
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@hq_router.get("/freshness-rules", response_model=List[schemas.FreshnessRuleResponse])
def list_freshness_rules(
    ingredient_category_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_active_user)
):
    query = db.query(models.FreshnessRule)
    if ingredient_category_id:
        query = query.filter(models.FreshnessRule.ingredient_category_id == ingredient_category_id)
    return query.all()


@hq_router.put("/freshness-rules/{rule_id}", response_model=schemas.FreshnessRuleResponse)
def update_freshness_rule(
    rule_id: int,
    rule_in: schemas.FreshnessRuleUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(models.UserRole.HQ_ADMIN.value))
):
    rule = db.query(models.FreshnessRule).filter(models.FreshnessRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="保鲜规则不存在")
    update_data = rule_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(rule, field, value)
    db.commit()
    db.refresh(rule)
    return rule


@hq_router.post("/production-stations", response_model=schemas.ProductionStationResponse)
def create_production_station(
    station_in: schemas.ProductionStationCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(models.UserRole.HQ_ADMIN.value))
):
    existing = db.query(models.ProductionStation).filter(
        models.ProductionStation.station_code == station_in.station_code
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="岗位编码已存在")
    station = models.ProductionStation(**station_in.model_dump())
    db.add(station)
    db.commit()
    db.refresh(station)
    return station


@hq_router.get("/production-stations", response_model=List[schemas.ProductionStationResponse])
def list_production_stations(
    is_active: Optional[bool] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_active_user)
):
    query = db.query(models.ProductionStation)
    if is_active is not None:
        query = query.filter(models.ProductionStation.is_active == is_active)
    return query.order_by(models.ProductionStation.sort_order, models.ProductionStation.station_code).all()


@hq_router.put("/production-stations/{station_id}", response_model=schemas.ProductionStationResponse)
def update_production_station(
    station_id: int,
    station_in: schemas.ProductionStationUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(models.UserRole.HQ_ADMIN.value))
):
    station = db.query(models.ProductionStation).filter(models.ProductionStation.id == station_id).first()
    if not station:
        raise HTTPException(status_code=404, detail="制作岗位不存在")
    update_data = station_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(station, field, value)
    db.commit()
    db.refresh(station)
    return station


@hq_router.post("/qc-templates", response_model=schemas.QCTemplateResponse)
def create_qc_template(
    tpl_in: schemas.QCTemplateCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(models.UserRole.HQ_ADMIN.value))
):
    cat = db.query(models.IngredientCategory).filter(
        models.IngredientCategory.id == tpl_in.ingredient_category_id
    ).first()
    if not cat:
        raise HTTPException(status_code=400, detail="原料类别不存在")
    tpl = models.QCTemplate(**tpl_in.model_dump())
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    return tpl


@hq_router.get("/qc-templates", response_model=List[schemas.QCTemplateResponse])
def list_qc_templates(
    ingredient_category_id: Optional[int] = None,
    is_active: Optional[bool] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_active_user)
):
    query = db.query(models.QCTemplate)
    if ingredient_category_id:
        query = query.filter(models.QCTemplate.ingredient_category_id == ingredient_category_id)
    if is_active is not None:
        query = query.filter(models.QCTemplate.is_active == is_active)
    return query.all()


@hq_router.put("/qc-templates/{tpl_id}", response_model=schemas.QCTemplateResponse)
def update_qc_template(
    tpl_id: int,
    tpl_in: schemas.QCTemplateUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(models.UserRole.HQ_ADMIN.value))
):
    tpl = db.query(models.QCTemplate).filter(models.QCTemplate.id == tpl_id).first()
    if not tpl:
        raise HTTPException(status_code=404, detail="品控模板不存在")
    update_data = tpl_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(tpl, field, value)
    db.commit()
    db.refresh(tpl)
    return tpl
