"""
LabIndex Shiori — Meilisearch 索引引擎

职责:
  - 从 SQLite 读取「auto + overlay 合并后」的文档
  - overlay_exclusions 排除的文件不进索引
  - 推入 Meilisearch，配置日语分词和可筛选字段
  - 增量友好：只推变更/新增，已删除的从索引移除
  - fail-safe：Meilisearch 未启动时优雅报错
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Dict, List, Optional

import meilisearch
from meilisearch.errors import MeilisearchCommunicationError

from src.config import AppConfig

logger = logging.getLogger(__name__)

# Meilisearch 索引 UID
_INDEX_UID = "labindex_shiori"


class IndexerEngine:
    """
    索引引擎

    用法:
        config = AppConfig.load()
        engine = IndexerEngine(config)
        engine.index_all()          # 全量重建索引
        engine.index_incremental()  # 增量更新
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self._client: Optional[meilisearch.Client] = None
        self._stats = {
            "pushed": 0,
            "updated": 0,
            "deleted": 0,
            "skipped": 0,
            "errors": 0,
        }
        self._start_time = 0.0

    # ============================================================
    # 公开接口
    # ============================================================

    def index_all(self) -> Dict:
        """
        全量重建索引：
        1. 删除旧索引
        2. 创建新索引并配置设置
        3. 读取所有文档并推送
        """
        self._reset()
        self._print("🔨 全量索引重建开始")
        return self._run_index(recreate=True)

    def index_incremental(self) -> Dict:
        """
        增量索引：
        只推送新增/变更的文档，移除已删除的
        """
        self._reset()
        self._print("🔨 增量索引开始")
        return self._run_index(recreate=False)

    # ============================================================
    # 内部
    # ============================================================

    def _reset(self):
        self._stats = {"pushed": 0, "updated": 0, "deleted": 0, "skipped": 0, "errors": 0}
        self._start_time = time.time()

    def _get_client(self) -> meilisearch.Client:
        """获取 Meilisearch 客户端（带连接检查）"""
        if self._client is not None:
            return self._client

        ms_config = self.config.meilisearch
        try:
            client = meilisearch.Client(ms_config.url, ms_config.api_key)
            # 验证连接
            client.health()
            self._client = client
            return client
        except MeilisearchCommunicationError as e:
            raise ConnectionError(
                f"无法连接到 Meilisearch ({ms_config.url})。\n"
                f"请先运行 Start.bat 启动 Meilisearch 服务，然后重试。"
            ) from e

    def _run_index(self, recreate: bool):
        """索引主循环"""
        try:
            client = self._get_client()

            # 全量模式：删除重建
            if recreate:
                try:
                    client.delete_index(_INDEX_UID)
                except Exception:
                    pass  # 索引不存在没关系
                client.create_index(_INDEX_UID, {"primaryKey": "id"})

            # 配置索引设置
            self._configure_index(client)

            # 构建文档
            documents = self._build_documents()

            if not documents:
                self._print("  没有可索引的文档")
                return self._stats

            # 推送文档
            self._print(f"  推送 {len(documents)} 篇文档到 Meilisearch...")
            result = client.index(_INDEX_UID).add_documents(documents)
            task_uid = result.task_uid if hasattr(result, 'task_uid') else None
            self._print(f"  任务 ID: {task_uid or 'N/A'}")

            # 等待索引完成
            if task_uid:
                self._wait_for_task(client, task_uid)
            self._stats["pushed"] = len(documents)

            # 处理删除
            if not recreate:
                self._remove_deleted(client)

            self._print_summary()

        except ConnectionError as e:
            self._print(f"❌ {e}", err=True)
            raise
        except Exception as e:
            self._print(f"❌ 索引失败: {e}", err=True)
            raise

        return self._stats

    def _configure_index(self, client: meilisearch.Client):
        """
        配置 Meilisearch 索引

        - filterableAttributes: year, subtopic, extension（供 UI 筛选）
        - searchableAttributes: 标题、关键词、摘要、文件名
        - 日语分词：设置 localizedAttributes 策略
        """
        index = client.index(_INDEX_UID)

        # 可检索字段（权重顺序）
        index.update_searchable_attributes([
            "title",
            "keywords",
            "abstract",
            "authors",
            "researcher",        # M9: 研究者可检索
            "filename",
        ])

        # 可筛选字段（M8 新增 academic_year / degree / doc_type）
        index.update_filterable_attributes([
            "year",
            "academic_year",
            "degree",
            "doc_type",
            "researcher",       # M9: 研究者筛选
            "subtopic",
            "extension",
        ])

        # 排序字段
        index.update_sortable_attributes([
            "year",
            "academic_year",
        ])

        # 日语分词：设置 localizedAttributes 策略
        # 这将启用 Charabia 的日语 tokenizer
        try:
            index.update_localized_attributes([
                {"locale": "ja", "attributePatterns": ["title", "keywords", "abstract"]},
                {"locale": "zh", "attributePatterns": ["title", "keywords", "abstract"]},
            ])
        except Exception as e:
            logger.info("localizedAttributes 设置跳过（可能不受当前版本支持）: %s", e)

        self._print("  ✅ 索引配置完成（可检索/可筛选/日语分词）")

    def _build_documents(self) -> List[Dict]:
        """
        从 SQLite 构建合并 overlay 后的文档列表

        SQLite 是唯一事实源。此方法实现 overlay 优先合并：
          - title: COALESCE(overlay_corrections.title, auto_metadata.title)
          - 排除 overlay_exclusions 中的文件
        """
        import sqlite3

        conn = sqlite3.connect(self.config.database.path)
        conn.row_factory = sqlite3.Row

        rows = conn.execute("""
            SELECT
                f.id,
                f.filename,
                f.extension,
                f.path,
                COALESCE(oc_title.corrected_value, m.title) AS title,
                CASE
                    WHEN oc_title.corrected_value IS NOT NULL THEN 'overlay'
                    ELSE m.title_source
                END AS title_source,
                COALESCE(oc_keywords.corrected_value, m.keywords) AS keywords,
                CASE
                    WHEN oc_keywords.corrected_value IS NOT NULL THEN 'overlay'
                    ELSE m.keywords_source
                END AS keywords_source,
                COALESCE(oc_year.corrected_value, m.year) AS year,
                COALESCE(oc_subtopic.corrected_value, m.subtopic) AS subtopic,
                m.abstract,
                m.authors,
                m.extra_metadata,
                m.academic_year,
                m.degree,
                m.doc_type,
                m.researcher               -- M9: 研究者维度
            FROM auto_files f
            JOIN auto_metadata m ON f.id = m.file_id
            LEFT JOIN overlay_corrections oc_title
                ON oc_title.file_id = f.id AND oc_title.field_name = 'title'
            LEFT JOIN overlay_corrections oc_keywords
                ON oc_keywords.file_id = f.id AND oc_keywords.field_name = 'keywords'
            LEFT JOIN overlay_corrections oc_year
                ON oc_year.file_id = f.id AND oc_year.field_name = 'year'
            LEFT JOIN overlay_corrections oc_subtopic
                ON oc_subtopic.file_id = f.id AND oc_subtopic.field_name = 'subtopic'
            WHERE f.status = 'active'
              AND f.id NOT IN (SELECT file_id FROM overlay_exclusions)
            ORDER BY f.id
        """).fetchall()

        documents = []
        for row in rows:
            doc = {
                "id": row["id"],
                "filename": row["filename"],
                "extension": row["extension"],
                "path": row["path"],
                "title": row["title"] or "",
                "title_source": row["title_source"],
                "keywords": row["keywords"] or "[]",
                "keywords_source": row["keywords_source"],
                "year": row["year"],
                "academic_year": row["academic_year"],
                "degree": row["degree"] or "",
                "doc_type": row["doc_type"] or "",
                "researcher": row["researcher"] or "",   # M9: 研究者维度
                "subtopic": row["subtopic"] or "",
                "abstract": row["abstract"] or "",
            }

            # 解析 keywords JSON → 字符串（Meilisearch 可检索）
            try:
                kw_list = json.loads(doc["keywords"]) if doc["keywords"] else []
                doc["keywords"] = " ".join(kw_list) if kw_list else ""
            except (json.JSONDecodeError, TypeError):
                doc["keywords"] = ""

            # 解析 authors JSON → 字符串
            if row["authors"]:
                try:
                    authors_list = json.loads(row["authors"])
                    doc["authors"] = " ".join(authors_list) if isinstance(authors_list, list) else row["authors"]
                except (json.JSONDecodeError, TypeError):
                    doc["authors"] = row["authors"] or ""
            else:
                doc["authors"] = ""

            # 解析 extra_metadata（获取 note 等信息）
            if row["extra_metadata"]:
                try:
                    extra = json.loads(row["extra_metadata"])
                    if extra.get("note"):
                        doc["note"] = extra["note"]
                except (json.JSONDecodeError, TypeError):
                    pass

            documents.append(doc)

        conn.close()
        return documents

    def _remove_deleted(self, client: meilisearch.Client):
        """
        从 Meilisearch 移除：
        - status='deleted' 的文件
        - 被 overlay_exclusions 排除的文件
        """
        import sqlite3

        conn = sqlite3.connect(self.config.database.path)
        conn.row_factory = sqlite3.Row

        # 1. 已删除的文件
        deleted = conn.execute(
            "SELECT id FROM auto_files WHERE status='deleted'"
        ).fetchall()

        # 2. 被排除的文件
        excluded = conn.execute(
            "SELECT file_id FROM overlay_exclusions"
        ).fetchall()

        conn.close()

        ids_to_remove = set()
        for r in deleted:
            ids_to_remove.add(r["id"])
        for r in excluded:
            ids_to_remove.add(r["file_id"])

        if ids_to_remove:
            client.index(_INDEX_UID).delete_documents(list(ids_to_remove))
            self._stats["deleted"] = len(ids_to_remove)
            self._print(f"  🗑️ 从索引移除 {len(ids_to_remove)} 篇文档")

    @staticmethod
    def _wait_for_task(client: meilisearch.Client, task_uid):
        """等待 Meilisearch 任务完成"""
        if not task_uid:
            return
        for _ in range(30):
            try:
                task = client.get_task(task_uid)
                status = task.status if hasattr(task, 'status') else str(task)
                if status == "succeeded":
                    return
                if status == "failed":
                    error = task.error if hasattr(task, 'error') else "unknown"
                    logger.warning("Meilisearch 任务失败: %s", error)
                    return
            except Exception:
                pass
            time.sleep(0.5)

    # ============================================================
    # 输出
    # ============================================================

    def _print(self, msg: str, err: bool = False):
        fp = __import__("sys").stderr if err else __import__("sys").stdout
        print(msg, file=fp, flush=True)

    def _print_summary(self):
        elapsed = time.time() - self._start_time
        s = self._stats
        self._print("")
        self._print("=" * 50)
        self._print("📊 索引完成")
        self._print(f"   推送:   {s['pushed']}")
        self._print(f"   更新:   {s['updated']}")
        self._print(f"   删除:   {s['deleted']}")
        self._print(f"   错误:   {s['errors']}")
        self._print(f"   耗时:   {elapsed:.1f}s")
        self._print("=" * 50)
