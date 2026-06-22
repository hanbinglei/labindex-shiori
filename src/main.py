#!/usr/bin/env python3
"""
LabIndex Shiori — 松尾研究室 研究资料横断检索系统
入口文件

用法:
    python -m src.main --help
    python -m src.main init                      (M1)
    python -m src.main scan --full                (M2)
    python -m src.main parse                      (M3)
    python -m src.main index --full               (M4)
    python -m src.main overlay correct ...         (M5)
    python -m src.main serve                      (M6)
    python -m src.main relation                   (M10)

CLI 命令逐步在后续里程碑中实现。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def cmd_scan(args: argparse.Namespace) -> None:
    """扫描命令入口"""
    from src.config import AppConfig
    from src.scanner.engine import ScannerEngine

    config = AppConfig.load()

    # --root 覆盖扫描根目录（不修改 config.yaml）
    if args.root:
        config.scan_roots = [args.root]

    engine = ScannerEngine(config)

    from src.database.schema import init_database
    init_database(config.database.path)

    if args.full:
        engine.scan_full()
    else:
        engine.scan_incremental()


def cmd_parse(args: argparse.Namespace) -> None:
    """解析命令入口"""
    from src.config import AppConfig
    from src.parser.engine import ParserEngine

    config = AppConfig.load()

    from src.database.schema import init_database
    init_database(config.database.path)

    engine = ParserEngine(config)
    if args.force:
        engine.parse_all()
    else:
        engine.parse_pending()


def cmd_index(args: argparse.Namespace) -> None:
    """索引命令入口：子主题归类 → 推入 Meilisearch"""
    from src.config import AppConfig
    from src.database.schema import init_database
    from src.indexer.classifier import classify_all
    from src.indexer.engine import IndexerEngine

    config = AppConfig.load()
    init_database(config.database.path)

    # Step 1: 子主题归类
    print("🔖 子主题自动归类...")
    cstats = classify_all(config)
    print(f"  归类: {cstats['classified']} 篇 | 未分類: {cstats['unclassified']} 篇 | 多归类: {cstats['multi']} 篇")

    # Step 2: 推入 Meilisearch
    engine = IndexerEngine(config)
    if args.full:
        engine.index_all()
    else:
        engine.index_incremental()


def cmd_overlay(args: argparse.Namespace) -> None:
    """Overlay 人工纠错入口"""
    import json
    from src.config import get_config
    from src.overlay.manager import OverlayManager

    config = get_config()
    mgr = OverlayManager(config)

    if args.action == "correct":
        result = mgr.correct(args.file_id, args.field, args.value)
    elif args.action == "exclude":
        result = mgr.exclude(args.file_id, args.reason)
    elif args.action == "include":
        result = mgr.include(args.file_id)
    elif args.action == "relation-add":
        result = mgr.add_relation(args.file_id_a, args.file_id_b,
                                   getattr(args, "type", "related"), args.note)
    elif args.action == "relation-remove":
        result = mgr.remove_relation(args.relation_id)
    elif args.action == "show":
        result = mgr.show(args.file_id)
    elif args.action == "status":
        result = mgr.status()
    else:
        result = {"status": "error", "message": f"未知操作: {args.action}"}

    print(json.dumps(result, ensure_ascii=False, indent=2))

    # 操作成功后提示重索引
    if result.get("status") == "ok" and args.action in ("correct", "exclude", "include"):
        print("\n💡 提示: 运行 'python -m src.main index' 将变更推送到 Meilisearch 检索")


def cmd_relation(args: argparse.Namespace) -> None:
    """M10: 关联推算命令入口"""
    from src.config import AppConfig
    from src.database.schema import init_database
    from src.relation.engine import RelationEngine

    config = AppConfig.load()
    init_database(config.database.path)

    engine = RelationEngine(config)
    stats = engine.calculate_all()

    if args.show:
        relations = engine.get_all_relations()
        print(f"\n共 {len(relations)} 条关联：")
        for r in relations:
            src_t = (r['source_title'] or '?')[:30]
            tgt_t = (r['target_title'] or '?')[:30]
            print(f"  [{r['relation_type']}|{r['confidence']}] "
                  f"ID{r['source_file_id']} → ID{r['target_file_id']} "
                  f"({src_t} → {tgt_t})")


def cmd_serve(args: argparse.Namespace) -> None:
    """启动 Web UI"""
    print("🌐 LabIndex Shiori Web UI を起動しています...")
    print("   http://127.0.0.1:5000")
    print("   Ctrl+C で停止\n")

    from src.web.app import app
    app.run(host="127.0.0.1", port=5000, debug=False)


def cmd_init(args: argparse.Namespace) -> None:
    """初始化数据库"""
    from src.database.schema import init_database
    from src.config import get_config

    config = get_config()
    init_database(config.database.path)
    print(f"✓ 数据库已初始化: {config.database.path}")
    print(f"  扫描根目录: {len(config.scan_roots)} 个")
    for i, root in enumerate(config.scan_roots):
        print(f"    [{i}] {root}")
    print(f"  大主题: {len(config.topics)} 个")
    for topic in config.topics:
        print(f"    - {topic.name} ({len(topic.subtopics)} 子主题)")
    print(f"  默认语言: {config.i18n.default_language}")


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器"""
    parser = argparse.ArgumentParser(
        prog="labindex-shiori",
        description="LabIndex Shiori — 研究资料横断检索系统",
    )
    parser.add_argument(
        "--config", default=None,
        help="配置文件路径（默认自动搜索 config.yaml）",
    )

    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # init
    subparsers.add_parser("init", help="初始化数据库")

    # scan
    scan_parser = subparsers.add_parser("scan", help="扫描文件（增量/全量）")
    scan_parser.add_argument("--root", type=str, default=None,
                             help="指定扫描根目录（覆盖 config.yaml 的 SCAN_ROOTS，不持久化）")
    scan_mode = scan_parser.add_mutually_exclusive_group()
    scan_mode.add_argument("--incremental", action="store_true", dest="incremental",
                           help="增量扫描（默认，只处理变更文件）")
    scan_mode.add_argument("--full", action="store_true", dest="full",
                           help="全量扫描（强制重新计算所有文件 hash）")
    scan_parser.set_defaults(incremental=False, full=False)

    # parse
    parse_parser = subparsers.add_parser("parse", help="解析文件元数据（新增/变更/强制）")
    parse_parser.add_argument("--force", action="store_true", dest="force",
                              help="强制重新解析所有文件")

    # index
    index_parser = subparsers.add_parser("index", help="建立 Meilisearch 索引（子主题归类+推送）")
    index_parser.add_argument("--full", action="store_true", dest="full",
                              help="全量重建索引（删除旧索引后重建）")

    # overlay
    overlay_parser = subparsers.add_parser("overlay", help="人工纠错（overlay 操作）")
    overlay_sub = overlay_parser.add_subparsers(dest="action", help="overlay 操作")

    p_correct = overlay_sub.add_parser("correct", help="修正字段值")
    p_correct.add_argument("--file-id", type=int, required=True, help="文件 ID")
    p_correct.add_argument("--field", required=True,
                           choices=["title", "keywords", "year", "subtopic", "researcher"],
                           help="字段名")
    p_correct.add_argument("--value", required=True, help="修正值")

    p_exclude = overlay_sub.add_parser("exclude", help="从检索中排除文件")
    p_exclude.add_argument("--file-id", type=int, required=True, help="文件 ID")
    p_exclude.add_argument("--reason", default="", help="排除原因")

    p_include = overlay_sub.add_parser("include", help="恢复被排除的文件")
    p_include.add_argument("--file-id", type=int, required=True, help="文件 ID")

    p_rel_add = overlay_sub.add_parser("relation-add", help="添加关联关系")
    p_rel_add.add_argument("--file-id-a", type=int, required=True, help="文件 A ID")
    p_rel_add.add_argument("--file-id-b", type=int, required=True, help="文件 B ID")
    p_rel_add.add_argument("--type", default="related",
                           choices=["related", "cites", "cited_by"], help="关联类型")
    p_rel_add.add_argument("--note", default="", help="备注")

    p_rel_rm = overlay_sub.add_parser("relation-remove", help="删除关联关系")
    p_rel_rm.add_argument("--relation-id", type=int, required=True, help="关联 ID")

    p_show = overlay_sub.add_parser("show", help="查看指定文件的 overlay 状态")
    p_show.add_argument("--file-id", type=int, required=True, help="文件 ID")
    overlay_sub.add_parser("status", help="查看全局 overlay 统计")

    # relation (M10)
    rel_parser = subparsers.add_parser("relation", help="M10: 研究关联推算")
    rel_parser.add_argument("--show", action="store_true", help="显示推算结果")

    # serve
    subparsers.add_parser("serve", help="启动 Web UI（M6 实现）")

    return parser


def main() -> None:
    """主入口"""
    from src.config import AppConfig

    # 提前解析 --config
    config_path = None
    if "--config" in sys.argv:
        idx = sys.argv.index("--config")
        if idx + 1 < len(sys.argv):
            config_path = sys.argv[idx + 1]

    try:
        AppConfig.load(config_path)
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"配置错误: {e}", file=sys.stderr)
        sys.exit(1)

    parser = build_parser()
    args = parser.parse_args()

    if args.command == "init":
        cmd_init(args)
    elif args.command == "scan":
        cmd_scan(args)
    elif args.command == "parse":
        cmd_parse(args)
    elif args.command == "index":
        cmd_index(args)
    elif args.command == "overlay":
        cmd_overlay(args)
    elif args.command == "relation":
        cmd_relation(args)
    elif args.command == "serve":
        cmd_serve(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
