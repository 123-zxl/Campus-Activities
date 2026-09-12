# -*- coding: utf-8 -*-
"""
校园活动管理系统 V1.0 —— Flask 后端

启动方式：
    1. 先执行过一次初始化：  python init_db.py
    2. 启动后端服务：        python app.py
    3. 浏览器打开：          http://127.0.0.1:5000/api/health

接口风格：RESTful JSON。除登录/注册/浏览活动外，其余接口都要带请求头：
    Authorization: Bearer <登录时拿到的token>
"""
import re
import secrets
import sqlite3
from datetime import datetime
from functools import wraps

from flask import Flask, g, jsonify, request
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_db

app = Flask(__name__)
app.json.ensure_ascii = False  # 让中文直接显示，不转成 \uXXXX
CORS(app)  # 允许前端页面跨域访问：前端网页和后端端口不同，浏览器默认会拦截，不加这个前端联调必报错

# ------------------------------------------------------------------
# 登录令牌（Token）存储
# 说明：V1.0 用内存字典保存，简单直观；服务重启后所有人需要重新登录。
# 以后想做成"重启不掉线"，可以把它存进数据库的一张 token 表。
# ------------------------------------------------------------------
TOKENS = {}  # {token字符串: 用户账号}


def get_conn():
    """
    获取本次请求专用的数据库连接：同一个请求内多次调用拿到的是同一条连接，
    请求结束时由 teardown_appcontext 统一关闭，避免连接泄漏导致 'database is locked'。
    """
    if "db" not in g:
        g.db = get_db()
    return g.db


@app.teardown_appcontext
def close_db(exc):
    """无论请求成功还是报错，都把数据库连接关掉"""
    db = g.pop("db", None)
    if db is not None:
        db.close()


# ------------------------------------------------------------------
# 通用小工具
# ------------------------------------------------------------------
DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")  # 形如 2026-09-20 14:00


def fail(message, code=400):
    """返回一个错误 JSON，例如 {"error":"密码不正确"}"""
    return jsonify({"error": message}), code


def parse_dt(text):
    """把 'YYYY-MM-DD HH:MM' 转成 datetime；格式不对返回 None"""
    if not isinstance(text, str) or not DATETIME_RE.match(text):
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def public_user(row):
    """用户对象的"对外版本"：绝不把密码哈希返回给前端"""
    return {
        "id": row["id"],
        "role": row["role"],
        "name": row["name"],
        "gender": row["gender"],
        "major_class": row["major_class"],
        "phone": row["phone"],
        "created_at": row["created_at"],
    }


def activity_status(row, now=None):
    """
    计算活动状态（不存数据库，按时间实时算）：
      已取消：教师手动取消
      已结束：当前时间 >= 结束时间
      已截止：当前时间 >= 报名截止时间（但活动还没结束）
      报名中：其余情况
    """
    if row["cancelled"] == 1:
        return "已取消"
    now = now or datetime.now()
    end_time = parse_dt(row["end_time"])
    deadline = parse_dt(row["register_deadline"])
    if end_time and now >= end_time:
        return "已结束"
    if deadline and now >= deadline:
        return "已截止"
    return "报名中"


def public_activity(row, now=None):
    """活动对象的对外版本：补充发布教师姓名、已报人数、剩余名额、实时状态"""
    registered = row["registered_count"] if "registered_count" in row.keys() else 0
    return {
        "id": row["id"],
        "name": row["name"],
        "start_time": row["start_time"],
        "end_time": row["end_time"],
        "register_deadline": row["register_deadline"],
        "location": row["location"],
        "total_count": row["total_count"],
        "description": row["description"],
        "cancelled": bool(row["cancelled"]),
        "publisher_id": row["publisher_id"],
        "publisher_name": row["publisher_name"] if "publisher_name" in row.keys() else None,
        "registered_count": registered,
        "remaining_count": row["total_count"] - registered,
        "status": activity_status(row, now),
        "created_at": row["created_at"],
    }


