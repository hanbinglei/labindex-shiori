"""
LabIndex Shiori — PDF 解析器

功能:
  1. 抽取标题（首页顶部，过滤页眉/日期等干扰）
  2. 抽取关键词（キーワード:/Keywords: 标注优先 → 频率推测兜底）
  3. 抽取摘要/正文前 N 字
  4. 抽取参考文献
  5. 抽取年份、作者
  6. 来源标注: annotated / inferred

只读保障: 仅 open(path, 'rb')
扫描版 PDF（无文字层）→ 标记「无文字层，仅元信息」
"""

from __future__ import annotations

import re
import logging
from collections import Counter
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# -----------------------------------------------------------
# 常量
# -----------------------------------------------------------
# 标题过滤：扫描版 / 页眉 / 日期 / 卷号等不应作为标题
_TITLE_SKIP_PATTERNS = [
    r"^\d+$",                              # 纯数字
    r"^Vol\.?\s*\d",                       # Vol.
    r"^No\.?\s*\d",                        # No.
    r"^第[\d]+卷",                         # 第X卷
    r"^第[\d]+号",                         # 第X号
    r"^pp?\.?\s*\d",                       # p./pp.
    r"受付", r"受理", r"投稿",            # 受理日期
    r"原稿",                               # 原稿
    r"^\d{4}\s*年",                        # 年份开头
    r"^\d{4}\s",                           # 数字年份开头
    r"^[A-Z\s]{10,}$",                     # 全大写短句（可能是学会名）
    r"日本建築学会", r"日本鋼構造",       # 学会名
    r"構造工学論文集",                     # 论文集名
    r"Summaries of Technical",             # 英语摘要标题
]

# 日文/英文关键词标注模式
_KEYWORD_PATTERNS = [
    r"キーワード\s*[：:]\s*(.+?)(?:\n|$)",
    r"Keywords?\s*[：:]\s*(.+?)(?:\n|$)",
    r"keyword\s*[：:]\s*(.+?)(?:\n|$)",
]

# 参考文献章节标题
_REF_SECTION_PATTERNS = [
    r"^参考文献\s*$",
    r"^参考文献\s",
    r"^References\s*$",
    r"^引用文献\s*$",
    r"^REFERENCES\s*$",
]

# 摘要章节标题
_ABSTRACT_PATTERNS = [
    r"^(概要|梗概|要旨)\s*[：:]?\s*$",
    r"^Abstract\s*$",
    r"^ABSTRACT\s*$",
]

# 日文停用词（用于关键词推测时过滤）
_STOPWORDS = {
    "する", "ため", "こと", "れる", "られる", "ある", "いる",
    "なる", "できる", "及び", "等", "について", "に関する",
    "おける", "よる", "よって", "ついて", "また", "さらに",
    "もの", "ここ", "そこ", "これ", "それ", "本研究", "本論文",
    "method", "model", "analysis", "study", "result", "using",
    "based", "proposed", "method", "data", "実験", "解析",
    "検討", "評価",
}

# 最大章节数（从后往前找参考文献时限制搜索范围）
_MAX_SECTIONS_FOR_REFS = 30


def parse_pdf(file_path: str, max_pages: int = 200) -> Dict:
    """
    解析 PDF 文件，返回结构化元数据

    Args:
        file_path: PDF 文件路径
        max_pages: 最大解析页数（防止超大 PDF 卡死）

    Returns:
        dict: {
            "title": str or None,
            "title_source": "annotated" or "inferred",
            "keywords": list[str] or None,
            "keywords_source": "annotated" or "inferred",
            "abstract": str or None,
            "year": int or None,
            "authors": list[str] or None,
            "references": list[str] or None,
            "has_text_layer": bool,
            "note": str or None,  # "扫描版PDF，仅元信息"
            "extra": dict,        # 其他元信息
        }
    """
    import fitz  # PyMuPDF

    doc = fitz.open(file_path)
    total_pages = min(doc.page_count, max_pages)

    # --- 获取 PDF 文件元信息 ---
    pdf_meta = doc.metadata or {}
    pdf_title = pdf_meta.get("title", "") or ""
    pdf_author = pdf_meta.get("author", "") or ""

    # --- 逐页提取文本 ---
    pages_text = []
    full_text = ""
    for i in range(total_pages):
        page = doc[i]
        text = page.get_text()
        pages_text.append(text)
        full_text += text + "\n"

    doc.close()

    # --- 检查是否有文字层 ---
    if not full_text.strip():
        return {
            "title": pdf_title or None,
            "title_source": "inferred" if not pdf_title else "annotated",
            "keywords": None,
            "keywords_source": None,
            "abstract": None,
            "year": _extract_year_from_text("") or None,
            "authors": _parse_authors(pdf_author) if pdf_author else None,
            "references": None,
            "has_text_layer": False,
            "note": "扫描版PDF，无文字层，仅元信息",
            "extra": {"pdf_metadata": pdf_meta},
        }

    # --- 正常解析 ---
    first_page_text = pages_text[0] if pages_text else ""

    # 1. 标题
    title, title_source = _extract_title(first_page_text, pdf_title)

    # 2. 关键词
    keywords, kw_source = _extract_keywords(full_text)

    # 3. 摘要
    abstract = _extract_abstract(full_text)

    # 4. 参考文献
    references = _extract_references(full_text)

    # 5. 年份
    year = _extract_year_from_text(first_page_text + full_text[:2000])

    # 6. 作者
    authors = _parse_authors(pdf_author) if pdf_author else _extract_authors(first_page_text)

    # 7. 提取序论/引言文本（用于 TF-IDF intro_similarity）
    intro_text = _extract_intro_text(full_text) or abstract

    return {
        "title": title,
        "title_source": title_source,
        "keywords": keywords,
        "keywords_source": kw_source,
        "abstract": abstract,
        "year": year,
        "authors": authors,
        "references": references,
        "has_text_layer": True,
        "note": None,
        "extra": {"pdf_metadata": pdf_meta, "intro_text": intro_text[:2000] if intro_text else None},
    }


