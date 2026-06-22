# LabIndex Shiori

> 研究室文献索引与研究系谱可视化系统 — 基于 Meilisearch 的全文搜索 + 研究者系谱图

**LabIndex Shiori** 是一个自托管、本地优先的学术研究室文档管理系统。它可以对共享网络驱动器上的论文、学位论文和实验数据进行索引，通过 Meilisearch 提供全文搜索，并自动发现**研究系谱**——以可视化方式展示研究主题和技术如何在历届学生和研究者之间传承。

## 功能特点

- **全文搜索** — 跨所有已索引论文和文档的即时、容错搜索（Meilisearch 后端）
- **研究系谱图** — 自动发现不同毕业年份研究者之间的引用关系、主题传承和关键词重叠
- **多语言支持** — 日语、中文、英语三种语言界面
- **纯规则驱动** — 零 AI/ML 依赖。所有元数据提取基于路径解析、TF-IDF 相似度和正则匹配
- **增量扫描** — 仅检测新增/变更文件，实现快速的日常更新
- **NAS 友好** — 支持 UNC 网络路径，专为共享实验室存储设计

## 快速开始

### 环境要求

- Windows 10+
- Python 3.11+
- uv（推荐）或 pip

### 安装步骤

```bash
# 1. 克隆仓库
git clone https://github.com/hanbinglei/labindex-shiori.git
cd labindex-shiori

# 2. 创建虚拟环境并安装依赖
uv venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
uv pip install -r requirements.txt

# 3. 从模板创建配置文件
cp config.example.yaml config.yaml
# 编辑 config.yaml：
#   - 在 SCAN_ROOTS 中设置你的 NAS 路径
#   - 生成 Meilisearch master key：python -c "import secrets; print(secrets.token_hex(20))"
#   - 填入 meilisearch.api_key

# 4. 下载 Meilisearch
#    从 https://github.com/meilisearch/meilisearch/releases 下载 meilisearch.exe
#    放到项目根目录

# 5. 初始化数据库
python -m src.main init

# 6. 扫描文档
python -m src.main scan --root "\\YOUR_NAS\Papers" --full

# 7. 解析元数据
python -m src.main parse --force

# 8. 计算研究关联
python -m src.main relation

# 9. 索引到 Meilisearch
python -m src.main index --full

# 10. 启动网页界面
python -m src.main serve
# 在浏览器中打开 http://127.0.0.1:5000
```

> **提示：** Windows 下可使用 `启动.bat` 一键启动所有服务，或 `扫描.bat` 进行交互式扫描流程。

## 项目结构

```
LabIndex Shiori/
├── src/
│   ├── main.py              # CLI 入口
│   ├── config.py            # 配置加载器
│   ├── scanner/             # 文件扫描（NAS 遍历、哈希计算）
│   ├── parser/              # 元数据提取（路径、PDF、DOCX、PPTX）
│   ├── relation/            # 研究关联发现（M10 引擎）
│   ├── indexer/             # Meilisearch 索引 + 自动分类
│   ├── overlay/             # 人工纠错层
│   ├── web/                 # Flask 网页应用
│   │   ├── app.py           # API 端点
│   │   ├── templates/       # Jinja2 模板
│   │   └── static/          # CSS、JS
│   ├── database/            # SQLite 模式与操作
│   └── i18n/                # 国际化
├── locales/
│   ├── ja.json              # 日语语言包
│   ├── zh.json              # 中文语言包
│   └── en.json              # 英语语言包
├── data/                    # 运行时数据（已 gitignore）
├── config.example.yaml      # 配置模板
├── 启动.bat                 # Windows 启动器
├── 扫描.bat                 # Windows 扫描流程
├── Clear.bat                # 数据库重置
└── Parse.bat                # 重新解析与索引工具
```

## 架构说明

| 层 | 技术 | 用途 |
|-------|-----------|---------|
| 文件扫描器 | Python + os.walk | 遍历 NAS 文件夹，计算文件哈希 |
| 解析器 | python-docx、python-pptx、PyMuPDF | 提取元数据（研究者、标题、年份、参考文献） |
| 关联引擎 | TF-IDF、正则 | 发现引用链接、主题传承、关键词重叠 |
| 搜索索引 | Meilisearch | 支持容错的全文搜索 |
| 网页界面 | Flask + Vanilla JS | 浏览器端搜索 + 系谱图可视化 |

## 配置说明

复制 `config.example.yaml` 到 `config.yaml` 并根据需要修改：

| 配置项 | 说明 |
|---------|-------------|
| `SCAN_ROOTS` | 要扫描的 UNC 路径（如 `\\NAS\Research`） |
| `meilisearch.api_key` | 40 位十六进制 Meilisearch master key |
| `i18n.default_language` | 显示语言：`ja`、`zh` 或 `en` |
| `scanner.supported_extensions` | 要索引的文件类型（`.pdf`、`.docx`、`.pptx`） |

## 许可证

MIT
