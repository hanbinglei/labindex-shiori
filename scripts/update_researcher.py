"""
脚本：只重新跑路径解析，更新 auto_metadata 中的 researcher 字段
（不读文件内容，不碰 NAS）
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
from src.config import AppConfig
from src.parser.path_parser import PathParser

def main():
    config = AppConfig.load()
    db_path = config.database.path
    conn = sqlite3.connect(db_path, timeout=60)
    conn.row_factory = sqlite3.Row

    parser = PathParser(config)

    # 获取所有 active 文件
    files = conn.execute(
        "SELECT id, path FROM auto_files WHERE status='active' ORDER BY id"
    ).fetchall()
    print(f"Total active files: {len(files)}")

    updated = 0
    stats = {"thesis": 0, "summary": 0, "presentation": 0, "other": 0}

    for f in files:
        file_id = f["id"]
        file_path = f["path"]

        # 只跑路径解析
        path_data = parser.parse(file_path)
        researcher = path_data.get("researcher")
        doc_type = path_data.get("doc_type")

        if doc_type in stats:
            stats[doc_type] += 1
        else:
            stats["other"] += 1

        # 只更新 researcher 字段
        if researcher is not None:
            conn.execute(
                "UPDATE auto_metadata SET researcher=? WHERE file_id=? "
                "AND (researcher IS NULL OR researcher != ?)",
                (researcher, file_id, researcher)
            )
            if conn.total_changes > 0:
                updated += 1

    conn.commit()
    conn.close()

    print(f"\nDoc type distribution: {stats}")
    print(f"Researcher field updated: {updated} files")

    # 验证
    conn2 = sqlite3.connect(db_path, timeout=60)
    empty = conn2.execute(
        "SELECT COUNT(*) FROM auto_metadata WHERE researcher IS NULL OR researcher = ''"
    ).fetchone()[0]
    total = conn2.execute("SELECT COUNT(*) FROM auto_metadata").fetchone()[0]
    print(f"\nAfter update:")
    print(f"  Total metadata: {total}")
    print(f"  Empty researcher: {empty} ({empty*100//total}%)")
    print(f"  Filled: {total - empty} ({(total-empty)*100//total}%)")

    # By doc_type
    print("\n  By doc_type:")
    rows = conn2.execute('''
        SELECT doc_type, COUNT(*) as total,
               SUM(CASE WHEN researcher IS NULL OR researcher = '' THEN 1 ELSE 0 END) as empty_res
        FROM auto_metadata
        GROUP BY doc_type
        ORDER BY total DESC
    ''').fetchall()
    for r in rows:
        filled = r['total'] - r['empty_res']
        print(f'    {r["doc_type"]:20s}: total={r["total"]:>4}, empty={r["empty_res"]:>4}, filled={filled:>4}')
    conn2.close()

if __name__ == "__main__":
    main()
