"""
LabIndex Shiori — SQLite 数据库 Schema

=== 设计原则 ===
- auto_* 表: 自动扫描/抽取层，扫描时完全由代码覆写
- overlay_* 表: 人工修正层，任何时候不被自动过程覆盖
- 查询时 LEFT JOIN overlay + COALESCE 实现 overlay 优先

=== overlay 优先合并策略 ===
SELECT
  f.*,
  COALESCE(oc_title.corrected_value, m.title) AS title,
  COALESCE(oc_keywords.corrected_value, m.keywords) AS keywords,
  COALESCE(oc_year.corrected_value, m.year) AS year,
  COALESCE(oc_subtopic.corrected_value, m.subtopic) AS subtopic
FROM auto_files f
LEFT JOIN auto_metadata m ON m.file_id = f.id
LEFT JOIN overlay_corrections oc_title ON oc_title.file_id = f.id AND oc_title.field_name = 'title'
LEFT JOIN overlay_corrections oc_keywords ON oc_keywords.file_id = f.id AND oc_keywords.field_name = 'keywords'
LEFT JOIN overlay_corrections oc_year ON oc_year.file_id = f.id AND oc_year.field_name = 'year'
LEFT JOIN overlay_corrections oc_subtopic ON oc_subtopic.file_id = f.id AND oc_subtopic.field_name = 'subtopic'
WHERE f.id NOT IN (SELECT file_id FROM overlay_exclusions)
"""

# ============================================================
# DDL 语句
# ============================================================

