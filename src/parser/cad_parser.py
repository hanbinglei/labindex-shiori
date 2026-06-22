"""
LabIndex Shiori — CAD 图纸解析器

图纸格式 (.dwg/.dxf/.vwx) 只抽取文件系统元信息:
  - 文件名、路径、修改日期、文件大小
  - 不解析内部内容

只读保障: 仅 os.stat，不打开文件内容
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def parse_cad(file_path: str) -> Dict:
    """
    解析 CAD 图纸文件（仅元信息）

    Returns:
        dict: title=文件名, extra={mtime, ctime, file_size},
              note="图纸文件，仅元信息"
    """
    stat_info = os.stat(file_path)
    filename = os.path.basename(file_path)
    mtime_dt = datetime.fromtimestamp(stat_info.st_mtime).isoformat()

    return {
        "title": filename,  # 图纸以文件名为标题
        "title_source": "annotated",
        "keywords": None,
        "keywords_source": None,
        "abstract": None,
        "year": datetime.fromtimestamp(stat_info.st_mtime).year,
        "authors": None,
        "references": None,
        "note": "图纸文件，仅元信息",
        "extra": {
            "mtime": mtime_dt,
            "file_size": stat_info.st_size,
            "extension": os.path.splitext(file_path)[1].lower(),
        },
    }
