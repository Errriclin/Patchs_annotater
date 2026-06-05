# 网格 Patch 图形标注工具

**可整夹 `grid_patch_annotator` 打包 zip 发给组员，解压后双击 `run.bat` 使用**（见 [使用说明.txt](使用说明.txt)、[分发打包说明.txt](分发打包说明.txt)）。

在**已画好红色网格线和 patch 编号**的预览图上，用鼠标点击标注「低语义」区域，结果自动写入 JSON。

**只需网格预览图**即可使用；推荐同时提供 **网格参数 JSON**（`rows` / `cols` / `patch_count`），点击会更准。不需要 `smasher.py` 或 `manifest.json`。

---

## build_vidore2_patch_annotations.py（画网格 + 规格 JSON）

项目根目录脚本：`../build_vidore2_patch_annotations.py`（与 `grid_patch_annotator/` 同级）。从 **ViDoRe2 本地 HF 缓存** 随机抽样页面，用 **ColQwen3 processor** 计算默认 patch 网格，并输出**带红线的预览 JPG** 与**每张图一个规格 JSON**，供本目录 `annotate.py` 使用。

### 做什么

| 步骤 | 说明 |
|------|------|
| 读图 | 从 `--dataset-path` + `--dataset-name` 加载语料（默认离线 `HF_DATASETS_OFFLINE=1`） |
| 划网格 | 调用本地 `tomoro-colqwen3-embed-4b`（或你的 ColQwen3 processor），读取 `image_grid_thw` + `merge_size` 得到 `rows` × `cols` |
| 画预览 | 在原图上画红色网格线、左上角 `patch_id`（行优先：`patch_id = row * cols + col`） |
| 写 JSON | 每张图一个 `.json`，字段含 `sample_id`、`image_index`、`rows`、`cols`、`patch_count` |

### 数据从哪来（ViDoRe2）

常用命令对应的是 **ViDoRe2 的一个子集**，不是整个多域合集自动合并：

| 参数 | 典型值 | 含义 |
|------|--------|------|
| `--dataset-name` | `vidore/esg_reports_v2` | ViDoRe2 ESG 报告子任务 |
| `--dataset-config` | `corpus` | 文档页语料（含 `image` 列） |
| `--split` | `test` | test split（例如 esg corpus 约 1538 页） |
| `--dataset-path` | `datasets` | 本地 Hugging Face 缓存根目录 |

换子集时改 `--dataset-name`（如 `vidore/economics_reports_v2`），并保证 `datasets/` 下已有对应缓存。

**抽样方式**：`random.sample`，无放回，且按 `image_index` 去重，保证**不重复同一张图**。

**主键对照**（找回原图时用）：

| 字段 | 含义 |
|------|------|
| `image_index` | 在该 `corpus/test` 表中的**行号**（稳定） |
| `sample_id` | 数据集中的 **`doc-id`**（如 `campbells_2024`） |
| 文件名 `0003_559_starbucks_2023` | `0003`=本次抽样序号，`559`=`image_index`，`starbucks_2023`=`sample_id` |

网格 JPG 是从语料当场渲染的；**无损原图**仍在 `datasets` 的 Parquet 里，用 `image_index` 再读即可（见下文「找回原图」）。

### 依赖安装

在项目根目录：

```bash
pip install -r requirements_vidore2_patch.txt
```

需要：`Pillow`、`datasets`、`transformers`、`torch`、`torchvision`，以及本地 **ColQwen3 processor** 目录（如 `tomoro-colqwen3-embed-4b`）。

### 输出文件

在 `--grid-output-dir`（未单独指定 `--json-output-dir` 时与网格目录相同）下，**每张图一对文件**：

```
grid_patch_annotator/input/patch_grids/
  0000_794_collin_foods_2024.jpg      ← 网格预览
  0000_794_collin_foods_2024.json     ← 规格 JSON
  0001_519_hellofresh_2023.jpg
  0001_519_hellofresh_2023.json
  ...
```

**单张规格 JSON 示例**：

```json
{
  "sample_id": "collin_foods_2024",
  "image_index": 794,
  "rows": 42,
  "cols": 30,
  "patch_count": 1260,
  "grid_preview": "0000_794_collin_foods_2024.jpg"
}
```

可选 `--auto-suggest`：在同名 JSON 中增加 `suggested_low_semantic_patches`（启发式低语义建议，非人工标注）。

### 常用命令（CMD，项目根目录执行）

**随机 50 张，输出到本工具输入目录**（完全随机且不重复，每次换种子）：

```cmd
cd /d d:\CSResearch\task1 && python build_vidore2_patch_annotations.py --dataset-path datasets --dataset-name vidore/esg_reports_v2 --dataset-config corpus --processor-path tomoro-colqwen3-embed-4b --grid-output-dir grid_patch_annotator\input\patch_grids --sample-size 50 --seed %RANDOM%
```