SCHEMA_DDL = """
-- ============================================================
-- 1. auto_files — 文件注册表
--    记录所有被发现文件的路径、状态、hash
-- ============================================================
CREATE TABLE IF NOT EXISTS auto_files (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    path            TEXT    NOT NULL UNIQUE,          -- 文件的完整路径（UNC 或本地）
    filename        TEXT    NOT NULL,                 -- 文件名（含扩展名）
    extension       TEXT    NOT NULL,                 -- 小写扩展名 .pdf / .docx / ...
    file_size       INTEGER,                         -- 文件大小（字节）
    mtime           REAL,                            -- 最后修改时间戳（os.path.getmtime）
    hash            TEXT,                             -- 文件内容 SHA256（增量扫描用）
    status          TEXT    NOT NULL DEFAULT 'active', -- active / deleted
    scan_root_index INTEGER DEFAULT 0,                -- 所属 SCAN_ROOTS 索引（便于多路径扩展）
    first_seen      TEXT,                             -- 首次发现时间 ISO8601
    last_seen       TEXT,                             -- 最近确认存在时间 ISO8601
    last_parsed     TEXT                              -- 最近解析时间 ISO8601
);

CREATE INDEX IF NOT EXISTS idx_auto_files_status ON auto_files(status);
CREATE INDEX IF NOT EXISTS idx_auto_files_hash   ON auto_files(hash);
CREATE INDEX IF NOT EXISTS idx_auto_files_mtime  ON auto_files(mtime);


-- ============================================================
-- 2. auto_metadata — 自动抽取的元数据
--    每次扫描重新抽取后 UPSERT 此表
-- ============================================================
CREATE TABLE IF NOT EXISTS auto_metadata (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id         INTEGER NOT NULL UNIQUE REFERENCES auto_files(id) ON DELETE CASCADE,
    -- 标题
    title           TEXT,                             -- 抽取的标题
    title_source    TEXT DEFAULT 'inferred',           -- 'annotated'(原文标注) / 'inferred'(算法推测)
    -- 关键词
    keywords        TEXT,                             -- JSON 字符串数组
    keywords_source TEXT DEFAULT 'inferred',           -- 'annotated'(キーワード:/Keywords:标注) / 'inferred'(算法推测)
    -- 摘要
    abstract        TEXT,                             -- 摘要/正文预览
    abstract_source TEXT DEFAULT 'inferred',
    -- 分类
    subtopic        TEXT,                             -- 自动归入的子主题名称（未归入则为 NULL）
    subtopic_source TEXT DEFAULT 'inferred',           -- 'classified'(自动归类) / NULL(未分类)
    -- M8: 路径结构解析字段
    academic_year   INTEGER,                          -- 路径推断的学年度（如 202403→2023年度）
    degree          TEXT,                             -- 学位: D(博士) / M(修士) / B(卒業)
    doc_type        TEXT,                             -- 文档类型: thesis / summary / presentation / ...
    -- M9: 研究者维度
    researcher      TEXT,                             -- 路径推断的研究者姓名（如"富重仁"）
    -- 元信息
    year            INTEGER,                          -- 年份
    authors         TEXT,                             -- JSON 字符串数组
    document_type   TEXT,                             -- pdf / docx / xlsx / dwg / dxf / vwx / ...
    ref_count       INTEGER DEFAULT 0,                -- 参考文献数量
    references_text TEXT,                             -- JSON 字符串数组（参考文献列表）
    extra_metadata  TEXT,                             -- JSON 其余元信息（灵活扩展）
    parsed_at       TEXT                              -- 解析时间戳 ISO8601
);

CREATE INDEX IF NOT EXISTS idx_auto_metadata_year     ON auto_metadata(year);
CREATE INDEX IF NOT EXISTS idx_auto_metadata_subtopic ON auto_metadata(subtopic);


-- ============================================================
-- 3. overlay_corrections — 人工字段级修正
--    每条记录修正一个字段，查询时 LEFT JOIN + COALESCE 覆盖 auto_metadata
--    扫描时永不删除或修改此表
-- ============================================================
CREATE TABLE IF NOT EXISTS overlay_corrections (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id         INTEGER NOT NULL REFERENCES auto_files(id) ON DELETE CASCADE,
    field_name      TEXT    NOT NULL,                 -- 修正的字段名: title / keywords / year / subtopic
    corrected_value TEXT    NOT NULL,                 -- 修正后的值
    original_value  TEXT,                             -- 修正前的值（记录用途，不用于合并）
    reason          TEXT,                             -- 修正原因备注（可选）
    created_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE(file_id, field_name)
);

CREATE INDEX IF NOT EXISTS idx_overlay_corrections_file ON overlay_corrections(file_id);


-- ============================================================
-- 4. overlay_exclusions — 检索排除列表
--    被排除的文件不在检索结果中出现
-- ============================================================
CREATE TABLE IF NOT EXISTS overlay_exclusions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id         INTEGER NOT NULL REFERENCES auto_files(id) ON DELETE CASCADE UNIQUE,
    reason          TEXT,                             -- 排除原因
    created_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);


-- ============================================================
-- 5. overlay_relations — 人工关联关系
--    文件间的关联（共同关键词关联由查询时计算，不存表）
-- ============================================================
CREATE TABLE IF NOT EXISTS overlay_relations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id_a       INTEGER NOT NULL REFERENCES auto_files(id) ON DELETE CASCADE,
    file_id_b       INTEGER NOT NULL REFERENCES auto_files(id) ON DELETE CASCADE,
    relation_type   TEXT    NOT NULL DEFAULT 'related',  -- related / cites / cited_by
    note            TEXT,                             -- 关系说明（可选）
    created_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE(file_id_a, file_id_b, relation_type),
    CHECK(file_id_a < file_id_b)  -- 防止重复(A,B)和(B,A)
);

CREATE INDEX IF NOT EXISTS idx_overlay_relations_a ON overlay_relations(file_id_a);
CREATE INDEX IF NOT EXISTS idx_overlay_relations_b ON overlay_relations(file_id_b);


-- ============================================================
-- 6. auto_relations — 机器推算的关联（M10）
--    每条记录：源文档、目标文档、依据类型、可信度
--    四类依据：citation / title_succession / keyword / title_similarity
--    两类可信度：'确定' / '候选'
-- ============================================================
CREATE TABLE IF NOT EXISTS auto_relations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file_id  INTEGER NOT NULL REFERENCES auto_files(id) ON DELETE CASCADE,
    target_file_id  INTEGER NOT NULL REFERENCES auto_files(id) ON DELETE CASCADE,
    relation_type   TEXT    NOT NULL,                 -- citation / title_succession / keyword / title_similarity
    confidence      TEXT    NOT NULL DEFAULT '候选',   -- '确定' / '候选'
    detail          TEXT,                             -- JSON 额外信息（匹配片段、相似度分数等）
    created_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE(source_file_id, target_file_id, relation_type),
    CHECK(source_file_id < target_file_id)            -- 防止重复(A,B)和(B,A)
);

CREATE INDEX IF NOT EXISTS idx_auto_relations_source ON auto_relations(source_file_id);
CREATE INDEX IF NOT EXISTS idx_auto_relations_target ON auto_relations(target_file_id);


-- ============================================================
-- 7. overlay_relation_actions — 人工确认/拒绝机器关联（M10）
--    人确认了某条 auto_relation → 机器不覆盖
--    人拒绝了某条 auto_relation → 查询时过滤掉
-- ============================================================
CREATE TABLE IF NOT EXISTS overlay_relation_actions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file_id  INTEGER NOT NULL REFERENCES auto_files(id) ON DELETE CASCADE,
    target_file_id  INTEGER NOT NULL REFERENCES auto_files(id) ON DELETE CASCADE,
    relation_type   TEXT    NOT NULL,                 -- 对应 auto_relations.relation_type
    action          TEXT    NOT NULL,                 -- 'confirm' / 'reject'
    reason          TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE(source_file_id, target_file_id, relation_type)
);

CREATE INDEX IF NOT EXISTS idx_overlay_rel_actions ON overlay_relation_actions(source_file_id, target_file_id);
"""


def init_database(db_path: str) -> None:
    """
    初始化数据库：创建所有表（幂等，多次调用安全）
    
    Args:
        db_path: SQLite 数据库文件路径
    """
    import sqlite3
    import os

    # 自动创建父目录
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    conn = sqlite3.connect(db_path, timeout=60)
    conn.executescript(SCHEMA_DDL)

    # --- M8 迁移：为已有数据库添加新字段 ---
    # academic_year / degree / doc_type（幂等，列已存在则跳过）
    _migrate_add_column(conn, "auto_metadata", "academic_year", "INTEGER")
    _migrate_add_column(conn, "auto_metadata", "degree", "TEXT")
    _migrate_add_column(conn, "auto_metadata", "doc_type", "TEXT")

    # --- M9 迁移：研究者维度 ---
    _migrate_add_column(conn, "auto_metadata", "researcher", "TEXT")

    conn.commit()
    conn.close()


def _migrate_add_column(conn, table: str, column: str, col_type: str) -> None:
    """安全添加列（幂等）"""
    import sqlite3
    cursor = conn.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cursor.fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
