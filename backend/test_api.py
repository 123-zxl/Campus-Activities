# -*- coding: utf-8 -*-
"""
后端接口自动化测试（不启动真实服务器，用 Flask 自带的测试客户端跑）。

用法：
    python test_api.py

特点：使用独立的临时数据库，测完自动清理，绝不影响 campus.db 里的数据。
"""
import os
import sqlite3
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

# 1) 必须在 import db/app 之前指定临时数据库路径
TMP_DIR = tempfile.mkdtemp(prefix="campus_test_")
os.environ["CAMPUS_DB_PATH"] = os.path.join(TMP_DIR, "test.db")

# 2) 在临时库里建表 + 灌入和 init_db.py 相同的演示数据
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import DB_PATH  # noqa: E402
from init_db import create_schema, seed  # noqa: E402

con = sqlite3.connect(DB_PATH)
con.execute("PRAGMA foreign_keys = ON")
create_schema(con)
seed(con)
con.commit()
con.close()

import app as appmod  # noqa: E402

client = appmod.app.test_client()
passed = 0


def check(name, ok, detail=""):
    global passed
    if ok:
        passed += 1
        print("[PASS] " + name + (("  (" + detail + ")") if detail else ""))
    else:
        print("[FAIL] " + name + (("  (" + detail + ")") if detail else ""))
        raise AssertionError(name)


def login(uid, password="123456"):
    r = client.post("/api/login", json={"id": uid, "password": password})
    return r.get_json()["token"]


def H(token):
    return {"Authorization": "Bearer " + token}


NEW_ACTIVITY = {
    "name": "Python学习讲座",
    "start_time": "2026-11-01 14:00",
    "end_time": "2026-11-01 16:00",
    "register_deadline": "2026-10-31 20:00",
    "location": "教学楼A101",
    "total_count": 1,
    "description": "零基础入门",
}

# ==================================================================
print("=" * 60); print("一、健康检查与注册"); print("=" * 60)
r = client.get("/api/health")
check("健康检查 200", r.status_code == 200 and r.get_json()["ok"] is True)

r = client.post("/api/register", json={
    "id": "T003", "role": "teacher", "password": "123456", "name": "赵老师", "phone": "13800000003"})
j = r.get_json()
check("新教师注册成功 201", r.status_code == 201 and j["id"] == "T003" and "password" not in j)

r = client.post("/api/register", json={"id": "T001", "role": "teacher", "password": "123456", "name": "重复"})
check("重复账号被拒绝", r.status_code == 400 and "已存在" in r.get_json()["error"])

r = client.post("/api/register", json={
    "id": "2026004", "role": "student", "password": "123456", "name": "赵六"})
check("学生不填性别/班级被拒绝", r.status_code == 400)

r = client.post("/api/register", json={"id": "X1", "role": "admin", "password": "123456", "name": "x"})
check("非法 role 被拒绝", r.status_code == 400)

r = client.post("/api/register", json={
    "id": "2026004", "role": "student", "password": "123", "name": "赵六",
    "gender": "男", "major_class": "软工2603"})
check("密码太短被拒绝", r.status_code == 400)

r = client.post("/api/register", json={
    "id": "2026004", "role": "student", "password": "123456", "name": "赵六",
    "gender": "男", "major_class": "软工2603", "phone": "13900000004"})
check("新学生注册成功", r.status_code == 201 and r.get_json()["id"] == "2026004")

# ==================================================================
print("=" * 60); print("二、登录与 Token"); print("=" * 60)
r = client.post("/api/login", json={"id": "T001", "password": "wrong"})
check("错误密码登录失败 401", r.status_code == 401)

r = client.post("/api/login", json={"id": "T001", "password": "123456"})
teacher1 = r.get_json().get("token")
check("王老师登录拿到 token", r.status_code == 200 and bool(teacher1))

r = client.get("/api/me")
check("不带 token 访问被拒 401", r.status_code == 401)
r = client.get("/api/me", headers=H(teacher1))
check("带 token 能查到当前用户", r.status_code == 200 and r.get_json()["id"] == "T001")

