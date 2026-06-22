"""
LabIndex Shiori — DOCX 解析器

抽取标题、关键词、正文、参考文献、年份、作者。
与 PDF 解析器共享关键词标注逻辑。
只读保障: 仅 open(path, 'rb')
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional

from src.parser.pdf_parser import (
    _KEYWORD_PATTERNS,
    _STOPWORDS,
    _extract_keywords,
    _extract_year_from_text,
    _parse_authors,
)

logger = logging.getLogger(__name__)


def parse_docx(file_path: str) -> Dict:
    """
    解析 DOCX 文件，返回结构化元数据

    Returns:
        dict: title, title_source, keywords, keywords_source,
              abstract, year, authors, references, note
    """
    from docx import Document

    doc = Document(file_path)

    # --- 提取全部段落文本 ---
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    full_text = "\n".join(paragraphs)

    if not full_text.strip():
        return {
            "title": None,
            "title_source": "inferred",
            "keywords": None,
            "keywords_source": None,
            "abstract": None,
            "year": None,
            "authors": None,
            "references": None,
            "note": "DOCX 文件无可提取文本",
        }

    # --- 标题 ---
    # 优先使用 DOCX 内置属性中的标题（可信）
    title = None
    title_source = "inferred"
    try:
        core_title = doc.core_properties.title
        if core_title and len(core_title.strip()) > 10:
            title = core_title.strip()
            title_source = "annotated"
    except Exception:
        pass

    if title is None:
        # 从首段提取（推测）
        for p in paragraphs[:10]:
            if len(p) >= 10 and not re.match(r"^\d+$", p):
                title = p.strip()
                title_source = "inferred"  # 正文推测，非显式标注
                break

    # --- 关键词 ---
    keywords, kw_source = _extract_keywords(full_text)

    # --- 摘要 ---
    abstract = None
    for i, p in enumerate(paragraphs):
        if re.match(r"^(概要|Abstract|要旨|梗概)", p.strip()):
            # 取接下来的段落直到下一个章节标题
            abs_parts = []
            for j in range(i + 1, min(i + 10, len(paragraphs))):
                if re.match(r"^(1\.|Ⅰ|参考文献|References)", paragraphs[j].strip()):
                    break
                abs_parts.append(paragraphs[j])
            if abs_parts:
                abstract = " ".join(abs_parts)[:2000]
            break

    # 兜底：没找到摘要章节则取正文前2000字符
    if abstract is None and paragraphs:
        body = " ".join(p for p in paragraphs[:50] if len(p) > 10)
        if len(body) > 50:
            abstract = body[:2000]

    # --- 序论/引言文本（用于 intro_similarity）---
    intro_text = None
    for i, p in enumerate(paragraphs):
        if re.match(r"^(1[．.．]?\s*)?(序論|緒言|はじめに|まえがき|序章|Introduction)", p.strip()):
            intro_parts = []
            for j in range(i + 1, min(i + 50, len(paragraphs))):
                if re.match(r"^(2[．.．]|第)", paragraphs[j].strip()):
                    break
                intro_parts.append(paragraphs[j])
            if intro_parts:
                intro_text = " ".join(intro_parts)[:2000]
            break
    if intro_text is None and paragraphs:
        intro_text = " ".join(p for p in paragraphs[:50] if len(p) > 10)[:2000]
    if intro_text and len(intro_text) < 50:
        intro_text = None

    # --- 年份 ---
    year = _extract_year_from_text(full_text)

    # --- 作者（从 DOCX 内置属性获取）---
    authors = None
    try:
        core_props = doc.core_properties
        if core_props.authors:
            authors = _parse_authors(core_props.authors)
    except Exception:
        pass

    # --- 参考文献 ---
    references = None
    ref_start = None
    for i, p in enumerate(paragraphs):
        if re.match(r"^(参考文献|References|引用文献)\s*$", p.strip()):
            ref_start = i
            break

    if ref_start is not None:
        refs = []
        for p in paragraphs[ref_start + 1:]:
            stripped = p.strip()
            if not stripped:
                continue
            if re.match(r"^\d+[\.\)]", stripped) or re.match(r"^\[\d+\]", stripped):
                refs.append(stripped)
            elif refs:
                refs[-1] += " " + stripped
        references = refs if refs else None

    return {
        "title": title,
        "title_source": title_source,
        "keywords": keywords,
        "keywords_source": kw_source,
        "abstract": abstract,
        "year": year,
        "authors": authors,
        "references": references,
        "note": None,
        "extra": {"intro_text": intro_text},
    }
