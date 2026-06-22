"""
LabIndex Shiori — 解析编排引擎

职责:
  - 查询需要解析的文件（auto_files.last_parsed IS NULL）
  - 按扩展名分发到对应解析器
  - 超时控制（parser.timeout_per_file）
  - fail-safe：单文件失败跳过，不中断整体
  - UPSERT 结果到 auto_metadata
  - 更新 auto_files.last_parsed
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional

from src.config import AppConfig

logger = logging.getLogger(__name__)

# -----------------------------------------------------------
# 解析器分发表
# M7: 移除 xlsx/cad，新增 pptx
# -----------------------------------------------------------
_PARSER_DISPATCH = {
    ".pdf":  "src.parser.pdf_parser",
    ".docx": "src.parser.docx_parser",
    ".pptx": "src.parser.pptx_parser",
}


def _import_parser(ext: str):
    """延迟导入对应解析器模块"""
    module_path = _PARSER_DISPATCH.get(ext)
    if module_path is None:
        raise ValueError(f"不支持的扩展名: {ext}")
    import importlib
    mod = importlib.import_module(module_path)

    # 每个模块暴露 parse_<ext_short>(file_path) 函数
    func_name = {
        ".pdf": "parse_pdf",
        ".docx": "parse_docx",
        ".pptx": "parse_pptx",
    }[ext]
    return getattr(mod, func_name)


class ParserEngine:
    """
    解析编排引擎

    用法:
        config = AppConfig.load()
        engine = ParserEngine(config)
        engine.parse_pending()   # 仅解析待处理文件
        engine.parse_all()       # 强制重新解析全部
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self._conn: Optional[sqlite3.Connection] = None
        self._stats = {
            "parsed": 0,
            "skipped": 0,
            "errors": 0,
            "unchanged": 0,
        }
        self._start_time = 0.0
        # M8: 路径解析器（延迟初始化）
        self._path_parser = None

    # ============================================================
    # 公开接口
    # ============================================================

    def parse_pending(self) -> Dict:
        """
        解析所有待处理文件（last_parsed IS NULL）
        仅处理新增/变更文件
        """
        self._reset()
        self._print("📄 解析开始（仅新增/变更文件）")
        self._run_parse(force=False)
        self._print_summary()
        return self._stats

    def parse_all(self) -> Dict:
        """
        强制重新解析所有 active 文件
        """
        self._reset()
        self._print("📄 强制重新解析所有文件")
        self._run_parse(force=True)
        self._print_summary()
        return self._stats

    # ============================================================
    # 内部
    # ============================================================

    def _reset(self):
        self._stats = {"parsed": 0, "skipped": 0, "errors": 0, "unchanged": 0}
        self._start_time = time.time()

    def _run_parse(self, force: bool = False):
        """解析主循环"""
        db_path = self.config.database.path
        self._conn = sqlite3.connect(db_path, timeout=60)
        self._conn.row_factory = sqlite3.Row

        try:
            # 获取需要解析的文件列表
            files = self._get_files_to_parse(force)

            if not files:
                self._print("  没有需要解析的文件")
                return

            self._print(f"  待解析文件: {len(files)} 个")

            for i, f in enumerate(files):
                self._parse_single_file(f, i + 1, len(files))

            self._conn.commit()

        except Exception as e:
            self._conn.rollback()
            self._print(f"❌ 解析过程异常: {e}", err=True)
            raise
        finally:
            if self._conn:
                self._conn.close()
                self._conn = None

    def _get_files_to_parse(self, force: bool) -> List[sqlite3.Row]:
        """
        获取需要解析的文件列表

        Args:
            force: True=全部文件 / False=仅 last_parsed IS NULL

        Returns:
            list of (id, path, extension) rows
        """
        if force:
            cursor = self._conn.execute(
                "SELECT id, path, extension FROM auto_files "
                "WHERE status='active' ORDER BY path"
            )
        else:
            cursor = self._conn.execute(
                "SELECT id, path, extension FROM auto_files "
                "WHERE status='active' AND last_parsed IS NULL "
                "ORDER BY path"
            )
        return cursor.fetchall()

    def _parse_single_file(self, file_row: sqlite3.Row, idx: int, total: int):
        """
        解析单个文件

        fail-safe: 所有异常只跳过文件，不中断整体
        timeout: 通过 threading 实现跨平台超时
        M8: 路径解析（不依赖文件内容）作为独立步骤，即使文件解析失败也会写入
        """
        file_id = file_row["id"]
        file_path = file_row["path"]
        ext = file_row["extension"]
        timeout = self.config.parser.timeout_per_file

        self._print(f"  [{idx}/{total}] {file_path}")

        # --- M8: 路径结构解析（不依赖文件内容，始终执行）---
        path_data = self._run_path_parsing(file_path)

        try:
            # --- 分发到对应解析器 ---
            parse_func = _import_parser(ext)

            # --- 超时控制 ---
            result = [None]
            error = [None]

            def worker():
                try:
                    # 传递 max_pages / max_slides 给对应解析器
                    if ext == ".pdf":
                        result[0] = parse_func(file_path, max_pages=self.config.parser.pdf_max_pages)
                    elif ext == ".pptx":
                        result[0] = parse_func(file_path, max_slides=self.config.parser.pptx_max_slides)
                    else:
                        result[0] = parse_func(file_path)
                except Exception as e:
                    error[0] = e

            thread = threading.Thread(target=worker, daemon=True)
            thread.start()
            thread.join(timeout)

            if thread.is_alive():
                # 超时
                logger.warning("解析超时 [%s] (%ds)", file_path, timeout)
                self._print(f"    ⚠️ 超时 ({timeout}s)，已跳过")
                self._stats["skipped"] += 1
                # 超时也写入路径数据（路径解析不依赖文件）
                self._upsert_metadata(file_id, path_data)
                self._update_last_parsed(file_id)
                return

            if error[0]:
                raise error[0]

            parsed = result[0]
            if parsed is None:
                self._print(f"    ⚠️ 解析返回空")
                self._stats["skipped"] += 1
                # 空结果也写入路径数据
                self._upsert_metadata(file_id, path_data)
                self._update_last_parsed(file_id)
                return

            # --- M8: 合并文件解析结果 + 路径解析结果 ---
            # academic_year（路径推断）与 year（文档内年份）互相独立，各自保留
            merged = dict(parsed)
            merged["academic_year"] = path_data.get("academic_year")
            # degree 和 doc_type 只有路径能有，直接写入
            merged["degree"] = path_data.get("degree")
            merged["doc_type"] = path_data.get("doc_type")
            # M9: 研究者（路径推断）
            merged["researcher"] = path_data.get("researcher")

            # --- UPSERT 到 auto_metadata ---
            self._upsert_metadata(file_id, merged)

            # --- 更新 last_parsed ---
            self._update_last_parsed(file_id)

            self._stats["parsed"] += 1
            self._print(f"    ✓ 解析完成")

        except Exception as e:
            logger.warning("解析失败 [%s]: %s", file_path, e)
            self._print(f"    ✗ 解析失败: {e}")
            self._stats["errors"] += 1
            # 文件解析失败，仍写入路径数据
            self._upsert_metadata(file_id, path_data)
            self._update_last_parsed(file_id)

    def _run_path_parsing(self, file_path: str) -> Dict:
        """M8: 从文件路径推断 academic_year / degree / doc_type"""
        if self._path_parser is None:
            from src.parser.path_parser import PathParser
            self._path_parser = PathParser(self.config)
        return self._path_parser.parse(file_path)

    def _update_last_parsed(self, file_id: int) -> None:
        """更新文件的解析时间戳"""
        now = datetime.now().isoformat()
        self._conn.execute(
            "UPDATE auto_files SET last_parsed=? WHERE id=?",
            (now, file_id),
        )

    def _upsert_metadata(self, file_id: int, data: Dict):
        """
        UPSERT 解析结果到 auto_metadata 表

        schema:
            auto_metadata(file_id, title, title_source, keywords, keywords_source,
                         abstract, year, authors, document_type, ref_count,
                         references_text, extra_metadata, parsed_at)

        overlay 优先:
            此表仅存储自动解析结果。人工修正存入 overlay_corrections 表，
            查询时通过 LEFT JOIN + COALESCE 实现 overlay 优先。
        """
        now = datetime.now().isoformat()

        keywords_json = json.dumps(data.get("keywords"), ensure_ascii=False) if data.get("keywords") else None
        authors_json = json.dumps(data.get("authors"), ensure_ascii=False) if data.get("authors") else None
        refs_json = json.dumps(data.get("references"), ensure_ascii=False) if data.get("references") else None
        extra = dict(data.get("extra") or {})
        # 将 has_text_layer 和 note 持久化到 extra_metadata
        if "has_text_layer" in data:
            extra["has_text_layer"] = data["has_text_layer"]
        if data.get("note"):
            extra["note"] = data["note"]
        extra_json = json.dumps(extra, ensure_ascii=False) if extra else None

        ref_count = len(data.get("references")) if data.get("references") else 0

        self._conn.execute(
            """
            INSERT INTO auto_metadata
                (file_id, title, title_source, keywords, keywords_source,
                 abstract, year, authors, document_type, ref_count,
                 references_text, extra_metadata, parsed_at,
                 academic_year, degree, doc_type, researcher)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_id) DO UPDATE SET
                title=excluded.title,
                title_source=excluded.title_source,
                keywords=excluded.keywords,
                keywords_source=excluded.keywords_source,
                abstract=excluded.abstract,
                year=excluded.year,
                authors=excluded.authors,
                document_type=excluded.document_type,
                ref_count=excluded.ref_count,
                references_text=excluded.references_text,
                extra_metadata=excluded.extra_metadata,
                parsed_at=excluded.parsed_at,
                academic_year=COALESCE(excluded.academic_year, auto_metadata.academic_year),
                degree=COALESCE(excluded.degree, auto_metadata.degree),
                doc_type=COALESCE(excluded.doc_type, auto_metadata.doc_type),
                researcher=excluded.researcher
            """,
            (
                file_id,
                data.get("title"),
                data.get("title_source"),
                keywords_json,
                data.get("keywords_source"),
                data.get("abstract"),
                data.get("year"),
                authors_json,
                None,  # document_type 在导入时由 scanner 负责
                ref_count,
                refs_json,
                extra_json,
                now,
                data.get("academic_year"),
                data.get("degree"),
                data.get("doc_type"),
                data.get("researcher"),
            ),
        )

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
        self._print("📊 解析完成")
        self._print(f"   解析成功: {s['parsed']}")
        self._print(f"   跳过:     {s['skipped']}")
        self._print(f"   错误:     {s['errors']}")
        self._print(f"   耗时:     {elapsed:.1f}s")
        self._print("=" * 50)