teacher2 = login("T002")
student1 = login("2026001")
student2 = login("2026002")
student3 = login("2026003")
student4 = login("2026004")
check("其余演示账号均可登录", all([teacher2, student1, student2, student3, student4]))

# 数据库里确实是哈希，不是明文
chk = sqlite3.connect(DB_PATH)
row = chk.execute("SELECT password FROM users WHERE id='T001'").fetchone()
chk.close()
check("数据库中密码已哈希（非123456）", row[0] != "123456" and len(row[0]) > 20)

# ==================================================================
print("=" * 60); print("三、活动浏览：列表/搜索/分页/状态"); print("=" * 60)
r = client.get("/api/activities")
j = r.get_json()
check("默认列出全部2个活动", r.status_code == 200 and j["total"] == 2)
yx = next(it for it in j["items"] if it["name"] == "迎新晚会")
check("满员活动剩余名额=0、状态=报名中", yx["remaining_count"] == 0 and yx["status"] == "报名中")
check("活动带发布教师姓名", yx["publisher_name"] == "王老师")

r = client.get("/api/activities?keyword=大礼堂")
check("关键词搜地点命中1个", r.get_json()["total"] == 1)
r = client.get("/api/activities?keyword=不存在的关键词")
check("无结果关键词 total=0", r.get_json()["total"] == 0)
r = client.get("/api/activities?page=2&page_size=1")
j2 = r.get_json()
check("分页第2页返回1条且total仍为2", len(j2["items"]) == 1 and j2["total"] == 2)

# ==================================================================
print("=" * 60); print("四、发布活动：身份与字段校验"); print("=" * 60)
r = client.post("/api/activities", json=NEW_ACTIVITY, headers=H(student1))
check("学生发活动被拒 403", r.status_code == 403)
r = client.post("/api/activities", json=NEW_ACTIVITY)
check("未登录发活动被拒 401", r.status_code == 401)

r = client.post("/api/activities", json=NEW_ACTIVITY, headers=H(teacher2))
check("李老师发布活动成功 201", r.status_code == 201 and r.get_json()["id"] == 3)

bad = dict(NEW_ACTIVITY, start_time="2026-11-01 16:00", end_time="2026-11-01 14:00")
r = client.post("/api/activities", json=bad, headers=H(teacher2))
check("结束早于开始被拒绝", r.status_code == 400 and "结束时间" in r.get_json()["error"])
bad = dict(NEW_ACTIVITY, register_deadline="2026-11-02 00:00")
r = client.post("/api/activities", json=bad, headers=H(teacher2))
check("报名截止晚于开始被拒绝", r.status_code == 400 and "截止" in r.get_json()["error"])
bad = dict(NEW_ACTIVITY, start_time="2026/11/01 14点")
r = client.post("/api/activities", json=bad, headers=H(teacher2))
check("时间格式错误被拒绝", r.status_code == 400 and "格式" in r.get_json()["error"])
bad = dict(NEW_ACTIVITY, total_count=0)
r = client.post("/api/activities", json=bad, headers=H(teacher2))
check("名额为0被拒绝", r.status_code == 400)

# ==================================================================
print("=" * 60); print("五、教师只能管理自己的活动"); print("=" * 60)
r = client.put("/api/activities/1", json={"location": "被篡改"}, headers=H(teacher2))
check("李老师改王老师的活动被拒 403", r.status_code == 403)
r = client.post("/api/activities/1/cancel", headers=H(teacher2))
check("李老师取消王老师的活动被拒 403", r.status_code == 403)
r = client.delete("/api/activities/1", headers=H(teacher2))
check("李老师删除王老师的活动被拒 403", r.status_code == 403)
r = client.get("/api/activities/1/registrations", headers=H(teacher2))
check("李老师查看王老师活动名单被拒 403", r.status_code == 403)

