# LabIndex Shiori

[![English](https://img.shields.io/badge/README-English-green)](README.md)
[![中文](https://img.shields.io/badge/README-中文-red)](README.zh.md)

> 研究室ドキュメント索引・研究系譜可視化システム — Meilisearch 全文検索 + 研究者系譜図連携

**LabIndex Shiori** は、セルフホスト・ローカルファーストの研究室ドキュメント管理システムです。共有 NAS 上の論文、学位論文、実験データをインデックス化し、Meilisearch による全文検索を提供します。さらに、**研究系譜**を自動的に発見し、研究テーマや技術がどのように先輩から後輩へ継承されていくかを可視化します。

## 機能

- **全文検索** — 全索引ドキュメントを対象とした高速・曖昧検索（Meilisearch バックエンド）
- **研究系譜図** — 卒業年度の異なる研究者間の引用関係、テーマ継承、キーワード重複を自動検出
- **多言語対応** — 日本語、中国語、英語の 3 言語インターフェース
- **ルールベース** — AI/ML 非依存。メタデータ抽出はパス解析、TF-IDF 類似度、正規表現パターンマッチングのみ
- **インクリメンタルスキャン** — 新規・変更ファイルのみを検出し、高速な日次更新を実現
- **NAS 対応** — UNC ネットワークパス対応、共有ラボストレージ向け設計

## クイックスタート

### 前提条件

- Windows 10+
- Python 3.11+
- uv（推奨）または pip

### セットアップ

```bash
# 1. リポジトリをクローン
git clone https://github.com/hanbinglei/labindex-shiori.git
cd labindex-shiori

# 2. 仮想環境を作成し依存関係をインストール
uv venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
uv pip install -r requirements.txt

# 3. テンプレートから設定ファイルを作成
cp config.example.yaml config.yaml
# config.yaml を編集：
#   - SCAN_ROOTS に NAS のパスを設定
#   - Meilisearch master key を生成：python -c "import secrets; print(secrets.token_hex(20))"
#   - meilisearch.api_key に設定

# 4. Meilisearch をダウンロード
#    https://github.com/meilisearch/meilisearch/releases から meilisearch.exe を取得
#    プロジェクトルートに配置

# 5. データベースを初期化
python -m src.main init

# 6. ドキュメントをスキャン
python -m src.main scan --root "\\YOUR_NAS\Papers" --full

# 7. メタデータを解析
python -m src.main parse --force

# 8. 研究関連を計算
python -m src.main relation

# 9. Meilisearch にインデックス
python -m src.main index --full

# 10. Web UI を起動
python -m src.main serve
# ブラウザで http://127.0.0.1:5000 を開く
```

> **ヒント：** Windows では `启动.bat` ですべてのサービスを一括起動、`扫描.bat` で対話型スキャンが可能です。

## プロジェクト構成

```
LabIndex Shiori/
├── src/
│   ├── main.py              # CLI エントリーポイント
│   ├── config.py            # 設定ローダー
│   ├── scanner/             # ファイルスキャン（NAS 走査、ハッシュ計算）
│   ├── parser/              # メタデータ抽出（パス、PDF、DOCX、PPTX）
│   ├── relation/            # 研究関連発見（M10 エンジン）
│   ├── indexer/             # Meilisearch 索引 + 自動分類
│   ├── overlay/             # 手動修正レイヤー
│   ├── web/                 # Flask Web アプリケーション
│   │   ├── app.py           # API エンドポイント
│   │   ├── templates/       # Jinja2 テンプレート
│   │   └── static/          # CSS、JS
│   ├── database/            # SQLite スキーマ・操作
│   └── i18n/                # 国際化
├── locales/
│   ├── ja.json              # 日本語ロケール
│   ├── zh.json              # 中国語ロケール
│   └── en.json              # 英語ロケール
├── data/                    # 実行時データ（gitignore）
├── config.example.yaml      # 設定テンプレート
├── 启动.bat                 # Windows ランチャー
├── 扫描.bat                 # Windows スキャンパイプライン
├── Clear.bat                # データベースリセット
└── Parse.bat                # 再解析・再インデックスツール
```

## アーキテクチャ

| レイヤー | 技術 | 目的 |
|-----------|-----------|---------|
| ファイルスキャナー | Python + os.walk | NAS フォルダを走査、ファイルハッシュを計算 |
| パーサー | python-docx, python-pptx, PyMuPDF | メタデータ抽出（研究者、タイトル、年度、参考文献） |
| 関連エンジン | TF-IDF、正規表現 | 引用リンク、テーマ継承、キーワード重複を発見 |
| 検索インデックス | Meilisearch | 曖昧検対応の全文検索 |
| Web UI | Flask + Vanilla JS | ブラウザ検索 + 系譜図可視化 |

## 設定

`config.example.yaml` を `config.yaml` にコピーして編集：

| 設定項目 | 説明 |
|---------|-------------|
| `SCAN_ROOTS` | スキャンする UNC パス（例：`\\NAS\Research`） |
| `meilisearch.api_key` | 40桁の16進数 Meilisearch master key |
| `i18n.default_language` | 表示言語：`ja`、`zh`、または `en` |
| `scanner.supported_extensions` | 索引対象のファイル形式（`.pdf`、`.docx`、`.pptx`） |

## ライセンス

MIT
