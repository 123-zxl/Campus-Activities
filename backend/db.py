# -*- coding: utf-8 -*-
"""
数据库连接的小工具模块。
所有接口要操作数据库时，都从这里拿连接，保证：
1. 外键约束已开启（否则 SQLite 默认不检查外键）；
2. 查询结果可以像字典一样按列名取值（row_factory = Row）。
"""
import os
import sqlite3

# backend 文件夹的绝对路径：无论从哪个目录启动程序，都能找到 campus.db
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 数据库文件路径；可用环境变量 CAMPUS_DB_PATH 覆盖（自动化测试时用临时库）
DB_PATH = os.environ.get("CAMPUS_DB_PATH", os.path.join(BASE_DIR, "campus.db"))

# 建表脚本路径
SQL_PATH = os.path.join(BASE_DIR, "database.sql")


def get_db():
    """打开一个数据库连接。用完记得 close() 或 with get_db() as con:。"""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row          # 结果行支持 row["name"] 这种写法
    con.execute("PRAGMA foreign_keys = ON")  # 开启外键强制检查
    return con
