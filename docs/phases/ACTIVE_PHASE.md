# Active Phase: M8 — 路径结构解析（规则化）

## 状态

**当前阶段**：M8 — 路径结构解析
**状态**：✅ 已完成（待 Opus 审查确认）
**前一阶段**：M7 — 索引对象调整 + PPT 解析（已通过审查）

## 任务完成清单

### 任务1: 路径解析模块 ✅
- 文件：`src/parser/path_parser.py`
- 纯规则实现，不调用任何 AI/模型
- 从文件路径目录结构推断三个属性

### 任务2: 字段写入 auto_metadata ✅
- Schema 新增 `academic_year`(INTEGER)、`degree`(TEXT)、`doc_type`(TEXT)
- 迁移函数幂等兼容已有数据库
- 路径解析在 parse 流程中作为独立步骤，即使文件内容解析失败也会写入

### 任务3: 合并逻辑（year vs academic_year 独立）✅
- `year` = 文档内容中抽取的年份（如论文刊登年）
- `academic_year` = 目录结构推断的学年度（如 202403 → 2023年度）
- 两者互相独立，各自保留，不互相覆盖
- 入库验证: 70/70 文件三字段全有值

### 任务4: 规则可配置 ✅
- 所有匹配模式写入 `config.yaml` 的 `path_parsing` 段
- 年度: pattern（YYYYMM正则）、graduation_month（毕业月，默认3月=前一年度）
- 学位: ignore_prefix（圈数字前缀）、mapping（目录名→D/M/B）
- 文档类型: ignore_prefix、mapping（目录名→thesis/summary/presentation...）

### 任务5: Meilisearch 索引 ✅
- 三字段设为 filterableAttributes（供 UI 筛选）
- academic_year 加入 sortableAttributes（供排序）

### M7 遗留项确认

#### ① Excel/图纸旧记录
- DB 重建后零残留（0 条 xlsx/dwg/dxf/vwx 记录）
- 代码保留（xlsx_parser.py / cad_parser.py 在磁盘上，不再被分发表引用）

#### ② PPT 真实抽检（M8 验证步骤中与路径解析一同完成）

## 硬性约束验证
- ✅ 纯规则无 AI：PathParser 全部基于正则 + 字典映射
- ✅ 无法判定 → NULL（当前路径下全部成功判定）
- ✅ 只读：仅解析路径字符串，不碰文件
- ✅ 白名单过滤：④解析データ/⑤実験データ 里的 Excel 自动不入库

## 真实数据验证结果

### 路径解析 70 条全覆盖

| 属性 | 有值 | NULL | 判定率 |
|------|------|------|--------|
| academic_year | 70 | 0 | 100% |
| degree | 70 | 0 | 100% |
| doc_type | 70 | 0 | 100% |

### doc_type 分布
| 类型 | 数量 |
|------|------|
| thesis（本論） | 31 |
| experiment_data（実験データ） | 25 |
| summary（梗概） | 12 |
| presentation（公聴会資料） | 2 |

### year vs academic_year 对比示例
| 文件名 | year(文档内) | academic_year(路径) |
|--------|-------------|-------------------|
| 07.本論.pdf | 2024 | 2023 → ✅ 202403→2023年度 |
| 本論文_...pdf | 2024 | 2023 → ✅ |
| 2.21について.docx | 2023 | 2023 → ✅ |
| 02.本論_第2章.pdf | 2022 | 2023 → ✅（引用文献年份不同）|

### 年度换算验证
- 路径 `202403_松尾研卒業生` → MM=03 ≤ 毕业月(3) → **2023年度** ✅

### 只读验证
- 路径解析仅操作字符串，不访问文件系统

## config 中 path_parsing 规则写法
```yaml
path_parsing:
  academic_year:
    pattern: '^(\d{4})(\d{2})_'        # 捕获 YYYYMM
    graduation_month: 3                 # 3月毕业→前一年度
  degree:
    ignore_prefix: '^[①②③④⑤⑥⑦⑧⑨⑩]+'
    mapping:
      博士論文: "D" / 修士論文: "M" / 卒業論文: "B"
  doc_type:
    ignore_prefix: '^[①②③④⑤⑥⑦⑧⑨⑩]+'
    mapping:
      本論: "thesis" / 梗概: "summary" / 公聴会: "presentation"
      実験データ: "experiment_data" / 解析データ: "analysis_data"
```

## 研究者姓名预留说明
`①本論\(学生姓名)` 层的学生姓名可通过路径段直接提取。M8 不在此展开，但 PathParser 模块架构支持后续扩展：只需在 `parse()` 返回值中添加 `researcher_name` 字段即可。完整的研究者提取/关联留到 M9。

## 超出 M8 范围
- ❌ Web UI 筛选器新增（academic_year/degree/doc_type 下拉框）
- ❌ 研究者维度提取及关联（M9）
- ❌ 子主题归类优化（M5 遗留）
- ❌ i18n 翻译修正

## 等待审查
完成后交由 Claude Opus 4.8 审查：
1. 路径规则是否合理（年度换算、学位/类型映射覆盖率）
2. year vs academic_year 的区分是否清晰
3. 规则写在 config 中是否满足可配置需求
4. 代码改动是否最小化，不破坏 M1-M7 逻辑
