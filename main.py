import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from app.database import Base, engine, SessionLocal, settings
from app import models, auth
from app.routers.auth_router import router as auth_router, router_users
from app.routers.hq_router import hq_router
from app.routers.store_router import store_router
from app.routers.qc_router import qc_router
from app.routers.stats_router import stats_router
from app.services import anomaly_detector


def init_seed_data(db: Session):
    hq = db.query(models.User).filter(models.User.username == "hq_admin").first()
    if not hq:
        db.add(models.User(
            username="hq_admin",
            hashed_password=auth.get_password_hash("admin123"),
            full_name="总部管理员",
            role=models.UserRole.HQ_ADMIN.value,
            is_active=True
        ))
    qc = db.query(models.User).filter(models.User.username == "qc_staff").first()
    if not qc:
        db.add(models.User(
            username="qc_staff",
            hashed_password=auth.get_password_hash("qc123456"),
            full_name="品控员01",
            role=models.UserRole.QC_STAFF.value,
            is_active=True
        ))
    store1 = db.query(models.Store).filter(models.Store.store_code == "S001").first()
    if not store1:
        store1 = models.Store(
            store_code="S001",
            store_name="旗舰店",
            address="示例街道001号",
            manager_name="张三",
            phone="13800000001",
            is_active=True
        )
        db.add(store1)
        db.flush()
        store_staff1 = db.query(models.User).filter(models.User.username == "store_staff").first()
        if not store_staff1:
            db.add(models.User(
                username="store_staff",
                hashed_password=auth.get_password_hash("store123"),
                full_name="门店员工01",
                role=models.UserRole.STORE_STAFF.value,
                store_id=store1.id,
                is_active=True
            ))
    if not db.query(models.Store).filter(models.Store.store_code == "S002").first():
        db.add(models.Store(
            store_code="S002",
            store_name="社区店",
            address="示例路88号",
            manager_name="李四",
            phone="13800000002",
            is_active=True
        ))
    categories_seed = [
        ("FRUIT-ORG", "鲜橙", "kg"),
        ("FRUIT-APP", "苹果", "kg"),
        ("FRUIT-WML", "西瓜", "kg"),
        ("FRUIT-GRP", "葡萄", "kg"),
        ("VEG-CAR", "胡萝卜", "kg"),
        ("VEG-TMT", "番茄", "kg"),
    ]
    for code, name, unit in categories_seed:
        if not db.query(models.IngredientCategory).filter(models.IngredientCategory.category_code == code).first():
            db.add(models.IngredientCategory(
                category_code=code, category_name=name, unit=unit
            ))
    db.flush()
    all_cats = db.query(models.IngredientCategory).all()
    for cat in all_cats:
        if not db.query(models.BatchRule).filter(models.BatchRule.ingredient_category_id == cat.id).first():
            db.add(models.BatchRule(
                rule_name=f"{cat.category_name}批次规则",
                ingredient_category_id=cat.id,
                min_batch_size=1.0,
                max_batch_size=20.0,
                required_wash_duration_minutes=10,
                high_discard_threshold=0.15,
                temperature_min=2.0,
                temperature_max=8.0,
                temperature_log_interval_minutes=30,
                qc_required_within_hours=4
            ))
        for stage_name, hours in [("after_arrival", 12), ("after_wash", 8), ("after_production", 6)]:
            existing_rule = db.query(models.FreshnessRule).filter(
                models.FreshnessRule.ingredient_category_id == cat.id,
                models.FreshnessRule.stage == stage_name
            ).first()
            if not existing_rule:
                db.add(models.FreshnessRule(
                    ingredient_category_id=cat.id,
                    stage=stage_name,
                    max_hours=hours,
                    description=f"{cat.category_name}-{stage_name}保鲜时长{hours}小时"
                ))
        if not db.query(models.QCTemplate).filter(models.QCTemplate.ingredient_category_id == cat.id).first():
            db.add(models.QCTemplate(
                template_name=f"{cat.category_name}品控模板",
                ingredient_category_id=cat.id,
                check_items="外观色泽\\n香气气味\\n口感风味\\n组织形态\\n杂质检查",
                scoring_standard="每项满分20分，总计100分，80分以上合格，60-80分留观复检，60分以下不合格",
                disposition_options="合格放行\\n复检留观\\n不合格废弃"
            ))
    stations_seed = [
        ("ST-CLEAN", "清洗岗", "原料清洗、消毒处理", 1),
        ("ST-CUT", "切配岗", "果蔬切配、称量", 2),
        ("ST-JUICE", "鲜榨岗", "榨汁、混合调配", 3),
        ("ST-FILL", "灌装岗", "装杯、封口、贴标", 4),
        ("ST-STORE", "储存岗", "冷藏保存、温度监控", 5),
    ]
    for code, name, desc, sort in stations_seed:
        if not db.query(models.ProductionStation).filter(models.ProductionStation.station_code == code).first():
            db.add(models.ProductionStation(
                station_code=code,
                station_name=name,
                description=desc,
                sort_order=sort
            ))
    db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        init_seed_data(db)
    finally:
        db.close()
    yield


app = FastAPI(
    title="饮品门店鲜榨品控管理系统 API",
    description="总部管理、门店操作、品控抽检、异常检测、统计分析一体化后端服务",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(router_users)
app.include_router(hq_router)
app.include_router(store_router)
app.include_router(qc_router)
app.include_router(stats_router)


@app.get("/", tags=["根路径"])
def root():
    return {
        "name": "饮品门店鲜榨品控管理系统",
        "version": "1.0.0",
        "docs": "/docs",
        "redoc": "/redoc",
        "default_accounts": [
            {"username": "hq_admin", "password": "admin123", "role": "总部管理员"},
            {"username": "store_staff", "password": "store123", "role": "门店人员"},
            {"username": "qc_staff", "password": "qc123456", "role": "品控人员"},
        ]
    }


@app.get("/health", tags=["健康检查"])
def health_check():
    from sqlalchemy import text
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        return {"status": "healthy", "database": "ok"}
    except Exception as e:
        return {"status": "unhealthy", "database": str(e)}


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=settings.port,
        reload=True
    )
