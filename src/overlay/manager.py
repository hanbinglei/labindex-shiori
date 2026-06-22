"""
LabIndex Shiori — Overlay 人工纠错管理器

职责:
  - 写入 overlay_corrections(字段级修正)
  - 写入 overlay_exclusions(排除文件)
  - 写入 overlay_relations(关联关系)
  - 展示当前 overlay 状态
  - 修改后标记文件待重新索引

只读保障: 所有操作仅写本地 SQLite,不碰 NAS
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from src.config import AppConfig

logger = logging.getLogger(__name__)

# 允许人工修正的字段
ALLOWED_FIELDS = {"title", "keywords", "year", "subtopic", "researcher"}

# 允许的关联类型
ALLOWED_RELATION_TYPES = {"related", "cites", "cited_by"}


class OverlayManager:
    """
    Overlay 人工纠错管理器

    用法:
        config = AppConfig.load()
        mgr = OverlayManager(config)

        # 修正标题
        mgr.correct(42, 'title', '新しいタイトル')

        # 排除文件
        mgr.exclude(42, '重複ファイル')

        # 添加关联
        mgr.add_relation(42, 43, 'related')
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self._conn: Optional[sqlite3.Connection] = None

    # ============================================================
    # 公开接口
    # ============================================================

    def correct(self, file_id: int, field: str, value: str) -> Dict:
        """
        修正指定文件的指定字段

        流程:
          1. 验证 file_id 存在
          2. 验证 field 在允许列表中
          3. 获取原始值（记录用）
          4. UPSERT 到 overlay_corrections
          5. 标记该文件需要重新索引（last_parsed = NULL）

        Args:
            file_id: auto_files.id
            field: 字段名 (title/keywords/year/subtopic)
            value: 修正后的值
                   keywords 传 JSON 字符串数组,如 '["kw1","kw2"]'

        Returns:
            {"status": "ok", "field": field, "file_id": file_id, ...}
        """
        self._connect()

        # 验证 file_id
        file_row = self._conn.execute(
            "SELECT id, filename, path FROM auto_files WHERE id=? AND status='active'",
            (file_id,),
        ).fetchone()
        if not file_row:
            self._close()
            return {"status": "error", "message": f"文件 ID {file_id} 不存在或非 active 状态"}

        # 验证 field
        if field not in ALLOWED_FIELDS:
            self._close()
            return {
                "status": "error",
                "message": f"不支持的字段: {field}。允许: {', '.join(sorted(ALLOWED_FIELDS))}",
            }

        # keywords 需要 JSON 验证
        if field == "keywords":
            try:
                parsed = json.loads(value)
                if not isinstance(parsed, list):
                    raise ValueError
                value = json.dumps(parsed, ensure_ascii=False)
            except (json.JSONDecodeError, ValueError):
                self._close()
                return {"status": "error", "message": "keywords 必须是 JSON 字符串数组，如 '[\"kw1\",\"kw2\"]'"}

        # year 需要整数验证
        if field == "year":
            try:
                int(value)
            except ValueError:
                self._close()
                return {"status": "error", "message": f"year 必须是整数，收到: {value}"}

        # 获取原始值
        original = self._conn.execute(
            "SELECT title, keywords, year, subtopic FROM auto_metadata WHERE file_id=?",
            (file_id,),
        ).fetchone()

        original_value = None
        if original:
            if field == "keywords":
                original_value = original["keywords"]
            elif field == "year":
                original_value = str(original["year"]) if original["year"] else None
            elif field == "title":
                original_value = original["title"]
            elif field == "subtopic":
                original_value = original["subtopic"]

        now = datetime.now().isoformat()

        # UPSERT
        self._conn.execute(
            """
            INSERT INTO overlay_corrections
                (file_id, field_name, corrected_value, original_value, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_id, field_name) DO UPDATE SET
                corrected_value=excluded.corrected_value,
                original_value=excluded.original_value,
                updated_at=excluded.updated_at
            """,
            (file_id, field, value, original_value, now, now),
        )

        # 标记需要重新索引
        self._mark_dirty(file_id)

        self._conn.commit()
        self._close()

        return {
            "status": "ok",
            "file_id": file_id,
            "field": field,
            "value": value,
            "original": original_value,
            "updated_at": now,
        }

    def exclude(self, file_id: int, reason: str = "") -> Dict:
        """
        将文件从检索结果中排除

        Args:
            file_id: auto_files.id
            reason: 排除原因（可选）

        Returns:
            {"status": "ok", "file_id": file_id, ...}
        """
        self._connect()

        file_row = self._conn.execute(
            "SELECT id, filename FROM auto_files WHERE id=?", (file_id,)
        ).fetchone()
        if not file_row:
            self._close()
            return {"status": "error", "message": f"文件 ID {file_id} 不存在"}

        self._conn.execute(
            "INSERT OR IGNORE INTO overlay_exclusions (file_id, reason) VALUES (?, ?)",
            (file_id, reason),
        )

        self._mark_dirty(file_id)
        self._conn.commit()
        self._close()

        return {"status": "ok", "file_id": file_id, "action": "excluded", "reason": reason}

    def include(self, file_id: int) -> Dict:
        """
        将被排除的文件恢复到检索中

        Args:
            file_id: auto_files.id

        Returns:
            {"status": "ok", "file_id": file_id, ...}
        """
        self._connect()

        self._conn.execute(
            "DELETE FROM overlay_exclusions WHERE file_id=?", (file_id,)
        )

        self._mark_dirty(file_id)
        self._conn.commit()
        self._close()

        return {"status": "ok", "file_id": file_id, "action": "included"}

    def add_relation(self, file_id_a: int, file_id_b: int,
                     relation_type: str = "related", note: str = "") -> Dict:
        """
        添加文件间关联关系

        Args:
            file_id_a: 第一个文件 ID
            file_id_b: 第二个文件 ID
            relation_type: 关联类型 (related/cites/cited_by)
            note: 备注（可选）

        Returns:
            {"status": "ok", "relation_id": id, ...}
        """
        self._connect()

        if relation_type not in ALLOWED_RELATION_TYPES:
            self._close()
            return {
                "status": "error",
                "message": f"不支持的关联类型: {relation_type}。允许: {', '.join(sorted(ALLOWED_RELATION_TYPES))}",
            }

        # 确保 a < b（schema 约束）
        if file_id_a > file_id_b:
            file_id_a, file_id_b = file_id_b, file_id_a

        try:
            cur = self._conn.execute(
                """
                INSERT INTO overlay_relations
                    (file_id_a, file_id_b, relation_type, note)
                VALUES (?, ?, ?, ?)
                """,
                (file_id_a, file_id_b, relation_type, note),
            )
            relation_id = cur.lastrowid
            self._mark_dirty(file_id_a)
            self._mark_dirty(file_id_b)
            self._conn.commit()
            self._close()
            return {"status": "ok", "relation_id": relation_id, "type": relation_type}
        except sqlite3.IntegrityError:
            self._close()
            return {"status": "error", "message": "该关联关系已存在"}

    def remove_relation(self, relation_id: int) -> Dict:
        """删除关联关系"""
        self._connect()
        cur = self._conn.execute(
            "DELETE FROM overlay_relations WHERE id=?", (relation_id,)
        )
        affected = cur.rowcount
        self._conn.commit()
        self._close()
        if affected > 0:
            return {"status": "ok", "deleted": True}
        return {"status": "error", "message": f"关联 ID {relation_id} 不存在"}

    def show(self, file_id: int) -> Dict:
        """
        展示指定文件的所有 overlay 状态
        包括: corrections, exclusions, relations
        """
        self._connect()
        conn = self._conn
        conn.row_factory = sqlite3.Row

        # 文件信息
        file_info = conn.execute(
            "SELECT id, filename, path FROM auto_files WHERE id=?",
            (file_id,),
        ).fetchone()

        if not file_info:
            self._close()
            return {"status": "error", "message": f"文件 ID {file_id} 不存在"}

        # overlay_corrections
        corrections = []
        for row in conn.execute(
            "SELECT field_name, corrected_value, original_value, created_at, updated_at "
            "FROM overlay_corrections WHERE file_id=? ORDER BY field_name",
            (file_id,),
        ).fetchall():
            corrections.append({
                "field": row["field_name"],
                "corrected": row["corrected_value"],
                "original": row["original_value"],
                "created": row["created_at"],
                "updated": row["updated_at"],
            })

        # overlay_exclusions
        excluded = conn.execute(
            "SELECT reason, created_at FROM overlay_exclusions WHERE file_id=?",
            (file_id,),
        ).fetchone()

        # overlay_relations
        relations = []
        for row in conn.execute(
            """
            SELECT r.id, r.file_id_a, r.file_id_b, r.relation_type, r.note, r.created_at
            FROM overlay_relations r
            WHERE r.file_id_a=? OR r.file_id_b=?
            ORDER BY r.id
            """,
            (file_id, file_id),
        ).fetchall():
            relations.append({
                "id": row["id"],
                "with_file": row["file_id_b"] if row["file_id_a"] == file_id else row["file_id_a"],
                "type": row["relation_type"],
                "note": row["note"],
                "created": row["created_at"],
            })

        self._close()

        return {
            "status": "ok",
            "file": {
                "id": file_info["id"],
                "filename": file_info["filename"],
                "path": file_info["path"],
            },
            "corrections": corrections,
            "excluded": bool(excluded),
            "exclude_reason": excluded["reason"] if excluded else None,
            "relations": relations,
        }

    def status(self) -> Dict:
        """展示全局 overlay 统计"""
        self._connect()
        conn = self._conn

        total_corrections = conn.execute(
            "SELECT COUNT(*) FROM overlay_corrections"
        ).fetchone()[0]

        total_exclusions = conn.execute(
            "SELECT COUNT(*) FROM overlay_exclusions"
        ).fetchone()[0]

        total_relations = conn.execute(
            "SELECT COUNT(*) FROM overlay_relations"
        ).fetchone()[0]

        # 按字段统计
        fields = conn.execute(
            "SELECT field_name, COUNT(*) FROM overlay_corrections GROUP BY field_name ORDER BY COUNT(*) DESC"
        ).fetchall()

        self._close()
        return {
            "total_corrections": total_corrections,
            "total_exclusions": total_exclusions,
            "total_relations": total_relations,
            "by_field": {row[0]: row[1] for row in fields},
        }

    # ============================================================
    # 内部
    # ============================================================

    def _connect(self):
        self._conn = sqlite3.connect(self.config.database.path)
        self._conn.row_factory = sqlite3.Row

    def _close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def _mark_dirty(self, file_id: int):
        """标记文件需要重新索引/解析"""
        self._conn.execute(
            "UPDATE auto_files SET last_parsed=NULL WHERE id=?",
            (file_id,),
        )