ACTIVITY_SQL = """
    SELECT a.*, u.name AS publisher_name,
           (SELECT COUNT(*) FROM registrations r WHERE r.activity_id = a.id) AS registered_count
    FROM activities a JOIN users u ON u.id = a.publisher_id
"""


def find_activity(activity_id):
    """按编号查活动（带教师姓名和报名人数）；不存在返回 None"""
    return get_conn().execute(ACTIVITY_SQL + " WHERE a.id = ?", (activity_id,)).fetchone()


def login_required(fn):
    """装饰器：要求请求带有效 Token，并把当前用户放到 g.user"""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        token = auth[7:].strip() if auth.startswith("Bearer ") else ""
        user_id = TOKENS.get(token)
        if not user_id:
            return fail("未登录或登录已失效，请重新登录", 401)
        g.user = get_conn().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if g.user is None:
            return fail("用户不存在", 401)
        return fn(*args, **kwargs)
    return wrapper


def role_required(role):
    """装饰器：在登录基础上进一步要求身份（student / teacher）"""
    def decorator(fn):
        @wraps(fn)
        @login_required
        def wrapper(*args, **kwargs):
            if g.user["role"] != role:
                who = "教师" if role == "teacher" else "学生"
                return fail("该操作仅%s可用" % who, 403)
            return fn(*args, **kwargs)
        return wrapper
    return decorator


# ------------------------------------------------------------------
# 健康检查（最简单的测试接口）
# ------------------------------------------------------------------
@app.get("/api/health")
def health():
    return jsonify({"ok": True, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})


# ==================================================================
# 一、注册 / 登录
# ==================================================================
@app.post("/api/register")
def register():
    data = request.get_json(silent=True) or {}
    uid = (data.get("id") or "").strip()
    password = data.get("password") or ""
    name = (data.get("name") or "").strip()
    role = data.get("role")
    gender = data.get("gender")
    major_class = (data.get("major_class") or None)
    phone = (data.get("phone") or None)

    # 通用必填校验
    if not uid or not password or not name:
        return fail("账号、密码、姓名均不能为空")
    if role not in ("student", "teacher"):
        return fail("role 只能是 student（学生）或 teacher（教师）")
    if len(password) < 6:
        return fail("密码长度至少 6 位")

    # 按身份做专属校验
    if role == "student":
        if gender not in ("男", "女"):
            return fail("学生必须填写性别（男/女）")
        if not major_class:
            return fail("学生必须填写专业班级")
    else:  # teacher
        if gender == "":
            gender = None  # 教师性别允许留空

    try:
        con = get_conn()
        cur = con.execute(
            "INSERT INTO users(id,role,password,name,gender,major_class,phone) "
            "VALUES(?,?,?,?,?,?,?)",
            (uid, role, generate_password_hash(password), name, gender, major_class, phone),
        )
        con.commit()
        row = con.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        return jsonify(public_user(row)), 201
    except sqlite3.IntegrityError as e:
        msg = str(e)
        if "UNIQUE" in msg:
            return fail("该账号已存在，请直接登录或更换账号")
        return fail("注册失败：" + msg)


@app.post("/api/login")
def login():
    data = request.get_json(silent=True) or {}
    uid = (data.get("id") or "").strip()
    password = data.get("password") or ""
    if not uid or not password:
        return fail("请输入账号和密码")

    row = get_conn().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if row is None or not check_password_hash(row["password"], password):
        return fail("账号或密码不正确", 401)

    token = secrets.token_hex(20)   # 生成 40 位随机令牌
    TOKENS[token] = uid
    return jsonify({"token": token, "user": public_user(row)})


@app.get("/api/me")
@login_required
def me():
    """查询当前登录者信息"""
    return jsonify(public_user(g.user))


