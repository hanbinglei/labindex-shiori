"""
LabIndex Shiori — Web UI (Flask 后端)

提供:
  - 検索 API (Meilisearch 検索, master key で内部呼び出し)
  - 文書詳細 / タイムライン / 関連図
  - overlay CRUD (修正 / 除外 / 関連)
  - i18n (多言語切り替え)
  - フロントエンドは master key を直接触らず、すべてこの API 経由
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

import meilisearch
from meilisearch.errors import MeilisearchCommunicationError
from flask import Flask, jsonify, request, render_template, send_from_directory

from src.config import AppConfig
from src.overlay.manager import OverlayManager

logger = logging.getLogger(__name__)

# -----------------------------------------------------------
# Flask アプリ初期化
# -----------------------------------------------------------
app = Flask(
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
    static_folder=os.path.join(os.path.dirname(__file__), "static"),
)

_config: Optional[AppConfig] = None
_client: Optional[meilisearch.Client] = None


def _get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = AppConfig.load()
    return _config


def _get_client() -> meilisearch.Client:
    global _client
    if _client is None:
        cfg = _get_config()
        _client = meilisearch.Client(cfg.meilisearch.url, cfg.meilisearch.api_key)
    return _client


def _get_db() -> sqlite3.Connection:
    cfg = _get_config()
    conn = sqlite3.connect(cfg.database.path)
    conn.row_factory = sqlite3.Row
    return conn


# -----------------------------------------------------------
# i18n 翻訳ローダー
# -----------------------------------------------------------
_i18n_cache: Dict[str, dict] = {}


def _load_i18n(lang: str) -> dict:
    if lang in _i18n_cache:
        return _i18n_cache[lang]
    cfg = _get_config()
    path = os.path.join(cfg.i18n.locales_dir, f"{lang}.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _i18n_cache[lang] = data
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        # fallback to Japanese
        path_ja = os.path.join(cfg.i18n.locales_dir, "ja.json")
        with open(path_ja, "r", encoding="utf-8") as f:
            data = json.load(f)
        _i18n_cache[lang] = data
        return data


def _tt(lang: str, *keys: str) -> str:
    """翻訳取得 (ドット区切りキー)"""
    data = _load_i18n(lang)
    for key in keys:
        if isinstance(data, dict):
            data = data.get(key, {})
        else:
            return str(data)
    return str(data) if data else ""


# -----------------------------------------------------------
# API Routes
# -----------------------------------------------------------


@app.route("/")
def index():
    """メインページ"""
    cfg = _get_config()
    default_lang = cfg.i18n.default_language
    return render_template("index.html", default_lang=default_lang)


@app.route("/api/config")
def api_config():
    """フロントエンド設定 (サブテーマ一覧、ファイルタイプ、言語など)"""
    cfg = _get_config()
    topics_data = []
    for topic in cfg.topics:
        subtopics = []
        for st in topic.subtopics:
            subtopics.append({
                "name": st.name,
                "display_name_ja": st.display_name_ja,
                "display_name_zh": st.display_name_zh,
                "display_name_en": st.display_name_en,
            })
        topics_data.append({
            "name": topic.name,
            "subtopics": subtopics,
        })

    return jsonify({
        "topics": topics_data,
        "file_types": [".pdf", ".docx", ".xlsx", ".dwg", ".dxf", ".vwx"],
        "default_lang": cfg.i18n.default_language,
        "unclassified": cfg.unclassified,
    })


@app.route("/api/search")
def api_search():
    """検索 API (Meilisearch 検索)"""
    q = request.args.get("q", "")
    year = request.args.get("year", "")
    subtopic = request.args.get("subtopic", "")
    file_type = request.args.get("type", "")
    page = int(request.args.get("page", 1))
    limit = 20
    offset = (page - 1) * limit

    try:
        client = _get_client()
        index = client.index("labindex_shiori")

        # フィルター構築
        filters = []
        if year:
            filters.append(f"year = {year}")
        if subtopic:
            filters.append(f"subtopic = {subtopic}")
        if file_type:
            filters.append(f"extension = '{file_type}'")

        search_params = {
            "limit": limit,
            "offset": offset,
            "attributesToHighlight": ["title", "abstract"],
        }
        if filters:
            search_params["filter"] = " AND ".join(filters)

        result = index.search(q, search_params)
        hits = result.get("hits", result.hits if hasattr(result, "hits") else [])
        total = result.get("estimatedTotalHits", result.estimated_total_hits if hasattr(result, "estimated_total_hits") else len(hits))

        docs = []
        for h in hits:
            hd = h if isinstance(h, dict) else h.__dict__
            doc = {
                "id": hd.get("id"),
                "filename": hd.get("filename", ""),
                "title": hd.get("title", ""),
                "title_source": hd.get("title_source", ""),
                "keywords": hd.get("keywords", ""),
                "keywords_source": hd.get("keywords_source", ""),
                "year": hd.get("year"),
                "extension": hd.get("extension", ""),
                "subtopic": hd.get("subtopic", ""),
                "abstract": (hd.get("abstract") or "")[:200],
                "note": hd.get("note", ""),
            }
            # ハイライト
            fmt = hd.get("_formatted", {})
            if fmt:
                doc["title_hl"] = fmt.get("title", doc["title"])
            docs.append(doc)

        return jsonify({
            "hits": docs,
            "total": total,
            "page": page,
            "pages": (total + limit - 1) // limit,
        })
    except MeilisearchCommunicationError:
        return jsonify({
            "hits": [], "total": 0, "page": 1, "pages": 0,
            "error": "Meilisearch が起動していません。「python -m src.main serve」の前に Meilisearch を起動してください。",
            "error_i18n_key": "search.meilisearch_not_running",
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/document/<int:file_id>")
def api_document(file_id: int):
    """文書詳細"""
    conn = _get_db()
    row = conn.execute("""
        SELECT
            f.id, f.filename, f.extension, f.path, f.file_size,
            COALESCE(oc_title.corrected_value, m.title) AS title,
            COALESCE(oc_keywords.corrected_value, m.keywords) AS keywords,
            COALESCE(oc_year.corrected_value, m.year) AS year,
            COALESCE(oc_subtopic.corrected_value, m.subtopic) AS subtopic,
            m.title_source, m.keywords_source,
            m.abstract, m.authors, m.references_text,
            m.extra_metadata
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
        WHERE f.id = ?
    """, (file_id,)).fetchone()

    if not row:
        return jsonify({"error": "not found"}), 404

    # 除外状態
    excluded = conn.execute(
        "SELECT reason FROM overlay_exclusions WHERE file_id=?", (file_id,)
    ).fetchone()

    conn.close()

    # キーワードパース
    keywords_list = []
    try:
        keywords_list = json.loads(row["keywords"]) if row["keywords"] else []
    except (json.JSONDecodeError, TypeError):
        keywords_list = [row["keywords"]] if row["keywords"] else []

    # 著者パース
    authors_list = []
    try:
        authors_list = json.loads(row["authors"]) if row["authors"] else []
    except (json.JSONDecodeError, TypeError):
        authors_list = [row["authors"]] if row["authors"] else []

    # extra_metadata から note 取得
    note = None
    try:
        extra = json.loads(row["extra_metadata"]) if row["extra_metadata"] else {}
        note = extra.get("note")
    except (json.JSONDecodeError, TypeError):
        pass

    # ファイルサイス表示
    size_str = ""
    if row["file_size"]:
        s = row["file_size"]
        if s < 1024:
            size_str = f"{s}B"
        elif s < 1024 * 1024:
            size_str = f"{s//1024}KB"
        else:
            size_str = f"{s/(1024*1024):.1f}MB"

    return jsonify({
        "id": row["id"],
        "filename": row["filename"],
        "extension": row["extension"],
        "path": row["path"],
        "file_size": size_str,
        "title": row["title"],
        "title_source": row["title_source"],
        "keywords": keywords_list,
        "keywords_source": row["keywords_source"],
        "year": row["year"],
        "subtopic": row["subtopic"],
        "abstract": row["abstract"],
        "authors": authors_list,
        "note": note,
        "excluded": bool(excluded),
        "exclude_reason": excluded["reason"] if excluded else None,
    })


@app.route("/api/timeline")
def api_timeline():
    """タイムライン (年度別文書数)"""
    conn = _get_db()
    rows = conn.execute("""
        SELECT COALESCE(oc_year.corrected_value, m.year) AS year,
               COUNT(*) AS cnt
        FROM auto_metadata m
        JOIN auto_files f ON f.id = m.file_id
        LEFT JOIN overlay_corrections oc_year
            ON oc_year.file_id = f.id AND oc_year.field_name = 'year'
        LEFT JOIN overlay_corrections oc_subtopic
            ON oc_subtopic.file_id = f.id AND oc_subtopic.field_name = 'subtopic'
        WHERE f.status = 'active'
          AND f.id NOT IN (SELECT file_id FROM overlay_exclusions)
          AND COALESCE(oc_year.corrected_value, m.year) IS NOT NULL
        GROUP BY year ORDER BY year
    """).fetchall()
    conn.close()
    return jsonify([{"year": r["year"], "count": r["cnt"]} for r in rows])


@app.route("/api/relations/<int:file_id>")
def api_relations(file_id: int):
    """関連文書 (共通キーワード + overlay 関連)"""
    conn = _get_db()

    # ドキュメントのキーワードを取得
    row = conn.execute("""
        SELECT COALESCE(oc_kw.corrected_value, m.keywords) AS keywords
        FROM auto_metadata m
        JOIN auto_files f ON f.id = m.file_id
        LEFT JOIN overlay_corrections oc_kw
            ON oc_kw.file_id = f.id AND oc_kw.field_name = 'keywords'
        WHERE f.id = ?
    """, (file_id,)).fetchone()

    if not row or not row["keywords"]:
        conn.close()
        return jsonify([])

    try:
        keywords = json.loads(row["keywords"])
    except (json.JSONDecodeError, TypeError):
        keywords = []

    if not keywords:
        conn.close()
        return jsonify([])

    # 共通キーワードを持つ文書を検索
    relations = []
    for kw in keywords:
        kw_lower = kw.lower()
        # SQLite の LIKE で部分一致検索
        related = conn.execute("""
            SELECT DISTINCT f.id, f.filename,
                   COALESCE(oc_title.corrected_value, m.title) AS title,
                   m.keywords
            FROM auto_metadata m
            JOIN auto_files f ON f.id = m.file_id
            LEFT JOIN overlay_corrections oc_title
                ON oc_title.file_id = f.id AND oc_title.field_name = 'title'
            WHERE f.id != ?
              AND f.status = 'active'
              AND f.id NOT IN (SELECT file_id FROM overlay_exclusions)
              AND (
                  m.keywords LIKE ? OR
                  m.abstract LIKE ?
              )
            LIMIT 20
        """, (file_id, f"%{kw_lower}%", f"%{kw_lower}%")).fetchall()

        for r in related:
            rel_kw = []
            try:
                rel_kw = json.loads(r["keywords"]) if r["keywords"] else []
            except (json.JSONDecodeError, TypeError):
                pass
            shared = [k for k in rel_kw if k.lower() in [kw.lower() for kw in keywords]]
            relations.append({
                "id": r["id"],
                "title": r["title"],
                "filename": r["filename"],
                "shared_keywords": shared[:5],
                "type": "keyword_shared",
            })

    # overlay 関連
    overlay_rels = conn.execute("""
        SELECT r.id AS rel_id, r.relation_type, r.note,
               CASE WHEN r.file_id_a = ? THEN r.file_id_b ELSE r.file_id_a END AS other_id
        FROM overlay_relations r
        WHERE r.file_id_a = ? OR r.file_id_b = ?
    """, (file_id, file_id, file_id)).fetchall()

    for r in overlay_rels:
        other = conn.execute("""
            SELECT COALESCE(oc_title.corrected_value, m.title) AS title, f.filename
            FROM auto_metadata m
            JOIN auto_files f ON f.id = m.file_id
            LEFT JOIN overlay_corrections oc_title
                ON oc_title.file_id = f.id AND oc_title.field_name = 'title'
            WHERE f.id = ?
        """, (r["other_id"],)).fetchone()
        if other:
            relations.append({
                "id": r["other_id"],
                "title": other["title"],
                "filename": other["filename"],
                "shared_keywords": [],
                "type": r["relation_type"],
                "rel_id": r["rel_id"],
                "note": r["note"],
            })

    # 重複除去 (id でユニーク)
    seen = set()
    unique = []
    for r in relations:
        if r["id"] not in seen:
            seen.add(r["id"])
            unique.append(r)

    conn.close()
    return jsonify(unique[:30])


@app.route("/api/overlay/<int:file_id>")
def api_overlay_get(file_id: int):
    """overlay 状態取得"""
    cfg = _get_config()
    mgr = OverlayManager(cfg)
    result = mgr.show(file_id)
    return jsonify(result)


@app.route("/api/overlay/correct", methods=["POST"])
def api_overlay_correct():
    """修正適用"""
    data = request.get_json()
    cfg = _get_config()
    mgr = OverlayManager(cfg)
    result = mgr.correct(data["file_id"], data["field"], data["value"])
    return jsonify(result)


@app.route("/api/overlay/exclude", methods=["POST"])
def api_overlay_exclude():
    """除外"""
    data = request.get_json()
    cfg = _get_config()
    mgr = OverlayManager(cfg)
    result = mgr.exclude(data["file_id"], data.get("reason", ""))
    return jsonify(result)


@app.route("/api/overlay/include", methods=["POST"])
def api_overlay_include():
    """復帰"""
    data = request.get_json()
    cfg = _get_config()
    mgr = OverlayManager(cfg)
    result = mgr.include(data["file_id"])
    return jsonify(result)


@app.route("/api/overlay/relation", methods=["POST"])
def api_overlay_relation_add():
    """関連追加"""
    data = request.get_json()
    cfg = _get_config()
    mgr = OverlayManager(cfg)
    result = mgr.add_relation(data["file_id_a"], data["file_id_b"],
                              data.get("type", "related"), data.get("note", ""))
    return jsonify(result)


@app.route("/api/overlay/relation/<int:relation_id>", methods=["DELETE"])
def api_overlay_relation_remove(relation_id: int):
    """関連削除"""
    cfg = _get_config()
    mgr = OverlayManager(cfg)
    result = mgr.remove_relation(relation_id)
    return jsonify(result)


@app.route("/api/i18n/<lang>")
def api_i18n(lang: str):
    """翻訳 JSON 取得"""
    try:
        data = _load_i18n(lang)
        return jsonify(data)
    except Exception:
        return jsonify(_load_i18n("ja"))


# -----------------------------------------------------------
# API: Lineage (M11: 研究系譜図)
# -----------------------------------------------------------


@app.route("/api/lineage")
def api_lineage():
    """系譜図データ（M11 重做）：研究者×年度の集約ノードと研究者間エッジ"""
    try:
        from src.relation.engine import RelationEngine
        cfg = _get_config()
        engine = RelationEngine(cfg)

        conn = _get_db()

        # 全ノード：ファイル→研究者ごとに集約
        rows = conn.execute("""
            SELECT f.id,
                   COALESCE(ocr.corrected_value, m.researcher) AS researcher,
                   COALESCE(ocy.corrected_value, m.academic_year) AS academic_year,
                   COALESCE(oct.corrected_value, m.title) AS title,
                   COALESCE(ocs.corrected_value, m.subtopic) AS subtopic,
                   f.filename, f.extension, m.degree
            FROM auto_files f
            JOIN auto_metadata m ON f.id = m.file_id
            LEFT JOIN overlay_corrections ocr ON ocr.file_id = f.id AND ocr.field_name = 'researcher'
            LEFT JOIN overlay_corrections ocy ON ocy.file_id = f.id AND ocy.field_name = 'academic_year'
            LEFT JOIN overlay_corrections oct ON oct.file_id = f.id AND oct.field_name = 'title'
            LEFT JOIN overlay_corrections ocs ON ocs.file_id = f.id AND ocs.field_name = 'subtopic'
            WHERE f.status = 'active'
              AND f.id NOT IN (SELECT file_id FROM overlay_exclusions)
              AND COALESCE(ocr.corrected_value, m.researcher) IS NOT NULL
              AND COALESCE(ocy.corrected_value, m.academic_year) IS NOT NULL
            ORDER BY COALESCE(ocy.corrected_value, m.academic_year), researcher
        """).fetchall()

        # 集約：researcher + academic_year → ノード
        node_map = {}  # key: (researcher, year) → node
        node_list = []
        for r in rows:
            key = (r["researcher"], r["academic_year"])
            if key not in node_map:
                n = {
                    "id": len(node_list),
                    "researcher": r["researcher"],
                    "academic_year": r["academic_year"],
                    "degree": r["degree"],
                    "file_count": 0,
                    "file_ids": [],
                    "titles": [],
                    "subtopics": set(),
                    "extensions": set(),
                }
                node_map[key] = n
                node_list.append(n)
            n = node_map[key]
            n["file_count"] += 1
            n["file_ids"].append(r["id"])
            if r["title"]:
                n["titles"].append(r["title"])
            if r["subtopic"]:
                n["subtopics"].add(r["subtopic"])
            if r["extension"]:
                n["extensions"].add(r["extension"])

        # 排序：按 academic_year 升序
        node_list.sort(key=lambda n: (n["academic_year"], n["researcher"]))

        # 收集年份列表
        years_set = set()
        for n in node_list:
            if n["academic_year"]:
                years_set.add(n["academic_year"])
        years = sorted(years_set)

        # 研究者名+年 → subtopics のルックアップ（エッジの色付け用）
        node_subtopics = {}
        for n in node_list:
            node_subtopics[(n["researcher"], n["academic_year"])] = n["subtopics"]

        # 研究者間エッジ：ファイルレベルの関連を研究者レベルに集約
        file_edges = engine.get_all_relations()

        # file_id → (researcher, year) のルックアップ
        file_to_node = {}
        for r in rows:
            file_to_node[r["id"]] = (r["researcher"], r["academic_year"])

        edge_set = {}  # key: (src_res, src_yr, tgt_res, tgt_yr, type) → edge
        for e in file_edges:
            src_key = file_to_node.get(e["source_file_id"])
            tgt_key = file_to_node.get(e["target_file_id"])
            if not src_key or not tgt_key:
                continue
            # 不保留自己对自己的边
            if src_key == tgt_key:
                continue

            rel_type = e["relation_type"]
            edge_key = (src_key[0], src_key[1], tgt_key[0], tgt_key[1], rel_type)
            if edge_key not in edge_set:
                # 提取 detail 中的 jaccard 或 dice 用于判断关键词重合度
                detail = e.get("detail", {}) if isinstance(e.get("detail"), dict) else {}
                best_jaccard = detail.get("jaccard", 0) or 0
                # 计算共有子主题（用于边缘颜色）
                src_st = node_subtopics.get(src_key, set())
                tgt_st = node_subtopics.get(tgt_key, set())
                shared = src_st & tgt_st
                # 取第一个有意义的子主题作为颜色标记
                raw_topic = "unclassified"
                for st in shared:
                    if st and st not in ("未分類", "unclassified", "Unclassified"):
                        raw_topic = st
                        break
                if raw_topic == "unclassified" and shared:
                    raw_topic = "unclassified"
                # 多值标签（如 "grout_filling,loading_test"）取第一个
                shared_topic = raw_topic.split(",")[0].strip() if raw_topic else "unclassified"
                # keyword/title_similarity 按重合关键词质量决定是否上色
                if rel_type in ("keyword", "title_similarity"):
                    detail = e.get("detail", {}) or {}
                    overlap_kws = detail.get("overlap_keywords", []) or []
                    overlap_words = detail.get("overlap_words", []) or []
                    all_kws = overlap_kws + overlap_words
                    # 过滤通用实验类关键词
                    generic_kws = ["実験", "試験", "試験体", "載荷", "図", "データ",
                                   "結果", "検討", "比較", "解析", "計算", "測定",
                                   "評価", "特性", "供試体", "実験体", "ケース",
                                   "test", "data", "specimen", "case", "sample"]
                    meaningful = [kw for kw in all_kws if not any(g in kw for g in generic_kws)
                                  and len(kw) > 1]
                    if not meaningful:
                        shared_topic = ""
                edge_set[edge_key] = {
                    "source_researcher": src_key[0],
                    "source_year": src_key[1],
                    "target_researcher": tgt_key[0],
                    "target_year": tgt_key[1],
                    "relation_type": rel_type,
                    "confidence": e["confidence"],
                    "detail": e.get("detail", {}),
                    "strength": 1,
                    "_best_jaccard": best_jaccard,
                    "shared_topic": shared_topic,
                }
            else:
                # 已有同类型边则增加强度（不同类型不会合并到同一key）
                cur = edge_set[edge_key]
                cur["strength"] += 1

        # 清理内部字段
        for e_data in edge_set.values():
            e_data.pop("_best_jaccard", None)

        conn.close()

        # 传递闭包剪枝：A→B + B→C 已存在 ⇒ A→C 是冗余
        # 只对"候选"（未升级的keyword/title_similarity）做剪枝
        edge_items = list(edge_set.items())
        adj = {}
        for e_key, e_data in edge_items:
            src = (e_data["source_researcher"], e_data["source_year"])
            tgt = (e_data["target_researcher"], e_data["target_year"])
            if src != tgt:
                adj.setdefault(src, set()).add(tgt)
        redundant_keys = set()
        for e_key, e_data in edge_items:
            if e_data.get("confidence") != "候选":
                continue
            src = (e_data["source_researcher"], e_data["source_year"])
            tgt = (e_data["target_researcher"], e_data["target_year"])
            if src == tgt:
                continue
            mids = adj.get(src, set())
            for mid in mids:
                if mid == src or mid == tgt:
                    continue
                if mid in adj and tgt in adj[mid]:
                    redundant_keys.add(e_key)
                    break
        for k in redundant_keys:
            del edge_set[k]

        # set→list
        for n in node_list:
            n["subtopics"] = list(n["subtopics"])
            n["extensions"] = list(n["extensions"])

        # 合并手动连线（以手动为准）
        manual_edges = _load_manual_edges()
        manual_pairs = set()
        for me in manual_edges:
            manual_pairs.add((me["source"], me["source_year"], me["target"], me["target_year"]))
        # 移除所有与手动边同研究者对的自动边（无论类型）
        edge_set = {k: v for k, v in edge_set.items()
                    if (v["source_researcher"], v["source_year"], v["target_researcher"], v["target_year"]) not in manual_pairs}
        for me in manual_edges:
            e_key = (me["source"], me["source_year"], me["target"], me["target_year"], "manual")
            edge_set[e_key] = {
                "source_researcher": me["source"],
                "source_year": me["source_year"],
                "target_researcher": me["target"],
                "target_year": me["target_year"],
                "relation_type": "manual",
                "confidence": me.get("confidence", "候选"),
                "shared_topic": "",
                "strength": 1,
            }

        return jsonify({
            "researcher_nodes": node_list,
            "researcher_edges": list(edge_set.values()),
            "years": years,
        })
    except Exception as e:
        logger.error("Lineage API error: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


# -----------------------------------------------------------
# API: Lineage 手动编辑（添加/删除研究者级连线）
# -----------------------------------------------------------
import json as json_module
import os

_MANUAL_EDGES_PATH = None


def _get_manual_edges_path() -> str:
    global _MANUAL_EDGES_PATH
    if _MANUAL_EDGES_PATH is None:
        cfg = _get_config()
        _MANUAL_EDGES_PATH = os.path.join(cfg.project_root, "data", "manual_lineage_edges.json")
    return _MANUAL_EDGES_PATH


def _load_manual_edges() -> list:
    path = _get_manual_edges_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json_module.load(f)
    except Exception:
        return []


def _save_manual_edges(edges: list) -> None:
    path = _get_manual_edges_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json_module.dump(edges, f, ensure_ascii=False, indent=2)


@app.route("/api/lineage/manual-edges")
def api_lineage_manual_edges():
    """返回手动连线列表（用于前端 Edit 模式判断）"""
    return jsonify(_load_manual_edges())


@app.route("/api/lineage/edge", methods=["POST"])
def api_lineage_add_edge():
    """添加手动研究者级连线"""
    data = request.get_json(force=True)
    src = data.get("source")
    src_year = data.get("source_year")
    tgt = data.get("target")
    tgt_year = data.get("target_year")
    if not src or not tgt or src_year is None or tgt_year is None:
        return jsonify({"error": "Missing source/target or year"}), 400

    key = (src, src_year, tgt, tgt_year)
    edges = _load_manual_edges()
    # 避免重复
    for e in edges:
        if (e["source"], e["source_year"], e["target"], e["target_year"]) == key:
            return jsonify({"status": "exists"})
    edges.append({
        "source": src,
        "source_year": src_year,
        "target": tgt,
        "target_year": tgt_year,
        "relation_type": "manual",
        "confidence": "候选",
    })
    _save_manual_edges(edges)
    return jsonify({"status": "added"})


@app.route("/api/lineage/edge", methods=["DELETE"])
def api_lineage_remove_edge():
    """删除手动研究者级连线"""
    data = request.get_json(force=True)
    src = data.get("source")
    src_year = data.get("source_year")
    tgt = data.get("target")
    tgt_year = data.get("target_year")

    edges = _load_manual_edges()
    before = len(edges)
    edges = [e for e in edges if not (
        e["source"] == src and e["source_year"] == src_year
        and e["target"] == tgt and e["target_year"] == tgt_year
    )]
    if len(edges) < before:
        _save_manual_edges(edges)
        return jsonify({"status": "removed"})
    return jsonify({"status": "not_found"})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