# ============================================================
# 标题提取
# ============================================================

def _extract_title(first_page_text: str, pdf_title: str) -> Tuple[Optional[str], str]:
    """
    从首页文本提取标题

    策略:
    1. 优先使用 PDF metadata 中的 title（如果合理且非空）
    2. 从首页文本中取第一段有意义的文字
    3. 过滤已知干扰模式（页眉、日期、学会名等）

    Returns:
        (title_text, source_label)
    """
    # 优先用 PDF metadata 标题
    if pdf_title and len(pdf_title) > 10:
        # 简单验证：不是默认值/占位符
        if not re.match(r"^(untitled|无题|Microsoft Word|Document)", pdf_title, re.I):
            return pdf_title.strip(), "annotated"

    # 从首页文本提取
    lines = first_page_text.strip().split("\n")
    lines = [l.strip() for l in lines if l.strip()]

    candidates = []
    for line in lines:
        # 太短跳过
        if len(line) < 10:
            continue
        # 匹配跳过模式
        if any(re.search(p, line) for p in _TITLE_SKIP_PATTERNS):
            continue
        candidates.append(line)

    if candidates:
        # 取第一个合理候选（从正文提取的一律标 inferred，非原文显式标注）
        return candidates[0], "inferred"

    return None, "inferred"


# ============================================================
# 关键词提取
# ============================================================

def _extract_keywords(full_text: str) -> Tuple[Optional[List[str]], Optional[str]]:
    """
    从全文提取关键词

    优先级:
    1. annotated: 找到 キーワード:/Keywords: 标注
    2. inferred: 无标注，TF 频率提取特征词

    Returns:
        (keywords_list, source_label)
    """
    # --- 方案1: 标注提取 ---
    for pattern in _KEYWORD_PATTERNS:
        m = re.search(pattern, full_text, re.IGNORECASE | re.DOTALL)
        if m:
            kw_text = m.group(1).strip()
            # 分割关键词（逗号、空格、全角逗号、顿号等）
            kws = re.split(r"[,、，・\s/／]+", kw_text)
            kws = [k.strip().strip(".") for k in kws if k.strip() and len(k.strip()) > 1]
            # 过滤纯数字/过短的
            kws = [k for k in kws if not re.match(r"^\d+$", k)]
            if kws:
                return kws, "annotated"

    # --- 方案2: 算法推测 ---
    # 提取日文汉字/平假名/片假名 + 英文词汇
    words = re.findall(
        r"[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]{2,}|[a-zA-Z][a-zA-Z\-]{2,}",
        full_text.lower(),
    )
    freq = Counter(words)
    # 过滤停用词和低频词
    candidates = [
        (w, c) for w, c in freq.most_common(30)
        if w not in _STOPWORDS and c >= 3  # 至少出现3次
    ]
    # 取前 5 个
    top_kw = [w for w, _ in candidates[:5]]
    if top_kw:
        return top_kw, "inferred"

    return None, None


# ============================================================
# 序论/引言文本提取（用于 intro_similarity TF-IDF）
# ============================================================

_INTRO_SECTION_PATTERNS = [
    r"^(1[．.．]?\s*)?(序論|緒言|はじめに|まえがき|序章|Introduction)",
    r"^(1[．.．]?\s*)?(研究背景|背景|背 景|目的)",
    r"^(はじめに|緒言|序論)",
]


