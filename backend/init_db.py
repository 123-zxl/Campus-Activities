# -*- coding: utf-8 -*-
"""
数据库初始化脚本（运行后会清空并重建 campus.db，请放心用于课程开发阶段）

用法：
    python init_db.py

完成的事：
    1. 按 database.sql 重建三张表、索引、触发器；
    2. 写入演示账号（密码统一为 123456，哈希加密存储）；
    3. 写入两个活动及若干报名记录。
"""
import os
import sqlite3
import sys

from werkzeug.security import generate_password_hash

from db import DB_PATH, SQL_PATH, get_db

# 演示账号：(账号, 身份, 姓名, 性别, 专业班级, 电话)
SEED_USERS = [
    ("T001", "teacher", "王老师", None, None, "13800000001"),
    ("T002", "teacher", "李老师", None, None, "13800000002"),
    ("2026001", "student", "张三", "男", "软工2601", "13900000001"),
    ("2026002", "student", "李四", "女", "软工2601", "13900000002"),
    ("2026003", "student", "王五", "男", "软工2602", "13900000003"),
]

# 演示活动：(名称, 开始, 结束, 报名截止, 地点, 名额, 介绍, 发布教师)
SEED_ACTIVITIES = [
    ("迎新晚会", "2026-09-20 19:00", "2026-09-20 21:00", "2026-09-19 23:59",
     "大礼堂", 2, "欢迎新同学，节目精彩，有礼相送", "T001"),
    ("校运会", "2026-10-05 08:00", "2026-10-05 12:00", "2026-10-04 18:00",
     "体育场", 5, "秋季田径运动会，欢迎报名各项目", "T001"),
]

# 演示报名：迎新晚会(活动1)=李四、王五（满员）；校运会(活动2)=张三
SEED_REGISTRATIONS = [
    ("2026002", 1),
    ("2026003", 1),
    ("2026001", 2),
]

SEED_PASSWORD = "123456"


def create_schema(con):
    """执行 database.sql，重建全部表结构"""
    with open(SQL_PATH, encoding="utf-8") as f:
        con.executescript(f.read())


def seed(con):
    """写入演示数据"""
    for uid, role, name, gender, major_class, phone in SEED_USERS:
        con.execute(
            "INSERT INTO users(id,role,password,name,gender,major_class,phone) "
            "VALUES(?,?,?,?,?,?,?)",
            (uid, role, generate_password_hash(SEED_PASSWORD), name, gender, major_class, phone),
        )
    for name, st, et, dl, loc, total, desc, publisher in SEED_ACTIVITIES:
        con.execute(
            "INSERT INTO activities(name,start_time,end_time,register_deadline,"
            "location,total_count,description,publisher_id) VALUES(?,?,?,?,?,?,?,?)",
            (name, st, et, dl, loc, total, desc, publisher),
        )
    for student_id, activity_id in SEED_REGISTRATIONS:
        con.execute(
            "INSERT INTO registrations(student_id,activity_id) VALUES(?,?)",
            (student_id, activity_id),
        )


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    con = get_db()
    try:
        create_schema(con)
        seed(con)
        con.commit()
    finally:
        con.close()

    print("数据库已重建：" + DB_PATH)
    print("演示账号（密码均为 %s）：" % SEED_PASSWORD)
    print("  教师：T001 王老师、T002 李老师")
    print("  学生：2026001 张三、2026002 李四、2026003 王五")
    print("活动：迎新晚会（满员）、校运会（可报名）")
    print("下一步：python app.py 启动后端服务")


if __name__ == "__main__":
    main()
