import urllib.request
import urllib.parse
import json
from datetime import date

BASE = "http://localhost:8247"

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

def expect_error(fn, expected_substring=None):
    try:
        fn()
        return False, None
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            detail = json.loads(body).get("detail", body)
        except Exception:
            detail = body
        if expected_substring and expected_substring not in str(detail):
            return False, detail
        return True, detail
    except Exception as e:
        return False, str(e)

def main():
    print("=" * 70)
    print("饮品门店鲜榨品控系统 - 完整功能 & 修复验证测试")
    print("=" * 70)

    print("\n【0】健康检查")
    h = http_get("/health")
    assert h["status"] == "healthy", f"健康检查失败: {h}"
    print(f"  ✅ 数据库连接正常")

    print("\n【1】三种角色登录")
    hq_token = http_post("/api/auth/login", {"username": "hq_admin", "password": "admin123"},
                        content_type="application/x-www-form-urlencoded")["access_token"]
    store_token = http_post("/api/auth/login", {"username": "store_staff", "password": "store123"},
                           content_type="application/x-www-form-urlencoded")["access_token"]
    qc_token = http_post("/api/auth/login", {"username": "qc_staff", "password": "qc123456"},
                        content_type="application/x-www-form-urlencoded")["access_token"]
    print(f"  ✅ 总部管理员、门店人员、品控人员均登录成功")

    stores = http_get("/api/hq/stores", hq_token)
    store_id = stores[0]["id"]
    cats = http_get("/api/hq/ingredient-categories", hq_token)
    cat_id = cats[0]["id"]
    stations = http_get("/api/hq/production-stations", hq_token)
    station_id = stations[0]["id"]

    import random
    suffix = random.randint(1000, 9999)

    print("\n" + "=" * 70)
    print(" 修复 1 验证：未清洗完成不可开始制作")
    print("=" * 70)

    batch_data = {
        "batch_no": f"BATCH-TEST-WASH-{suffix}",
        "store_id": store_id,
        "ingredient_category_id": cat_id,
        "quantity": 5,
        "unit": "kg"
    }
    b = http_post("/api/store/batches", batch_data, store_token)
    bid1 = b["id"]
    print(f"  ✅ 创建批次 id={bid1}, 状态={b['status']}")

    acc_data = {"batch_id": bid1, "accepted_quantity": 5, "is_accepted": True}
    http_post("/api/store/acceptance", acc_data, store_token)
    print(f"  ✅ 验收通过，状态变为可制作")

    ok, detail = expect_error(lambda: http_post(f"/api/store/batches/{bid1}/start-production", {}, store_token),
                              "清洗尚未完成")
    if ok:
        print(f"  ✅ 未清洗直接开始制作被正确拦截: {detail[:60]}")
    else:
        print(f"  ❌ 修复未生效！未清洗也能开始制作: {detail}")
        raise AssertionError("修复1验证失败")

    http_post(f"/api/store/batches/{bid1}/wash-complete", {}, store_token)
    print(f"  ✅ 标记清洗完成")
    b2 = http_post(f"/api/store/batches/{bid1}/start-production", {}, store_token)
    print(f"  ✅ 清洗后可正常开始制作，状态={b2['status']}")

    print("\n" + "=" * 70)
    print(" 修复 2 验证：未开始制作不可创建制作记录")
    print("=" * 70)

    batch_data2 = {
        "batch_no": f"BATCH-TEST-PROD-{suffix}",
        "store_id": store_id,
        "ingredient_category_id": cat_id,
        "quantity": 3,
        "unit": "kg"
    }
    b3 = http_post("/api/store/batches", batch_data2, store_token)
    bid2 = b3["id"]
    acc_data2 = {"batch_id": bid2, "accepted_quantity": 3, "is_accepted": True}
    http_post("/api/store/acceptance", acc_data2, store_token)
    http_post(f"/api/store/batches/{bid2}/wash-complete", {}, store_token)
    print(f"  ✅ 新建批次并验收清洗，状态为可制作")

    rec_data = {"batch_id": bid2, "station_id": station_id}
    ok, detail = expect_error(lambda: http_post("/api/store/production-records", rec_data, store_token),
                              "不可创建制作记录")
    if ok:
        print(f"  ✅ 未开始制作直接建记录被正确拦截: {detail[:60]}")
    else:
        print(f"  ❌ 修复未生效！未制作也能创建记录: {detail}")
        raise AssertionError("修复2验证失败")

    http_post(f"/api/store/batches/{bid2}/start-production", {}, store_token)
    rec = http_post("/api/store/production-records", rec_data, store_token)
    print(f"  ✅ 开始制作后可正常创建制作记录 id={rec['id']}")

    print("\n" + "=" * 70)
    print(" 修复 3 验证：未进入待抽检不可提前写入抽检记录")
    print("=" * 70)

    batch_data3 = {
        "batch_no": f"BATCH-TEST-QC-{suffix}",
        "store_id": store_id,
        "ingredient_category_id": cat_id,
        "quantity": 4,
        "unit": "kg"
    }
    b4 = http_post("/api/store/batches", batch_data3, store_token)
    bid3 = b4["id"]
    acc_data3 = {"batch_id": bid3, "accepted_quantity": 4, "is_accepted": True}
    http_post("/api/store/acceptance", acc_data3, store_token)
    print(f"  ✅ 新建批次并验收，状态为可制作")

    qc_data = {"batch_id": bid3, "overall_score": 90, "disposition": "合格放行"}
    ok, detail = expect_error(lambda: http_post("/api/qc/inspections", qc_data, qc_token),
                              "不可执行抽检")
    if ok:
        print(f"  ✅ 待验收状态下抽检被正确拦截: {detail[:60]}")
    else:
        print(f"  ❌ 修复未生效！待验收状态也能抽检: {detail}")
        raise AssertionError("修复3验证失败")

    http_post(f"/api/store/batches/{bid3}/wash-complete", {}, store_token)
    http_post(f"/api/store/batches/{bid3}/start-production", {}, store_token)
    rec_data2 = {"batch_id": bid3, "station_id": station_id}
    rec2 = http_post("/api/store/production-records", rec_data2, store_token)
    http_put(f"/api/store/production-records/{rec2['id']}",
             {"end_time": "2026-06-21T12:00:00", "cups_produced": 50, "cups_discarded": 2},
             store_token)
    print(f"  ✅ 批次进入待抽检状态")

    qc_inspection = http_post("/api/qc/inspections", qc_data, qc_token)
    print(f"  ✅ 待抽检状态下可正常抽检: overall={qc_inspection['overall_score']}")

    print("\n" + "=" * 70)
    print(" 修复 4 验证：日期范围筛选包含当天数据")
    print("=" * 70)

    today = date.today().isoformat()
    all_batches = http_get(f"/api/store/batches?date_from={today}&date_to={today}", hq_token)
    print(f"  查询日期范围 {today} ~ {today}，返回 {len(all_batches)} 条批次")
    if len(all_batches) > 0:
        print(f"  ✅ 日期筛选正确包含当天数据")
    else:
        print(f"  ❌ 日期筛选漏掉当天数据！")
        raise AssertionError("修复4验证失败")

    print("\n" + "=" * 70)
    print(" 修复 5 验证：门店人员无品控记录不可直接放行")
    print("=" * 70)

    batch_data5 = {
        "batch_no": f"BATCH-TEST-SALE-{suffix}",
        "store_id": store_id,
        "ingredient_category_id": cat_id,
        "quantity": 2.5,
        "unit": "kg"
    }
    b5 = http_post("/api/store/batches", batch_data5, store_token)
    bid4 = b5["id"]
    acc_data5 = {"batch_id": bid4, "accepted_quantity": 2.5, "is_accepted": True}
    http_post("/api/store/acceptance", acc_data5, store_token)
    http_post(f"/api/store/batches/{bid4}/wash-complete", {}, store_token)
    http_post(f"/api/store/batches/{bid4}/start-production", {}, store_token)
    rec5 = http_post("/api/store/production-records", {"batch_id": bid4, "station_id": station_id}, store_token)
    http_put(f"/api/store/production-records/{rec5['id']}",
             {"end_time": "2026-06-21T14:00:00", "cups_produced": 40, "cups_discarded": 1},
             store_token)
    print(f"  ✅ 批次进入待抽检状态，尚无品控记录")

    ok, detail = expect_error(lambda: http_post(f"/api/store/batches/{bid4}/mark-saleable", {}, store_token),
                              "门店人员不可直接放行")
    if ok:
        print(f"  ✅ 门店人员无品控记录直接放行被正确拦截: {detail[:60]}")
    else:
        print(f"  ❌ 修复未生效！门店无品控也能放行: {detail}")
        raise AssertionError("修复5验证失败")

    qc_data5 = {"batch_id": bid4, "overall_score": 88, "disposition": "合格放行"}
    http_post("/api/qc/inspections", qc_data5, qc_token)
    print(f"  ✅ 品控人员完成抽检并放行")

    print("\n" + "=" * 70)
    print(" 回归测试：其他核心功能")
    print("=" * 70)

    qc_list = http_get("/api/qc/inspections", qc_token)
    print(f"  ✅ 品控抽检列表: {len(qc_list)} 条")

    anomaly_list = http_get("/api/qc/anomalies?is_resolved=false", qc_token)
    print(f"  ✅ 异常事件列表: {len(anomaly_list)} 条未解决")

    overview = http_get("/api/stats/overview?days=7", hq_token)
    print(f"  ✅ 统计概览: 批次={overview['total_batches']}, 异常={overview['total_anomalies']}")

    ranking = http_get("/api/stats/abnormal-ranking?days=7", hq_token)
    print(f"  ✅ 异常原料排行: {len(ranking)} 条")

    todos = http_get("/api/stats/qc-todos", qc_token)
    print(f"  ✅ 抽检待办: {len(todos)} 条")

    trend = http_get("/api/stats/discard-trend?days=14", hq_token)
    print(f"  ✅ 废弃趋势: {len(trend)} 个数据点")

    print("\n" + "=" * 70)
    print("🎉 全部 5 项修复验证通过！系统运行正常")
    print("=" * 70)

if __name__ == "__main__":
    import urllib.error
    main()
