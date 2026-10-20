"""
绩效考核数据库模块
使用 SQLite 存储配置和打分数据
支持多季度会话管理
"""
import sqlite3
import os
import hashlib
import secrets
from typing import List, Dict, Optional

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_v2.db")


class Database:
    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # 生产环境开启 WAL 模式，提升多人并发读写性能并降低锁冲突
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._init_tables()
        self._seed_default_data()

    def _init_tables(self):
        cursor = self.conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admin_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS assessors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id TEXT UNIQUE,
                name TEXT NOT NULL,
                access_token TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 迁移：为存量考核人补 access_token 列（列已存在时跳过）
        try:
            cursor.execute("ALTER TABLE assessors ADD COLUMN access_token TEXT")
        except sqlite3.OperationalError:
            pass

        # 为 access_token 为空的存量考核人生成随机令牌（32位十六进制，128位熵）
        # 不使用自增 id 推算，避免他人通过改 URL 数字枚举他人打分链接
        cursor.execute("SELECT id FROM assessors WHERE access_token IS NULL OR access_token = ''")
        for (aid,) in cursor.fetchall():
            cursor.execute(
                "UPDATE assessors SET access_token = ? WHERE id = ?",
                (secrets.token_hex(16), aid)
            )
        # 保证新建考核人也自带随机令牌
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_assessors_token
            ON assessors(access_token)
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS evaluatees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id TEXT UNIQUE,
                name TEXT NOT NULL,
                position TEXT,
                department TEXT,
                group_type TEXT,
                department_ref_score TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS assessment_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                quarter TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 生命周期字段迁移（存量库无损升级，列已存在时跳过）
        try:
            cursor.execute("ALTER TABLE assessment_sessions ADD COLUMN lifecycle TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE assessment_sessions ADD COLUMN lifecycle_prev TEXT")
        except sqlite3.OperationalError:
            pass
        # 兜底：无 lifecycle 的存量会话按 status 推导（active→running，inactive→ended）
        cursor.execute("""
            UPDATE assessment_sessions
            SET lifecycle = CASE WHEN status = 'active' THEN 'running' ELSE 'ended' END
            WHERE lifecycle IS NULL OR lifecycle = ''
        """)

        # 打分历史快照表：管理员重置考核人时归档其原始打分记录
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS score_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                assessor_id INTEGER NOT NULL,
                evaluatee_id INTEGER NOT NULL,
                indicator_id INTEGER NOT NULL,
                score REAL,
                archived_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES assessment_sessions(id),
                FOREIGN KEY (assessor_id) REFERENCES assessors(id),
                FOREIGN KEY (evaluatee_id) REFERENCES evaluatees(id),
                FOREIGN KEY (indicator_id) REFERENCES indicators(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS assessment_relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                assessor_id INTEGER NOT NULL,
                evaluatee_id INTEGER NOT NULL,
                weight REAL DEFAULT 1.0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES assessment_sessions(id),
                FOREIGN KEY (assessor_id) REFERENCES assessors(id),
                FOREIGN KEY (evaluatee_id) REFERENCES evaluatees(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS indicators (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                weight REAL DEFAULT 1.0,
                max_score REAL DEFAULT 100,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                assessor_id INTEGER NOT NULL,
                evaluatee_id INTEGER NOT NULL,
                indicator_id INTEGER NOT NULL,
                score REAL,
                submitted INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES assessment_sessions(id),
                FOREIGN KEY (assessor_id) REFERENCES assessors(id),
                FOREIGN KEY (evaluatee_id) REFERENCES evaluatees(id),
                FOREIGN KEY (indicator_id) REFERENCES indicators(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS system_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        # ========== 按 session 隔离的人员属性表 ==========
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS session_assessors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                assessor_id INTEGER NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES assessment_sessions(id),
                FOREIGN KEY (assessor_id) REFERENCES assessors(id),
                UNIQUE(session_id, assessor_id)
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_session_assessors_session
            ON session_assessors(session_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_session_assessors_assessor
            ON session_assessors(assessor_id)
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS session_evaluatees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                evaluatee_id INTEGER NOT NULL,
                position TEXT,
                department TEXT,
                group_type TEXT,
                department_ref_score TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES assessment_sessions(id),
                FOREIGN KEY (evaluatee_id) REFERENCES evaluatees(id),
                UNIQUE(session_id, evaluatee_id)
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_session_evaluatees_session
            ON session_evaluatees(session_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_session_evaluatees_evaluatee
            ON session_evaluatees(evaluatee_id)
        """)

        self.conn.commit()

        # 存量迁移：把全局 evaluatees 的属性按 session 复制到 session_evaluatees
        self._migrate_session_evaluatees()
        self._migrate_session_assessors()

    def _seed_default_data(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) as cnt FROM indicators")
        if cursor.fetchone()['cnt'] == 0:
            defaults = [
                ("工作业绩", 0.7, 100),
                ("能力评价", 0.3, 100),
            ]
            for name, w, mx in defaults:
                cursor.execute(
                    "INSERT INTO indicators (name, weight, max_score) VALUES (?,?,?)",
                    (name, w, mx)
                )

        cursor.execute("SELECT COUNT(*) as cnt FROM assessment_sessions")
        if cursor.fetchone()['cnt'] == 0:
            cursor.execute(
                "INSERT INTO assessment_sessions (name, quarter, status, lifecycle) VALUES (?, ?, 'active', 'running')",
                ("2026年二季度中层考核", "2026-Q2")
            )

        cursor.execute("SELECT COUNT(*) as cnt FROM admin_users")
        if cursor.fetchone()['cnt'] == 0:
            salt = secrets.token_hex(16)
            pw_hash = hashlib.sha256(("admin123" + salt).encode()).hexdigest()
            cursor.execute(
                "INSERT INTO admin_users (username, password_hash, salt) VALUES (?, ?, ?)",
                ("admin", pw_hash, salt)
            )

        self.conn.commit()

    def _migrate_session_evaluatees(self):
        """
        将全局 evaluatees 的季度属性，按每个 session 复制到 session_evaluatees。
        改造前所有 session 共享全局表，改造后冻结为各自 session 的数据。
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) as cnt FROM session_evaluatees")
        if cursor.fetchone()['cnt'] > 0:
            return
        cursor.execute("""
            SELECT DISTINCT session_id, evaluatee_id
            FROM (
                SELECT session_id, evaluatee_id FROM assessment_relations
                UNION
                SELECT session_id, evaluatee_id FROM scores
            )
            ORDER BY session_id, evaluatee_id
        """)
        pairs = cursor.fetchall()
        for row in pairs:
            sid, eid = row['session_id'], row['evaluatee_id']
            cursor.execute(
                "SELECT position, department, group_type, department_ref_score FROM evaluatees WHERE id = ?",
                (eid,)
            )
            ev = cursor.fetchone()
            if not ev:
                continue
            cursor.execute("""
                INSERT OR IGNORE INTO session_evaluatees
                (session_id, evaluatee_id, position, department, group_type, department_ref_score)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (sid, eid, ev['position'], ev['department'], ev['group_type'], ev['department_ref_score']))
        self.conn.commit()

    def _migrate_session_assessors(self):
        """
        将全局 assessors 按每个 session 复制到 session_assessors，保持季度维度一致。
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) as cnt FROM session_assessors")
        if cursor.fetchone()['cnt'] > 0:
            return
        cursor.execute("""
            SELECT DISTINCT session_id, assessor_id
            FROM (
                SELECT session_id, assessor_id FROM assessment_relations
                UNION
                SELECT session_id, assessor_id FROM scores
            )
            ORDER BY session_id, assessor_id
        """)
        pairs = cursor.fetchall()
        for row in pairs:
            sid, aid = row['session_id'], row['assessor_id']
            cursor.execute("SELECT id FROM assessors WHERE id = ?", (aid,))
            if not cursor.fetchone():
                continue
            cursor.execute("""
                INSERT OR IGNORE INTO session_assessors (session_id, assessor_id)
                VALUES (?, ?)
            """, (sid, aid))
        self.conn.commit()

    # ========== 考核链接令牌（防止枚举他人打分链接）============
    def get_assessor_by_token(self, token: str) -> Optional[Dict]:
        """凭访问令牌定位考核人；令牌无效返回 None"""
        if not token or len(token) < 16:
            return None
        cursor = self.conn.cursor()
        # 参数化查询，杜绝 SQL 注入
        cursor.execute("SELECT * FROM assessors WHERE access_token = ?", (token,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_assessor_token(self, assessor_id: int) -> Optional[str]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT access_token FROM assessors WHERE id = ?", (assessor_id,))
        row = cursor.fetchone()
        if not row:
            return None
        tok = row['access_token']
        if not tok:
            # 兜底：极端情况下补生成，保证链接始终可用
            tok = secrets.token_hex(16)
            cursor.execute("UPDATE assessors SET access_token = ? WHERE id = ?", (tok, assessor_id))
            self.conn.commit()
        return tok

    def reset_assessor_token(self, assessor_id: int) -> Optional[str]:
        """重置某考核人的访问令牌（旧链接立即失效），返回新令牌"""
        new_token = secrets.token_hex(16)
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE assessors SET access_token = ? WHERE id = ?",
            (new_token, assessor_id)
        )
        self.conn.commit()
        return new_token if cursor.rowcount > 0 else None

    def ensure_assessor_token(self, assessor_id: int) -> Optional[str]:
        """确保某考核人已有令牌（新建/导入时调用）"""
        return self.get_assessor_token(assessor_id)

    # ========== 登录认证 ==========
    def verify_admin(self, username: str, password: str) -> bool:
        cursor = self.conn.cursor()
        cursor.execute("SELECT password_hash, salt FROM admin_users WHERE username = ?", (username,))
        row = cursor.fetchone()
        if not row:
            return False
        pw_hash = hashlib.sha256((password + row['salt']).encode()).hexdigest()
        return pw_hash == row['password_hash']

    def change_password(self, username: str, old_password: str, new_password: str) -> bool:
        if not self.verify_admin(username, old_password):
            return False
        salt = secrets.token_hex(16)
        pw_hash = hashlib.sha256((new_password + salt).encode()).hexdigest()
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE admin_users SET password_hash=?, salt=? WHERE username=?",
            (pw_hash, salt, username)
        )
        self.conn.commit()
        return True

    # ========== 会话管理 ==========
    def create_session(self, name: str, quarter: str) -> int:
        cursor = self.conn.cursor()
        cursor.execute("UPDATE assessment_sessions SET status = 'inactive'")
        cursor.execute(
            "INSERT INTO assessment_sessions (name, quarter, status, lifecycle) VALUES (?, ?, 'active', 'draft')",
            (name, quarter)
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_all_sessions(self) -> List[Dict]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM assessment_sessions ORDER BY id DESC")
        return [dict(row) for row in cursor.fetchall()]

    def get_current_session(self) -> Optional[Dict]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM assessment_sessions WHERE status = 'active' ORDER BY id DESC LIMIT 1"
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_current_session_id(self) -> int:
        session = self.get_current_session()
        if session:
            return session['id']
        sid = self.create_session("默认考核", "2026-Q2")
        return sid

    def set_current_session(self, session_id: int):
        cursor = self.conn.cursor()
        cursor.execute("UPDATE assessment_sessions SET status = 'inactive'")
        cursor.execute("UPDATE assessment_sessions SET status = 'active' WHERE id = ?", (session_id,))
        self.conn.commit()

    def delete_session(self, session_id: int):
        cursor = self.conn.cursor()
        # 先清理该任务下的所有从表数据（含历史归档），否则外键约束会阻止删除
        cursor.execute("DELETE FROM scores WHERE session_id = ?", (session_id,))
        cursor.execute("DELETE FROM score_history WHERE session_id = ?", (session_id,))
        cursor.execute("DELETE FROM assessment_relations WHERE session_id = ?", (session_id,))
        cursor.execute("DELETE FROM session_evaluatees WHERE session_id = ?", (session_id,))
        cursor.execute("DELETE FROM session_assessors WHERE session_id = ?", (session_id,))
        cursor.execute("DELETE FROM assessment_sessions WHERE id = ?", (session_id,))
        self.conn.commit()

    def clear_session_relations(self, session_id: int):
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM scores WHERE session_id = ?", (session_id,))
        cursor.execute("DELETE FROM assessment_relations WHERE session_id = ?", (session_id,))
        self.conn.commit()

    # ========== 考核任务生命周期 ==========
    # 状态机：draft(未启动) → testing(测试) / running(正式)；testing|running ⇄ paused；均可达 ended(终态)
    LIFECYCLE_TRANSITIONS = {
        "start_test": {"draft": "testing"},
        "start": {"draft": "running", "testing": "running"},
        "pause": {"testing": "paused", "running": "paused"},
        "resume": {"paused": "running"},  # 实际恢复目标取 lifecycle_prev
        "end": {"testing": "ended", "running": "ended", "paused": "ended"},
    }

    def get_session_lifecycle(self, session_id: int = None) -> str:
        if session_id is None:
            session = self.get_current_session()
            return (session or {}).get('lifecycle') or 'draft'
        cursor = self.conn.cursor()
        cursor.execute("SELECT lifecycle FROM assessment_sessions WHERE id = ?", (session_id,))
        row = cursor.fetchone()
        return (row['lifecycle'] if row else None) or 'draft'

    WEIGHT_TOLERANCE = 0.001  # 权重浮点容差

    def check_session_weights(self, session_id: int) -> Dict:
        """
        校验当前 session 的权重规则：
        1. 考核关系：每个被考核人的所有考核人权重合计应为 1
        2. 指标：全部指标权重合计应为 1（指标为全局配置）
        返回 {"relation_issues": [...], "indicator_sum": float, "ok": bool}
        relation_issues 每项: {evaluatee_name, sum, relations: [{assessor_name, weight}]}
        """
        cursor = self.conn.cursor()
        issues = []

        # 关系权重：按被考核人分组求和
        cursor.execute("""
            SELECT e.id as evaluatee_id, e.name as evaluatee_name,
                   SUM(ar.weight) as weight_sum,
                   GROUP_CONCAT(a.name || ':' || ROUND(ar.weight, 4), ', ') as detail
            FROM assessment_relations ar
            JOIN evaluatees e ON ar.evaluatee_id = e.id
            JOIN assessors a ON ar.assessor_id = a.id
            WHERE ar.session_id = ?
            GROUP BY ar.evaluatee_id
            HAVING ABS(SUM(ar.weight) - 1.0) > ?
        """, (session_id, self.WEIGHT_TOLERANCE))
        for row in cursor.fetchall():
            issues.append({
                "evaluatee_id": row['evaluatee_id'],
                "evaluatee_name": row['evaluatee_name'],
                "sum": round(row['weight_sum'], 4),
                "detail": row['detail']
            })

        # 指标权重：全局合计
        cursor.execute("SELECT SUM(weight) as s FROM indicators")
        row = cursor.fetchone()
        indicator_sum = round(row['s'] if row['s'] is not None else 0, 4)
        indicator_ok = abs(indicator_sum - 1.0) <= self.WEIGHT_TOLERANCE

        return {
            "relation_issues": issues,
            "indicator_sum": indicator_sum,
            "indicator_ok": indicator_ok,
            "ok": len(issues) == 0 and indicator_ok
        }

    def _weight_block_reason(self, session_id: int) -> Optional[str]:
        """启动考核前的权重硬校验，不通过时返回原因文本（用于拒绝启动）"""
        check = self.check_session_weights(session_id)
        if check["ok"]:
            return None
        parts = []
        if check["relation_issues"]:
            detail = "; ".join(
                f"{i['evaluatee_name']}(合计{i['sum']}，各考核人权重: {i['detail']})"
                for i in check["relation_issues"]
            )
            parts.append(f"考核关系权重异常（每个被考核人的考核人权重合计应为1）：{detail}")
        if not check["indicator_ok"]:
            parts.append(f"指标权重合计应为1，当前为 {check['indicator_sum']}")
        return "；".join(parts)

    def set_lifecycle(self, session_id: int, action: str, clear_scores: bool = False) -> Dict:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM assessment_sessions WHERE id = ?", (session_id,))
        row = cursor.fetchone()
        if not row:
            return {"success": False, "message": "考核任务不存在"}
        current = row['lifecycle'] or 'draft'

        if action == "resume":
            if current != 'paused':
                return {"success": False, "message": "只有已暂停的考核才能恢复"}
            target = row['lifecycle_prev'] or 'running'
        else:
            allowed = self.LIFECYCLE_TRANSITIONS.get(action)
            if not allowed:
                return {"success": False, "message": f"未知操作: {action}"}
            target = allowed.get(current)
            if not target:
                return {"success": False, "message": "当前考核状态不允许该操作"}

        # 启动（测试/正式）前硬校验权重规则：关系权重合计=1、指标权重合计=1
        if action in ("start", "start_test"):
            reason = self._weight_block_reason(session_id)
            if reason:
                return {"success": False, "message": f"无法启动：{reason}"}

        prev_value = current if action == 'pause' else row['lifecycle_prev']
        cursor.execute(
            "UPDATE assessment_sessions SET lifecycle=?, lifecycle_prev=? WHERE id=?",
            (target, prev_value, session_id)
        )

        cleared = 0
        if action == 'start' and current == 'testing' and clear_scores:
            cleared = self.clear_session_scores(session_id)
        self.conn.commit()
        return {"success": True, "lifecycle": target, "cleared_scores": cleared}

    def clear_session_scores(self, session_id: int) -> int:
        """清空某考核任务的全部打分数据（保留考核关系）"""
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM scores WHERE session_id = ?", (session_id,))
        self.conn.commit()
        return cursor.rowcount

    # ========== 考核人打分重置 ==========
    def reset_assessor(self, assessor_id: int, session_id: int = None) -> int:
        """
        重置考核人的提交状态：
        1. 将其已提交的分数快照归档到 score_history（保留原打分记录）
        2. 置 submitted=0 解锁，允许重新打分提交（旧分数保留为预填）
        返回归档的记录条数
        """
        if session_id is None:
            session_id = self.get_current_session_id()
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO score_history (session_id, assessor_id, evaluatee_id, indicator_id, score)
            SELECT session_id, assessor_id, evaluatee_id, indicator_id, score
            FROM scores
            WHERE session_id=? AND assessor_id=? AND submitted=1
        """, (session_id, assessor_id))
        archived = cursor.rowcount
        cursor.execute(
            "UPDATE scores SET submitted=0 WHERE session_id=? AND assessor_id=?",
            (session_id, assessor_id)
        )
        self.conn.commit()
        return archived

    def get_assessor_reset_count(self, assessor_id: int, session_id: int = None) -> int:
        if session_id is None:
            session_id = self.get_current_session_id()
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT COUNT(DISTINCT archived_at) as cnt FROM score_history
            WHERE session_id=? AND assessor_id=?
        """, (session_id, assessor_id))
        return cursor.fetchone()['cnt']

    def get_score_history(self, assessor_id: int, session_id: int = None) -> List[Dict]:
        """
        按归档批次返回某考核人的历史打分记录（每次管理员重置归档为一批，最新在前）
        每批内按被考核人分组，附各指标分数与加权合计
        """
        if session_id is None:
            session_id = self.get_current_session_id()
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT sh.archived_at, sh.score, sh.evaluatee_id,
                   e.name as evaluatee_name, e.employee_id as evaluatee_employee_id,
                   COALESCE(se.position, e.position) as position,
                   COALESCE(se.department, e.department) as department,
                   COALESCE(se.group_type, e.group_type) as group_type,
                   COALESCE(se.department_ref_score, e.department_ref_score) as department_ref_score,
                   i.name as indicator_name, i.weight as indicator_weight, i.max_score
            FROM score_history sh
            JOIN evaluatees e ON sh.evaluatee_id = e.id
            LEFT JOIN session_evaluatees se
                ON se.session_id = sh.session_id AND se.evaluatee_id = e.id
            JOIN indicators i ON sh.indicator_id = i.id
            WHERE sh.session_id=? AND sh.assessor_id=?
            ORDER BY sh.archived_at DESC, sh.evaluatee_id, sh.indicator_id
        """, (session_id, assessor_id))
        rows = [dict(r) for r in cursor.fetchall()]

        batches = []
        batch_map = {}
        for r in rows:
            key = r['archived_at']
            if key not in batch_map:
                batch = {"archived_at": key, "evaluatees": {}}
                batch_map[key] = batch
                batches.append(batch)
            batch = batch_map[key]
            eid = r['evaluatee_id']
            if eid not in batch['evaluatees']:
                batch['evaluatees'][eid] = {
                    "evaluatee_id": eid,
                    "evaluatee_name": r['evaluatee_name'],
                    "employee_id": r['evaluatee_employee_id'],
                    "indicators": [],
                    "weighted_total": 0
                }
            entry = batch['evaluatees'][eid]
            entry['indicators'].append({
                "indicator_name": r['indicator_name'],
                "score": r['score'],
                "indicator_weight": r['indicator_weight'],
                "max_score": r['max_score']
            })
            if r['score'] is not None:
                entry['weighted_total'] += r['score'] * r['indicator_weight']

        # dict -> list，加权合计取整到 2 位
        for batch in batches:
            batch['evaluatees'] = list(batch['evaluatees'].values())
            for ev in batch['evaluatees']:
                ev['weighted_total'] = round(ev['weighted_total'], 2)
        return batches

    # ========== 系统设置 ==========
    def get_setting(self, key: str, default: str = "") -> str:
        cursor = self.conn.cursor()
        cursor.execute("SELECT value FROM system_settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row['value'] if row else default

    def set_setting(self, key: str, value: str):
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO system_settings (key, value) VALUES (?, ?)",
            (key, value)
        )
        self.conn.commit()

    # ========== 考核人 ==========
    def add_assessor(self, name: str, employee_id: str = None, session_id: int = None) -> int:
        if session_id is None:
            session_id = self.get_current_session_id()
        cursor = self.conn.cursor()
        if employee_id:
            cursor.execute("SELECT id FROM assessors WHERE employee_id = ?", (employee_id,))
            existing = cursor.fetchone()
            if existing:
                assessor_id = existing['id']
                cursor.execute(
                    "UPDATE assessors SET name=? WHERE id=?",
                    (name, assessor_id)
                )
            else:
                cursor.execute(
                    "INSERT INTO assessors (name, employee_id, access_token) VALUES (?, ?, ?)",
                    (name, employee_id, secrets.token_hex(16))
                )
                assessor_id = cursor.lastrowid
        else:
            cursor.execute(
                "INSERT INTO assessors (name, employee_id, access_token) VALUES (?, ?, ?)",
                (name, employee_id, secrets.token_hex(16))
            )
            assessor_id = cursor.lastrowid

        cursor.execute(
            "SELECT id FROM session_assessors WHERE session_id=? AND assessor_id=?",
            (session_id, assessor_id)
        )
        if not cursor.fetchone():
            cursor.execute(
                "INSERT OR IGNORE INTO session_assessors (session_id, assessor_id) VALUES (?, ?)",
                (session_id, assessor_id)
            )
        self.conn.commit()
        return assessor_id

    def get_all_assessors(self, session_id: int = None) -> List[Dict]:
        if session_id is None:
            session_id = self.get_current_session_id()
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT a.* FROM assessors a
            JOIN session_assessors sa ON sa.assessor_id = a.id
            WHERE sa.session_id = ?
            ORDER BY a.id
        """, (session_id,))
        return [dict(row) for row in cursor.fetchall()]

    def get_assessor(self, assessor_id: int) -> Optional[Dict]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM assessors WHERE id = ?", (assessor_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_assessor_by_employee_id(self, employee_id: str, session_id: int = None) -> Optional[Dict]:
        if session_id is None:
            session_id = self.get_current_session_id()
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT a.* FROM assessors a
            JOIN session_assessors sa ON sa.assessor_id = a.id
            WHERE a.employee_id = ? AND sa.session_id = ?
        """, (employee_id, session_id))
        row = cursor.fetchone()
        return dict(row) if row else None

    def delete_assessor(self, assessor_id: int):
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM scores WHERE assessor_id = ?", (assessor_id,))
        cursor.execute("DELETE FROM assessment_relations WHERE assessor_id = ?", (assessor_id,))
        cursor.execute("DELETE FROM session_assessors WHERE assessor_id = ?", (assessor_id,))
        cursor.execute("DELETE FROM assessors WHERE id = ?", (assessor_id,))
        self.conn.commit()

    def import_assessors(self, data: List[Dict], session_id: int = None) -> Dict:
        if session_id is None:
            session_id = self.get_current_session_id()
        created, updated = 0, 0
        for item in data:
            emp_id = item.get('employee_id')
            name = item.get('name')
            if not emp_id or not name:
                continue
            cursor = self.conn.cursor()
            cursor.execute("SELECT id FROM assessors WHERE employee_id = ?", (str(emp_id),))
            existing = cursor.fetchone()
            if existing:
                assessor_id = existing['id']
                # 仅更新全局姓名
                cursor.execute(
                    "UPDATE assessors SET name=? WHERE id=?",
                    (str(name), assessor_id)
                )
                updated += 1
            else:
                cursor.execute(
                    "INSERT INTO assessors (name, employee_id, access_token) VALUES (?,?,?)",
                    (str(name), str(emp_id), secrets.token_hex(16))
                )
                assessor_id = cursor.lastrowid
                created += 1

            cursor.execute(
                "SELECT id FROM session_assessors WHERE session_id=? AND assessor_id=?",
                (session_id, assessor_id)
            )
            if not cursor.fetchone():
                cursor.execute(
                    "INSERT OR IGNORE INTO session_assessors (session_id, assessor_id) VALUES (?, ?)",
                    (session_id, assessor_id)
                )
        self.conn.commit()
        return {"created": created, "updated": updated, "total": len(data)}

    # ========== 被考核人 ==========
    def add_evaluatee(self, name: str, employee_id: str = None, position: str = None,
                      department: str = None, group_type: str = None,
                      department_ref_score: str = None, session_id: int = None) -> int:
        if session_id is None:
            session_id = self.get_current_session_id()
        cursor = self.conn.cursor()
        if employee_id:
            cursor.execute("SELECT id FROM evaluatees WHERE employee_id = ?", (employee_id,))
            existing = cursor.fetchone()
            if existing:
                evaluatee_id = existing['id']
            else:
                cursor.execute(
                    "INSERT INTO evaluatees (name, employee_id) VALUES (?, ?)",
                    (name, employee_id)
                )
                evaluatee_id = cursor.lastrowid
        else:
            cursor.execute(
                "INSERT INTO evaluatees (name, employee_id) VALUES (?, ?)",
                (name, employee_id)
            )
            evaluatee_id = cursor.lastrowid

        cursor.execute("""
            INSERT OR REPLACE INTO session_evaluatees
            (session_id, evaluatee_id, position, department, group_type, department_ref_score)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (session_id, evaluatee_id, position, department, group_type, department_ref_score))
        self.conn.commit()
        return evaluatee_id

    def get_all_evaluatees(self, session_id: int = None) -> List[Dict]:
        if session_id is None:
            session_id = self.get_current_session_id()
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT e.id, e.employee_id, e.name,
                   COALESCE(se.position, '') as position,
                   COALESCE(se.department, '') as department,
                   COALESCE(se.group_type, '') as group_type,
                   COALESCE(se.department_ref_score, '') as department_ref_score,
                   e.created_at
            FROM evaluatees e
            LEFT JOIN session_evaluatees se
                ON se.session_id = ? AND se.evaluatee_id = e.id
            WHERE EXISTS (
                SELECT 1 FROM session_evaluatees se2
                WHERE se2.session_id = ? AND se2.evaluatee_id = e.id
            )
            ORDER BY e.id
        """, (session_id, session_id))
        return [dict(row) for row in cursor.fetchall()]

    def get_evaluatee_by_employee_id(self, employee_id: str, session_id: int = None) -> Optional[Dict]:
        if session_id is None:
            session_id = self.get_current_session_id()
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT e.id, e.employee_id, e.name,
                   COALESCE(se.position, '') as position,
                   COALESCE(se.department, '') as department,
                   COALESCE(se.group_type, '') as group_type,
                   COALESCE(se.department_ref_score, '') as department_ref_score,
                   e.created_at
            FROM evaluatees e
            LEFT JOIN session_evaluatees se
                ON se.session_id = ? AND se.evaluatee_id = e.id
            WHERE e.employee_id = ?
        """, (session_id, employee_id))
        row = cursor.fetchone()
        return dict(row) if row else None

    def delete_evaluatee(self, evaluatee_id: int):
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM scores WHERE evaluatee_id = ?", (evaluatee_id,))
        cursor.execute("DELETE FROM assessment_relations WHERE evaluatee_id = ?", (evaluatee_id,))
        cursor.execute("DELETE FROM session_evaluatees WHERE evaluatee_id = ?", (evaluatee_id,))
        cursor.execute("DELETE FROM evaluatees WHERE id = ?", (evaluatee_id,))
        self.conn.commit()

    def import_evaluatees(self, data: List[Dict], session_id: int = None) -> Dict:
        if session_id is None:
            session_id = self.get_current_session_id()
        created, updated = 0, 0
        for item in data:
            emp_id = item.get('employee_id')
            name = item.get('name')
            if not emp_id or not name:
                continue
            cursor = self.conn.cursor()
            cursor.execute("SELECT id FROM evaluatees WHERE employee_id = ?", (str(emp_id),))
            existing = cursor.fetchone()
            if existing:
                evaluatee_id = existing['id']
            else:
                cursor.execute(
                    "INSERT INTO evaluatees (name, employee_id) VALUES (?, ?)",
                    (str(name), str(emp_id))
                )
                evaluatee_id = cursor.lastrowid
                created += 1

            cursor.execute(
                "SELECT id FROM session_evaluatees WHERE session_id=? AND evaluatee_id=?",
                (session_id, evaluatee_id)
            )
            se_existing = cursor.fetchone()
            if se_existing:
                updated += 1
            cursor.execute("""
                INSERT OR REPLACE INTO session_evaluatees
                (session_id, evaluatee_id, position, department, group_type, department_ref_score)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                session_id, evaluatee_id,
                item.get('position'), item.get('department'),
                item.get('group_type'), item.get('department_ref_score')
            ))
        self.conn.commit()
        return {"created": created, "updated": updated, "total": len(data)}

    # ========== 考核关系 ==========
    def add_relation(self, assessor_id: int, evaluatee_id: int,
                     weight: float = 1.0, session_id: int = None) -> int:
        if session_id is None:
            session_id = self.get_current_session_id()
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO session_assessors (session_id, assessor_id) VALUES (?, ?)",
            (session_id, assessor_id)
        )
        cursor.execute(
            "INSERT OR IGNORE INTO session_evaluatees (session_id, evaluatee_id) VALUES (?, ?)",
            (session_id, evaluatee_id)
        )
        cursor.execute(
            "INSERT INTO assessment_relations (session_id, assessor_id, evaluatee_id, weight) VALUES (?, ?, ?, ?)",
            (session_id, assessor_id, evaluatee_id, weight)
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_assessor_relations(self, assessor_id: int, session_id: int = None) -> List[Dict]:
        if session_id is None:
            session_id = self.get_current_session_id()
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT ar.*, e.name as evaluatee_name, e.employee_id as evaluatee_employee_id,
                   COALESCE(se.position, e.position) as position,
                   COALESCE(se.department, e.department) as department,
                   COALESCE(se.group_type, e.group_type) as group_type,
                   COALESCE(se.department_ref_score, e.department_ref_score) as department_ref_score
            FROM assessment_relations ar
            JOIN evaluatees e ON ar.evaluatee_id = e.id
            LEFT JOIN session_evaluatees se
                ON se.session_id = ar.session_id AND se.evaluatee_id = e.id
            WHERE ar.session_id = ? AND ar.assessor_id = ?
            ORDER BY ar.id
        """, (session_id, assessor_id))
        return [dict(row) for row in cursor.fetchall()]

    def get_all_relations(self, session_id: int = None) -> List[Dict]:
        if session_id is None:
            session_id = self.get_current_session_id()
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT ar.*, a.name as assessor_name, a.employee_id as assessor_employee_id,
                   e.name as evaluatee_name, e.employee_id as evaluatee_employee_id,
                   COALESCE(se.position, e.position) as position,
                   COALESCE(se.department, e.department) as department,
                   COALESCE(se.group_type, e.group_type) as group_type,
                   COALESCE(se.department_ref_score, e.department_ref_score) as department_ref_score
            FROM assessment_relations ar
            JOIN assessors a ON ar.assessor_id = a.id
            JOIN evaluatees e ON ar.evaluatee_id = e.id
            LEFT JOIN session_evaluatees se
                ON se.session_id = ar.session_id AND se.evaluatee_id = e.id
            WHERE ar.session_id = ?
            ORDER BY ar.id
        """, (session_id,))
        return [dict(row) for row in cursor.fetchall()]

    def delete_relation(self, relation_id: int):
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT session_id, assessor_id, evaluatee_id FROM assessment_relations WHERE id = ?",
            (relation_id,)
        )
        rel = cursor.fetchone()
        if rel:
            cursor.execute(
                "DELETE FROM scores WHERE session_id=? AND assessor_id=? AND evaluatee_id=?",
                (rel['session_id'], rel['assessor_id'], rel['evaluatee_id'])
            )
        cursor.execute("DELETE FROM assessment_relations WHERE id = ?", (relation_id,))
        self.conn.commit()

    def import_relations(self, data: List[Dict], session_id: int) -> Dict:
        created, errors = 0, []
        involved_evaluatee_ids = set()
        for item in data:
            assessor_emp = str(item.get('assessor_employee_id', '')).strip()
            evaluatee_emp = str(item.get('evaluatee_employee_id', '')).strip()
            weight = item.get('weight', 1.0)
            try:
                weight = float(weight)
            except (ValueError, TypeError):
                weight = 1.0
            if weight <= 0 or weight > 1:
                errors.append(f"权重必须在 (0, 1] 之间：{assessor_emp} → {evaluatee_emp} 权重 {weight}")
                continue

            cursor = self.conn.cursor()
            cursor.execute("SELECT id FROM assessors WHERE employee_id = ?", (assessor_emp,))
            assessor = cursor.fetchone()
            cursor.execute("SELECT id FROM evaluatees WHERE employee_id = ?", (evaluatee_emp,))
            evaluatee = cursor.fetchone()

            if not assessor:
                errors.append(f"考核人工号 {assessor_emp} 不存在")
                continue
            if not evaluatee:
                errors.append(f"被考核人工号 {evaluatee_emp} 不存在")
                continue

            # 确保考核人和被考核人在当前 session 的属性表中存在
            cursor.execute(
                "SELECT id FROM session_assessors WHERE session_id=? AND assessor_id=?",
                (session_id, assessor['id'])
            )
            if not cursor.fetchone():
                cursor.execute(
                    "INSERT OR IGNORE INTO session_assessors (session_id, assessor_id) VALUES (?, ?)",
                    (session_id, assessor['id'])
                )
            cursor.execute(
                "SELECT id FROM session_evaluatees WHERE session_id=? AND evaluatee_id=?",
                (session_id, evaluatee['id'])
            )
            if not cursor.fetchone():
                cursor.execute(
                    "INSERT OR IGNORE INTO session_evaluatees (session_id, evaluatee_id) VALUES (?, ?)",
                    (session_id, evaluatee['id'])
                )

            cursor.execute(
                "SELECT id FROM assessment_relations WHERE session_id=? AND assessor_id=? AND evaluatee_id=?",
                (session_id, assessor['id'], evaluatee['id'])
            )
            existing = cursor.fetchone()
            if existing:
                cursor.execute(
                    "UPDATE assessment_relations SET weight=? WHERE id=?",
                    (weight, existing['id'])
                )
            else:
                cursor.execute(
                    "INSERT INTO assessment_relations (session_id, assessor_id, evaluatee_id, weight) VALUES (?,?,?,?)",
                    (session_id, assessor['id'], evaluatee['id'], weight)
                )
                created += 1
            involved_evaluatee_ids.add(evaluatee['id'])
        self.conn.commit()

        # 导入后校验涉及被考核人的权重合计（警告不阻塞，支持分批导入逐步补齐）
        warnings = []
        if involved_evaluatee_ids:
            cursor = self.conn.cursor()
            placeholders = ",".join("?" for _ in involved_evaluatee_ids)
            cursor.execute(f"""
                SELECT e.name as evaluatee_name, SUM(ar.weight) as weight_sum,
                       GROUP_CONCAT(a.name || ':' || ROUND(ar.weight, 4), ', ') as detail
                FROM assessment_relations ar
                JOIN evaluatees e ON ar.evaluatee_id = e.id
                JOIN assessors a ON ar.assessor_id = a.id
                WHERE ar.session_id = ? AND ar.evaluatee_id IN ({placeholders})
                GROUP BY ar.evaluatee_id
                HAVING ABS(SUM(ar.weight) - 1.0) > ?
            """, (session_id, *involved_evaluatee_ids, self.WEIGHT_TOLERANCE))
            for row in cursor.fetchall():
                warnings.append(
                    f"被考核人「{row['evaluatee_name']}」的考核人权重合计为 {round(row['weight_sum'], 4)}（应为1），"
                    f"当前各权重: {row['detail']}"
                )
        return {"created": created, "errors": errors, "warnings": warnings, "total": len(data)}

    # ========== 指标 ==========
    def add_indicator(self, name: str, weight: float = 1.0,
                      max_score: float = 100) -> int:
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO indicators (name, weight, max_score) VALUES (?, ?, ?)",
            (name, weight, max_score)
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_all_indicators(self) -> List[Dict]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM indicators ORDER BY id")
        return [dict(row) for row in cursor.fetchall()]

    def delete_indicator(self, indicator_id: int):
        cursor = self.conn.cursor()
        # 先清理引用该指标的所有打分及历史归档，再删除指标本身
        cursor.execute("DELETE FROM scores WHERE indicator_id = ?", (indicator_id,))
        cursor.execute("DELETE FROM score_history WHERE indicator_id = ?", (indicator_id,))
        cursor.execute("DELETE FROM indicators WHERE id = ?", (indicator_id,))
        self.conn.commit()

    def update_indicator(self, indicator_id: int, name: str = None, weight: float = None, max_score: float = None):
        cursor = self.conn.cursor()
        fields = []
        params = []
        if name is not None:
            fields.append("name = ?")
            params.append(name)
        if weight is not None:
            fields.append("weight = ?")
            params.append(weight)
        if max_score is not None:
            fields.append("max_score = ?")
            params.append(max_score)
        if not fields:
            return
        params.append(indicator_id)
        cursor.execute(f"UPDATE indicators SET {', '.join(fields)} WHERE id = ?", params)
        self.conn.commit()

    # ========== 打分 ==========
    def save_score(self, assessor_id: int, evaluatee_id: int, indicator_id: int,
                   score: float, session_id: int = None) -> int:
        if session_id is None:
            session_id = self.get_current_session_id()
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT id FROM scores
            WHERE session_id=? AND assessor_id=? AND evaluatee_id=? AND indicator_id=?
        """, (session_id, assessor_id, evaluatee_id, indicator_id))
        existing = cursor.fetchone()

        if existing:
            cursor.execute("""
                UPDATE scores SET score=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
            """, (score, existing['id']))
            score_id = existing['id']
        else:
            cursor.execute("""
                INSERT INTO scores (session_id, assessor_id, evaluatee_id, indicator_id, score)
                VALUES (?, ?, ?, ?, ?)
            """, (session_id, assessor_id, evaluatee_id, indicator_id, score))
            score_id = cursor.lastrowid
        self.conn.commit()
        return score_id

    def get_assessor_scores(self, assessor_id: int, session_id: int = None) -> List[Dict]:
        if session_id is None:
            session_id = self.get_current_session_id()
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT s.*, e.name as evaluatee_name, e.employee_id as evaluatee_employee_id,
                   COALESCE(se.position, e.position) as position,
                   COALESCE(se.department, e.department) as department,
                   COALESCE(se.group_type, e.group_type) as group_type,
                   COALESCE(se.department_ref_score, e.department_ref_score) as department_ref_score,
                   i.name as indicator_name, i.weight as indicator_weight, i.max_score
            FROM scores s
            JOIN evaluatees e ON s.evaluatee_id = e.id
            LEFT JOIN session_evaluatees se
                ON se.session_id = s.session_id AND se.evaluatee_id = e.id
            JOIN indicators i ON s.indicator_id = i.id
            WHERE s.session_id=? AND s.assessor_id=?
            ORDER BY s.evaluatee_id, s.indicator_id
        """, (session_id, assessor_id))
        return [dict(row) for row in cursor.fetchall()]

    def get_score_map(self, assessor_id: int, session_id: int = None) -> Dict:
        if session_id is None:
            session_id = self.get_current_session_id()
        scores = self.get_assessor_scores(assessor_id, session_id)
        score_map = {}
        for s in scores:
            eval_id = s['evaluatee_id']
            ind_id = s['indicator_id']
            if eval_id not in score_map:
                score_map[eval_id] = {}
            score_map[eval_id][ind_id] = s['score']
        return score_map

    def mark_all_submitted(self, assessor_id: int, session_id: int = None):
        if session_id is None:
            session_id = self.get_current_session_id()
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE scores SET submitted=1 WHERE session_id=? AND assessor_id=?",
            (session_id, assessor_id)
        )
        self.conn.commit()

    def is_assessor_completed(self, assessor_id: int, session_id: int = None) -> bool:
        if session_id is None:
            session_id = self.get_current_session_id()
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) as cnt FROM scores WHERE session_id=? AND assessor_id=? AND submitted=1",
            (session_id, assessor_id)
        )
        return cursor.fetchone()['cnt'] > 0

    def check_all_scores_complete(self, assessor_id: int, session_id: int = None) -> Dict:
        if session_id is None:
            session_id = self.get_current_session_id()
        relations = self.get_assessor_relations(assessor_id, session_id)
        indicators = self.get_all_indicators()
        score_map = self.get_score_map(assessor_id, session_id)

        missing = []
        for rel in relations:
            eval_id = rel['evaluatee_id']
            eval_name = rel['evaluatee_name']
            for ind in indicators:
                ind_id = ind['id']
                val = score_map.get(eval_id, {}).get(ind_id)
                if val is None:
                    missing.append({
                        "evaluatee_name": eval_name,
                        "indicator_name": ind['name']
                    })

        return {"complete": len(missing) == 0, "missing": missing}

    def get_assessment_summary(self, assessor_id: int, session_id: int = None) -> Dict:
        if session_id is None:
            session_id = self.get_current_session_id()
        scores = self.get_assessor_scores(assessor_id, session_id)
        completed = self.is_assessor_completed(assessor_id, session_id)

        evaluatee_scores = {}
        for s in scores:
            eval_id = s['evaluatee_id']
            if eval_id not in evaluatee_scores:
                evaluatee_scores[eval_id] = {
                    'name': s['evaluatee_name'],
                    'total_score': 0,
                    'indicator_count': 0
                }
            evaluatee_scores[eval_id]['total_score'] += s['score'] * s['indicator_weight']
            evaluatee_scores[eval_id]['indicator_count'] += 1

        return {
            'total_scores': len(scores),
            'completed': completed,
            'evaluatees': list(evaluatee_scores.values())
        }

    # ========== 统计 ==========
    def get_all_results(self, session_id: int = None) -> List[Dict]:
        if session_id is None:
            session_id = self.get_current_session_id()
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT
                e.id as evaluatee_id, e.name as evaluatee_name,
                COALESCE(se.position, e.position) as position,
                COALESCE(se.department, e.department) as department,
                COALESCE(se.group_type, e.group_type) as group_type,
                e.employee_id as evaluatee_employee_id,
                a.name as assessor_name, a.id as assessor_id,
                a.employee_id as assessor_employee_id,
                ar.weight as assessor_weight,
                i.name as indicator_name,
                i.weight as indicator_weight, i.max_score,
                s.score, s.submitted
            FROM scores s
            JOIN evaluatees e ON s.evaluatee_id = e.id
            JOIN assessors a ON s.assessor_id = a.id
            JOIN assessment_relations ar ON ar.assessor_id = a.id
                AND ar.evaluatee_id = e.id AND ar.session_id = s.session_id
            LEFT JOIN session_evaluatees se
                ON se.session_id = s.session_id AND se.evaluatee_id = e.id
            JOIN indicators i ON s.indicator_id = i.id
            WHERE s.score IS NOT NULL AND s.session_id = ?
            ORDER BY e.id, a.id, i.id
        """, (session_id,))
        return [dict(row) for row in cursor.fetchall()]

    def get_final_scores(self, session_id: int = None) -> List[Dict]:
        if session_id is None:
            session_id = self.get_current_session_id()
        all_results = self.get_all_results(session_id)

        evaluatee_map = {}
        for r in all_results:
            eid = r['evaluatee_id']
            if eid not in evaluatee_map:
                evaluatee_map[eid] = {
                    'evaluatee_id': eid,
                    'evaluatee_name': r['evaluatee_name'],
                    'evaluatee_employee_id': r.get('evaluatee_employee_id'),
                    'position': r['position'],
                    'department': r['department'],
                    'group_type': r['group_type'],
                    'assessors': {},
                    'final_score': 0
                }
            aid = r['assessor_id']
            if aid not in evaluatee_map[eid]['assessors']:
                evaluatee_map[eid]['assessors'][aid] = {
                    'assessor_name': r['assessor_name'],
                    'assessor_employee_id': r.get('assessor_employee_id'),
                    'assessor_weight': r['assessor_weight'],
                    'indicator_scores': {},
                    'weighted_total': 0
                }

            ind_weight = r['indicator_weight']
            score = r['score']
            evaluatee_map[eid]['assessors'][aid]['indicator_scores'][r['indicator_name']] = {
                'score': score,
                'indicator_weight': ind_weight
            }
            evaluatee_map[eid]['assessors'][aid]['weighted_total'] += score * ind_weight

        results = []
        for eid, data in evaluatee_map.items():
            final = 0
            for aid, adata in data['assessors'].items():
                final += adata['weighted_total'] * adata['assessor_weight']
            data['final_score'] = round(final, 2)
            results.append(data)

        results.sort(key=lambda x: x['final_score'], reverse=True)
        return results

    # ========== 考核进度 ==========
    def get_all_progress(self, session_id: int = None) -> List[Dict]:
        if session_id is None:
            session_id = self.get_current_session_id()
        cursor = self.conn.cursor()

        cursor.execute("""
            SELECT DISTINCT a.id, a.name, a.employee_id
            FROM assessors a
            JOIN assessment_relations ar ON ar.assessor_id = a.id
            WHERE ar.session_id = ?
            ORDER BY a.name
        """, (session_id,))
        assessors = [dict(row) for row in cursor.fetchall()]

        results = []
        for a in assessors:
            cursor.execute(
                "SELECT COUNT(*) as cnt FROM assessment_relations WHERE session_id=? AND assessor_id=?",
                (session_id, a['id'])
            )
            total_evals = cursor.fetchone()['cnt']

            cursor.execute(
                "SELECT COUNT(*) as cnt FROM scores WHERE session_id=? AND assessor_id=? AND submitted=1",
                (session_id, a['id'])
            )
            submitted_count = cursor.fetchone()['cnt']

            cursor.execute(
                "SELECT COUNT(DISTINCT evaluatee_id) as cnt FROM scores WHERE session_id=? AND assessor_id=?",
                (session_id, a['id'])
            )
            scored_count = cursor.fetchone()['cnt']

            if submitted_count > 0:
                status = "已完成"
            elif scored_count > 0:
                status = "进行中"
            else:
                status = "未开始"

            reset_count = self.get_assessor_reset_count(a['id'], session_id)

            results.append({
                "assessor_id": a['id'],
                "assessor_name": a['name'],
                "employee_id": a.get('employee_id'),
                "total_evaluatees": total_evals,
                "scored_evaluatees": scored_count,
                "status": status,
                "reset_count": reset_count
            })

        return results

    def get_session_stats(self, session_id: int = None) -> Dict:
        if session_id is None:
            session_id = self.get_current_session_id()
        progress = self.get_all_progress(session_id)
        total = len(progress)
        completed = sum(1 for p in progress if p['status'] == '已完成')
        in_progress = sum(1 for p in progress if p['status'] == '进行中')
        not_started = sum(1 for p in progress if p['status'] == '未开始')
        return {
            "total": total,
            "completed": completed,
            "in_progress": in_progress,
            "not_started": not_started
        }

    def close(self):
        self.conn.close()


# 全局数据库实例
_db_instance = None


def get_db() -> Database:
    global _db_instance
    if _db_instance is None:
        _db_instance = Database()
    return _db_instance