# ==================================================================
# 二、活动：浏览、发布、修改、取消、删除
# ==================================================================
@app.get("/api/activities")
def list_activities():
    """
    活动列表（无需登录即可浏览），支持查询参数：
      keyword    关键词（匹配活动名称/地点/介绍）
      status     状态筛选：报名中 / 已截止 / 已结束 / 已取消
      page       页码，默认 1
      page_size  每页条数，默认 10，最大 50
    """
    keyword = (request.args.get("keyword") or "").strip()
    status = (request.args.get("status") or "").strip()
    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = min(50, max(1, int(request.args.get("page_size", 10))))
    except ValueError:
        return fail("page 和 page_size 必须是整数")

    sql = ACTIVITY_SQL + " WHERE 1=1"
    params = []
    if keyword:
        sql += " AND (a.name LIKE ? OR a.location LIKE ? OR a.description LIKE ?)"
        like = "%" + keyword + "%"
        params += [like, like, like]
    sql += " ORDER BY a.start_time ASC, a.id ASC"

    rows = get_conn().execute(sql, params).fetchall()

    # 状态是实时算出来的，所以状态筛选和分页在内存里做（课程项目数据量小，完全够用）
    now = datetime.now()
    items = [public_activity(r, now) for r in rows]
    if status:
        items = [it for it in items if it["status"] == status]

    total = len(items)
    start = (page - 1) * page_size
    return jsonify({"items": items[start:start + page_size], "total": total,
                    "page": page, "page_size": page_size})


@app.get("/api/activities/<int:activity_id>")
def activity_detail(activity_id):
    row = find_activity(activity_id)
    if row is None:
        return fail("活动不存在", 404)
    return jsonify(public_activity(row))


def read_activity_fields(data, partial=False):
    """
    从请求里读取并校验活动字段。
    partial=False（发布）：所有必填字段都必须给。
    partial=True（修改）：只校验"给了的"字段。
    返回 (fields_dict, error_message)；error_message 非 None 表示校验失败。
    """
    fields = {}
    texts = {
        "name": "活动名称",
        "start_time": "开始时间",
        "end_time": "结束时间",
        "register_deadline": "报名截止时间",
        "location": "活动地点",
    }
    for key, label in texts.items():
        if key in data:
            value = data.get(key)
            if value is None or str(value).strip() == "":
                return None, label + "不能为空"
            fields[key] = str(value).strip()
    if "description" in data:
        fields["description"] = data.get("description")
    if "total_count" in data:
        try:
            total = int(data.get("total_count"))
        except (TypeError, ValueError):
            return None, "总人数必须是整数"
        if total <= 0:
            return None, "总人数必须大于 0"
        fields["total_count"] = total
    if not partial and not all(k in fields for k in
                               ("name", "start_time", "end_time", "register_deadline",
                                "location", "total_count")):
        return None, "name/start_time/end_time/register_deadline/location/total_count 均必填"

    # 校验时间格式与先后关系（需要凑齐三个时间：新给的优先，没给的稍后由调用方补旧值）
    return fields, None


def validate_times(start_time, end_time, deadline):
    """三个时间的格式和逻辑关系；返回错误信息，没问题返回 None"""
    s, e, d = parse_dt(start_time), parse_dt(end_time), parse_dt(deadline)
    if s is None or e is None or d is None:
        return "时间格式应为 'YYYY-MM-DD HH:MM'，例如 2026-09-20 14:00"
    if e <= s:
        return "结束时间必须晚于开始时间"
    if d > s:
        return "报名截止时间不能晚于活动开始时间"
    return None