**固定种子复现同一批 50 张**：

```cmd
cd /d d:\CSResearch\task1 && python build_vidore2_patch_annotations.py --dataset-path datasets --dataset-name vidore/esg_reports_v2 --dataset-config corpus --processor-path tomoro-colqwen3-embed-4b --grid-output-dir grid_patch_annotator\input\patch_grids --sample-size 50 --seed 42
```

**只生成 1 张（调试）**：

```cmd
cd /d d:\CSResearch\task1 && python build_vidore2_patch_annotations.py --dataset-path datasets --dataset-name vidore/esg_reports_v2 --dataset-config corpus --processor-path tomoro-colqwen3-embed-4b --grid-output-dir grid_patch_annotator\input\patch_grids --sample-size 1 --seed 42
```

### 命令行参数一览

| 参数 | 必填 | 默认 | 说明 |
|------|------|------|------|
| `--dataset-path` | 是 | — | 本地 HF 缓存目录、图片目录或 manifest |
| `--processor-path` | 是 | — | ColQwen3 processor 本地路径 |
| `--grid-output-dir` | 建议 | — | 网格预览 JPG 输出目录 |
| `--json-output-dir` | 否 | 同 `grid-output-dir` | 规格 JSON 输出目录 |
| `--sample-size` | 否 | `500` | 抽样张数（无放回） |
| `--seed` | 否 | `42` | 随机种子 |
| `--dataset-name` | 否* | — | HF 数据集名，如 `vidore/esg_reports_v2` |
| `--dataset-config` | 否 | — | 配置名，如 `corpus` |
| `--split` | 否 | `test` | 数据 split |
| `--grid-line-width` | 否 | `1` | 网格线宽（像素） |
| `--grid-label-font-size` | 否 | `10` | patch 编号字号 |
| `--auto-suggest` | 否 | 关 | 写入启发式 `suggested_low_semantic_patches` |
| `--image-column` / `--id-column` | 否 | 自动推断 | 自定义列名 |
| `--max-num-visual-tokens` | 否 | — | 传给 processor 的可选上限 |

\* 使用 ViDoRe2 HF 缓存时建议始终指定 `--dataset-name` 与 `--dataset-config`。

### 找回评测集原图

用 JSON 里的 `image_index`（与生成网格时相同的 `dataset-name` / `config` / `split`）：

```cmd
cd /d d:\CSResearch\task1 && python -c "from pathlib import Path; from datasets import load_dataset; import os; os.environ['HF_DATASETS_OFFLINE']='1'; ds=load_dataset('vidore/esg_reports_v2','corpus',split='test',cache_dir='datasets',trust_remote_code=True); i=794; img=ds[i]['image']; sid=ds[i]['doc-id']; out=Path('exports')/f'{i}_{sid}.png'; out.parent.mkdir(exist_ok=True); img.save(out); print(out)"
```

将 `794` 换成目标 JSON 中的 `image_index`。也可用项目根目录 `smasher.py`，按 `image_index` 从 `datasets` 读原图裁 patch（不读带网格 JPG）。

### 画网格 → 人工标注 流程

1. 运行 `build_vidore2_patch_annotations.py`，输出到 `input/patch_grids/`（JPG + 同名 JSON）。
2. 双击 `run.bat` 或运行 `annotate.py`，在预览图上点击标注。
3. 标注结果写入 `output/vidore2_patch_annotations_manual.json`（**单一合并 JSON**，见下文）。

---

## 目录结构

```
task1/
├── build_vidore2_patch_annotations.py   ← 画网格（见上一节）
├── requirements_vidore2_patch.txt
├── datasets/                              ← ViDoRe2 本地缓存
└── grid_patch_annotator/
    ├── annotate.py
    ├── run.bat                              ← 双击启动标注
    ├── input/
    │   └── patch_grids/                     ← build 脚本输出：JPG + 规格 JSON
    └── output/
        └── vidore2_patch_annotations_manual.json   ← 人工标注（合并）
```

打包分发时通常只 zip `grid_patch_annotator/`；组员机器上需**先有** `input/patch_grids/` 里的 JPG+JSON（由负责人用 build 脚本生成后拷入）。

---

## 使用前必须先做什么？

### 第 1 步：准备带网格的预览图

用上一节的 **`build_vidore2_patch_annotations.py`** 生成，或从他人处获得同格式文件，要求：

- **红色网格线**（脚本默认线宽 1px、编号字号 10；高分辨率页可在命令行加大）
- 每个格子左上角有 **patch_id** 数字
- 文件名：`序号_image_index_sample_id.jpg`，例如 `0000_794_collin_foods_2024.jpg`
- **推荐**同名 `.json`（`rows` / `cols` / `patch_count`）

### 第 2 步：放入 `input/`

