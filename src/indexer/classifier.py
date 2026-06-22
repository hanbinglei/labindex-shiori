"""
LabIndex Shiori — 子主题自动归类

基于 config.yaml 中的种子清单，对每篇文档做子主题归类：
  1. 标题/关键词/正文命中某子主题的 keywords → 归入该子主题
  2. 一篇可命中多个子主题（允许多归类）
  3. 归不进任何子主题的标「未分類」
  4. 归类结果写回 auto_metadata.subtopic + subtopic_source

overlay 优先：人工改过的子主题（overlay_corrections.subtopic）保持不变
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Dict, List, Optional, Set, Tuple

from src.config import AppConfig, TopicConfig, SubtopicConfig

logger = logging.getLogger(__name__)


def classify_all(config: AppConfig) -> Dict:
    """
    对所有 active 文档执行子主题自动归类

    Returns:
        {"classified": int, "unclassified": int, "multi": int}
    """
    conn = sqlite3.connect(config.database.path)
    conn.row_factory = sqlite3.Row

    stats = {"classified": 0, "unclassified": 0, "multi": 0}

    # 获取所有子主题种子
    subtopics = []
    for topic in config.topics:
        for st in topic.subtopics:
            subtopics.append(st)

    if not subtopics:
        logger.warning("没有任何子主题种子配置，跳过归类")
        conn.close()
        return stats

    # 获取所有需要归类的文档（跳过已有 overlay 修正的）
    rows = conn.execute("""
        SELECT f.id, m.title, m.keywords, m.abstract, m.subtopic
        FROM auto_files f
        JOIN auto_metadata m ON f.id = m.file_id
        WHERE f.status = 'active'
          AND m.title IS NOT NULL
    """).fetchall()

    unclassified_label = config.unclassified.get(config.i18n.default_language, "未分類")

    for row in rows:
        file_id = row["id"]

        # 跳过已被 overlay 修正过子主题的文档
        existing = conn.execute(
            "SELECT 1 FROM overlay_corrections WHERE file_id=? AND field_name='subtopic'",
            (file_id,),
        ).fetchone()
        if existing:
            continue

        title = (row["title"] or "").lower()
        keywords_text = " ".join(json.loads(row["keywords"] or "[]")).lower()
        abstract = (row["abstract"] or "").lower()
        search_text = f"{title} {keywords_text} {abstract}"

        matched = _classify_document(search_text, subtopics)

        if matched:
            subtopic_str = ",".join(matched)
            conn.execute(
                "UPDATE auto_metadata SET subtopic=?, subtopic_source='classified' WHERE file_id=?",
                (subtopic_str, file_id),
            )
            stats["classified"] += 1
            if len(matched) > 1:
                stats["multi"] += 1
        else:
            # 归不进 → 未分類
            conn.execute(
                "UPDATE auto_metadata SET subtopic=?, subtopic_source=NULL WHERE file_id=?",
                (unclassified_label, file_id),
            )
            stats["unclassified"] += 1

    conn.commit()
    conn.close()
    return stats


def _classify_document(search_text: str, subtopics: List[SubtopicConfig]) -> List[str]:
    """
    对單篇文档执行子主题匹配

    匹配规则:
      - 将文档的 title + keywords + abstract 转为小写
      - 对每个子主题的 keywords 列表做精确子串匹配
      - 命中任一 keyword 即归入该子主题

    Args:
        search_text: 小写化后的标题+关键词+摘要
        subtopics: 所有子主题配置

    Returns:
        匹配到的子主题 name 列表（可能多个）
    """
    matched = []
    for st in subtopics:
        for kw in st.keywords:
            # 跳过过短的英文关键词（防误命中：如 "GA" 命中 organization）
            if len(kw) < 3:
                continue
            if kw.lower() in search_text:
                matched.append(st.name)
                break  # 一个子主题只归一次
    return matched