def _extract_intro_text(full_text: str) -> Optional[str]:
    """提取序论/引言文本（前2000字符）"""
    lines = full_text.split("\n")
    # 从第5行之后开始（跳过可能在首页首页的标题/作者区域）
    intro_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if any(re.match(p, stripped) for p in _INTRO_SECTION_PATTERNS):
            intro_start = i + 1
            break

    # 如果找到了序论章节标题，从中开始取1500字符
    if intro_start > 0:
        text = " ".join(l.strip() for l in lines[intro_start:intro_start + 100] if l.strip())
        if len(text) > 100:
            return text[:2000]

    # 没找到章节标题：取全文前2000字符（跳过前5行干扰）
    body = " ".join(l.strip() for l in lines[5:200] if l.strip() and len(l.strip()) > 5)
    if len(body) > 100:
        return body[:2000]
    return None


# ============================================================
# 摘要提取
# ============================================================

def _extract_abstract(full_text: str) -> Optional[str]:
    """提取摘要（概要/Abstract 段落）"""
    lines = full_text.split("\n")

    in_abstract = False
    abstract_lines = []

    for i, line in enumerate(lines):
        stripped = line.strip()

        # 检测摘要开始
        if any(re.match(p, stripped) for p in _ABSTRACT_PATTERNS):
            in_abstract = True
            continue

        # 检测摘要结束（下一个章节标题）
        if in_abstract:
            # 参考文献/下一个大标题/结论 → 结束
            if any(re.match(p, stripped) for p in _REF_SECTION_PATTERNS):
                break
            if re.match(r"^(1\.|Ⅰ|II\s|第)", stripped) and len(stripped) < 50:
                break
            abstract_lines.append(stripped)

    abstract = " ".join(abstract_lines).strip()
    if len(abstract) > 50:  # 至少50字才算有效的摘要
        return abstract[:2000]  # 截断到2000字

    # 兜底：没有检测到摘要章节，但文档有正文内容
    # 取前 N 字符作为摘要（适用于 梗概 等整篇文档就是摘要的文件）
    body_text = " ".join(l.strip() for l in lines[:200] if l.strip() and len(l.strip()) > 10)
    body_text = body_text.strip()
    if len(body_text) > 50:
        return body_text[:2000]

    return None


# ============================================================
# 参考文献提取
# ============================================================

def _extract_references(full_text: str) -> Optional[List[str]]:
    """从文本中提取参考文献列表"""
    lines = full_text.split("\n")

    # 从后往前找参考文献章节
    ref_start = None
    for i in range(min(len(lines), _MAX_SECTIONS_FOR_REFS)):
        idx = len(lines) - 1 - i
        stripped = lines[idx].strip()
        if any(re.match(p, stripped) for p in _REF_SECTION_PATTERNS):
            ref_start = idx
            break

    if ref_start is None:
        return None

    # 收集引用条目
    refs = []
    current_ref = ""
    for line in lines[ref_start + 1:]:
        stripped = line.strip()
        if not stripped:
            if current_ref:
                refs.append(current_ref.strip())
                current_ref = ""
            continue
        # 新编号引用开始
        if re.match(r"^\d+[\.\)]\s", stripped) or re.match(r"^\[\d+\]", stripped):
            if current_ref:
                refs.append(current_ref.strip())
            current_ref = stripped
        else:
            current_ref += " " + stripped

    if current_ref:
        refs.append(current_ref.strip())

    return refs if refs else None


# ============================================================
# 年份提取
# ============================================================

def _extract_year_from_text(text: str) -> Optional[int]:
    """从文本中提取论文年份"""
    # 模式1: 西历 4 位数
    years = re.findall(r"(?:19|20)\d{2}", text)
    valid = [int(y) for y in years if 1950 < int(y) < 2030]
    if valid:
        return max(set(valid), key=valid.count)  # 最频繁的年份

    return None


# ============================================================
# 作者提取
# ============================================================

def _parse_authors(author_str: str) -> Optional[List[str]]:
    """解析 PDF metadata 中的作者字符串"""
    if not author_str or not author_str.strip():
        return None
    # 按逗号/分号/全角逗号分割
    authors = re.split(r"[,、;；\s]+", author_str)
    authors = [a.strip() for a in authors if a.strip() and len(a.strip()) > 1]
    return authors if authors else None


def _extract_authors(first_page_text: str) -> Optional[List[str]]:
    """从首页文本尝试提取作者（简单启发式）"""
    lines = first_page_text.split("\n")[:30]
    # 在标题之后找包含 〇 或 名+姓 模式的行
    for line in lines:
        stripped = line.strip()
        # 日文作者名模式
        if re.search(r"[\u30a1-\u30f6]+\s[\u30a1-\u30f6]+", stripped):
            return [stripped]
        if re.search(r"[A-Z][a-z]+\s[A-Z][a-z]+", stripped) and len(stripped) < 60:
            pass  # 需要更复杂的消歧

    return None
