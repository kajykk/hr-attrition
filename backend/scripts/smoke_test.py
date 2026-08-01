"""HRA 后端冒烟脚本（对 127.0.0.1:8010）.

覆盖：health / 根路径 / 401 守卫 / 登录失败审计 / 2FA 强制 / 登录成功 /
创建员工 / 风险预测（写库）/ 全局解释。
"""
import sys

import httpx

BASE = "http://127.0.0.1:8010"
ADMIN_EMAIL = "admin@hra-demo.com"
ADMIN_PASSWORD = "hra-admin-2026"
TOTP_SECRET = "5EMGZQL24FUFFBGWBY4WNSJ6DCOJJO5I"
PASS = []
FAIL = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL"), name, detail if not cond else "")


def main():
    # 1. 健康检查（真实探测 DB/Redis）
    r = httpx.get(f"{BASE}/health", timeout=10)
    body = r.json()
    check("health 200", r.status_code == 200)
    check("health db/redis healthy", body.get("components", {}).get("database") == "healthy"
          and body.get("components", {}).get("redis") == "healthy", str(body.get("components")))
    check("health X-Request-ID", bool(r.headers.get("x-request-id")))

    # 2. 根路径
    r = httpx.get(f"{BASE}/", timeout=10)
    check("root 200", r.status_code == 200 and r.json().get("app") == "HRA")

    # 3. 无 token 访问需认证端点
    r = httpx.get(f"{BASE}/api/v1/employees", timeout=10)
    check("employees 无 token -> 401", r.status_code == 401)

    # 4. 登录失败（错误密码）
    r = httpx.post(f"{BASE}/api/v1/auth/login",
                   json={"email": "admin@hra-demo.com", "password": "wrong-pass"}, timeout=10)
    check("登录错误密码 -> 401", r.status_code == 401)

    # 5. 正确密码但无 2FA -> 401（管理员强制 TOTP）
    r = httpx.post(f"{BASE}/api/v1/auth/login",
                   json={"email": "admin@hra-demo.com", "password": "hra-admin-2026"}, timeout=10)
    check("管理员无 2FA -> 401", r.status_code == 401)

    # 6. 正确密码 + 2FA -> 200
    import pyotp
    totp = pyotp.TOTP("5EMGZQL24FUFFBGWBY4WNSJ6DCOJJO5I").now()
    r = httpx.post(f"{BASE}/api/v1/auth/login",
                   json={"email": "admin@hra-demo.com", "password": "hra-admin-2026",
                         "totp_code": totp}, timeout=10)
    check("登录+2FA -> 200", r.status_code == 200, str(r.text[:200]))
    if r.status_code != 200:
        print("SUMMARY: %d pass, %d fail" % (len(PASS), len(FAIL)))
        sys.exit(1)
    access = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {access}"}

    # 7. 带 token 拉员工列表（空）
    r = httpx.get(f"{BASE}/api/v1/employees", headers=headers, timeout=10)
    check("员工列表 200", r.status_code == 200)
    if r.status_code == 200:
        check("员工列表结构", r.json()["total"] >= 0)

    # 8. 创建员工（HR 角色）
    r = httpx.post(f"{BASE}/api/v1/employees", headers=headers,
                   json={"employee_no": "SMK001", "name": "冒烟测试",
                         "email": "smoke@hra-demo.com", "hire_date": "2024-01-15",
                         "salary_percentile": 42}, timeout=10)
    check("创建员工 201（重复冒烟 409 视为通过）", r.status_code in (201, 409), str(r.text[:300]))
    emp_id = None
    if r.status_code == 201:
        emp_id = r.json()["id"]
    else:
        # 可能已存在（重复冒烟）：查列表找
        r2 = httpx.get(f"{BASE}/api/v1/employees", headers=headers, params={"keyword": "SMK001"}, timeout=10)
        if r2.status_code == 200 and r2.json()["total"] > 0:
            emp_id = r2.json()["items"][0]["id"]
            check("创建员工已存在（幂等回退）", True)
    check("重复创建 -> 409", httpx.post(f"{BASE}/api/v1/employees", headers=headers,
          json={"employee_no": "SMK001", "name": "冒烟测试", "hire_date": "2024-01-15"},
          timeout=10).status_code == 409)

    # 9. 风险预测（写库链路）
    if emp_id:
        r = httpx.post(f"{BASE}/api/v1/risk/predict", headers=headers,
                       json={"employee_id": emp_id, "force_refresh": False}, timeout=30)
        check("风险预测 200", r.status_code == 200, str(r.text[:300]))
        if r.status_code == 200:
            d = r.json()
            check("预测含 risk_score", "risk_score" in d)
            check("预测含 prediction_id", bool(d.get("prediction_id")))
            print("     risk_score=%s level=%s model=%s cached=%s" % (
                d.get("risk_score"), d.get("risk_level"), d.get("model_version"), d.get("cached")))

        # 10. 员工列表应带风险分（M12）
        r = httpx.get(f"{BASE}/api/v1/employees", headers=headers, params={"keyword": "SMK001"}, timeout=10)
        if r.status_code == 200 and r.json()["total"] > 0:
            item = r.json()["items"][0]
            check("列表含风险分", item.get("risk_score") is not None, str(item))

        # 11. 全局解释（SQL 聚合 + to_thread）
        r = httpx.get(f"{BASE}/api/v1/risk/global-explanation", headers=headers,
                      params={"window_days": 30}, timeout=15)
        check("全局解释 200", r.status_code == 200)
        if r.status_code == 200:
            check("全局解释 top_features", len(r.json().get("top_features", [])) > 0,
                  str(r.json())[:200])

    # 12. refresh 链路
    r = httpx.post(f"{BASE}/api/v1/auth/refresh",
                   json={"refresh_token": "invalid.token"}, timeout=10)
    check("无效 refresh -> 401", r.status_code == 401)

    print("SUMMARY: %d pass, %d fail" % (len(PASS), len(FAIL)))
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()


