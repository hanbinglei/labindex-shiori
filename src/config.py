"""
LabIndex Shiori — 配置加载模块
从 config.yaml 读取并验证所有配置项，提供类型安全的访问接口。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# -----------------------------------------------------------
# 默认配置路径（优先取项目根目录下的 config.yaml）
# -----------------------------------------------------------
# 查找策略：从当前文件位置向上找，直到找到 config.yaml
def _find_project_root() -> Path:
    """从代码位置向上搜索，找到项目根目录（包含 config.yaml 的目录）"""
    # 优先从环境变量取
    env_root = os.environ.get("LABINDEX_ROOT")
    if env_root:
        return Path(env_root).resolve()

    # 从当前文件位置向上搜索
    current = Path(__file__).resolve().parent  # src/
    for _ in range(4):  # 最多向上 4 层
        if (current / "config.yaml").exists():
            return current
        current = current.parent

    # 兜底：当前工作目录
    return Path.cwd().resolve()


# -----------------------------------------------------------
# 配置数据结构（类型安全）
# -----------------------------------------------------------

class MeilisearchConfig:
    """Meilisearch 连接配置"""
    def __init__(self, data: dict):
        self.host: str = data.get("host", "http://127.0.0.1")
        self.port: int = data.get("port", 7700)
        # 优先从环境变量读取 master key（安全），其次 config.yaml
        import os
        self.api_key: str = os.environ.get("MEILI_MASTER_KEY") or data.get("api_key", "")
        self.index_name: str = data.get("index_name", "labindex_shiori")

    @property
    def url(self) -> str:
        return f"{self.host}:{self.port}"


class SubtopicConfig:
    """子主题配置"""
    def __init__(self, data: dict):
        self.name: str = data["name"]
        self.display_name_ja: str = data.get("display_name_ja", self.name)
        self.display_name_zh: str = data.get("display_name_zh", self.name)
        self.display_name_en: str = data.get("display_name_en", self.name)
        self.keywords: List[str] = data.get("keywords", [])

    def display_name(self, lang: str) -> str:
        key = f"display_name_{lang}"
        return getattr(self, key, self.name)


class TopicConfig:
    """大主题配置"""
    def __init__(self, data: dict):
        self.name: str = data["name"]
        self.display_name_ja: str = data.get("display_name_ja", self.name)
        self.display_name_zh: str = data.get("display_name_zh", self.name)
        self.display_name_en: str = data.get("display_name_en", self.name)
        self.subtopics: List[SubtopicConfig] = [
            SubtopicConfig(s) for s in data.get("subtopics", [])
        ]

    def display_name(self, lang: str) -> str:
        key = f"display_name_{lang}"
        return getattr(self, key, self.name)


class DatabaseConfig:
    """数据库配置"""
    def __init__(self, data: dict, project_root: Path):
        path_str = data.get("path", "data/labindex.db")
        self.path: str = str(project_root / path_str)


class ScannerConfig:
    """扫描行为配置"""
    def __init__(self, data: dict):
        self.stable_threshold_sec: int = data.get("stable_threshold_sec", 60)
        self.exclude_dirs: List[str] = data.get("exclude_dirs", [])
        self.supported_extensions: List[str] = [
            ext.lower() for ext in data.get("supported_extensions", [])
        ]
        # 两层目录过滤（Previous_Research 专用）
        self.top_level_year_pattern: Optional[str] = data.get("top_level_year_pattern")
        self.skip_data_dir_patterns: List[str] = data.get("skip_data_dir_patterns", [])


class PathParsingConfig:
    """路径结构解析配置（M8）"""
    def __init__(self, data: dict):
        d = data or {}
        # 年度
        ay = d.get("academic_year", {})
        self.academic_year_pattern: str = ay.get("pattern", r"^(\d{4})(\d{2})_")
        self.graduation_month: int = int(ay.get("graduation_month", 3))

        # 学位
        deg = d.get("degree", {})
        self.degree_ignore_prefix: str = deg.get("ignore_prefix", r"^[①②③④⑤⑥⑦⑧⑨⑩]+")
        self.degree_mapping: dict = deg.get("mapping", {})

        # 文档类型
        dt = d.get("doc_type", {})
        self.doc_type_ignore_prefix: str = dt.get("ignore_prefix", r"^[①②③④⑤⑥⑦⑧⑨⑩]+")
        self.doc_type_mapping: dict = dt.get("mapping", {})

        # M9: 研究者姓名提取
        r = d.get("researcher", {})
        self.researcher_position_relative_to: str = r.get("position_relative_to", "doc_type")
        self.researcher_offset: int = int(r.get("offset", 1))
        norm = r.get("normalize", {})
        self.researcher_trim: bool = norm.get("trim", True)
        self.researcher_unify_whitespace: bool = norm.get("unify_whitespace", True)


class RelationConfig:
    """M10: 研究关联推算配置"""
    def __init__(self, data: dict):
        d = data or {}
        self.keyword_overlap_threshold: float = float(d.get("keyword_overlap_threshold", 0.5))
        self.title_similarity_enabled: bool = d.get("title_similarity_enabled", True)
        self.title_similarity_threshold: float = float(d.get("title_similarity_threshold", 0.5))
        self.succession_patterns: list = d.get("succession_patterns", [])
        self.predecessor_patterns: list = d.get("predecessor_patterns", [])
        self.citation_min_title_match_len: int = int(d.get("citation_min_title_match_len", 10))
        self.citation_title_similarity_threshold: float = float(d.get("citation_title_similarity_threshold", 0.6))
        # M10 新增: 序论 TF-IDF 相似度
        intro = d.get("intro_similarity", {})
        self.intro_similarity_enabled: bool = intro.get("enabled", True)
        self.intro_similarity_threshold: float = float(intro.get("threshold", 0.3))
        self.intro_max_chars: int = int(intro.get("max_chars", 1000))
        self.intro_min_chars: int = int(intro.get("min_chars", 50))
        self.intro_n_gram_range: tuple = tuple(intro.get("n_gram_range", [1, 3]))
        # 书目耦合（bibliographic_coupling）
        bc = d.get("bibliographic_coupling", {})
        self.bc_enabled: bool = bc.get("enabled", True)
        self.bc_min_shared_refs: int = int(bc.get("min_shared_refs", 2))
        self.bc_certain_threshold: int = int(bc.get("certain_threshold", 2))
        bc_norm = bc.get("reference_normalization", {})
        self.bc_strip_prefixes: list = bc_norm.get("strip_prefixes",
            [r"^\[\d+\]\s*", r"^\d+\.\d+\)\s*", r"^\d+\)\s*", r"^\d+\.\s*"])
        self.bc_year_pattern: str = bc_norm.get("year_pattern", r"\b(19\d{2}|20\d{2})\b")


class ParserConfig:
    """解析器配置"""
    def __init__(self, data: dict):
        self.timeout_per_file: int = data.get("timeout_per_file", 30)
        self.pdf_max_pages: int = data.get("pdf_max_pages", 200)
        self.pptx_max_slides: int = data.get("pptx_max_slides", 200)


class I18nConfig:
    """国际化配置"""
    def __init__(self, data: dict, project_root: Path):
        self.default_language: str = data.get("default_language", "ja")
        locales_dir = data.get("locales_dir", "locales")
        self.locales_dir: str = str(project_root / locales_dir)


class AppConfig:
    """
    应用全局配置
    用法: config = AppConfig.load() 或 AppConfig(path/to/config.yaml)
    """

    def __init__(self, data: dict, project_root: Path):
        # 扫描范围
        self.scan_roots: List[str] = data.get("SCAN_ROOTS", [])

        # 子模块配置
        self.database = DatabaseConfig(data.get("database", {}), project_root)
        self.meilisearch = MeilisearchConfig(data.get("meilisearch", {}))
        self.scanner = ScannerConfig(data.get("scanner", {}))
        self.path_parsing = PathParsingConfig(data.get("path_parsing", {}))
        self.parser = ParserConfig(data.get("parser", {}))
        self.i18n = I18nConfig(data.get("i18n", {}), project_root)
        self.relation = RelationConfig(data.get("relation", {}))  # M10

        # 分类体系
        self.unclassified: Dict[str, str] = data.get("unclassified", {})
        self.topics: List[TopicConfig] = [
            TopicConfig(t) for t in data.get("topics", [])
        ]

        # 项目根目录
        self.project_root: Path = project_root

    @classmethod
    def load(cls, config_path: Optional[str] = None) -> "AppConfig":
        """
        加载并验证配置文件

        Args:
            config_path: 配置文件路径，为 None 则自动搜索

        Returns:
            AppConfig 实例

        Raises:
            FileNotFoundError: 配置文件不存在
            ValueError: 配置验证失败
        """
        if config_path:
            path = Path(config_path).resolve()
        else:
            project_root = _find_project_root()
            path = project_root / "config.yaml"

        if not path.exists():
            raise FileNotFoundError(
                f"配置文件不存在: {path}\n"
                f"请确保 config.yaml 位于项目根目录，或设置 LABINDEX_ROOT 环境变量"
            )

        project_root = path.parent.resolve()

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not data:
            raise ValueError("配置文件为空")

        # 基本验证
        cls._validate(data)

        return cls(data, project_root)

    @staticmethod
    def _validate(data: dict) -> None:
        """验证必要配置项是否存在"""
        required = ["SCAN_ROOTS"]
        for key in required:
            if key not in data:
                raise ValueError(f"缺少必要配置项: {key}")

        if not data.get("SCAN_ROOTS"):
            raise ValueError("SCAN_ROOTS 不能为空，至少需要指定一个扫描根目录")


# -----------------------------------------------------------
# 全局单例（懒加载）
# -----------------------------------------------------------
_config: Optional[AppConfig] = None


def get_config() -> AppConfig:
    """
    获取全局配置单例
    第一次调用时自动加载 config.yaml
    """
    global _config
    if _config is None:
        _config = AppConfig.load()
    return _config


def reload_config(config_path: Optional[str] = None) -> AppConfig:
    """重新加载配置（调试/热重载用）"""
    global _config
    _config = AppConfig.load(config_path)
    return _config