复制到 `grid_patch_annotator/input/` 或 `input/patch_grids/`（仅该目录**第一层**，不要套子文件夹）。

### 第 2b 步（推荐）：网格参数 JSON

build 脚本会在同目录生成**每张图一个** `0000_794_collin_foods_2024.json`。`annotate.py` 也会自动扫描目录下 `*.json`（跳过已含 `low_semantic_patches` 的标注文件）。

也可用手写汇总文件（多页数组），见 [input/README.txt](input/README.txt)。

`sample_id`、`image_index` 必须与 JPG 文件名一致。工具**优先用 JSON 的行列数**画点击区域；状态栏显示 `grid=42x30 (JSON)`。

### 第 3 步：安装依赖（仅标注）

```bash
pip install -r grid_patch_annotator/requirements.txt
```

或 `pip install Pillow`。`tkinter` 一般随 Python 自带（Windows 通常已包含）。

**不需要** `smashed_pages`、`manifest.json` 或打碎小图。

---

## 输出的 JSON 放在哪里？

界面里**一张一张**标注，结果**合并写入同一个 JSON**（与 `first_ten/vidore2_patch_annotations_first_ten.json` 数组格式相同）。

| 文件 | 默认路径 |
|------|----------|
| **全部页的标注** | `grid_patch_annotator/output/vidore2_patch_annotations_manual.json` |

示例结构：

```json
[
  {
    "sample_id": "campbells_2024",
    "image_index": 1309,
    "low_semantic_patches": [0, 1, 2]
  },
  {
    "sample_id": "chipotle_2023",
    "image_index": 831,
    "low_semantic_patches": [5, 6]
  }
]
```

每标完一页、点「下一张」或关闭工具时都会更新该文件。每次点击约 300ms 后也会自动保存。

指定其它输出路径：

```bash
python grid_patch_annotator/annotate.py --output D:\path\to\all_labels.json
```

---

## 如何启动？

**双击** `run.bat`，或在项目根目录：

```bash
python grid_patch_annotator/annotate.py
```

启动后会先弹出起始页提示框。输入 **1 到总页数** 表示“从第几张图片开始”；点击取消则沿用自动逻辑，打开已标注的最后一页。

指定图片目录：

```cmd
python grid_patch_annotator\annotate.py --patch-grids-dir D:\my_grid_images
```

断点续标（跳过合并 JSON 里已有记录的页）：

```bash
python grid_patch_annotator/annotate.py --resume-skip-done
```

### 常用参数（annotate.py）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--patch-grids-dir` | `input/` 或 `input/patch_grids/` | 网格预览图目录（同目录 JSON 会自动加载） |
| `--grid-meta-json` | 无 | 额外指定网格参数 JSON 路径 |
| `--output` | `output/vidore2_patch_annotations_manual.json` | **单一合并**标注 JSON |
| `--start-page` | 弹窗选择 | 指定 N 则从第 N 页开始（0 起），并跳过起始页弹窗 |
| `--resume-skip-done` | 关闭 | 跳到第一张尚未写入合并 JSON 的页，并跳过起始页弹窗 |
| `--no-start-dialog` | 关闭 | 不弹出起始页提示框，恢复旧版自动打开逻辑 |

---

## 操作说明

- **单击网格格**：切换该 `patch_id` 是否在 `low_semantic_patches` 中（绿色高亮）。
- **Shift + 拖动**：框选矩形内格子，批量**添加**为低语义。
- **Alt + 拖动**：框选矩形内格子，批量**移除**低语义标记。
- **下一张 / 上一张**：保存并翻页。
- **滚轮**：上下滚动；**Ctrl + 滚轮**：缩放。
- **快捷键**：`←`/`→` 翻页，`Ctrl+S` 保存，`Z`/`Backspace` 撤销（支持整批框选），`Ctrl++`/`Ctrl+-`/`Ctrl+0` 缩放。

`patch_id`：行优先，`patch_id = row * cols + col`（与 `build_vidore2_patch_annotations.patch_box` 一致）。

无 JSON 时从红线自动推断行列数；有 JSON 时以 JSON 为准。若状态栏出现 `(识别=52x47)` 等与 JSON 不一致，请核对 JSON 是否与画网格时一致。

---

## 相关脚本（项目根目录）

| 脚本 | 作用 |
|------|------|
| `build_vidore2_patch_annotations.py` | ViDoRe2 抽样 + ColQwen3 网格 + 预览 JPG + **每图一个规格 JSON** |
| `grid_patch_annotator/annotate.py` | 在预览图上人工标 `low_semantic_patches` → **合并 JSON** |
| `smasher.py` | 按 `image_index` 从 `datasets` 原图裁 patch（不依赖网格 JPG） |
| `annotate_full_patches.py` | 用 API 对整页网格图自动标注（可选） |