@app.post("/api/activities")
@role_required("teacher")
def create_activity():
    data = request.get_json(silent=True) or {}
    fields, err = read_activity_fields(data, partial=False)
    if err:
        return fail(err)
    err = validate_times(fields["start_time"], fields["end_time"], fields["register_deadline"])
    if err:
        return fail(err)

    try:
        con = get_conn()
        cur = con.execute(
            "INSERT INTO activities(name,start_time,end_time,register_deadline,"
            "location,total_count,description,publisher_id) VALUES(?,?,?,?,?,?,?,?)",
            (fields["name"], fields["start_time"], fields["end_time"], fields["register_deadline"],
             fields["location"], fields["total_count"], fields.get("description"), g.user["id"]),
        )
        con.commit()
        return jsonify(public_activity(find_activity(cur.lastrowid))), 201
    except sqlite3.IntegrityError as e:
        # 数据库触发器兜底（正常情况下当前登录者一定是教师，走不到这里）
        return fail("发布失败：" + str(e))


@app.put("/api/activities/<int:activity_id>")
@role_required("teacher")
def update_activity(activity_id):
    row = find_activity(activity_id)
    if row is None:
        return fail("活动不存在", 404)
    if row["publisher_id"] != g.user["id"]:          # 教师只能改自己的活动
        return fail("只能修改自己发布的活动", 403)
    if row["cancelled"] == 1:
        return fail("活动已取消，不能修改")

    data = request.get_json(silent=True) or {}
    fields, err = read_activity_fields(data, partial=True)
    if err:
        return fail(err)

    # 合并旧值后统一校验时间关系
    start_time = fields.get("start_time", row["start_time"])
    end_time = fields.get("end_time", row["end_time"])
    deadline = fields.get("register_deadline", row["register_deadline"])
    err = validate_times(start_time, end_time, deadline)
    if err:
        return fail(err)

    # 新名额不能小于当前已报名人数，否则已报名的学生就"装不下"了
    if "total_count" in fields and fields["total_count"] < row["registered_count"]:
        return fail("总人数不能小于已报名人数（当前已报名 %d 人）" % row["registered_count"])

    if not fields:
        return fail("没有需要更新的字段")
    columns = ", ".join(k + " = ?" for k in fields)
    try:
        con = get_conn()
        con.execute("UPDATE activities SET " + columns + " WHERE id = ?",
                    list(fields.values()) + [activity_id])
        con.commit()
    except sqlite3.IntegrityError as e:
        return fail("修改失败：" + str(e))
    return jsonify(public_activity(find_activity(activity_id)))


@app.post("/api/activities/<int:activity_id>/cancel")
@role_required("teacher")
def cancel_activity(activity_id):
    """取消活动（保留活动和报名记录，学生端显示"已取消"，不能再报名）"""
    row = find_activity(activity_id)
    if row is None:
        return fail("活动不存在", 404)
    if row["publisher_id"] != g.user["id"]:
        return fail("只能取消自己发布的活动", 403)
    if row["cancelled"] == 1:
        return fail("活动已经是取消状态")
    con = get_conn()
    con.execute("UPDATE activities SET cancelled = 1 WHERE id = ?", (activity_id,))
    con.commit()
    return jsonify(public_activity(find_activity(activity_id)))


@app.delete("/api/activities/<int:activity_id>")
@role_required("teacher")
def delete_activity(activity_id):
    """彻底删除活动（其报名记录由数据库级联自动删除）"""
    row = find_activity(activity_id)
    if row is None:
        return fail("活动不存在", 404)
    if row["publisher_id"] != g.user["id"]:
        return fail("只能删除自己发布的活动", 403)
    con = get_conn()
    con.execute("DELETE FROM activities WHERE id = ?", (activity_id,))
    con.commit()
    return jsonify({"message": "活动已删除", "id": activity_id})


@app.get("/api/my/activities")
@role_required("teacher")
def my_activities():
    """教师查看自己发布的全部活动"""
    rows = get_conn().execute(
        ACTIVITY_SQL + " WHERE a.publisher_id = ? ORDER BY a.created_at DESC",
        (g.user["id"],)).fetchall()
    now = datetime.now()
    return jsonify({"items": [public_activity(r, now) for r in rows]})


