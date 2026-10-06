# -*- coding: utf-8 -*-
"""
V2.0 后端接口自动化测试（不启动真实服务器，用 Flask 测试客户端跑）。

用法：
    python test_v2.py

特点：使用独立的临时数据库，测完自动清理，绝不影响 campus.db 里的数据。
覆盖：V2.0 新增规则（活动审核、候补补位、账号停用恢复、管理员权限），
     并回归 V1.0 核心流程（注册登录、普通报名退选、发布/编辑/删除活动、导出）。
"""
import importlib.util
import io
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))

# 文件名 bakend2.0.py 含点号，不能直接 import，用 importlib 按路径加载
spec = importlib.util.spec_from_file_location("bakend2", os.path.join(HERE, "bakend2.0.py"))
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)

# 把数据库指向临时文件，必须在任何请求之前替换
TMP_DIR = tempfile.mkdtemp(prefix="campus_v2_test_")
b.DB_PATH = os.path.join(TMP_DIR, "test.db")

client = b.app.test_client()
passed = 0


def check(name, ok, detail=""):
    global passed
    if ok:
        passed += 1
        print("[PASS] " + name + (("  (" + detail + ")") if detail else ""))
    else:
        print("[FAIL] " + name + (("  (" + detail + ")") if detail else ""))
        raise AssertionError(name)


def reset():
    """重置为初始演示数据（用户1~6：T001/T002/三个学生/admin；活动1~3）"""
    client.get("/init_test")


def reg_status(activity_id, teacher_id, username):
    """查某活动名单里指定学生的报名状态"""
    r = client.get("/activity/%d/registrations?teacher_id=%d" % (activity_id, teacher_id))
    for item in r.get_json()["registrations"]:
        if item["username"] == username:
            return item
    return None


def people(activity_id):
    r = client.get("/activity/%d" % activity_id)
    return r.get_json()["current_people"]


def publish(teacher_id, title, max_people, need_review):
    r = client.post("/activities", json={
        "user_id": teacher_id, "title": title,
        "time": "2026-11-20 14:00", "location": "测试教室",
        "max_people": max_people, "description": "测试用活动",
        "need_review": need_review})
    return r.get_json()["id"]


# 演示数据约定的 id
T1, T2 = 1, 2          # 王老师、李老师
S1, S2, S3 = 3, 4, 5   # 张三、李四、王五
ADMIN = 6

reset()

# ---------- TEST-01 注册（V1.0 回归） ----------
r = client.post("/register", json={"name": "赵六", "username": "2026999",
                                   "password": "123456", "role": "student"})
check("TEST-01a 合法信息注册成功", r.status_code == 201)
r = client.post("/register", json={"name": "赵六", "username": "2026999",
                                   "password": "123456", "role": "student"})
check("TEST-01b 重复账号注册被拒", r.status_code == 400)

# ---------- TEST-02 登录（V1.0 回归） ----------
r = client.post("/login", json={"username": "2026001", "password": "123456"})
check("TEST-02a 正确账号密码登录", r.status_code == 200 and r.get_json()["user"]["role"] == "student")
r = client.post("/login", json={"username": "2026001", "password": "wrong"})
check("TEST-02b 错误密码登录被拒", r.status_code == 401)

# ---------- TEST-03 发布/编辑/删除活动（V1.0 回归） ----------
new_id = publish(T1, "临时测试讲座", 10, 0)
r = client.put("/activity/%d" % new_id, json={"user_id": T1, "description": "改后的介绍"})
check("TEST-03a 发布者编辑活动成功", r.status_code == 200 and r.get_json()["description"] == "改后的介绍")
r = client.put("/activity/%d" % new_id, json={"user_id": T2, "description": "别人改"})
check("TEST-03b 非发布者编辑被拒", r.status_code == 403)
r = client.delete("/activity/%d" % new_id, json={"user_id": T1})
check("TEST-03c 发布者删除活动成功", r.status_code == 200)

# ---------- TEST-04 需审核活动报名 → 待审核，不占名额 ----------
r = client.post("/activity/2/register", json={"user_id": S1})  # 活动2 编程马拉松 need_review=1
check("TEST-04a 报名需审核活动进入待审核", r.status_code == 200 and "等待教师审核" in r.get_json()["message"])
check("TEST-04b 待审核不占名额", people(2) == 0, "活动2 current_people=%d" % people(2))

# ---------- TEST-05 发布教师审核通过 → 占名额 ----------
pending = reg_status(2, T2, "2026001")
r = client.post("/registration/%d/review" % pending["reg_id"], json={"user_id": T2, "action": "approve"})
check("TEST-05a 发布教师审核通过", r.status_code == 200)
check("TEST-05b 审核通过后占名额", people(2) == 1, "活动2 current_people=%d" % people(2))

# ---------- TEST-06 非发布教师不能审核 ----------
client.post("/activity/2/register", json={"user_id": S2})  # 李四再报一个待审核
pending2 = reg_status(2, T2, "2026002")
r = client.post("/registration/%d/review" % pending2["reg_id"], json={"user_id": T1, "action": "approve"})
check("TEST-06 非发布教师审核被拒", r.status_code == 403)