r = client.put("/api/activities/1", json={"name": "迎新晚会（更新版）"}, headers=H(teacher1))
check("王老师修改自己的活动成功", r.status_code == 200 and "更新版" in r.get_json()["name"])
r = client.get("/api/activities/1/registrations", headers=H(teacher1))
j = r.get_json()
check("王老师看到自己活动的2人名单", r.status_code == 200 and j["registered_count"] == 2)

# ==================================================================
print("=" * 60); print("六、报名/重复/满员/退选释放名额"); print("=" * 60)
r = client.post("/api/activities/3/register", headers=H(student1))
check("张三报名李老师的活动（名额1）成功", r.status_code == 201)
r = client.post("/api/activities/3/register", headers=H(student1))
check("张三重复报名被拒绝", r.status_code == 400 and "重复" in r.get_json()["error"])
r = client.post("/api/activities/3/register", headers=H(student2))
check("满员后李四报名被拒绝", r.status_code == 400 and "满" in r.get_json()["error"])
r = client.post("/api/activities/3/register", headers=H(teacher1))
check("教师报名被拒 403", r.status_code == 403)

r = client.get("/api/my/registrations", headers=H(student1))
ids = [it["id"] for it in r.get_json()["items"]]
check("张三看到自己的2条报名（校运会+讲座）", set(ids) == {2, 3})

r = client.delete("/api/activities/3/register", headers=H(student1))
check("张三退选成功，名额释放", r.status_code == 200 and r.get_json()["activity"]["remaining_count"] == 1)
r = client.post("/api/activities/3/register", headers=H(student2))
check("李四补报成功", r.status_code == 201)
r = client.delete("/api/activities/3/register", headers=H(student1))
check("张三再次退选（本来就没报）被拒", r.status_code == 400)

# ==================================================================
print("=" * 60); print("七、报名截止与活动取消"); print("=" * 60)
past = dict(NEW_ACTIVITY, name="过期活动",
            start_time="2030-01-01 10:00", end_time="2030-01-01 12:00",
            register_deadline="2020-01-01 00:00")
r = client.post("/api/activities", json=past, headers=H(teacher1))
past_id = r.get_json()["id"]
check("创建报名期已过的活动成功", r.status_code == 201)
r = client.post("/api/activities/%d/register" % past_id, headers=H(student4))
check("过期活动报名被拒（已截止）", r.status_code == 400 and "截止" in r.get_json()["error"])

r = client.post("/api/activities/2/cancel", headers=H(teacher1))
check("王老师取消校运会成功，状态=已取消",
      r.status_code == 200 and r.get_json()["status"] == "已取消")
r = client.post("/api/activities/2/register", headers=H(student4))
check("已取消活动不能报名", r.status_code == 400 and "取消" in r.get_json()["error"])
r = client.post("/api/activities/2/cancel", headers=H(teacher1))
check("重复取消被拒绝", r.status_code == 400)
r = client.put("/api/activities/2", json={"location": "x"}, headers=H(teacher1))
check("已取消活动不能修改", r.status_code == 400)
r = client.get("/api/activities?status=已取消")
check("按状态筛选“已取消”能查到校运会",
      any(it["id"] == 2 for it in r.get_json()["items"]))

# ==================================================================
print("=" * 60); print("八、删除活动与报名记录级联清理"); print("=" * 60)
tmp_act = dict(NEW_ACTIVITY, name="临时活动", total_count=5)
r = client.post("/api/activities", json=tmp_act, headers=H(teacher1))
tmp_id = r.get_json()["id"]
client.post("/api/activities/%d/register" % tmp_id, headers=H(student3))
r = client.delete("/api/activities/%d" % tmp_id, headers=H(teacher1))
check("王老师删除自己的活动成功", r.status_code == 200)
r = client.get("/api/activities/%d" % tmp_id)
check("删除后活动详情 404", r.status_code == 404)
r = client.get("/api/my/registrations", headers=H(student3))
check("王五的报名记录随活动级联删除",
      all(it["id"] != tmp_id for it in r.get_json()["items"]))

# ==================================================================
print(); print("=" * 60)
print("断言通过数：%d" % passed)
print("测试全部通过")
