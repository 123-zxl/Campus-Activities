-- ============================================================
-- 校园活动管理系统 V1.0 —— SQLite 建表脚本
-- 共三张表：
--   users         用户表（学生和教师都放在这张表，用 role 区分）
--   activities    活动表（教师发布的活动）
--   registrations 报名表（记录"哪个学生报了哪个活动"）
-- ============================================================

-- SQLite 默认不强制外键约束，每次连接数据库后都要先开启
PRAGMA foreign_keys = ON;

-- ------------------------------------------------------------
-- 方便重复执行本脚本：按"先子表、后父表"的顺序删除旧表
-- 注意：这会清空数据！第一次运行时这三句不会有任何影响
-- ------------------------------------------------------------
DROP TABLE IF EXISTS registrations;
DROP TABLE IF EXISTS activities;
DROP TABLE IF EXISTS users;

-- ============================================================
-- 1. users 用户表
-- ============================================================
CREATE TABLE users (
    id          TEXT PRIMARY KEY,                                   -- 登录账号：学生填学号，教师填工号
    role        TEXT NOT NULL CHECK (role IN ('student','teacher')), -- 身份：student=学生，teacher=教师
    password    TEXT NOT NULL,                                      -- 登录密码（V1.0 学习版先明文存放，正式项目应加密）
    name        TEXT NOT NULL,                                      -- 姓名
    gender      TEXT CHECK (gender IN ('男','女')),                 -- 性别：学生填写，教师可以留空(NULL)
    major_class TEXT,                                               -- 专业班级：学生填写，教师留空(NULL)
    phone       TEXT,                                               -- 电话：学生、教师都有
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))       -- 注册时间：自动填入当前时间，不用手动写
);

-- ============================================================
-- 2. activities 活动表
-- ============================================================
CREATE TABLE activities (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,                -- 活动编号：数据库自动生成 1、2、3……
    name          TEXT NOT NULL,                                    -- 活动名称
    activity_time TEXT NOT NULL,                                    -- 活动时间，例如 '2026-09-20 14:00'
    location      TEXT NOT NULL,                                    -- 活动地点
    total_count   INTEGER NOT NULL CHECK (total_count > 0),         -- 总人数（报名名额上限，必须大于 0）
    description   TEXT,                                             -- 活动介绍，可以留空
    publisher_id  TEXT NOT NULL,                                    -- 发布教师的工号（对应 users.id）
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),    -- 发布时间：自动生成
    -- 外键：发布教师必须是 users 表中真实存在的用户
    FOREIGN KEY (publisher_id) REFERENCES users(id)
);

-- ============================================================
-- 3. registrations 报名记录表
--    一行 = 一条报名信息；学生退选 = 删除对应的那一行
-- ============================================================
CREATE TABLE registrations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,                -- 报名记录编号：自动生成
    student_id    TEXT NOT NULL,                                    -- 报名学生的学号（对应 users.id）
    activity_id   INTEGER NOT NULL,                                 -- 所报活动的编号（对应 activities.id）
    registered_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),    -- 报名时间：自动生成

    -- 联合唯一约束：同一个学生对同一个活动只能有一条记录 => 不能重复报名
    UNIQUE (student_id, activity_id),

    -- 外键：学生和活动都必须真实存在
    -- ON DELETE CASCADE：学生或活动被删除时，相关报名记录自动一起删除
    FOREIGN KEY (student_id)  REFERENCES users(id)      ON DELETE CASCADE,
    FOREIGN KEY (activity_id) REFERENCES activities(id) ON DELETE CASCADE
);

-- ============================================================
-- 索引：让"查某教师发布的活动""查某活动的报名名单"更快
-- ============================================================
CREATE INDEX idx_activities_publisher   ON activities(publisher_id);
CREATE INDEX idx_registrations_activity ON registrations(activity_id);

-- ============================================================
-- 触发器：让数据库自动帮我们守住业务规则
-- ============================================================

-- 小知识：同一张表上有多个同类触发器时，SQLite 按“后创建的先执行”（像栈一样）。
-- 所以下面把“名额检查”写在前面、“身份检查”写在后面，实际执行时反而先查身份、再查名额。

-- 规则一：只有教师(role='teacher')才能发布活动
DROP TRIGGER IF EXISTS trg_activities_teacher_only;
CREATE TRIGGER trg_activities_teacher_only
BEFORE INSERT ON activities
FOR EACH ROW
-- 用 IS NOT 而不是 <>：工号不存在时查出来是 NULL，IS NOT 仍能正确识别
WHEN (SELECT role FROM users WHERE id = NEW.publisher_id) IS NOT 'teacher'
BEGIN
    SELECT RAISE(ABORT, '发布失败：该工号不是教师或不存在');
END;

-- 规则二：报名人数不能超过活动总人数（先创建 => 后执行）
DROP TRIGGER IF EXISTS trg_registrations_capacity;
CREATE TRIGGER trg_registrations_capacity
BEFORE INSERT ON registrations
FOR EACH ROW
-- 新增报名前先数一下该活动已有多少人报名，满员就拒绝插入
WHEN (SELECT COUNT(*) FROM registrations WHERE activity_id = NEW.activity_id)
     >= (SELECT total_count FROM activities WHERE id = NEW.activity_id)
BEGIN
    SELECT RAISE(ABORT, '报名人数已满，无法继续报名');
END;

-- 规则三：只有学生(role='student')才能报名活动（后创建 => 先执行，优先报身份错误）
DROP TRIGGER IF EXISTS trg_registrations_student_only;
CREATE TRIGGER trg_registrations_student_only
BEFORE INSERT ON registrations
FOR EACH ROW
WHEN (SELECT role FROM users WHERE id = NEW.student_id) IS NOT 'student'
BEGIN
    SELECT RAISE(ABORT, '报名失败：该账号不是学生或不存在');
END;
