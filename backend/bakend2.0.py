# -*- coding: utf-8 -*-
# ============================================================
# 校园活动管理系统 V2.0
# 在 V1.0基础上迭代
#
# V2.0 变化（依据需求访谈）：
#   1. 新增管理员角色：用户账号查看/停用/恢复、全部活动监督查看
#   2. 活动增加"是否需要审核"：需审核的活动报名后进入待审核，教师逐个确认
#   3. 满员活动允许候补：有人退出时按报名顺序补位
#   4. 学生可查看自己的报名记录和状态
# ============================================================

import os
import io
import sqlite3
from datetime import datetime

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash
from openpyxl import Workbook

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "campus.db")

app = Flask(__name__)
CORS(app)

OCCUPY_STATUS = ('registered', 'approved')

STATUS_MAP = {
    'registered': '已报名',
    'pending': '待审核',
    'approved': '审核通过',
    'rejected': '审核拒绝',
    'waitlisted': '候补中',
    'cancelled': '已退选',
}


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


SCHEMA_SQL = """
DROP TABLE IF EXISTS registrations;
DROP TABLE IF EXISTS activities;
DROP TABLE IF EXISTS users;

CREATE TABLE users (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT,
    username TEXT UNIQUE,
    password TEXT,
    role     TEXT,
    status   TEXT DEFAULT 'active'
);

CREATE TABLE activities (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    title          TEXT,
    time           TEXT,
    location       TEXT,
    max_people     INTEGER,
    current_people INTEGER DEFAULT 0,
    description    TEXT,
    publisher_id   INTEGER,
    cancelled      INTEGER DEFAULT 0,
    need_review    INTEGER DEFAULT 0
);

CREATE TABLE registrations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER,
    activity_id INTEGER,
    status      TEXT DEFAULT 'registered'
);
"""


def seed_demo_data(conn):
    pw = generate_password_hash("123456")
    conn.executemany(
        "INSERT INTO users(name,username,password,role,status) VALUES(?,?,?,?,?)",
        [("王老师", "T001", pw, "teacher", "active"),
         ("李老师", "T002", pw, "teacher", "active"),
         ("张三", "2026001", pw, "student", "active"),
         ("李四", "2026002", pw, "student", "active"),
         ("王五", "2026003", pw, "student", "active"),
         ("系统管理员", "admin", pw, "admin", "active")])
    conn.executemany(
        "INSERT INTO activities(title,time,location,max_people,description,publisher_id,need_review) VALUES(?,?,?,?,?,?,?)",
        [("校运会开幕式", "2026-10-25 08:00", "学校体育馆", 200, "一年一度的校运会开幕仪式", 1, 0),
         ("编程马拉松", "2026-10-28 09:00", "计算机学院301", 30, "48小时组队开发，需要有编程基础，报名后由教师审核", 2, 1),
         ("校园摄影展", "2026-11-01 09:00", "图书馆一楼大厅", 50, "学生摄影作品展览", 1, 0)])
    conn.commit()


