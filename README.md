# 网格 Patch 图形标注工具

**可整夹 `grid_patch_annotator` 打包 zip 发给组员，解压后双击 `run.bat` 使用**（见 [使用说明.txt](使用说明.txt)、[分发打包说明.txt](分发打包说明.txt)）。

在**已画好红色网格线和 patch 编号**的预览图上，用鼠标点击标注「低语义」区域，结果自动写入 JSON。

**只需网格预览图**即可使用；推荐同时提供 **网格参数 JSON**（`rows` / `cols` / `patch_count`），点击会更准。不需要 `smasher.py` 或 `manifest.json`。

---

## 目录结构

```
grid_patch_annotator/
├── annotate.py
├── run.bat                     ← 双击启动
├── input/                      ← 【输入】网格 JPG + 可选参数 JSON
│   ├── 0000_131_loungers_2023.jpg
│   ├── patch_grids/
│   │   ├── 0000_1309_campbells_2024.jpg
│   │   └── vidore2_patch_annotations_one.json   ← rows/cols 元数据
└── output/                     ← 【输出】单一合并 JSON（全部页）
    └── vidore2_patch_annotations_manual.json
```

所有路径相对本文件夹，**不依赖**上级 `task1` 或其它仓库脚本。

---

## 使用前必须先做什么？

### 第 1 步：准备带网格的预览图

用 `build_vidore2_patch_annotations.py`（或你现有的画网格脚本）生成 JPG，要求：

- **红色网格线**（与上述脚本默认样式一致）
- 每个格子左上角有 **patch_id** 数字
- 文件名：`序号_image_index_sample_id.jpg`，例如 `0000_131_loungers_2023.jpg`

### 第 2 步：放入 `input/`

复制预览图到 `grid_patch_annotator/input/` 或 `input/patch_grids/`。

### 第 2b 步（推荐）：放入网格参数 JSON

与图片放在**同一目录**，工具会自动读取该目录下 `*.json`：

```json
[
  {
    "sample_id": "campbells_2024",
    "image_index": 1309,
    "rows": 26,
    "cols": 47,
    "patch_count": 1222
  }
]
```

`sample_id`、`image_index` 必须与 JPG 文件名一致（如 `0000_1309_campbells_2024.jpg`）。  
也可为单张图单独放 `0000_1309_campbells_2024.json`。

**多张图**：同一目录下放多张 JPG + **一个**多行 JSON（方式 A），或每张 JPG 配一个同名 JSON（方式 B）。详见 [input/README.txt](input/README.txt)。

工具**优先用 JSON 的行列数**画点击区域，并用图片红线识别做校验；状态栏显示 `grid=26x47 (JSON)`。

### 第 3 步：安装依赖

```bash
pip install Pillow
```

`tkinter` 一般随 Python 自带（Windows 通常已包含）。

**不需要**再准备 `smashed_pages`、`manifest.json` 或打碎的小图。  
**不需要**项目根目录下的 `auto_annotate_next_90.py` / `build_vidore2_patch_annotations.py`（已内置到 `grid_patch_annotator/`）。

---

## 输出的 JSON 放在哪里？

虽然界面里**一张一张图**标注，但结果会**合并写入同一个 JSON 文件**（与 `first_ten/vidore2_patch_annotations_first_ten.json` 格式相同）。

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

每标完一页、点「下一张」或关闭工具时都会更新该文件；已标过的页会保留在数组里。每次点击约 300ms 后也会自动保存。

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

指定图片目录：

```cmd
python grid_patch_annotator\annotate.py --patch-grids-dir D:\my_grid_images
```

断点续标（跳过 JSON 里已有记录的页）：

```bash
python grid_patch_annotator/annotate.py --resume-skip-done
```

### 常用参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--patch-grids-dir` | `input/` 或 `input/patch_grids/` | 网格预览图目录（同目录 JSON 会自动加载） |
| `--grid-meta-json` | 无 | 额外指定网格参数 JSON 路径 |
| `--output` | `output/vidore2_patch_annotations_manual.json` | **单一合并**标注 JSON（读写同一文件） |
| `--start-page` | 自动 | 未指定时打开**已标注的最后一页**；指定 N 则从第 N 页开始（0 起） |
| `--resume-skip-done` | 关闭 | 跳到第一张尚未写入 JSON 的页（连续标新页时用） |

---

## 操作说明

- **单击网格格**：切换该 `patch_id` 是否在 `low_semantic_patches` 中（绿色高亮）。
- **Shift + 拖动**：框选矩形内格子，批量**添加**为低语义。
- **Alt + 拖动**：框选矩形内格子，批量**移除**低语义标记。
- **下一张 / 上一张**：保存并翻页。
- **滚轮**：上下滚动；**Ctrl + 滚轮**：缩放。
- **快捷键**：`←`/`→` 翻页，`Ctrl+S` 保存，`Z`/`Backspace` 撤销（支持整批框选），`Ctrl++`/`Ctrl+-`/`Ctrl+0` 缩放。

`patch_id`：行优先，`patch_id = row * cols + col`（与 `build_vidore2_patch_annotations.patch_box` 一致）。

无 JSON 时，工具从红线自动推断行列数；有 JSON 时以 JSON 为准（更准确）。若状态栏出现 `(识别=52x47)` 等与 JSON 不一致的提示，请核对 JSON 是否与画网格时使用的参数一致。

---

## 输出 JSON 示例

```json
{
  "sample_id": "loungers_2023",
  "image_index": 131,
  "low_semantic_patches": [0, 1, 2]
}
```

`sample_id` 与 `image_index` 来自文件名。`build_vidore2_patch_annotations.py` 生成的 JSON 含 `rows`、`cols`、`patch_count`；本工具标注结果另含 `low_semantic_patches`，`--full-output` 时写入推断的 `patch_grid`。

---

## 与根目录脚本的关系

| 脚本 | 关系 |
|------|------|
| `build_vidore2_patch_annotations.py` | 生成带红网格的预览图（推荐输入来源） |
| `smasher.py` | **不再需要**（本工具已不读 manifest） |
| `annotate_full_patches.py` | 可使用本工具输出的 JSON 作人工真值 |
