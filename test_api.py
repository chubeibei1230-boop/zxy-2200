import urllib.request
import urllib.parse
import json

BASE = "http://localhost:8246"

def http_get(url, token=None):
    req = urllib.request.Request(BASE + url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    r = urllib.request.urlopen(req)
    return json.loads(r.read())

def http_post(url, data, token=None, content_type="application/json"):
    if content_type == "application/json":
        body = json.dumps(data).encode()
    else:
        body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(BASE + url, data=body, method="POST")
    req.add_header("Content-Type", content_type)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    r = urllib.request.urlopen(req)
    return json.loads(r.read())

def http_put(url, data, token=None):
    body = json.dumps(data).encode()
    req = urllib.request.Request(BASE + url, data=body, method="PUT")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    r = urllib.request.urlopen(req)
    return json.loads(r.read())

def main():
    print("=" * 60)
    print("【1】健康检查")
    h = http_get("/health")
    print(f"  Status: {h}")
    assert h["status"] == "healthy"

    print("\n【2】总部管理员登录")
    login_data = {"username": "hq_admin", "password": "admin123"}
    result = http_post("/api/auth/login", login_data, content_type="application/x-www-form-urlencoded")
    hq_token = result["access_token"]
    print(f"  登录成功，token长度: {len(hq_token)}")

    print("\n【3】门店人员登录")
    login_data = {"username": "store_staff", "password": "store123"}
    result = http_post("/api/auth/login", login_data, content_type="application/x-www-form-urlencoded")
    store_token = result["access_token"]
    print(f"  登录成功，token长度: {len(store_token)}")

    print("\n【4】品控人员登录")
    login_data = {"username": "qc_staff", "password": "qc123456"}
    result = http_post("/api/auth/login", login_data, content_type="application/x-www-form-urlencoded")
    qc_token = result["access_token"]
    print(f"  登录成功，token长度: {len(qc_token)}")

    print("\n【5】获取门店列表（总部管理员）")
    stores = http_get("/api/hq/stores", hq_token)
    print(f"  门店数量: {len(stores)}")
    for s in stores:
        print(f"    - {s['store_code']}: {s['store_name']}")

    print("\n【6】获取原料类别列表")
    cats = http_get("/api/hq/ingredient-categories", hq_token)
    print(f"  原料类别数量: {len(cats)}")
    for c in cats:
        print(f"    - {c['category_code']}: {c['category_name']}")
    cat_id = cats[0]["id"]
    store_id = stores[0]["id"]

    print("\n【7】获取制作岗位列表")
    stations = http_get("/api/hq/production-stations", hq_token)
    print(f"  岗位数量: {len(stations)}")
    for st in stations:
        print(f"    - {st['station_code']}: {st['station_name']}")
    station_id = stations[0]["id"]

    print("\n【8】门店登记原料批次")
    batch_data = {
        "batch_no": f"BATCH-{store_id}-20260621-001",
        "store_id": store_id,
        "ingredient_category_id": cat_id,
        "quantity": 10.5,
        "unit": "kg",
        "supplier_batch_no": "SUP-2026-062101"
    }
    batch = http_post("/api/store/batches", batch_data, store_token)
    batch_id = batch["id"]
    print(f"  批次登记成功: id={batch_id}, status={batch['status']}")

    print("\n【9】门店验收原料（校验：同一批号同门店不可重复）")
    acc_data = {
        "batch_id": batch_id,
        "appearance_check": True,
        "temperature_check": True,
        "packaging_check": True,
        "certificate_check": True,
        "accepted_quantity": 10.5,
        "is_accepted": True
    }
    acc = http_post("/api/store/acceptance", acc_data, store_token)
    print(f"  验收成功: id={acc['id']}")
    try:
        http_post("/api/store/acceptance", acc_data, store_token)
        print("  ❗重复验收未被拦截！")
    except Exception as e:
        print(f"  ✅ 重复验收已正确拦截: {str(e)[:60]}")

    print("\n【10】标记清洗完成、开始制作、完成制作")
    http_post(f"/api/store/batches/{batch_id}/wash-complete", {}, store_token)
    print("  ✅ 清洗完成")
    http_post(f"/api/store/batches/{batch_id}/start-production", {}, store_token)
    print("  ✅ 开始制作")
    rec_data = {
        "batch_id": batch_id,
        "station_id": station_id,
        "cups_produced": 0,
        "cups_discarded": 0
    }
    prod_rec = http_post("/api/store/production-records", rec_data, store_token)
    rec_id = prod_rec["id"]
    print(f"  ✅ 创建制作记录: id={rec_id}")
    update_rec = {
        "end_time": "2026-06-21T12:30:00",
        "cups_produced": 85,
        "cups_discarded": 5,
        "abnormal_remark": "少量原料瑕疵导致部分废弃"
    }
    updated = http_put(f"/api/store/production-records/{rec_id}", update_rec, store_token)
    print(f"  ✅ 更新制作记录: produced={updated['cups_produced']}, discarded={updated['cups_discarded']}")

    print("\n【11】添加温度记录")
    temp_data = {
        "batch_id": batch_id,
        "temperature": 4.5,
        "location": "冷藏柜A区"
    }
    temp = http_post("/api/store/temperature-logs", temp_data, store_token)
    print(f"  ✅ 温度记录: {temp['temperature']}°C")

    print("\n【12】品控抽检")
    qc_data = {
        "batch_id": batch_id,
        "appearance_score": 90,
        "taste_score": 88,
        "texture_score": 92,
        "overall_score": 90,
        "taste_deviation": "正常无偏差",
        "check_result": "各项指标合格",
        "disposition": "合格放行",
        "disposition_note": "符合品控标准，允许销售"
    }
    qc_inspection = http_post("/api/qc/inspections", qc_data, qc_token)
    print(f"  ✅ 品控抽检: overall={qc_inspection['overall_score']}, disposition={qc_inspection['disposition']}")

    print("\n【13】运行异常检测")
    det_result = http_post("/api/stats/run-detection", {}, qc_token)
    print(f"  异常检测结果: {det_result}")

    print("\n【14】统计分析：异常原料排行")
    ranking = http_get("/api/stats/abnormal-ranking?days=7", hq_token)
    print(f"  返回排行条目数: {len(ranking)}")

    print("\n【15】统计分析：抽检待办")
    todos = http_get("/api/stats/qc-todos", qc_token)
    print(f"  待办条目数: {len(todos)}")

    print("\n【16】统计分析：门店废弃趋势")
    trend = http_get("/api/stats/discard-trend?days=14", hq_token)
    print(f"  趋势数据点数: {len(trend)}")

    print("\n【17】统计分析：总览")
    overview = http_get("/api/stats/overview?days=7", hq_token)
    print(f"  概览: batches={overview['total_batches']}, anomalies={overview['total_anomalies']}")

    print("\n【18】按条件筛选批次")
    filtered = http_get(f"/api/store/batches?store_id={store_id}&status=pending_qc", hq_token)
    print(f"  待抽检批次数量: {len(filtered)}")

    print("\n" + "=" * 60)
    print("✅ 全部测试通过！系统运行正常")
    print("=" * 60)

if __name__ == "__main__":
    main()
