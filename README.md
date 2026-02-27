# Warehouse - 鉄骨倉庫図面解析システム

> CAD出力PDFから構造パラメータを自動抽出し、3Dモデル生成・数量拾い出し・重量計算を行うWebアプリケーション

---

## 目次

1. [アーキテクチャ概要](#1-アーキテクチャ概要)
2. [主要な設計判断とその理由](#2-主要な設計判断とその理由)
3. [データフロー・LLM/MLパイプラインの説明](#3-データフローllmmlパイプラインの説明)
4. [環境構築手順](#4-環境構築手順)
5. [既知の課題・今後の改善点](#5-既知の課題今後の改善点)

---

## 1. アーキテクチャ概要

### システム全体図

```
┌─────────────────────────────────────────────────────────────┐
│                    クライアント (Browser)                     │
│                   templates/index.html                       │
│              PDF Upload / 結果表示 / Chat Q&A                 │
└───────┬──────────────┬──────────────┬────────────────────────┘
        │              │              │
   POST /api/analyze  POST /api/chat  POST /api/gemini-analyze-axial
        │              │              │
┌───────▼──────────────▼──────────────▼────────────────────────┐
│                    main.py (FastAPI)                          │
│               4 エンドポイント + Jinja2テンプレート              │
└───────┬──────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────────────────────┐
│              analyzer.py (パイプラインオーケストレーター)         │
│                                                              │
│  ┌─Step A──┐  ┌─Step B──┐  ┌─Step C──┐  ┌─Step D──┐        │
│  │primitives│→│  views  │→│  grids  │→│dimensions│        │
│  │PDF解析   │  │図面分割  │  │通り芯抽出│  │寸法抽出   │        │
│  └─────────┘  └─────────┘  └─────────┘  └──────────┘        │
│        ↓                                                     │
│  ┌─Step E──┐  ┌─Step F──┐                                   │
│  │ heights │→│ quality │    品質ゲート (7項目)                 │
│  │高さ抽出  │  │検証     │                                     │
│  └─────────┘  └─────────┘                                    │
│        ↓                                                     │
│  ┌─Step 2──────────────┐  ┌─Step 3──────────┐               │
│  │     matching        │→│ reconstruction  │               │
│  │ 通り芯クロスビュー照合│  │ 3Dワイヤーフレーム│               │
│  │ (最も複雑な処理)      │  │ 生成            │               │
│  └─────────────────────┘  └─────────────────┘               │
│        ↓                        ↓                            │
│  ┌─Step 4──────┐                                             │
│  │  quantity   │  数量拾い出し                                 │
│  └─────────────┘                                             │
│        ↓                                                     │
│  ┌─Step 5 ─────────────────────────────────────────┐        │
│  │ ┌koyafuse.py┐  ┌axial_frame.py (×5通り)──────┐ │        │
│  │ │小屋伏図     │  │軸組図 Y1/Y2/X1/Xn+1/X2~Xn  │ │        │
│  │ │部材検出     │  │部材検出 + Gemini Vision     │ │        │
│  │ └────────────┘  └─────────────────────────────┘ │        │
│  │        ↓                     ↓                   │        │
│  │  ┌steel_sections.py──────────────────────┐      │        │
│  │  │ 鋼材断面解析 + 重量計算                  │      │        │
│  │  │ P-, □-, L-, M-, FB-, ラチストラス       │      │        │
│  │  └───────────────────────────────────────┘      │        │
│  └──────────────────────────────────────────────────┘        │
│        ↓                                                     │
│  AnalysisResult (Pydantic) → JSON                            │
└──────────────────────────────────────────────────────────────┘
```

### モジュール構成

| モジュール | 行数 | 責務 |
|-----------|------|------|
| `drawing/models.py` | 420 | 全データモデル定義（20+型） |
| `drawing/analyzer.py` | 293 | パイプライン実行制御 |
| `drawing/primitives.py` | 175 | PDF → テキスト・線分・矩形抽出 |
| `drawing/views.py` | 399 | 図面種別判定・ページ分割 |
| `drawing/grids.py` | 247 | 通り芯ラベル抽出 (X1, Y2等) |
| `drawing/dimensions.py` | 94 | 寸法値解析 (単一・ピッチ・繰返し) |
| `drawing/heights.py` | 129 | 軒高・最高高さ抽出 |
| `drawing/quality.py` | 198 | 品質ゲート検証（7チェック） |
| `drawing/matching.py` | 843 | **クロスビュー照合**（最大・最複雑） |
| `drawing/reconstruction.py` | ~175 | 3Dワイヤーフレーム生成 |
| `drawing/quantity.py` | 81 | 数量集計 |
| `drawing/koyafuse.py` | ~400 | 小屋伏図部材検出 |
| `drawing/axial_frame.py` | ~350 | 軸組図部材検出 |
| `drawing/steel_sections.py` | 525 | 鋼材断面パーサー・重量計算 |
| `main.py` | 151 | FastAPIサーバー |

### 技術スタック

| レイヤー | 技術 |
|---------|------|
| バックエンド | Python 3.10+, FastAPI, Uvicorn |
| PDF解析 | PyMuPDF (fitz) |
| AI/LLM | Google Gemini API (2.0 Flash / 3 Pro Preview) |
| データモデル | Pydantic v2 |
| フロントエンド | Jinja2テンプレート, バニラJS/HTML |
| 環境管理 | python-dotenv |

---

## 2. 主要な設計判断とその理由

### 2-1. 通り芯（グリッドラベル）を「ユニバーサルアドレス」として使用

**判断**: X1, Y2 等の通り芯ラベルを、複数の図面ビューを紐付ける共通座標系として採用。

**理由**: 建築図面では、平面図・立面図・断面図が同じ通り芯を共有する。これを利用することで、ビュー間の寸法照合が可能になる。例えば平面図のX1-Xn+1間の距離と、立面図の同じグリッド間の部材長さを一致させられる。

**実装箇所**: `drawing/matching.py` L172-199 — `_build_frame_links()` で各X通りがどの立面図に存在するかマッピング。

### 2-2. マルチ戦略フォールバック方式のパラメータ解決

**判断**: スパン・桁行長さ・ベイピッチ・ベイ数の各パラメータについて、3~4段階の独立した解決戦略を用意。

**理由**: PDF図面はCADソフトや作成者によって構造が異なる。寸法線がない図面、通り芯が不完全な図面にも対応するため、一つの方法が失敗しても次の方法で試みる。

**実装例** — スパン解決 (`drawing/matching.py` L231-293):

| 優先度 | 戦略 | 条件 |
|-------|------|------|
| 1 | 平面図のグリッド線間距離 × 縮尺 → 寸法値と照合 | 平面図あり + 縮尺あり |
| 2 | 断面図・X方向立面図から最大寸法を取得 | クロスビューあり |
| 3 | Y通り芯の位置差 × 縮尺で算出 | Y通り芯2本以上 |

### 2-3. Gemini Vision APIによる部材検出

**判断**: 軸組図の部材検出にコンピュータビジョン（OpenCV等）ではなく、Google Gemini LLMのVision機能を使用。

**理由**:
- 日本の鉄骨図面の引出線・丸番号・寸法値の読取りは、ルールベースのCV実装が非常に困難
- LLMは「①が主架構材」「寸法線の値を読む」といった高レベルの理解が可能
- 開発コストの大幅削減（CV実装なら数千行 → プロンプト約100行）

**トレードオフ**:

| メリット | デメリット |
|---------|----------|
| 開発コスト大幅削減 | API依存（レイテンシ・コスト） |
| 高レベルな画像理解 | オフライン動作不可 |
| プロンプト修正で柔軟に調整 | 応答フォーマットの不安定さ |

**実装箇所**: `main.py` L73-150 — `gemini-3-pro-preview` モデルに画像+詳細プロンプトを送信。

### 2-4. Pydanticによる型安全なデータフロー

**判断**: 全データモデルを `pydantic.BaseModel` で定義し、パイプライン全体を型安全に。

**理由**:
- パイプラインの各ステップ間でデータ構造が明確になる
- `model_dump()` でJSON APIレスポンスに直接変換可能
- 開発時のバグ発見が早くなる（型エラー、必須フィールド欠落）

**定義箇所**: `drawing/models.py` — 20以上のモデルクラスを定義。

### 2-5. ページ回転への全方位対応

**判断**: 0°/90°/180°/270° の全回転状態で座標変換を実装。

**理由**: CADソフトからのPDF出力は、用紙の向きによって回転が設定されることがある。回転が90°の場合、X方向の通り芯が実際には水平線になる等、座標系が入れ替わるため明示的な変換が必要。

**実装箇所**: `drawing/matching.py` L219 — `swapped = page_rotation in (90, 270)` で軸の入替を判定し、グリッド線の検索方向を切替。

### 2-6. 品質ゲートを「非ブロッキング」で設計

**判断**: Step F の品質検証は PASS/WARN/FAIL を返すが、FAIL でもパイプラインを中断しない。

**理由**: 部分的な結果でも有用な場合がある（例: 高さ情報が取れなくても平面情報は正しい）。クライアント側で信頼度を判断できるようにする。

**検証項目** (7項目):

| # | チェック項目 | PASS条件 |
|---|------------|---------|
| 1 | ビュー検出数 | ≥2 |
| 2 | 平面図の有無 | 存在する |
| 3 | 通り芯ラベル | X≥2, Y≥1 |
| 4 | 通り芯-線分関連付け率 | ≥80% |
| 5 | 寸法値検出数 | ≥5 |
| 6 | 高さパラメータ | ≥1 |
| 7 | 主要高さ (軒高+最高高さ) | 両方あり |

---

## 3. データフロー・LLM/MLパイプラインの説明

### 3-1. メインデータフロー

```
[PDF バイナリ]
    │
    ▼ PyMuPDF (fitz)
[PagePrimitives]
  ├─ texts: TextSpan[]      ← page.get_text('dict') + SHXアノテーション
  ├─ lines: Line[]           ← page.get_drawings()
  └─ rects: BBox[]

    │ views.py — タイトルテキストの正規表現マッチ + ページ分割
    ▼
[View[]]  ×4種 (屋根伏図, 平面図, 立面図, 断面図)
  各ビュー = { region: BBox, texts[], lines[] }

    │ grids.py + dimensions.py + heights.py — 並列抽出
    ▼
[GridSystem]           X1,X7,Xn+1 / Y1,Y2 + 線分関連付け
[Dimension[]]          7500, @2000, 2000×7 等
[HeightParam[]]        軒高=4900, 最高高さ=7950 等

    │ quality.py
    ▼
[QualityReport]        7項目の検証結果

    │ matching.py (最も複雑)
    ▼
[MatchingResult]
  ├─ span: 15000mm          (Y方向スパン)
  ├─ length: 30000mm        (X方向桁行長さ)
  ├─ bay_pitch: 2000mm      (ベイピッチ)
  ├─ bay_count: 15           (ベイ数)
  ├─ eave_height: 4900mm    (軒高)
  └─ max_height: 7950mm     (最高高さ)

    │ reconstruction.py
    ▼
[StructuralModel]
  ├─ members: Member3D[]    柱・ラフター・棟木・母屋
  ├─ envelope: BuildingEnvelope
  └─ x/y grid positions

    │ quantity.py
    ▼
[QuantityTakeoff]
  └─ groups: MemberGroup[]  タイプ×長さでグルーピング
```

### 3-2. LLMパイプライン（Gemini Vision）

軸組図（立面の骨組図）の部材検出に Google Gemini Vision を使用する。

```
┌─────────────────────────────────────────────┐
│            Step 5b-5f: 軸組図解析             │
│                                             │
│  ┌──────────────┐                           │
│  │ axial_frame.py│                          │
│  │  図面領域検出   │                          │
│  └──────┬───────┘                           │
│         ▼                                   │
│  ┌──────────────┐                           │
│  │ PyMuPDF       │  DPI=150で画像レンダリング  │
│  │ get_pixmap()  │  → base64 PNG            │
│  └──────┬───────┘                           │
│         ▼                                   │
│  ┌──────────────────────────────────────┐   │
│  │        Gemini 3 Pro Preview          │   │
│  │                                      │   │
│  │  入力:                                │   │
│  │   - 画像: 軸組図のクロップ画像          │   │
│  │   - プロンプト: ~100行の詳細指示        │   │
│  │     ├ 丸番号(①②③)の検出               │   │
│  │     ├ 各部材の本数カウント              │   │
│  │     ├ 方向判定 (x/y/diagonal/arch)    │   │
│  │     ├ 寸法線の読取り → unit_length_mm  │   │
│  │     └ アーチ長さの近似計算              │   │
│  │                                      │   │
│  │  出力: JSON                           │   │
│  │   { members: [{                      │   │
│  │       member_number, label,           │   │
│  │       line_count, orientation,        │   │
│  │       unit_length_mm, total_length_mm │   │
│  │   }]}                                │   │
│  └──────────┬───────────────────────────┘   │
│             ▼                               │
│  ┌──────────────────────────────────────┐   │
│  │ 後処理 (main.py L131-136)             │   │
│  │  total_length = unit_length × count  │   │
│  └──────────┬───────────────────────────┘   │
│             ▼                               │
│  ┌──────────────────────────────────────┐   │
│  │ 重量割当 (analyzer.py L46-73)         │   │
│  │  steel_sections.py のカタログ照合      │   │
│  │  unit_weight (kg/m) × total_length   │   │
│  └──────────────────────────────────────┘   │
└─────────────────────────────────────────────┘
```

#### Geminiプロンプトの重要な指示 (`main.py` L77-113)

| ルール | 内容 |
|-------|------|
| 寸法読取り | 寸法線の値を**実際に画像から読取る**（推測禁止） |
| 分割寸法 | セグメントは合算する（例: 7500+7500=15000） |
| 主架構材① | 基礎～基礎の連続要素として柱+アーチを合計 |
| アーチ長さ | 近似式: `arc ≈ span × (1 + (2/3) × (rise/span)²)` |
| 修飾子 | 「内側」「外側」は別部材として扱う |

#### 使用モデル

| 用途 | モデル | 理由 |
|------|-------|------|
| チャットQ&A | `gemini-2.0-flash` | 低コスト・高速応答 |
| 軸組図解析 | `gemini-3-pro-preview` | 画像理解精度が重要 |

### 3-3. チャット機能

```
[ユーザーメッセージ] → POST /api/chat
    → Gemini 2.0 Flash
    → テキスト応答（1往復）
```

現状はシンプルな1往復のQ&Aであり、分析結果のコンテキストは渡していない（独立したチャット）。

### 3-4. 鋼材断面解析パイプライン

`steel_sections.py` は日本の鋼材表記を解析し、断面積・単位重量を算出する。

```
入力テキスト例:
  "2Ps-42.7φ×2.3t, D=450, ラチスP-42.7φ×1.9t, θ=45°"

    │ parse_member_entry()
    ▼
  ラチス検出 → _parse_lattice_entry()
    ├─ 弦材: 2 × Ps-42.7φ×2.3t
    │    A = π(42.7-2.3)×2.3 = 291.9 mm²
    │    w = 291.9 × 7.85e-3 = 2.291 kg/m × 2本 = 4.582 kg/m
    ├─ ラチス: P-42.7φ×1.9t
    │    A = π(42.7-1.9)×1.9 = 243.6 mm²
    │    w = 243.6 × 7.85e-3 / cos(45°) = 2.704 kg/m
    └─ 合計: 4.582 + 2.704 = 7.286 kg/m
```

対応する断面形状:

| 表記 | 形状 | 断面積の計算式 |
|------|------|--------------|
| `P-Dφ×t` | パイプ (STK) | A = π(D-t)t |
| `□-B×H×t` | 角形鋼管 (STKR) | A = 2(B+H-2t)t |
| `L-a×b×t` | 山形鋼 | A = (a+b-t)t |
| `M-d` | 丸鋼 | A = πd²/4 |
| `FB-b×t` | 平鋼 | A = bt |
| `nX-..., D=d, ラチスP-...` | ラチストラス | 弦材+ラチスの合成 |

---

## 4. 環境構築手順

### 前提条件

- Python 3.10+（`float | None` 等の新構文を使用）
- Google Gemini API キー（[Google AI Studio](https://aistudio.google.com/) で取得）

### セットアップ

```bash
# 1. リポジトリのクローン
git clone <repository-url>
cd Warehouse

# 2. 仮想環境の作成と有効化
python -m venv venv
source venv/bin/activate        # Linux / macOS
# venv\Scripts\activate         # Windows

# 3. 依存パッケージのインストール
pip install -r requirements.txt
```

### 依存パッケージ一覧 (`requirements.txt`)

| パッケージ | バージョン | 用途 |
|-----------|----------|------|
| `fastapi` | latest | Web APIフレームワーク |
| `uvicorn[standard]` | latest | ASGIサーバー |
| `jinja2` | latest | HTMLテンプレートエンジン |
| `google-generativeai` | latest | Google Gemini API クライアント |
| `python-dotenv` | latest | `.env` ファイルからの環境変数読込 |
| `PyMuPDF` | latest | PDF解析ライブラリ (import名: `fitz`) |

### 環境変数の設定

```bash
# .env ファイルを作成
echo "GEMINI_API_KEY=your-api-key-here" > .env
```

| 変数名 | 必須 | 説明 |
|--------|------|------|
| `GEMINI_API_KEY` | Yes | Google Gemini APIの認証キー |

### サーバー起動

```bash
# 開発モード（ホットリロード有効）
uvicorn main:app --reload

# 本番モード
uvicorn main:app --host 0.0.0.0 --port 8000
```

ブラウザで `http://localhost:8000` を開き、PDFをアップロードして解析を開始する。

### APIエンドポイント一覧

| メソッド | パス | 説明 | リクエスト | レスポンス |
|---------|------|------|----------|----------|
| GET | `/` | フロントエンドUI表示 | — | HTML |
| POST | `/api/analyze` | PDF解析パイプライン実行 | `multipart/form-data` (PDF) | `AnalysisResult` JSON |
| POST | `/api/chat` | Geminiチャット | `{ message: string }` | `{ reply: string }` |
| GET | `/api/member-catalog` | FIX-R-15部材カタログ取得 | — | `MemberCatalog` JSON |
| POST | `/api/gemini-analyze-axial` | 軸組図のAI部材検出 | `{ image, view_name, span?, length? }` | `{ members[], raw_response }` |

---

## 5. 既知の課題・今後の改善点

### 重大度: 高

| # | 課題 | 詳細 | 対象箇所 |
|---|------|------|---------|
| 1 | **テストが存在しない** | ユニットテスト・統合テストが一切ない。パイプラインの各ステップの正確性が手動確認に依存しており、リグレッションの検出が不可能。 | プロジェクト全体 |
| 2 | **Gemini応答パースが脆弱** | 正規表現 `r"```(?:json)?\s*([\s\S]*?)```"` によるJSON抽出。Geminiの出力形式が変わると即座に破綻する。 | `main.py` L124-128 |
| 3 | **エラー復旧メカニズムがない** | 初期ステップ（例: ビュー検出失敗）が誤データを返した場合、後続ステップがそれを検出・補正する仕組みがない。 | `drawing/analyzer.py` |
| 4 | **トレースバックがAPIレスポンスに露出** | `traceback.format_exc()` がそのままJSONレスポンスに含まれ、内部パス・コード構造が漏洩するセキュリティリスク。 | `main.py` L62-63, L148-149 |

### 重大度: 中

| # | 課題 | 詳細 | 対象箇所 |
|---|------|------|---------|
| 5 | **マジックナンバーの散在** | グリッド線最小長50pt、ラベル距離100pt、検索半径40pt、均一性判定±10%等が定数化されていない。 | 複数モジュール |
| 6 | **キャッシュ機構がない** | 同一PDFの再解析でも全処理をやり直す。Gemini APIコールも毎回実行される。 | `drawing/analyzer.py` |
| 7 | **部材カタログがハードコード** | FIX-R-15の部材リストが `build_fix_r15_catalog()` に直書きされており、他の図面番号に対応不可。 | `drawing/steel_sections.py` L430-484 |
| 8 | **matching.pyの巨大化** | 843行の単一ファイルに複数の責務が混在。パラメータ解決・整合性検証・ヘルパー関数が分離されていない。 | `drawing/matching.py` |
| 9 | **チャット機能にコンテキストがない** | `/api/chat` は分析結果を参照せず独立したGemini呼出し。ユーザーが分析結果について質問しても回答できない。 | `main.py` L35-38 |
| 10 | **同期APIでの重い処理** | `analyze()` エンドポイントが同期（`def`）で定義されており、PDF解析中にサーバーがブロックされる。 | `main.py` L48-63 |

### 重大度: 低

| # | 課題 | 詳細 |
|---|------|------|
| 11 | **矩形建物のみ対応** | 不整形平面・スキップフロア・多棟建物は想定外。 |
| 12 | **マルチページの限定対応** | ページ1で平面図・立面図を解析し、ページ2以降は小屋伏図・軸組図のみ。 |
| 13 | **ログ機構がない** | `print()` のみ使用。構造化ログ（`logging` モジュール）未導入。 |
| 14 | **バッチ処理非対応** | 1ファイルずつの処理のみ。複数PDF一括解析機能がない。 |
| 15 | **フロントエンドが単一HTML** | 26KB超の `index.html` にJS/CSS全て含まれており、コンポーネント分割されていない。 |

### 今後の改善ロードマップ（推奨優先順）

```
Phase 1: 品質基盤
  ├─ 1. テスト基盤の構築 — pytest + サンプルPDFでの統合テスト
  ├─ 2. セキュリティ修正 — トレースバック非露出、入力バリデーション強化
  └─ 3. ログ機構の導入 — Python logging モジュール + 構造化ログ

Phase 2: 堅牢性向上
  ├─ 4. Gemini応答の堅牢化 — Structured Output (JSON mode)、リトライ、バリデーション
  ├─ 5. 設定の外部化 — 閾値・モデル名・DPIをconfig.yaml or 環境変数へ
  └─ 6. エラー復旧 — パイプラインステップ間のバリデーション・部分結果返却

Phase 3: 機能拡張
  ├─ 7. matching.pyの分割 — span_resolver.py, length_resolver.py 等に分離
  ├─ 8. 部材カタログの動的化 — PDFからの自動抽出 or DB/JSONファイルからロード
  ├─ 9. チャットへのRAG統合 — 分析結果をコンテキストとしてGeminiに渡す
  └─ 10. 非同期化 — async def + バックグラウンドタスクでUX改善

Phase 4: スケーラビリティ
  ├─ 11. キャッシュ機構 — PDF解析結果・Gemini応答のキャッシュ
  ├─ 12. バッチ処理 — 複数PDF一括解析
  └─ 13. 対応図面の拡張 — 不整形平面、H形鋼、混構造への対応
```

---

## 付録: データモデル一覧

```
Primitives (Step A):
  Point, BBox, TextSpan, Line, PagePrimitives

Views (Step B):
  ViewType (ROOF_PLAN | FLOOR_PLAN | ELEVATION | SECTION)
  View

Grid (Step C):
  GridAxis (X | Y), GridLabel, GridSystem

Dimensions (Step D):
  DimensionType (SINGLE | PITCH | REPEAT), Dimension

Heights (Step E):
  HeightType (EAVE_HEIGHT | MAX_HEIGHT | GL | FL | DESIGN_GL)
  HeightParam

Quality (Step F):
  GateStatus (PASS | WARN | FAIL), QualityCheck, QualityReport

Matching (Step 2):
  ViewGridInfo, FrameLink, AnchoredParam, MatchingResult

3D Reconstruction (Step 3):
  Point3D, MemberType, Member3D, BuildingEnvelope, StructuralModel

Quantity (Step 4):
  MemberGroup, QuantityTakeoff

Member Detection (Step 5):
  LeaderTip, DetectedMember, KoyafuseResult, AxialFrameResult

Steel Sections:
  SectionShape, SteelSection, LatticeTrussSpec, MemberEntry, MemberCatalog

Final Output:
  AnalysisResult (全ステップの結果を統合)
```