def ensure_tables():
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        name     TEXT,
        username TEXT UNIQUE,
        password TEXT,
        role     TEXT,
        status   TEXT DEFAULT 'active'
    );
    CREATE TABLE IF NOT EXISTS activities (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        title          TEXT,
        time           TEXT,
        location       TEXT,
        max_people     INTEGER,
        current_people INTEGER DEFAULT 0,
        description    TEXT,
        publisher_id   INTEGER,
        cancelled      INTEGER DEFAULT 0,
        need_review    INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS registrations (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER,
        activity_id INTEGER,
        status      TEXT DEFAULT 'registered'
    );
    """)
    conn.commit()
    count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()['c']
    if count == 0:
        seed_demo_data(conn)
        print("检测到空数据库，已自动灌入演示数据")
    conn.close()


@app.route('/init_test', methods=['GET'])
def init_test():
    conn = get_db()
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    seed_demo_data(conn)
    conn.close()
    return jsonify({"message": "测试数据初始化完成"})


def get_user(conn, user_id):
    if not user_id:
        return None
    return conn.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()


def require_admin(conn, admin_id):
    u = get_user(conn, admin_id)
    if u is None or u['role'] != 'admin':
        return jsonify({"message": "只有系统管理员能执行该操作"}), 403
    return None


def promote_waitlist(conn, activity_id):
    act = conn.execute('SELECT * FROM activities WHERE id=?', (activity_id,)).fetchone()
    if act is None or act['current_people'] >= act['max_people']:
        return
    nxt = conn.execute(
        "SELECT * FROM registrations WHERE activity_id=? AND status='waitlisted' ORDER BY id",
        (activity_id,)).fetchone()
    if nxt is None:
        return
    if act['need_review'] == 1:
        conn.execute("UPDATE registrations SET status='pending' WHERE id=?", (nxt['id'],))
    else:
        conn.execute("UPDATE registrations SET status='registered' WHERE id=?", (nxt['id'],))
        conn.execute('UPDATE activities SET current_people=current_people+1 WHERE id=?', (activity_id,))

        # ============================================================
# =============== A 负责的接口 ===============================
# ============================================================

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"ok": True, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})


@app.route('/register', methods=['POST'])
def register():
    data = request.json or {}
    name = (data.get('name') or '').strip()
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    role = data.get('role')
    if not name or not username or not password:
        return jsonify({"message": "姓名、账号、密码均不能为空"}), 400
    if role not in ('student', 'teacher'):
        return jsonify({"message": "role 只能是 student 或 teacher"}), 400
    if len(password) < 6:
        return jsonify({"message": "密码长度至少 6 位"}), 400
    conn = get_db()
    try:
        conn.execute('INSERT INTO users(name,username,password,role,status) VALUES(?,?,?,?,?)',
                     (name, username, generate_password_hash(password), role, 'active'))
        conn.commit()
        row = conn.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        conn.close()
        return jsonify(dict(row)), 201
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"message": "该账号已存在"}), 400


@app.route('/login', methods=['POST'])
def login():
    data = request.json or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    if not username or not password:
        return jsonify({"message": "请输入账号和密码"}), 400
    conn = get_db()
    row = conn.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
    conn.close()
    if row is None or not check_password_hash(row['password'], password):
        return jsonify({"message": "账号或密码不正确"}), 401
    if row['status'] == 'disabled':
        return jsonify({"message": "该账号已被停用，请联系系统管理员"}), 403
    return jsonify({"user_id": row['id'], "user": dict(row)})


@app.route('/activities', methods=['GET'])
def list_activities():
    conn = get_db()
    rows = conn.execute(
        'SELECT a.*, u.name AS publisher_name FROM activities a '
        'LEFT JOIN users u ON u.id = a.publisher_id ORDER BY a.id DESC').fetchall()
    result = []
    now = datetime.now()
    for r in rows:
        d = dict(r)
        try:
            activity_time = datetime.strptime(d['time'], "%Y-%m-%d %H:%M")
        except Exception:
            activity_time = None
        if d['cancelled'] == 1:
            d['status'] = '已取消'
        elif activity_time and now >= activity_time:
            d['status'] = '已结束'
        elif d['current_people'] >= d['max_people']:
            d['status'] = '已满员'
        else:
            d['status'] = '报名中'
        result.append(d)
    conn.close()
    return jsonify({"activities": result})


@app.route('/activity/<int:activity_id>', methods=['GET'])
def activity_detail(activity_id):
    conn = get_db()
    row = conn.execute(
        'SELECT a.*, u.name AS publisher_name FROM activities a '
        'LEFT JOIN users u ON u.id = a.publisher_id WHERE a.id=?', (activity_id,)).fetchone()
    conn.close()
    if row is None:
        return jsonify({"message": "活动不存在"}), 404
    return jsonify(dict(row))


@app.route('/activities', methods=['POST'])
def create_activity():
    data = request.json or {}
    user_id = data.get('user_id')
    title = (data.get('title') or '').strip()
    time = (data.get('time') or '').strip()
    location = (data.get('location') or '').strip()
    try:
        max_people = int(data.get('max_people', 0))
    except (TypeError, ValueError):
        return jsonify({"message": "max_people 必须是整数"}), 400
    if not user_id:
        return jsonify({"message": "请传 user_id"}), 400
    if not title or not time or not location or max_people <= 0:
        return jsonify({"message": "title/time/location/max_people 均必填且 max_people > 0"}), 400

    conn = get_db()
    user = get_user(conn, user_id)
    if user is None or user['role'] != 'teacher':
        conn.close()
        return jsonify({"message": "只有教师能发布活动"}), 403

    need_review = 1 if data.get('need_review') else 0
    cur = conn.execute(
        'INSERT INTO activities(title,time,location,max_people,description,publisher_id,need_review) VALUES(?,?,?,?,?,?,?)',
        (title, time, location, max_people, data.get('description'), user_id, need_review))
    conn.commit()
    new_row = conn.execute('SELECT * FROM activities WHERE id=?', (cur.lastrowid,)).fetchone()
    conn.close()
    return jsonify(dict(new_row)), 201


@app.route('/activity/<int:activity_id>', methods=['PUT'])
def update_activity(activity_id):
    data = request.json or {}
    user_id = data.get('user_id')
    conn = get_db()
    row = conn.execute('SELECT * FROM activities WHERE id=?', (activity_id,)).fetchone()
    if row is None:
        conn.close()
        return jsonify({"message": "活动不存在"}), 404
    if row['publisher_id'] != user_id:
        conn.close()
        return jsonify({"message": "只能修改自己发布的活动"}), 403
    if row['cancelled'] == 1:
        conn.close()
        return jsonify({"message": "活动已取消，不能修改"}), 403

    fields = {}
    if 'title' in data: fields['title'] = data['title']
    if 'time' in data: fields['time'] = data['time']
    if 'location' in data: fields['location'] = data['location']
    if 'description' in data: fields['description'] = data['description']
    if 'need_review' in data: fields['need_review'] = 1 if data['need_review'] else 0
    if 'max_people' in data:
        try:
            fields['max_people'] = int(data['max_people'])
        except (TypeError, ValueError):
            conn.close()
            return jsonify({"message": "max_people 必须是整数"}), 400
        if fields['max_people'] <= 0:
            conn.close()
            return jsonify({"message": "max_people 必须大于 0"}), 400
        if fields['max_people'] < row['current_people']:
            conn.close()
            return jsonify({"message": "max_people 不能小于已报人数"}), 400

    if not fields:
        conn.close()
        return jsonify({"message": "没有需要更新的字段"}), 400

    conn.execute('UPDATE activities SET ' + ', '.join(k + '=?' for k in fields) + ' WHERE id=?',
                 list(fields.values()) + [activity_id])
    conn.commit()
    updated = conn.execute('SELECT * FROM activities WHERE id=?', (activity_id,)).fetchone()
    conn.close()
    return jsonify(dict(updated))


@app.route('/activity/<int:activity_id>/cancel_activity', methods=['POST'])
def cancel_activity_by_teacher(activity_id):
    data = request.json or {}
    user_id = data.get('user_id')
    conn = get_db()
    row = conn.execute('SELECT * FROM activities WHERE id=?', (activity_id,)).fetchone()
    if row is None:
        conn.close()
        return jsonify({"message": "活动不存在"}), 404
    if row['publisher_id'] != user_id:
        conn.close()
        return jsonify({"message": "只能取消自己发布的活动"}), 403
    if row['cancelled'] == 1:
        conn.close()
        return jsonify({"message": "活动已经是取消状态"}), 400
    conn.execute('UPDATE activities SET cancelled=1 WHERE id=?', (activity_id,))
    conn.commit()
    updated = conn.execute('SELECT * FROM activities WHERE id=?', (activity_id,)).fetchone()
    conn.close()
    return jsonify(dict(updated))


@app.route('/activity/<int:activity_id>', methods=['DELETE'])
def delete_activity(activity_id):
    data = request.json or {}
    user_id = data.get('user_id')
    conn = get_db()
    row = conn.execute('SELECT * FROM activities WHERE id=?', (activity_id,)).fetchone()
    if row is None:
        conn.close()
        return jsonify({"message": "活动不存在"}), 404
    if row['publisher_id'] != user_id:
        conn.close()
        return jsonify({"message": "只能删除自己发布的活动"}), 403
    conn.execute('DELETE FROM activities WHERE id=?', (activity_id,))
    conn.execute('DELETE FROM registrations WHERE activity_id=?', (activity_id,))
    conn.commit()
    conn.close()
    return jsonify({"message": "活动已删除", "id": activity_id})


@app.route('/my_activities', methods=['GET'])
def my_activities():
    user_id = request.args.get('user_id', type=int)
    if not user_id:
        return jsonify({"message": "请传 user_id 参数"}), 400
    conn = get_db()
    rows = conn.execute('SELECT * FROM activities WHERE publisher_id=? ORDER BY id DESC', (user_id,)).fetchall()
    conn.close()
    return jsonify({"activities": [dict(r) for r in rows]})


@app.route('/activity/<int:activity_id>/registrations', methods=['GET'])
def activity_roster(activity_id):
    teacher_id = request.args.get('teacher_id', type=int)
    if not teacher_id:
        return jsonify({"message": "请传 teacher_id 参数"}), 400
    conn = get_db()
    act = conn.execute('SELECT * FROM activities WHERE id=?', (activity_id,)).fetchone()
    if act is None:
        conn.close()
        return jsonify({"message": "活动不存在"}), 404
    if act['publisher_id'] != teacher_id:
        conn.close()
        return jsonify({"message": "只能查看自己活动的报名名单"}), 403
    regs = conn.execute(
        'SELECT r.id AS reg_id, u.name, u.username, r.status '
        'FROM registrations r JOIN users u ON u.id=r.user_id '
        'WHERE r.activity_id=? ORDER BY r.id', (activity_id,)).fetchall()
    conn.close()
    items = []
    for r in regs:
        d = dict(r)
        d['status_text'] = STATUS_MAP.get(d['status'], d['status'])
        items.append(d)
    return jsonify({
        "activity_id": activity_id,
        "activity_title": act['title'],
        "need_review": act['need_review'],
        "max_people": act['max_people'],
        "current_people": act['current_people'],
        "registrations": items})


@app.route('/registration/<int:reg_id>/review', methods=['POST'])
def review_registration(reg_id):
    data = request.json or {}
    user_id = data.get('user_id')
    action = data.get('action')
    if action not in ('approve', 'reject'):
        return jsonify({"message": "action 只能是 approve 或 reject"}), 400
    conn = get_db()
    reg = conn.execute('SELECT * FROM registrations WHERE id=?', (reg_id,)).fetchone()
    if reg is None:
        conn.close()
        return jsonify({"message": "报名记录不存在"}), 404
    act = conn.execute('SELECT * FROM activities WHERE id=?', (reg['activity_id'],)).fetchone()
    if act['publisher_id'] != user_id:
        conn.close()
        return jsonify({"message": "只能审核自己活动的报名"}), 403
    if reg['status'] != 'pending':
        conn.close()
        return jsonify({"message": "该报名不在待审核状态，当前状态：" + STATUS_MAP.get(reg['status'], reg['status'])}), 400

    if action == 'reject':
        conn.execute("UPDATE registrations SET status='rejected' WHERE id=?", (reg_id,))
        conn.commit()
        conn.close()
        return jsonify({"message": "已拒绝该报名"})

    if act['current_people'] >= act['max_people']:
        conn.execute("UPDATE registrations SET status='waitlisted' WHERE id=?", (reg_id,))
        conn.commit()
        conn.close()
        return jsonify({"message": "名额已满，该学生已转入候补"})
    conn.execute("UPDATE registrations SET status='approved' WHERE id=?", (reg_id,))
    conn.execute('UPDATE activities SET current_people=current_people+1 WHERE id=?', (act['id'],))
    conn.commit()
    conn.close()
    return jsonify({"message": "已通过该报名"})

# ============================================================
# =============== C 负责的接口（V2.0 迭代） ==================
# ============================================================

@app.route('/activity/<int:activity_id>/register', methods=['POST'])
def register_activity(activity_id):
    data = request.json
    user_id = data.get('user_id')

    conn = get_db()
    activity = conn.execute('SELECT * FROM activities WHERE id = ?', (activity_id,)).fetchone()
    if not activity:
        conn.close()
        return jsonify({"message": "活动不存在"}), 404
    if activity['cancelled'] == 1:
        conn.close()
        return jsonify({"message": "活动已取消，无法报名"}), 400

    exist = conn.execute(
        'SELECT * FROM registrations WHERE user_id=? AND activity_id=? '
        'AND status IN ("registered","pending","approved","waitlisted")',
        (user_id, activity_id)
    ).fetchone()
    if exist:
        conn.close()
        return jsonify({"message": "您已经报名过该活动"}), 400

    if activity['current_people'] >= activity['max_people']:
        conn.execute(
            'INSERT INTO registrations (user_id, activity_id, status) VALUES (?, ?, "waitlisted")',
            (user_id, activity_id))
        conn.commit()
        conn.close()
        return jsonify({"message": "活动已满，您已进入候补名单"})

    if activity['need_review'] == 1:
        conn.execute(
            'INSERT INTO registrations (user_id, activity_id, status) VALUES (?, ?, "pending")',
            (user_id, activity_id))
        conn.commit()
        conn.close()
        return jsonify({"message": "报名已提交，等待教师审核"})

    conn.execute(
        'INSERT INTO registrations (user_id, activity_id, status) VALUES (?, ?, "registered")',
        (user_id, activity_id))
    conn.execute('UPDATE activities SET current_people = current_people + 1 WHERE id = ?', (activity_id,))
    conn.commit()
    conn.close()
    return jsonify({"message": "报名成功"})


@app.route('/activity/<int:activity_id>/cancel', methods=['POST'])
def cancel_activity(activity_id):
    data = request.json
    user_id = data.get('user_id')

    conn = get_db()
    exist = conn.execute(
        'SELECT * FROM registrations WHERE user_id=? AND activity_id=? '
        'AND status IN ("registered","pending","approved","waitlisted")',
        (user_id, activity_id)
    ).fetchone()
    if not exist:
        conn.close()
        return jsonify({"message": "您没有报名该活动，无法取消"}), 400

    old_status = exist['status']
    conn.execute('UPDATE registrations SET status="cancelled" WHERE id=?', (exist['id'],))

    if old_status in OCCUPY_STATUS:
        conn.execute('UPDATE activities SET current_people = current_people - 1 WHERE id = ?', (activity_id,))
        promote_waitlist(conn, activity_id)

    conn.commit()
    conn.close()
    return jsonify({"message": "取消报名成功"})


@app.route('/my/registrations', methods=['GET'])
def my_registrations():
    user_id = request.args.get('user_id', type=int)
    if not user_id:
        return jsonify({"message": "请传 user_id 参数"}), 400
    conn = get_db()
    rows = conn.execute('''
        SELECT r.id AS reg_id, r.status, r.activity_id,
               a.title AS activity_title, a.time AS activity_time,
               a.location AS activity_location, a.cancelled AS activity_cancelled
        FROM registrations r
        JOIN activities a ON a.id = r.activity_id
        WHERE r.user_id=? ORDER BY r.id DESC
    ''', (user_id,)).fetchall()
    conn.close()
    items = []
    for r in rows:
        d = dict(r)
        d['status_text'] = STATUS_MAP.get(d['status'], d['status'])
        items.append(d)
    return jsonify({"registrations": items})


@app.route('/admin/registrations', methods=['GET'])
def get_registrations():
    conn = get_db()
    regs = conn.execute('''
        SELECT 
            r.id AS reg_id,
            u.name AS user_name,
            u.username AS user_username,
            a.title AS activity_title,
            r.status AS status
        FROM registrations r
        JOIN users u ON r.user_id = u.id
        JOIN activities a ON r.activity_id = a.id
        ORDER BY r.id DESC
    ''').fetchall()
    conn.close()
    items = []
    for r in regs:
        d = dict(r)
        d['status_text'] = STATUS_MAP.get(d['status'], d['status'])
        items.append(d)
    return jsonify({"registrations": items})


@app.route('/admin/approve/<int:reg_id>', methods=['POST'])
def approve_registration(reg_id):
    data = request.json
    status = data.get('status')
    if status not in ['approved', 'rejected']:
        return jsonify({"message": "状态不合法"}), 400
    conn = get_db()
    reg = conn.execute('SELECT * FROM registrations WHERE id = ?', (reg_id,)).fetchone()
    if not reg:
        conn.close()
        return jsonify({"message": "报名记录不存在"}), 404
    conn.execute('UPDATE registrations SET status = ? WHERE id = ?', (status, reg_id))
    conn.commit()
    conn.close()
    return jsonify({"message": "审核完成"})


@app.route('/admin/export', methods=['GET'])
def export_registrations():
    conn = get_db()
    regs = conn.execute('''
        SELECT 
            r.id AS reg_id,
            u.name AS user_name,
            u.username AS user_username,
            a.title AS activity_title,
            r.status AS status
        FROM registrations r
        JOIN users u ON r.user_id = u.id
        JOIN activities a ON r.activity_id = a.id
        ORDER BY r.id DESC
    ''').fetchall()
    conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "报名表"
    ws.append(['报名ID', '学生姓名', '学号', '活动名称', '状态'])
    for row in regs:
        ws.append([
            row['reg_id'], row['user_name'], row['user_username'],
            row['activity_title'], STATUS_MAP.get(row['status'], row['status'])
        ])

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='registrations.xlsx'
    )


@app.route('/admin/users', methods=['GET'])
def admin_list_users():
    admin_id = request.args.get('admin_id', type=int)
    conn = get_db()
    err = require_admin(conn, admin_id)
    if err:
        conn.close()
        return err
    rows = conn.execute(
        'SELECT id, name, username, role, status FROM users ORDER BY id'
    ).fetchall()
    conn.close()
    return jsonify({"users": [dict(r) for r in rows]})


@app.route('/admin/user/<int:user_id>/disable', methods=['POST'])
def admin_disable_user(user_id):
    data = request.json or {}
    admin_id = data.get('admin_id')
    conn = get_db()
    err = require_admin(conn, admin_id)
    if err:
        conn.close()
        return err
    target = get_user(conn, user_id)
    if target is None:
        conn.close()
        return jsonify({"message": "用户不存在"}), 404
    if target['role'] == 'admin':
        conn.close()
        return jsonify({"message": "不能停用管理员账号"}), 400
    conn.execute("UPDATE users SET status='disabled' WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    return jsonify({"message": "账号已停用"})


@app.route('/admin/user/<int:user_id>/enable', methods=['POST'])
def admin_enable_user(user_id):
    data = request.json or {}
    admin_id = data.get('admin_id')
    conn = get_db()
    err = require_admin(conn, admin_id)
    if err:
        conn.close()
        return err
    target = get_user(conn, user_id)
    if target is None:
        conn.close()
        return jsonify({"message": "用户不存在"}), 404
    conn.execute("UPDATE users SET status='active' WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    return jsonify({"message": "账号已恢复"})


@app.route('/admin/activities', methods=['GET'])
def admin_list_activities():
    admin_id = request.args.get('admin_id', type=int)
    conn = get_db()
    err = require_admin(conn, admin_id)
    if err:
        conn.close()
        return err
    rows = conn.execute(
        'SELECT a.*, u.name AS publisher_name FROM activities a '
        'LEFT JOIN users u ON u.id=a.publisher_id ORDER BY a.id DESC'
    ).fetchall()
    conn.close()
    return jsonify({"activities": [dict(r) for r in rows]})


# ============================================================
# 启动
# ============================================================
if __name__ == '__main__':
    ensure_tables()
    app.run(debug=True, port=5001)