@app.get("/api/activities/<int:activity_id>/registrations")
@role_required("teacher")
def activity_roster(activity_id):
    """教师查看自己活动的报名名单"""
    row = find_activity(activity_id)
    if row is None:
        return fail("活动不存在", 404)
    if row["publisher_id"] != g.user["id"]:
        return fail("只能查看自己活动的报名名单", 403)
    rows = get_conn().execute(
        """SELECT u.id, u.name, u.gender, u.major_class, u.phone, r.registered_at
           FROM registrations r JOIN users u ON u.id = r.student_id
           WHERE r.activity_id = ? ORDER BY r.registered_at""",
        (activity_id,)).fetchall()
    return jsonify({
        "activity_id": activity_id,
        "activity_name": row["name"],
        "total_count": row["total_count"],
        "registered_count": len(rows),
        "students": [dict(s) for s in rows],
    })


# ==================================================================
# 三、报名 / 退选 / 我的报名
# ==================================================================
@app.post("/api/activities/<int:activity_id>/register")
@role_required("student")
def register_activity(activity_id):
    row = find_activity(activity_id)
    if row is None:
        return fail("活动不存在", 404)

    # 应用层先给出对学生友好的提示（数据库触发器是最后一道防线）
    if row["cancelled"] == 1:
        return fail("活动已取消，不能报名")
    already = get_conn().execute(
        "SELECT 1 FROM registrations WHERE student_id = ? AND activity_id = ?",
        (g.user["id"], activity_id)).fetchone()
    if already:  # 先查重复：已报名的学生应得到"重复报名"而不是"已满"的提示
        return fail("你已经报名过该活动，不能重复报名")
    now = datetime.now()
    deadline = parse_dt(row["register_deadline"])
    if deadline and now >= deadline:
        return fail("报名已截止（截止时间：%s）" % row["register_deadline"])
    if row["registered_count"] >= row["total_count"]:
        return fail("报名人数已满")

    try:
        con = get_conn()
        con.execute("INSERT INTO registrations(student_id, activity_id) VALUES(?, ?)",
                    (g.user["id"], activity_id))
        con.commit()
    except sqlite3.IntegrityError as e:
        msg = str(e)
        if "UNIQUE" in msg:
            return fail("你已经报名过该活动，不能重复报名")
        if "已满" in msg:
            return fail("报名人数已满")
        if "FOREIGN KEY" in msg:
            return fail("活动不存在", 404)
        return fail("报名失败：" + msg)

    return jsonify({"message": "报名成功", "activity": public_activity(find_activity(activity_id))}), 201


@app.delete("/api/activities/<int:activity_id>/register")
@role_required("student")
def cancel_registration(activity_id):
    """学生退选：只能删自己的报名记录"""
    row = find_activity(activity_id)
    if row is None:
        return fail("活动不存在", 404)
    con = get_conn()
    cur = con.execute(
        "DELETE FROM registrations WHERE student_id = ? AND activity_id = ?",
        (g.user["id"], activity_id))
    con.commit()
    if cur.rowcount == 0:
        return fail("你没有报名该活动，无需退选")
    return jsonify({"message": "退选成功", "activity": public_activity(find_activity(activity_id))})


@app.get("/api/my/registrations")
@role_required("student")
def my_registrations():
    """学生查看自己的报名记录"""
    rows = get_conn().execute(
        ACTIVITY_SQL + " JOIN registrations r ON r.activity_id = a.id "
        "WHERE r.student_id = ? ORDER BY a.start_time",
        (g.user["id"],)).fetchall()
    now = datetime.now()
    items = []
    for r in rows:
        item = public_activity(r, now)
        item["registered_at"] = get_conn().execute(
            "SELECT registered_at FROM registrations WHERE student_id=? AND activity_id=?",
            (g.user["id"], r["id"])).fetchone()["registered_at"]
        items.append(item)
    return jsonify({"items": items})


if __name__ == "__main__":
    # debug=True：代码改动后自动重启，出错时浏览器里显示详细信息（仅开发阶段使用）
    app.run(host="127.0.0.1", port=5000, debug=True)
