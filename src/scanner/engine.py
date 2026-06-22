"""
LabIndex Shiori — 扫描引擎（只读增量扫描）

=== 硬约束 ===
1. 只读强制：所有文件操作用 open(path, 'rb') 只读，绝不写 NAS
2. 路径护栏：每个文件在处理前校验是否在 SCAN_ROOTS 范围内
3. fail-safe：单文件失败只跳过不中断，NAS 断连优雅退出
4. mtime+size 预筛优化：先比对，不变则跳过 hash 计算
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from src.config import AppConfig

logger = logging.getLogger(__name__)

# -----------------------------------------------------------
# 常量
# -----------------------------------------------------------
_HASH_BUF_SIZE = 65536       # 64KB 缓冲区，大文件高效 hash
_PROGRESS_INTERVAL = 50      # 每处理 N 个文件输出一次进度


class ScanStats:
    """扫描统计（线程安全、仅主线程读写）"""

    __slots__ = (
        "total_found", "total_new", "total_changed",
        "total_unchanged", "total_deleted", "total_skipped",
        "total_errors", "start_time",
    )

    def __init__(self):
        self.total_found = 0
        self.total_new = 0
        self.total_changed = 0
        self.total_unchanged = 0
        self.total_deleted = 0
        self.total_skipped = 0
        self.total_errors = 0
        self.start_time = time.time()

    @property
    def elapsed(self) -> str:
        """返回人类可读的运行时间"""
        t = time.time() - self.start_time
        if t < 60:
            return f"{t:.1f}s"
        return f"{t // 60:.0f}m{t % 60:.0f}s"


class ScannerEngine:
    """
    扫描引擎

    用法:
        config = AppConfig.load()
        engine = ScannerEngine(config)
        stats = engine.scan_incremental()   # 增量
        stats = engine.scan_full()          # 全量
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self.stats = ScanStats()

        # 已扫描的文件路径集合（用于检测删除）
        self._seen_paths: Set[str] = set()

        # 数据库连接（在 _run_scan 中打开，_process_file 复用）
        self._conn: Optional[sqlite3.Connection] = None

        # 当前遍历的根目录索引
        self._current_root_idx: int = 0

    # ============================================================
    # 公开接口
    # ============================================================

    def scan_incremental(self) -> ScanStats:
        """
        增量扫描：比对 mtime+size，未变则跳过 hash
        首次全量扫描（DB 无记录时）等价于全量扫描的行为
        """
        self._reset()
        self._print("🔍 增量扫描开始")
        return self._run_scan(force_rehash=False)

    def scan_full(self) -> ScanStats:
        """
        全量扫描：强制重新计算所有文件的 hash
        """
        self._reset()
        self._print("🔍 全量扫描开始")
        return self._run_scan(force_rehash=True)

    # ============================================================
    # 内部：扫描主流程
    # ============================================================

    def _reset(self) -> None:
        """重置扫描状态"""
        self.stats = ScanStats()
        self._seen_paths.clear()
        self._conn = None
        self._current_root_idx = 0

    def _run_scan(self, force_rehash: bool) -> ScanStats:
        """
        扫描主循环

        Args:
            force_rehash: True=强制全量hash / False=mtime+size预筛跳过

        Raises:
            OSError: NAS 断连等严重错误，优雅退出
        """
        db_path = self.config.database.path
        self._conn = sqlite3.connect(db_path, timeout=60)
        self._conn.row_factory = sqlite3.Row

        try:
            # 加载数据库现有记录（path → record 字典）
            db_records = self._load_db_records()

            # 遍历每个 SCAN_ROOT
            for root_idx, root in enumerate(self.config.scan_roots):
                self._current_root_idx = root_idx
                self._print(f"\n📂 扫描根目录 [{root_idx}]: {root}")

                # 检查根目录是否可访问
                if not os.path.isdir(root):
                    self._print(f"   ❌ 无法访问: {root}（路径不存在或无权限）", err=True)
                    self.stats.total_errors += 1
                    continue

                # 递归遍历
                self._walk_root(root, root_idx, db_records, force_rehash)

            # 检测删除：DB 有但遍历未见 → status='deleted'
            self._detect_deletions(db_records)

            # 提交事务
            self._conn.commit()

            # 输出报告
            self._print_summary()
            return self.stats

        except (OSError, ConnectionError, PermissionError) as e:
            # NAS 断连等严重错误 → 回滚 + 优雅退出
            self._conn.rollback()
            self._print(f"\n❌ 严重错误: {e}", err=True)
            self._print(
                "   扫描中断，请检查 NAS 连接后重试。"
                "数据库未写入增量数据。",
                err=True,
            )
            raise
        finally:
            if self._conn:
                self._conn.close()
                self._conn = None

    def _walk_root(
        self,
        root: str,
        root_idx: int,
        db_records: Dict[str, dict],
        force_rehash: bool,
    ) -> None:
        """递归遍历一个扫描根目录，支持两层目录过滤"""
        root = os.path.abspath(root)

        # 获取顶层过滤配置
        cfg = self.config.scanner
        top_pattern = cfg.top_level_year_pattern  # e.g. r"^\d{6}_"

        # 如果开启了顶层过滤，只遍历匹配的目录
        if top_pattern:
            try:
                top_re = re.compile(top_pattern)
            except re.error:
                top_re = re.compile(r"^\d{6}_")  # fallback

            # 列出根目录下的一级子目录
            try:
                all_entries = os.listdir(root)
            except PermissionError:
                self._print(f"   ❌ 无权限读取: {root}", err=True)
                return

            matched_dirs = []
            for entry in sorted(all_entries):
                entry_path = os.path.join(root, entry)
                if os.path.isdir(entry_path):
                    if top_re.match(entry):
                        matched_dirs.append(entry)
                    else:
                        self._print(f"   [跳过顶层] {entry}")

            if not matched_dirs:
                self._print(f"   ⚠️ 没有匹配的届文件夹（需匹配: {top_pattern})")
                return

            for subdir in matched_dirs:
                sub_path = os.path.join(root, subdir)
                self._print(f"   [扫描届] {subdir}")
                for dirpath, dirnames, filenames in os.walk(sub_path):
                    # 同时应用标准排除 + 数据目录跳过
                    dirnames[:] = [
                        d for d in dirnames
                        if not self._should_exclude(d)
                        and not self._should_skip_data_dir(d)
                    ]
                    for filename in filenames:
                        full_path = os.path.join(dirpath, filename)
                        self._process_file(full_path, root, root_idx, db_records, force_rehash)
        else:
            # 未开启过滤：原有逻辑
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [
                    d for d in dirnames
                    if not self._should_exclude(d)
                    and not self._should_skip_data_dir(d)
                ]
                for filename in filenames:
                    full_path = os.path.join(dirpath, filename)
                    self._process_file(full_path, root, root_idx, db_records, force_rehash)

    # ============================================================
    # 路径范围护栏
    # ============================================================

    @staticmethod
    def _is_path_in_scope(full_path: str, root: str) -> bool:
        """
        校验文件路径是否在 SCAN_ROOT 范围内
        使用 normpath + startswith 做前缀匹配，兼容 UNC 路径

        Returns:
            True  → 安全，在范围内
            False → 跳过（不在范围内）
        """
        try:
            abs_path = os.path.normpath(os.path.abspath(full_path))
            abs_root = os.path.normpath(os.path.abspath(root))
            # UNC 路径下 commonpath 可能抛 ValueError，用 startswith 替代
            return abs_path == abs_root or abs_path.startswith(abs_root + os.sep)
        except (ValueError, OSError):
            return False

    # ============================================================
    # 排除目录判断
    # ============================================================

    def _should_exclude(self, dir_name: str) -> bool:
        """检查目录名是否在排除列表中"""
        return dir_name in self.config.scanner.exclude_dirs

    def _should_skip_data_dir(self, dir_name: str) -> bool:
        """
        检查目录名是否匹配数据目录跳过规则
        去掉圈数字前缀后，匹配 skip_data_dir_patterns 中的模式
        """
        patterns = self.config.scanner.skip_data_dir_patterns
        if not patterns:
            return False
        # 去掉开头的圈数字标记（①②③④⑤等）
        stripped = re.sub(r"^[①②③④⑤⑥⑦⑧⑨⑩]+", "", dir_name).strip()
        for pat in patterns:
            if re.search(pat, stripped):
                return True
        return False

    # ============================================================
    # 单文件处理
    # ============================================================

    def _process_file(
        self,
        full_path: str,
        root: str,
        root_idx: int,
        db_records: Dict[str, dict],
        force_rehash: bool,
    ) -> None:
        """
        处理单个文件

        流程:
            路径护栏 → 扩展名白名单 → 稳定期检查 → mtime+size 预筛 → hash → 分类
            fail-safe: 任何异常只跳过该文件不中断整体

        只读保障:
            - 只用 os.stat（只读元数据）
            - 只用 open(path, 'rb') 读文件内容
            - 绝不写 NAS 路径
        """
        # --- 1. 路径范围护栏 ---
        if not self._is_path_in_scope(full_path, root):
            self.stats.total_skipped += 1
            return

        self.stats.total_found += 1
        self._seen_paths.add(full_path)

        try:
            # --- 2. 只读 stat 获取文件信息 ---
            stat_info = os.stat(full_path)
            file_size = stat_info.st_size
            mtime = stat_info.st_mtime
            filename = os.path.basename(full_path)
            extension = os.path.splitext(full_path)[1].lower()

            # --- 3a. 扩展名白名单过滤 ---
            # 不在 supported_extensions 中的文件直接跳过（不索引）
            if extension not in self.config.scanner.supported_extensions:
                self.stats.total_skipped += 1
                return

            # --- 3b. Office 临时文件过滤 ---
            # ~$ 开头的文件是 Office 打开时生成的锁文件，非真实文档
            if filename.startswith("~$") or "~$" in filename:
                self.stats.total_skipped += 1
                return

            # --- 3c. 稳定期检查：跳过正在写入的文件 ---
            # 如果文件的 mtime 距今不足 stable_threshold_sec 秒，
            # 说明可能正在被写入，跳过到下一次扫描
            now = time.time()
            age = now - mtime
            if 0 <= age < self.config.scanner.stable_threshold_sec:
                logger.info(
                    "跳过可能未写完的文件 [%s] (mtime=%.1fs ago, 阈值=%ds)",
                    full_path, age, self.config.scanner.stable_threshold_sec,
                )
                self.stats.total_skipped += 1
                return

            # --- 4. 查询数据库历史记录 ---
            db_rec = db_records.get(full_path)

            if db_rec is None:
                # === 新增文件 ===
                file_hash = self._hash_file(full_path)
                self._insert_file(full_path, filename, extension, file_size, mtime, file_hash, root_idx)
                self.stats.total_new += 1

            elif force_rehash:
                # === 全量模式：强制重新计算 hash ===
                new_hash = self._hash_file(full_path)
                if new_hash != db_rec["hash"]:
                    self._update_file(full_path, file_size, mtime, new_hash)
                    self.stats.total_changed += 1
                else:
                    # hash 未变，只更新时间戳
                    self._update_file_touch(full_path, mtime)
                    self.stats.total_unchanged += 1

            else:
                # === 增量模式：mtime+size 预筛 ===
                # 关键优化：mtime 和 size 都没变 → 跳过 hash 计算
                if (mtime == db_rec["mtime"]) and (file_size == db_rec["file_size"]):
                    self.stats.total_unchanged += 1
                else:
                    # mtime 或 size 变了 → 需要重新 hash
                    new_hash = self._hash_file(full_path)
                    if new_hash != db_rec["hash"]:
                        self._update_file(full_path, file_size, mtime, new_hash)
                        self.stats.total_changed += 1
                    else:
                        # 内容没变但 mtime 变了（touch、copy 等操作）
                        self._update_file_touch(full_path, mtime)
                        self.stats.total_unchanged += 1

            # --- 4. 进度输出 ---
            if self.stats.total_found % _PROGRESS_INTERVAL == 0:
                self._print(
                    f"  ⏳ 已处理 {self.stats.total_found} 个文件"
                    f"（新增 {self.stats.total_new} / "
                    f"变更 {self.stats.total_changed}）"
                )

        except Exception as e:
            # fail-safe：单个文件失败只跳过，不中断扫描
            logger.warning("跳过文件 [%s]: %s", full_path, e)
            self.stats.total_errors += 1
            self.stats.total_skipped += 1

    # ============================================================
    # SHA256 只读 hash
    # ============================================================

    @staticmethod
    def _hash_file(file_path: str) -> str:
        """
        以只读方式计算文件 SHA256

        只读保障：
            - open(path, 'rb') 只读模式
            - 不创建、不修改任何文件

        Args:
            file_path: 文件绝对路径

        Returns:
            小写十六进制 SHA256 字符串
        """
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(_HASH_BUF_SIZE)
                if not chunk:
                    break
                hasher.update(chunk)
        return hasher.hexdigest()

    # ============================================================
    # 删除检测
    # ============================================================

    def _detect_deletions(self, db_records: Dict[str, dict]) -> None:
        """
        遍历数据库中有记录但本次遍历未见的文件 → 标记 status='deleted'
        """
        now = datetime.now().isoformat()
        deleted_count = 0

        for path, rec in db_records.items():
            if rec["status"] == "active" and path not in self._seen_paths:
                self._conn.execute(
                    "UPDATE auto_files SET status=?, last_seen=? WHERE path=?",
                    ("deleted", now, path),
                )
                deleted_count += 1

        self.stats.total_deleted = deleted_count

    # ============================================================
    # 数据库操作
    # ============================================================

    def _load_db_records(self) -> Dict[str, dict]:
        """
        加载所有 active 文件的 DB 记录（path → dict）
        用于快速比对和删除检测

        注意：一次性加载全部 active 记录到内存。
        当前量级（数千文件）完全可行；若未来扩展到数万级以上，
        可改为分批查询或 SQLite 临时表。
        """
        cursor = self._conn.execute(
            "SELECT path, filename, extension, file_size, mtime, hash, status "
            "FROM auto_files WHERE status = 'active'"
        )
        return {row["path"]: dict(row) for row in cursor.fetchall()}

    def _insert_file(
        self,
        path: str,
        filename: str,
        extension: str,
        file_size: int,
        mtime: float,
        file_hash: str,
        root_idx: int,
    ) -> None:
        """插入新文件记录"""
        now = datetime.now().isoformat()
        self._conn.execute(
            """
            INSERT OR REPLACE INTO auto_files
                (path, filename, extension, file_size, mtime, hash,
                 status, scan_root_index, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
            """,
            (path, filename, extension, file_size, mtime, file_hash,
             root_idx, now, now),
        )

    def _update_file(
        self,
        path: str,
        file_size: int,
        mtime: float,
        file_hash: str,
    ) -> None:
        """更新已变更文件（同时标记需要重新解析）"""
        now = datetime.now().isoformat()
        self._conn.execute(
            """
            UPDATE auto_files
            SET file_size=?, mtime=?, hash=?, last_seen=?,
                status='active', last_parsed=NULL
            WHERE path=?
            """,
            (file_size, mtime, file_hash, now, path),
        )

    def _update_file_touch(self, path: str, mtime: float) -> None:
        """仅更新时间戳（hash 未变时使用）"""
        now = datetime.now().isoformat()
        self._conn.execute(
            "UPDATE auto_files SET mtime=?, last_seen=? WHERE path=?",
            (mtime, now, path),
        )

    # ============================================================
    # 输出
    # ============================================================

    def _print(self, msg: str, end: str = "\n", err: bool = False) -> None:
        """统一输出（默认 stdout，err=True 走 stderr）"""
        fp = sys.stderr if err else sys.stdout
        print(msg, end=end, file=fp, flush=True)

    def _print_summary(self) -> None:
        """输出扫描总结"""
        s = self.stats
        self._print("")
        self._print("=" * 50)
        self._print("📊 扫描完成")
        self._print(f"   总文件数:     {s.total_found}")
        self._print(f"   ├ 新增:       {s.total_new}")
        self._print(f"   ├ 变更:       {s.total_changed}")
        self._print(f"   ├ 未变化:     {s.total_unchanged}")
        self._print(f"   ├ 删除:       {s.total_deleted}")
        self._print(f"   └ 跳过/错误:  {s.total_skipped}")
        self._print(f"   耗时:         {s.elapsed}")
        self._print("=" * 50)