# ---------- TEST-07 满员 → 候补，候补不占名额 ----------
act_a = publish(T1, "名额只有1个的普通活动", 1, 0)
client.post("/activity/%d/register" % act_a, json={"user_id": S2})  # 李四直接报名成功
r = client.post("/activity/%d/register" % act_a, json={"user_id": S3})  # 王五满员
check("TEST-07a 满员后报名进入候补", r.status_code == 200 and "候补" in r.get_json()["message"])
wait_item = reg_status(act_a, T1, "2026003")
check("TEST-07b 候补状态为候补中", wait_item and wait_item["status"] == "waitlisted")
check("TEST-07c 候补不占名额", people(act_a) == 1)

# ---------- TEST-08 退出后候补自动补位 ----------
client.post("/activity/%d/cancel" % act_a, json={"user_id": S2})  # 李四退出
promoted = reg_status(act_a, T1, "2026003")
check("TEST-08a 普通活动候补直接补位为已报名", promoted and promoted["status"] == "registered")
check("TEST-08b 补位后名额正确", people(act_a) == 1)

# 需审核活动：候补补位后转为待审核，仍须教师确认
act_b = publish(T1, "名额1个且需审核的活动", 1, 1)
client.post("/activity/%d/register" % act_b, json={"user_id": S2})  # 李四待审核
p = reg_status(act_b, T1, "2026002")
client.post("/registration/%d/review" % p["reg_id"], json={"user_id": T1, "action": "approve"})  # 1/1
client.post("/activity/%d/register" % act_b, json={"user_id": S3})  # 王五满员 → 候补
client.post("/activity/%d/cancel" % act_b, json={"user_id": S2})   # 李四退出
w = reg_status(act_b, T1, "2026003")
check("TEST-08c 需审核活动候补补位后转待审核", w and w["status"] == "pending",
      "王五当前状态=%s" % (w and w["status"]))

# ---------- TEST-09 重复报名被拒（含退选后可重新报名） ----------
r = client.post("/activity/2/register", json={"user_id": S1})  # 张三在活动2已是审核通过
check("TEST-09a 重复报名被拒", r.status_code == 400)
client.post("/activity/1/register", json={"user_id": S1})
client.post("/activity/1/cancel", json={"user_id": S1})
r = client.post("/activity/1/register", json={"user_id": S1})  # 退选后重新报名
check("TEST-09b 退选后可重新报名", r.status_code == 200)

# ---------- TEST-10 我的报名：状态中文可见 ----------
r = client.get("/my/registrations?user_id=%d" % S1)
mine = r.get_json()["registrations"]
check("TEST-10a 能查到自己的报名记录", len(mine) >= 2)
check("TEST-10b 报名状态有中文文案", all("status_text" in m for m in mine))

# ---------- TEST-11 管理员停用/恢复账号 ----------
r = client.post("/admin/user/%d/disable" % S1, json={"admin_id": ADMIN})
check("TEST-11a 管理员停用账号", r.status_code == 200)
r = client.post("/login", json={"username": "2026001", "password": "123456"})
check("TEST-11b 被停用账号登录被拒", r.status_code == 403 and "停用" in r.get_json()["message"])
r = client.post("/admin/user/%d/enable" % S1, json={"admin_id": ADMIN})
check("TEST-11c 管理员恢复账号", r.status_code == 200)
r = client.post("/login", json={"username": "2026001", "password": "123456"})
check("TEST-11d 恢复后可正常登录", r.status_code == 200)
r = client.post("/admin/user/%d/disable" % ADMIN, json={"admin_id": ADMIN})
check("TEST-11e 管理员账号不能被停用", r.status_code == 400)

# ---------- TEST-12 非管理员不能访问管理接口 ----------
r = client.get("/admin/users?admin_id=%d" % S1)
check("TEST-12a 学生访问用户管理被拒", r.status_code == 403)
r = client.post("/admin/user/%d/disable" % S2, json={"admin_id": T1})
check("TEST-12b 教师停用账号被拒", r.status_code == 403)
r = client.get("/admin/activities?admin_id=%d" % ADMIN)
check("TEST-12c 管理员可查看全部活动（监督）",
      r.status_code == 200 and len(r.get_json()["activities"]) >= 3)

# ---------- 回归：导出 Excel 状态列为中文 ----------
r = client.get("/admin/export")
check("导出接口返回 xlsx", r.status_code == 200 and r.data[:2] == b"PK")
from openpyxl import load_workbook
wb = load_workbook(io.BytesIO(r.data))
ws = wb.active
statuses = [row[4] for row in ws.iter_rows(min_row=2, values_only=True)]
check("导出状态列为中文", statuses and all(s in ("已报名", "待审核", "审核通过", "审核拒绝", "候补中", "已退选")
                                            for s in statuses), str(statuses))

print("\n========== 共 %d 项检查全部通过 ==========" % passed)
