"""
LabIndex Shiori — 研究关联推算引擎（M10）

五层关联判定规则（严格按优先级实现）：
  1. citation（决定性/确定）：B的论文引用了本研究室A的论文
  2. title_succession（决定性/确定）：标题含明确承接表述（その2等），且找到对应前序
  3. bibliographic_coupling（决定性或候选）：共享参考文献数 ≥ 阈值
  4. intro_similarity（提示性/候选）：序论 TF-IDF 文本相似度
  5. keyword（提示性/候选）：关键词重合度超过阈值（降级为最弱）
  6. title_similarity（提示性/候选）：标题核心词重叠但无承接标识

设计原则：
  - 只读：仅读取数据库，不碰文件
  - 规则可配置：阈值和模式写 config.yaml
  - overlay 保障：人工确认/删除的关联，机器不覆盖
"""
from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
import time
from collections import defaultdict, Counter
from typing import Dict, List, Optional, Set, Tuple

from src.config import AppConfig

logger = logging.getLogger(__name__)

# 日语/中文停用词（标题相似度计算时过滤）
_STOP_WORDS = {
    "の", "に", "を", "は", "が", "と", "で", "へ", "より",
    "から", "まで", "も", "て", "し", "た", "だ", "です", "ます",
    "について", "による", "に関する", "おける", "ある", "いる",
    "及び", "並びに", "ただ", "ただし", "及びの",
    "于", "的", "之", "和", "与", "在", "对", "就", "被", "由",
    "the", "a", "an", "of", "in", "on", "at", "for", "to",
    "with", "by", "and", "or", "from", "as", "is", "was",
    "are", "were", "be", "been", "being", "have", "has",
    "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can",
    # 短字/无意义
    "第", "号", "編", "巻",
}


