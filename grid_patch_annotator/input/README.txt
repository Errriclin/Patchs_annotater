【输入目录】网格预览图 + 网格参数 JSON（支持多张）

所有文件放在同一目录下（二选一）：
  grid_patch_annotator/input/
  grid_patch_annotator/input/patch_grids/

不要建子文件夹放图片，工具只扫描该目录「第一层」的 JPG。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
方式 A：一个 JSON 管多张图（推荐）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

目录示例：

  input/patch_grids/
    0000_1309_campbells_2024.jpg
    0001_831_chipotle_2023.jpg
    0002_927_ssp_2023.jpg
    grid_metadata.json          ← 一个文件里写多页参数

grid_metadata.json 示例：

  [
    {
      "sample_id": "campbells_2024",
      "image_index": 1309,
      "rows": 26,
      "cols": 47,
      "patch_count": 1222
    },
    {
      "sample_id": "chipotle_2023",
      "image_index": 831,
      "rows": 42,
      "cols": 30,
      "patch_count": 1260
    },
    {
      "sample_id": "ssp_2023",
      "image_index": 927,
      "rows": 38,
      "cols": 32,
      "patch_count": 1216
    }
  ]

匹配规则：每条 JSON 的 sample_id、image_index 必须与对应 JPG 文件名一致。
  0000_1309_campbells_2024.jpg  →  image_index=1309, sample_id=campbells_2024

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
方式 B：每张图旁边一个 JSON
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  input/patch_grids/
    0000_1309_campbells_2024.jpg
    0000_1309_campbells_2024.json    ← 只写这一页
    0001_831_chipotle_2023.jpg
    0001_831_chipotle_2023.json

单页 JSON 可以是对象，也可以是数组里一条：

  {
    "sample_id": "campbells_2024",
    "image_index": 1309,
    "rows": 26,
    "cols": 47,
    "patch_count": 1222
  }

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
注意
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. 标注结果合并写在 ../output/vidore2_patch_annotations_manual.json（一个文件含全部页）。
   不要和上面的「参数 JSON」混在同一个文件里。
2. 参数 JSON 里只需 rows/cols/patch_count，不要和 output 里带 low_semantic_patches 的标注结果混用。
3. 用「下一张」按文件名序号顺序翻页；每页会自动匹配自己的 JSON 行。

启动：双击 run.bat，或：
  python annotate.py --patch-grids-dir input/patch_grids
