"""
LabIndex Shiori — 路径结构解析器（M8）

从文件路径的目录结构推断三个属性：
  - academic_year: 学年度（如 202403 → 2023年度）
  - degree: 学位类别（D/M/B）
  - doc_type: 文档类型（thesis/summary/presentation/...）

设计原则：
  - 纯规则，不调用任何 AI/模型
  - 所有匹配模式从 config 读取，不硬编码
  - 无法判定 → 诚实标 None/空，绝不猜测
  - 只读：仅解析路径字符串，不碰文件内容
"""

from __future__ import annotations

import logging
import re
from pathlib import PureWindowsPath, PurePosixPath
from typing import Dict, Optional, Tuple

from src.config import AppConfig

logger = logging.getLogger(__name__)


class PathParser:
    """
    路径结构解析器

    用法:
        parser = PathParser(config)
        result = parser.parse(file_path)
        # → {"academic_year": 2023, "degree": "M", "doc_type": "thesis"}
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self._pp = config.path_parsing

        # 预编译正则
        self._year_re = re.compile(self._pp.academic_year_pattern)
        self._deg_prefix_re = re.compile(self._pp.degree_ignore_prefix)
        self._dt_prefix_re = re.compile(self._pp.doc_type_ignore_prefix)

    # ============================================================
    # 公开接口
    # ============================================================

    def parse(self, file_path: str) -> Dict:
        """
        解析文件路径，返回路径推断的元数据

        Args:
            file_path: 文件的完整路径（UNC 或本地绝对路径）

        Returns:
            dict: {
                "academic_year": int or None,
                "degree": str or None,         # D / M / B
                "doc_type": str or None,       # thesis / summary / presentation / ...
            }
            无法判定的字段返回 None（诚实）
        """
        # 标准化路径并拆分为段
        segments = self._split_path(file_path)

        result = {
            "academic_year": None,
            "degree": None,
            "doc_type": None,
            "researcher": None,  # M9
        }

        if not segments:
            return result

        # --- 第1层：年度提取 ---
        result["academic_year"] = self._parse_academic_year(segments)

        # --- 第2层：学位类别 ---
        result["degree"] = self._parse_degree(segments)

        # --- 第3层：文档类型 ---
        result["doc_type"] = self._parse_doc_type(segments)

        # --- M9: 研究者提取 ---
        result["researcher"] = self._parse_researcher(segments, result.get("doc_type"))

        return result

    # ============================================================
    # 路径分割
    # ============================================================

    @staticmethod
    def _split_path(file_path: str) -> list:
        """
        将文件路径拆分为目录段列表

        处理 UNC 路径（\\\\server\\share\\...）和本地路径（D:\\...）
        返回从根向下的目录段列表，空段会被过滤。
        """
        # 尝试作为 Windows 路径处理
        try:
            p = PureWindowsPath(file_path)
            parts = list(p.parts)
        except (TypeError, ValueError):
            # 兜底：用 POSIX 方式处理
            p = PurePosixPath(file_path)
            parts = list(p.parts)

        # 过滤空段、驱动器字母（D:\）、UNC 服务器/共享
        segments = []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            # 跳过纯驱动器（D:）和 UNC 前缀（\\）
            if part.endswith(":") or part in ("\\\\",):
                continue
            # 跳过 UNC 中的服务器名和共享名（判断方式：不含点且不含路径分隔符
            # 且不是有效目录名模式）
            segments.append(part)

        # 去掉文件名（最后一段含扩展名）
        if segments and "." in segments[-1] and not segments[-1].startswith("."):
            # 最后一段看起来是文件
            pass  # 保留文件名段，后续解析靠段的内容判断，不依赖位置

        return segments

    # ============================================================
    # 年度解析
    # ============================================================

    def _parse_academic_year(self, segments: list) -> Optional[int]:
        """
        从路径段中寻找 YYYYMM_ 模式，计算学年度

        规则：
          从第一段（最靠近根）开始匹配
          毕业月 <= graduation_month → 学年度 = YYYY - 1
          毕业月 >  graduation_month → 学年度 = YYYY
        """
        for seg in segments:
            m = self._year_re.match(seg)
            if m:
                year = int(m.group(1))
                month = int(m.group(2))
                grad_month = self._pp.graduation_month
                if month <= grad_month:
                    return year - 1
                else:
                    return year
        return None

    # ============================================================
    # 学位解析
    # ============================================================

    def _parse_degree(self, segments: list) -> Optional[str]:
        """
        从路径段中识别学位类别目录

        扫描所有段，匹配 degree_mapping 中的关键词
        匹配前先移除 ignore_prefix（圈数字等前缀）
        """
        for seg in segments:
            cleaned = self._deg_prefix_re.sub("", seg).strip()
            if not cleaned:
                continue
            # 精确匹配
            if cleaned in self._pp.degree_mapping:
                return self._pp.degree_mapping[cleaned]
            # 模糊匹配：段中包含映射键
            for key, code in self._pp.degree_mapping.items():
                if key in cleaned:
                    return code
        return None

    # ============================================================
    # 文档类型解析
    # ============================================================

    def _parse_doc_type(self, segments: list) -> Optional[str]:
        """
        从路径段中识别文档类型目录

        扫描所有段，匹配 doc_type_mapping 中的关键词
        匹配前先移除 ignore_prefix（圈数字等前缀）
        """
        for seg in segments:
            cleaned = self._dt_prefix_re.sub("", seg).strip()
            if not cleaned:
                continue
            # 精确匹配
            if cleaned in self._pp.doc_type_mapping:
                return self._pp.doc_type_mapping[cleaned]
            # 模糊匹配：段中包含映射键
            for key, code in self._pp.doc_type_mapping.items():
                if key in cleaned:
                    return code
        return None


    # ============================================================
    # M9: 研究者提取
    # ============================================================

    def _parse_researcher(self, segments: list, doc_type: Optional[str] = None) -> Optional[str]:
        """
        从路径段中提取研究者姓名（M9）

        规则（从 config 读取，不硬编码）：
          定位到文档类型目录（doc_type matching segment），
          取其下一层子目录名作为研究者姓名。
          
        限制（M11 扩展）：
          1. 从 thesis(①本論) + summary(②梗概) + presentation(③公聴会資料) 提取研究者；
             ④解析データ/⑤実験データ 无姓名层 → None
          2. 提取后做非人名过滤，含「実験」「試験」「図」「データ」等关键词 → None

        Args:
            segments: 路径段列表
            doc_type: 已解析的文档类型

        Returns:
            str or None: 规整后的研究者姓名，无法判定则为 None
        """
        # M11 修复: 从 thesis(本論) + summary(梗概) + presentation(公聴会資料) 提取研究者
        # ④解析データ/⑤実験データ 等无姓名层, 跳过
        _ALLOWED_RESEARCHER_TYPES = {"thesis", "summary", "presentation"}
        if doc_type and doc_type not in _ALLOWED_RESEARCHER_TYPES:
            return None

        # 找到 doc_type 匹配的索引位置
        doc_type_idx = self._find_first_match_index(
            segments, self._pp.doc_type_mapping, self._dt_prefix_re
        )

        if doc_type_idx is None:
            return None

        # 根据配置偏移取研究者层
        researcher_idx = doc_type_idx + self._pp.researcher_offset
        if researcher_idx >= len(segments):
            return None

        researcher_raw = segments[researcher_idx]

        # 跳过文件名（含扩展名且不是 .开头的隐藏目录）
        if "." in researcher_raw and not researcher_raw.startswith("."):
            return None

        # 规整化
        researcher = researcher_raw.strip() if self._pp.researcher_trim else researcher_raw

        if self._pp.researcher_unify_whitespace:
            # 全角空格 → 半角
            researcher = researcher.replace("\u3000", " ")
            # 连续空白 → 单个空格
            researcher = re.sub(r"\s+", " ", researcher).strip()

        if not researcher:
            return None

        # M11: 非人名过滤
        if self._is_invalid_person_name(researcher):
            return None

        return researcher

    _INVALID_NAME_KEYWORDS = [
        "実験", "試験", "図", "データ", "解析", "計算", "載荷",
        "結果", "比較", "検討", "提案", "数値", "モデル",
        "パラメータ", "パラメタ", "ケース", "test", "data",
        "分析", "測定", "評価", "特性", "実験体", "供試体",
        "プログラム", "フロー", "手順", "マニュアル",
    ]

    @staticmethod
    def _is_invalid_person_name(name: str) -> bool:
        """
        判断提取到的名称是否为有效的人名，而不是实验名/文件名

        返回 True = 无效（非人名），应排除
        """
        if not name:
            return True

        # 含非人名关键词
        for kw in PathParser._INVALID_NAME_KEYWORDS:
            if kw in name:
                return True

        # 含文件扩展名（如 .docx 被切成段时）
        if "." in name and not name.startswith("."):
            return True

        # 纯数字或数字+字母短编号（如 "A1", "S-01"）
        import re
        if re.match(r"^[\d\-]+[A-Z]?\d*$", name):
            return True

        # 过短（< 2个字符，不含标点）
        clean = name.replace(" ", "").replace("　", "")
        if len(clean) <= 1:
            return True

        return False

    def _find_first_match_index(self, segments: list, mapping: dict,
                                 prefix_re: re.Pattern) -> Optional[int]:
        """
        在 segments 中搜索第一个匹配 mapping 的段，返回其索引（M9 辅助）

        匹配逻辑与 _parse_degree / _parse_doc_type 一致：
          1. 移除前缀
          2. 精确匹配
          3. 模糊匹配（key in cleaned）

        Args:
            segments: 路径段列表
            mapping: 映射字典（如 doc_type_mapping）
            prefix_re: 前缀正则

        Returns:
            int or None: 第一个匹配的段索引，无匹配则为 None
        """
        for idx, seg in enumerate(segments):
            cleaned = prefix_re.sub("", seg).strip()
            if not cleaned:
                continue
            if cleaned in mapping:
                return idx
            for key in mapping:
                if key in cleaned:
                    return idx
        return None