class RelationEngine:
    """
    关联推算引擎

    用法:
        config = AppConfig.load()
        engine = RelationEngine(config)

        # 执行全量关联推算
        stats = engine.calculate_all()

        # 查询某个文档的所有关联（含 auto + overlay 合并）
        relations = engine.get_relations_for_doc(file_id)
    """

    RELATION_TYPES = {"citation", "title_succession", "keyword", "title_similarity",
                      "intro_similarity", "bibliographic_coupling"}

    def __init__(self, config: AppConfig):
        self.config = config
        self._conn: Optional[sqlite3.Connection] = None
        self._stats = {
            "citation": 0,
            "title_succession": 0,
            "keyword": 0,
            "title_similarity": 0,
            "intro_similarity": 0,
            "bibliographic_coupling": 0,
        }
        self._start_time = 0.0

        # 预编译承接标识正则
        rc = config.relation
        self._succession_res = [re.compile(p) for p in rc.succession_patterns]
        self._predecessor_res = [re.compile(p) for p in rc.predecessor_patterns]

    # ============================================================
    # 公开接口
    # ============================================================

    def calculate_all(self) -> Dict:
        """
        全量关联推算：
        1. 清空 auto_relations 表
        2. 应用四层规则
        3. 人工确认的保留，人工拒绝的不重建
        """
        self._reset()

        self._conn = sqlite3.connect(self.config.database.path)
        self._conn.row_factory = sqlite3.Row

        try:
            # 清空机器关联（但保留被人工 confirm 的）
            self._clear_recomputable()

            # 获取所有 active 文档的元数据
            docs = self._load_all_docs()

            if len(docs) < 2:
                self._print("⚠️ 文档数不足 2，无法计算关联")
                return self._stats

            self._print(f"📊 载入 {len(docs)} 篇文档，开始推算...")

            # --- 规则1: 参考文献引用（citation）---
            self._calc_citation(docs)

            # --- 规则2: 标题承接（title_succession）---
            self._calc_title_succession(docs)

            # --- 规则3: 书目耦合（bibliographic_coupling）---
            if self.config.relation.bc_enabled:
                self._calc_bibliographic_coupling(docs)

            # --- 规则4: 关键词重合（keyword）---
            self._calc_keyword_overlap(docs)

            # --- 规则5: 标题相似（title_similarity）---
            if self.config.relation.title_similarity_enabled:
                self._calc_title_similarity(docs)

            # --- 规则6: 序论 TF-IDF 相似（intro_similarity）---
            if self.config.relation.intro_similarity_enabled:
                self._calc_intro_similarity(docs)

            self._conn.commit()
            self._print_summary()

        except Exception as e:
            self._conn.rollback()
            self._print(f"❌ 关联推算失败: {e}", err=True)
            raise
        finally:
            if self._conn:
                self._conn.close()
                self._conn = None

        return self._stats

    def get_relations_for_doc(self, file_id: int) -> List[Dict]:
        """
        查询指定文档的所有关联（合并 auto + overlay）

        Returns:
            list of dict: {
                "target_id": int,
                "relation_type": str,
                "confidence": str,
                "detail": dict or None,
                "source": "auto" or "overlay",
            }
        """
        conn = sqlite3.connect(self.config.database.path)
        conn.row_factory = sqlite3.Row

        results = []

        # 1. auto_relations（排除被 reject 的）
        rows = conn.execute("""
            SELECT a.target_file_id, a.relation_type, a.confidence, a.detail
            FROM auto_relations a
            WHERE a.source_file_id = ?
              AND NOT EXISTS (
                  SELECT 1 FROM overlay_relation_actions o
                  WHERE o.source_file_id = a.source_file_id
                    AND o.target_file_id = a.target_file_id
                    AND o.relation_type = a.relation_type
                    AND o.action = 'reject'
              )
            UNION
            SELECT a.source_file_id, a.relation_type, a.confidence, a.detail
            FROM auto_relations a
            WHERE a.target_file_id = ?
              AND NOT EXISTS (
                  SELECT 1 FROM overlay_relation_actions o
                  WHERE o.source_file_id = a.source_file_id
                    AND o.target_file_id = a.target_file_id
                    AND o.relation_type = a.relation_type
                    AND o.action = 'reject'
              )
        """, (file_id, file_id)).fetchall()

        for r in rows:
            results.append({
                "target_id": r["target_file_id"],
                "relation_type": r["relation_type"],
                "confidence": r["confidence"],
                "detail": json.loads(r["detail"]) if r["detail"] else None,
                "source": "auto",
            })

        # 2. overlay_relations（人工建的）
        rows = conn.execute("""
            SELECT
                CASE WHEN file_id_a = ? THEN file_id_b ELSE file_id_a END AS target_id,
                relation_type, '确定' AS confidence, note AS detail
            FROM overlay_relations
            WHERE file_id_a = ? OR file_id_b = ?
        """, (file_id, file_id, file_id)).fetchall()

        for r in rows:
            results.append({
                "target_id": r["target_id"],
                "relation_type": r["relation_type"],
                "confidence": r["confidence"],
                "detail": r["detail"],
                "source": "overlay",
            })

        conn.close()
        return results

    def get_all_relations(self) -> List[Dict]:
        """获取所有机器推算的关联（供 M11 使用）"""
        conn = sqlite3.connect(self.config.database.path)
        conn.row_factory = sqlite3.Row

        rows = conn.execute("""
            SELECT a.id, a.source_file_id, a.target_file_id,
                   a.relation_type, a.confidence, a.detail,
                   sf.filename AS src_filename, tf.filename AS tgt_filename,
                   sm.title AS src_title, tm.title AS tgt_title
            FROM auto_relations a
            JOIN auto_files sf ON sf.id = a.source_file_id
            JOIN auto_files tf ON tf.id = a.target_file_id
            LEFT JOIN auto_metadata sm ON sm.file_id = a.source_file_id
            LEFT JOIN auto_metadata tm ON tm.file_id = a.target_file_id
            WHERE NOT EXISTS (
                SELECT 1 FROM overlay_relation_actions o
                WHERE o.source_file_id = a.source_file_id
                  AND o.target_file_id = a.target_file_id
                  AND o.relation_type = a.relation_type
                  AND o.action = 'reject'
            )
            ORDER BY a.relation_type, a.source_file_id
        """).fetchall()

        results = []
        for r in rows:
            results.append({
                "id": r["id"],
                "source_file_id": r["source_file_id"],
                "target_file_id": r["target_file_id"],
                "relation_type": r["relation_type"],
                "confidence": r["confidence"],
                "detail": json.loads(r["detail"]) if r["detail"] else None,
                "source_title": r["src_title"],
                "target_title": r["tgt_title"],
                "source_filename": r["src_filename"],
                "target_filename": r["tgt_filename"],
            })
        conn.close()
        return results

    # ============================================================
    # 内部方法
    # ============================================================

    def _reset(self):
        self._stats = {"citation": 0, "title_succession": 0, "keyword": 0,
                       "title_similarity": 0, "intro_similarity": 0,
                       "bibliographic_coupling": 0}
        self._start_time = time.time()

    def _clear_recomputable(self):
        """
        清空可重新计算的机器关联（保留被人工 confirm 的）
        """
        # 删除未被人工确认的 auto_relations
        self._conn.execute("""
            DELETE FROM auto_relations
            WHERE NOT EXISTS (
                SELECT 1 FROM overlay_relation_actions o
                WHERE o.source_file_id = auto_relations.source_file_id
                  AND o.target_file_id = auto_relations.target_file_id
                  AND o.relation_type = auto_relations.relation_type
                  AND o.action = 'confirm'
            )
        """)
        self._print("  清空旧机器关联（保留人工确认的）")

    def _load_all_docs(self) -> List[Dict]:
        """加载所有 active 文档的元数据"""
        rows = self._conn.execute("""
            SELECT f.id, f.filename, f.path,
                   m.title, m.keywords, m.authors, m.year, m.academic_year,
                   m.degree, m.doc_type, m.researcher,
                   m.references_text, m.abstract, m.extra_metadata
            FROM auto_files f
            JOIN auto_metadata m ON f.id = m.file_id
            WHERE f.status = 'active'
            ORDER BY f.id
        """).fetchall()

        docs = []
        for r in rows:
            # 解析 keywords JSON
            kw = []
            if r["keywords"]:
                try:
                    kw = json.loads(r["keywords"])
                    if not isinstance(kw, list):
                        kw = []
                except (json.JSONDecodeError, TypeError):
                    kw = []

            # 解析 references JSON
            refs = []
            if r["references_text"]:
                try:
                    refs = json.loads(r["references_text"])
                    if not isinstance(refs, list):
                        refs = []
                except (json.JSONDecodeError, TypeError):
                    refs = []

            # 解析 extra_metadata（内含 intro_text 等）
            extra = {}
            if r["extra_metadata"]:
                try:
                    extra = json.loads(r["extra_metadata"])
                    if not isinstance(extra, dict):
                        extra = {}
                except (json.JSONDecodeError, TypeError):
                    extra = {}

            docs.append({
                "id": r["id"],
                "title": r["title"] or "",
                "filename": r["filename"],
                "keywords": [k.lower() for k in kw],
                "keywords_raw": kw,
                "authors": r["authors"],
                "year": r["year"],
                "academic_year": r["academic_year"],
                "researcher": r["researcher"],
                "references_text": refs,
                "abstract": r["abstract"] or "",
                "extra_metadata": extra,
            })
        return docs

    # ============================================================
    # 规则1: 参考文献引用（citation）
    # ============================================================

    def _calc_citation(self, docs: List[Dict]):
        """
        规则1: 参考文献引用 → 决定性关联

        对每篇有参考文献的文档，尝试在已入库文档的标题中找匹配。
        匹配策略（按优先级）：
          a) 标题完全匹配（去除标点符号后）
          b) 标题包含关键片段匹配
        """
        # 建立标题索引（去标点化）
        title_db = {}
        for d in docs:
            title = d["title"].strip()
            if not title:
                continue
            clean_title = self._clean_title(title)
            title_db[d["id"]] = {
                "original": title,
                "clean": clean_title,
                "short_clean": self._shorten_title(clean_title),
            }

        threshold = self.config.relation.citation_title_similarity_threshold
        min_len = self.config.relation.citation_min_title_match_len

        counted = 0

        for doc in docs:
            refs = doc["references_text"]
            if not refs:
                continue

            for ref_text in refs:
                ref_text = str(ref_text).strip()
                if not ref_text:
                    continue
                ref_clean = self._clean_title(ref_text)

                if len(ref_clean) < min_len:
                    continue

                # 尝试匹配每篇文档的标题
                best_match_id = None
                best_score = 0.0

                for tid, tinfo in title_db.items():
                    if tid == doc["id"]:
                        continue  # 不自引用

                    # 方法1: 精确包含匹配
                    clean = tinfo["clean"]
                    if ref_clean == clean:
                        best_score = 1.0
                        best_match_id = tid
                        break

                    # 方法2: 包含匹配（参考文献字符串包含目标标题）
                    if ref_clean and clean and clean in ref_clean:
                        # 如果标题完整出现在参考文献中，直接设 score=1.0
                        # 因为参考文献通常含作者名+年份等信息，比标题长得多
                        if len(clean) >= min_len:
                            best_score = 1.0
                            best_match_id = tid
                            break
                        score = len(clean) / max(len(ref_clean), 1)
                        if score > best_score:
                            best_score = score
                            best_match_id = tid

                    # 方法3: 短标题包含匹配
                    short = tinfo["short_clean"]
                    if short and len(short) >= min_len and short in ref_clean:
                        score = len(short) / max(len(ref_clean), 1) * 0.9
                        if score > best_score:
                            best_score = score
                            best_match_id = tid

                if best_match_id is not None and best_score >= threshold:
                    self._insert_relation(
                        doc["id"], best_match_id,
                        relation_type="citation",
                        confidence="确定",
                        detail={"matched_ref": ref_text[:200], "score": round(best_score, 2)},
                    )
                    counted += 1

        self._stats["citation"] = counted
        self._print(f"  📖 引用关联: {counted} 条")

    # ============================================================
    # 规则2: 标题承接（title_succession）
    # ============================================================

    def _calc_title_succession(self, docs: List[Dict]):
        """
        规则2: 标题承接标识 → 决定性关联

        对每篇文档：
          1. 检查标题是否含承接标识（その2、第2報、続報等）
          2. 若含有，去掉承接标识，得到"基干标题"
          3. 在同年度或前序年度中找"基干标题" + "前序标识"的文档
        """
        # 按 researcher + academic_year 分组（同一研究者的研究序列）
        by_researcher = defaultdict(list)
        for d in docs:
            by_researcher[d["researcher"] or "__no_researcher__"].append(d)

        counted = 0

        for researcher, group_docs in by_researcher.items():
            for doc in group_docs:
                title = doc["title"]
                if not title:
                    continue

                base_title, successor_type = self._extract_succession_info(title)
                if base_title is None:
                    continue

                # 寻找对应前序
                predecessor_id = self._find_predecessor(
                    doc, base_title, successor_type, group_docs
                )

                if predecessor_id is not None:
                    self._insert_relation(
                        predecessor_id, doc["id"],
                        relation_type="title_succession",
                        confidence="确定",
                        detail={
                            "successor_title": title[:200],
                            "base_title": base_title[:200],
                            "marker": successor_type,
                        },
                    )
                    counted += 1

        self._stats["title_succession"] = counted
        self._print(f"  🔗 标题承接: {counted} 条")

    def _extract_succession_info(self, title: str) -> Tuple[Optional[str], Optional[str]]:
        """
        从标题中提取承接信息和基干标题

        Returns:
            (base_title, successor_type):
                base_title: 去掉承接标识后的基干标题
                successor_type: 匹配到的承接标识（如 'その2'）
            若无承接标识，返回 (None, None)
        """
        for i, (suc_re, pre_re) in enumerate(zip(self._succession_res, self._predecessor_res)):
            m = suc_re.search(title)
            if m:
                # 去掉匹配到的承接标识，得到基干标题
                marker = m.group(1) if m.lastindex else m.group(0)
                base = suc_re.sub("", title).strip()
                # 清理残留的空白/括号
                base = re.sub(r"\s+", " ", base).strip()
                base = base.rstrip("（").rstrip("(").strip()
                if base:
                    return base, marker
        return None, None

    def _find_predecessor(self, current_doc: Dict, base_title: str,
                           successor_type: str, group: List[Dict]) -> Optional[int]:
        """
        寻找前序文档

        策略：
          1. 在同 researcher 组内找
          2. 标题去掉承接标识后，匹配"基干标题 + 前序标识"
          3. 优先年度更早的
        """
        current_year = current_doc.get("academic_year") or current_doc.get("year") or 9999

        candidates = []
        for d in group:
            if d["id"] == current_doc["id"]:
                continue

            d_title = d["title"]
            if not d_title:
                continue

            # 检查这篇是否含前序标识
            for pre_re in self._predecessor_res:
                m = pre_re.search(d_title)
                if m:
                    pre_base = pre_re.sub("", d_title).strip()
                    pre_base = re.sub(r"\s+", " ", pre_base).strip()

                    # 基干标题匹配
                    if self._titles_match(base_title, pre_base):
                        d_year = d.get("academic_year") or d.get("year") or 0
                        if d_year <= current_year:
                            candidates.append((d_year, d["id"]))
                    break

        if candidates:
            # 选最近的前序
            candidates.sort(key=lambda x: -x[0])
            return candidates[0][1]

        return None

    @staticmethod
    def _titles_match(a: str, b: str) -> bool:
        """判断两个基干标题是否匹配（模糊）"""
        a_clean = re.sub(r"\s+", " ", a.strip()).lower()
        b_clean = re.sub(r"\s+", " ", b.strip()).lower()

        if a_clean == b_clean:
            return True

        # 包含匹配
        if len(a_clean) >= 10 and a_clean in b_clean:
            return True
        if len(b_clean) >= 10 and b_clean in a_clean:
            return True

        # 核心词重叠率
        a_words = set(re.findall(r"[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\w]+", a_clean))
        b_words = set(re.findall(r"[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\w]+", b_clean))

        if not a_words or not b_words:
            return False

        intersection = a_words & b_words
        # 核心 Jaccard 相似度
        jaccard = len(intersection) / len(a_words | b_words)
        return jaccard >= 0.5

    # ============================================================
    # 规则3: 书目耦合（bibliographic_coupling）
    # ============================================================

    def _calc_bibliographic_coupling(self, docs: List[Dict]):
        """
        规则3: 书目耦合 → 决定性(≥阈值) 或 候选关联(=1)

        对每对文档，计算其共享参考文献的数量。
        参考文件通过归一化指纹匹配（去前缀序号 + 字符归一化）。
        """
        bc = self.config.relation
        min_shared = bc.bc_min_shared_refs
        certain_th = bc.bc_certain_threshold
        strip_pats = [re.compile(p) for p in bc.bc_strip_prefixes]
        year_pat = re.compile(bc.bc_year_pattern)

        counted = 0
        existing_definitive = self._get_existing_definitive_pairs()

        # 1) 为每篇有参考文献的文档建立指纹集合
        doc_refs = {}  # doc_id → set of fingerprints
        for d in docs:
            refs = d.get("references_text", []) or []
            if not refs:
                continue
            fingerprints = set()
            for raw_ref in refs:
                if not raw_ref:
                    continue
                # 清理参考文件字符串
                text = raw_ref.strip()
                # 去掉前缀序号
                for pat in strip_pats:
                    text = pat.sub("", text).strip()
                if not text:
                    continue
                # 提取年份并附在指纹后（辅助区分）
                years = year_pat.findall(text)
                year_suffix = f"@@{years[0]}" if years else ""
                # 生成指纹：去除非中日文字符，取前60字符
                # 保留中日文字母数字，去除标点空格
                clean = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]", "", text)
                if len(clean) < 15:
                    continue  # 太短不可能是有效参考文献
                fingerprint = clean[:60].lower() + year_suffix
                fingerprints.add(fingerprint)
            if fingerprints:
                doc_refs[d["id"]] = fingerprints

        if len(doc_refs) < 2:
            self._stats["bibliographic_coupling"] = 0
            self._print(f"  📚 书目耦合: 0 条（有参考文献的文档不足 2 篇）")
            return

        # 2) 两两比对
        doc_ids = list(doc_refs.keys())
        for i in range(len(doc_ids)):
            for j in range(i + 1, len(doc_ids)):
                id1, id2 = doc_ids[i], doc_ids[j]
                pair = (id1, id2)
                if pair in existing_definitive:
                    continue

                shared = doc_refs[id1] & doc_refs[id2]
                n_shared = len(shared)
                if n_shared < min_shared:
                    continue

                confidence = "确定" if n_shared >= certain_th else "候选"
                self._insert_relation(
                    id1, id2,
                    relation_type="bibliographic_coupling",
                    confidence=confidence,
                    detail={
                        "shared_refs": n_shared,
                        "min_shared": min_shared,
                        "certain_threshold": certain_th,
                        "shared_fingerprints": list(shared)[:5],
                    },
                )
                counted += 1

        self._stats["bibliographic_coupling"] = counted
        self._print(f"  📚 书目耦合: {counted} 条")

    # ============================================================
    # 规则4: 关键词重合（keyword）
    # ============================================================

    def _calc_keyword_overlap(self, docs: List[Dict]):
        """
        规则3: 关键词重合 → 候选关联

        对每对文档（排除已有更强关联的），计算关键词 Jaccard 相似度。
        """
        threshold = self.config.relation.keyword_overlap_threshold
        counted = 0

        # 已有确定关联的 pair 跳过（不重复计算提示性关联）
        existing_definitive = self._get_existing_definitive_pairs()

        for i in range(len(docs)):
            for j in range(i + 1, len(docs)):
                d1, d2 = docs[i], docs[j]

                # 跳过已有确定关联的
                pair = (d1["id"], d2["id"])
                if pair in existing_definitive:
                    continue

                kw1 = set(d1["keywords"])
                kw2 = set(d2["keywords"])

                if not kw1 or not kw2:
                    continue

                intersection = kw1 & kw2
                union = kw1 | kw2

                if not union:
                    continue

                jaccard = len(intersection) / len(union)

                if jaccard >= threshold:
                    self._insert_relation(
                        d1["id"], d2["id"],
                        relation_type="keyword",
                        confidence="候选",
                        detail={
                            "jaccard": round(jaccard, 3),
                            "overlap_keywords": list(intersection)[:10],
                            "threshold": threshold,
                        },
                    )
                    counted += 1

        self._stats["keyword"] = counted
        self._print(f"  🏷️ 关键词关联: {counted} 条")

    # ============================================================
    # 规则4: 标题相似（title_similarity）
    # ============================================================

    def _calc_title_similarity(self, docs: List[Dict]):
        """
        规则4: 标题措辞相似 → 候选关联

        对每对无更强关联的文档，计算标题核心词重叠率。
        """
        threshold = self.config.relation.title_similarity_threshold
        counted = 0

        # 已有任何关联的 pair 跳过
        existing_all = self._get_existing_all_pairs()

        for i in range(len(docs)):
            for j in range(i + 1, len(docs)):
                d1, d2 = docs[i], docs[j]

                pair = (d1["id"], d2["id"])
                if pair in existing_all:
                    continue

                title1 = d1["title"]
                title2 = d2["title"]

                if not title1 or not title2:
                    continue

                # 跳过标题承接的基干标题匹配（那归规则2）
                base1, _ = self._extract_succession_info(title1)
                base2, _ = self._extract_succession_info(title2)
                if base1 and base2 and self._titles_match(base1, base2):
                    existing_all.add(pair)
                    continue

                words1 = self._extract_core_words(title1)
                words2 = self._extract_core_words(title2)

                if not words1 or not words2:
                    continue

                intersection = words1 & words2
                # 使用 Dice 系数（更适合同长度标题的比较）
                dice = 2 * len(intersection) / (len(words1) + len(words2))

                if dice >= threshold:
                    self._insert_relation(
                        d1["id"], d2["id"],
                        relation_type="title_similarity",
                        confidence="候选",
                        detail={
                            "dice": round(dice, 3),
                            "overlap_words": list(intersection)[:10],
                            "threshold": threshold,
                        },
                    )
                    counted += 1

        self._stats["title_similarity"] = counted
        self._print(f"  📝 标题相似: {counted} 条")

    # ============================================================
    # 规则5: 序论 TF-IDF 相似（intro_similarity）
    # ============================================================

    def _calc_intro_similarity(self, docs: List[Dict]):
        """
        规则5: 序论 TF-IDF 文本相似度 → 候选关联

        使用字符 n-gram（1~3）将每篇文档的序论文本拆分为特征向量，
        计算 TF-IDF + 余弦相似度。纯统计方法，不依赖任何 AI/嵌入。

        文本来源优先级（M11 修复2）：
          1. 该研究者对应的②梗概(summary)全文（信息密度最高）
          2. 本論(thesis)序论/intro_text
          3. ③公聴会資料(presentation)全文（PPTX文本提取较完整）
          4. 本論(thesis)的 abstract
        """
        threshold = self.config.relation.intro_similarity_threshold
        max_chars = self.config.relation.intro_max_chars
        min_chars = self.config.relation.intro_min_chars
        n_min, n_max = self.config.relation.intro_n_gram_range

        # 1) 构建研究者级最佳文本查找表（M11 修复2）
        # key: (researcher, academic_year) → {text, doc_type, file_id}
        researcher_best_text = {}
        for d in docs:
            researcher = d.get("researcher")
            year = d.get("academic_year")
            if not researcher or not year:
                continue
            key = (researcher, year)

            # 提取当前文档的文本
            intro = (d.get("extra_metadata") or {}).get("intro_text", "")
            raw_text = intro or (d.get("abstract", "") or "")
            text_len = len(raw_text) if raw_text else 0

            # 优先级：summary(3) > thesis_with_intro(2.5) > presentation(2) > thesis_no_intro(1)
            doc_type = d.get("doc_type", "")
            has_intro = bool(intro and len(intro) >= min_chars)
            if doc_type == "summary":
                priority = 3.0
            elif doc_type == "thesis" and has_intro:
                priority = 2.5
            elif doc_type == "presentation":
                priority = 2.0
            elif doc_type == "thesis":
                priority = 1.0
            else:
                priority = 0.0

            # 只保留满足最小字符数的文本
            if text_len < min_chars:
                continue

            # 取更高优先级的文本（优先级相同取更长文本）
            existing = researcher_best_text.get(key)
            if existing is None or priority > existing["priority"] or (
                    abs(priority - existing["priority"]) < 0.01 and text_len > existing["len"]):
                researcher_best_text[key] = {
                    "text": raw_text[:max_chars],
                    "priority": priority,
                    "len": text_len,
                    "doc_type": doc_type,
                    "file_id": d["id"],
                }

        # 1b) 按文档遍历，用研究者级最佳文本替换
        texts = []
        valid_indices = []
        for i, d in enumerate(docs):
            researcher = d.get("researcher")
            year = d.get("academic_year")
            if not researcher or not year:
                continue
            key = (researcher, year)
            best = researcher_best_text.get(key)
            if best is None:
                continue
            texts.append(best["text"])
            valid_indices.append(i)

        if len(valid_indices) < 2:
            self._stats["intro_similarity"] = 0
            self._print(f"  📖 序论相似: 0 条（有效序论文本不足）")
            return

        # 2) 构建 TF-IDF 特征（手工实现）
        # 对每篇文档生成字符 n-gram 向量
        def _char_ngrams(text: str, n_min: int, n_max: int) -> Counter:
            """生成字符 n-gram 计数"""
            c = Counter()
            for n in range(n_min, n_max + 1):
                for i in range(len(text) - n + 1):
                    gram = text[i:i + n]
                    c[gram] += 1
            return c

        # 文档总数
        N = len(valid_indices)

        # 所有文档的 n-gram 计数列表
        doc_ngrams = []
        # 收集所有出现的 gram（用于 IDF）
        all_grams = set()
        for idx in valid_indices:
            gram_counter = _char_ngrams(texts[len(doc_ngrams)], n_min, n_max)
            doc_ngrams.append(gram_counter)
            all_grams.update(gram_counter.keys())

        # 计算 IDF: idf(t) = log(N / df(t)) + 1
        gram_idf = {}
        for gram in all_grams:
            df = sum(1 for gc in doc_ngrams if gc.get(gram, 0) > 0)
            gram_idf[gram] = math.log(N / (1 + df)) + 1  # +1 平滑

        # 3) 计算每对文档的余弦相似度
        def _cosine_sim(gc1: Counter, gc2: Counter, idf_map: dict) -> float:
            """计算两个 TF-IDF 向量的余弦相似度"""
            common = set(gc1.keys()) & set(gc2.keys())
            if not common:
                return 0.0
            dot = sum(gc1[g] * gc2[g] * idf_map[g] ** 2 for g in common)
            norm1 = math.sqrt(sum(v ** 2 * idf_map[g] ** 2 for g, v in gc1.items()))
            norm2 = math.sqrt(sum(v ** 2 * idf_map[g] ** 2 for g, v in gc2.items()))
            if norm1 == 0 or norm2 == 0:
                return 0.0
            return dot / (norm1 * norm2)

        counted = 0
        existing_definitive = self._get_existing_definitive_pairs()
        existing_all = self._get_existing_all_pairs()

        for i in range(len(valid_indices)):
            for j in range(i + 1, len(valid_indices)):
                d1 = docs[valid_indices[i]]
                d2 = docs[valid_indices[j]]
                pair = (d1["id"], d2["id"])
                # 已有更强关联的不重复计算
                if pair in existing_definitive:
                    continue

                sim = _cosine_sim(doc_ngrams[i], doc_ngrams[j], gram_idf)

                if sim >= threshold:
                    # 提取共有 n-gram 片段用于 detail
                    common_grams = set(doc_ngrams[i].keys()) & set(doc_ngrams[j].keys())
                    # 取最长的前 5 个共有字符片段作为展示
                    top_grams = sorted(common_grams, key=len, reverse=True)[:5]

                    self._insert_relation(
                        d1["id"], d2["id"],
                        relation_type="intro_similarity",
                        confidence="候选",
                        detail={
                            "cosine_sim": round(sim, 4),
                            "threshold": threshold,
                            "top_grams": top_grams,
                            "text1_preview": texts[i][:80],
                            "text2_preview": texts[j][:80],
                        },
                    )
                    counted += 1
                    # 标记已关联，避免 keywords/标题再算一次
                    existing_all.add(pair)

        self._stats["intro_similarity"] = counted
        self._print(f"  📖 序论相似: {counted} 条")

    # ============================================================
    # 内部辅助
    # ============================================================

    def _insert_relation(self, src_id: int, tgt_id: int,
                          relation_type: str, confidence: str,
                          detail: dict):
        """插入一条机器关联到 auto_relations 表"""
        # 确保 src_id < tgt_id（CHECK 约束）
        if src_id > tgt_id:
            src_id, tgt_id = tgt_id, src_id
        elif src_id == tgt_id:
            return

        detail_json = json.dumps(detail, ensure_ascii=False) if detail else None

        try:
            self._conn.execute("""
                INSERT OR IGNORE INTO auto_relations
                    (source_file_id, target_file_id, relation_type, confidence, detail)
                VALUES (?, ?, ?, ?, ?)
            """, (src_id, tgt_id, relation_type, confidence, detail_json))
        except sqlite3.IntegrityError:
            pass  # 重复忽略

    def _get_existing_definitive_pairs(self) -> Set[Tuple[int, int]]:
        """获取已有确定关联的 pair 集合"""
        pairs = set()
        rows = self._conn.execute("""
            SELECT source_file_id, target_file_id FROM auto_relations
            WHERE confidence = '确定'
        """).fetchall()
        for r in rows:
            pairs.add((r[0], r[1]))
        return pairs

    def _get_existing_all_pairs(self) -> Set[Tuple[int, int]]:
        """获取已有任何关联的 pair 集合"""
        pairs = set()
        rows = self._conn.execute("""
            SELECT source_file_id, target_file_id FROM auto_relations
        """).fetchall()
        for r in rows:
            pairs.add((r[0], r[1]))
        return pairs

    @staticmethod
    def _clean_title(title: str) -> str:
        """清理标题：去标点、统一空格、小写"""
        title = re.sub(r"[、。，．,．!！?？;；:：\"\"''「」『』【】（）()\[\]【】《》〈〉]", " ", title)
        title = re.sub(r"\s+", " ", title).strip().lower()
        return title

    @staticmethod
    def _shorten_title(title: str) -> str:
        """截取标题的核心部分（取前60个字符，利于匹配）"""
        return title[:60] if title else ""

    @staticmethod
    def _extract_core_words(text: str) -> Set[str]:
        """
        提取标题的核心词

        对中日文（无空格分隔的语言），拆分为单个字符（n-gram 方式）。
        对英文，保留完整单词。
        过滤停用词、纯数字、过短的词。
        """
        words = set()
        # 提取英文单词
        for token in re.findall(r"[a-zA-Z]+", text):
            token = token.strip().lower()
            if token and len(token) >= 2 and token not in _STOP_WORDS:
                words.add(token)

        # 提取中日文字符（拆分为单个字符，过滤停用词）
        cjk_chars = re.findall(r"[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]", text)
        for ch in cjk_chars:
            ch_lower = ch.lower()
            if ch_lower in _STOP_WORDS:
                continue
            if len(ch_lower.strip()) >= 1:
                words.add(ch_lower)

        return words

    # ============================================================
    # 输出
    # ============================================================

    def _print(self, msg: str, err: bool = False):
        fp = __import__("sys").stderr if err else __import__("sys").stdout
        print(msg, file=fp, flush=True)

    def _print_summary(self):
        elapsed = time.time() - self._start_time
        s = self._stats
        total = sum(s.values())
        self._print("")
        self._print("=" * 50)
        self._print("📊 关联推算完成")
        self._print(f"   引用(citation):       {s['citation']}")
        self._print(f"   承接(title_succession): {s['title_succession']}")
        self._print(f"   书目耦合(bibliographic_coupling): {s['bibliographic_coupling']}")
        self._print(f"   关键词(keyword):       {s['keyword']}")
        self._print(f"   标题相似(title_similarity): {s['title_similarity']}")
        self._print(f"   序论相似(intro_similarity): {s['intro_similarity']}")
        self._print(f"   ─────────────────────")
        self._print(f"   合计:                 {total}")
        self._print(f"   耗时:                 {elapsed:.1f}s")
        self._print("=" * 50)
