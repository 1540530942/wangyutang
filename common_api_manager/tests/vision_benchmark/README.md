# Vision Benchmark

评测 lv_server 本地 VL 模型与 Spark 外部接口的图像理解能力和响应速度。

## 测试集（18 张图）

| 编号 | 文件名 | 测试内容 |
|---|---|---|
| 01-06 | `solid_*.jpg` | 纯色识别（红/蓝/绿/黄/白/黑） |
| 07-08 | `gradient_*.jpg` | 水平渐变色描述 |
| 09 | `basic_shapes.jpg` | 多种几何形状识别（6色矩形/圆/三角形） |
| 10 | `checkerboard.jpg` | 棋盘格图案识别 |
| 11-12 | `stripes_*.jpg` | 竖向/横向彩色条纹 |
| 13 | `text_mixed.jpg` | 多语言混合文字识别（中英文、数字、特殊字符） |
| 14 | `bar_chart.jpg` | 柱状图理解 |
| 15 | `low_contrast.jpg` | 低对比度图形识别 |
| 16 | `dark_night_scene.jpg` | 暗光场景（星空+地面） |
| 17 | `radial_gradient.jpg` | 径向渐变色分布 |
| 18 | `mixed_content.jpg` | 图文混排（标题+图形+文字说明） |

## 快速运行

```bash
# 1. 生成图片
python3 generate_images.py

# 2. 运行评测（默认对比 lv_server 和 Spark）
python3 run_benchmark.py

# 自定义端点
python3 run_benchmark.py \
  --lv   http://127.0.0.1:8013/common/api/vision/spark/analyze-json \
  --spark https://www.wangyutang.cn/common/api/vision/spark/analyze-json \
  --out  results.json
```

## 基准结果（2026-07-01，640×480 JPEG）

| 端点 | 模型 | 均值响应时间 |
|---|---|---|
| lv_server 本地 | Qwen2.5-VL-7B-Q4KM (RTX 4090) | **0.401s** |
| Spark 外部 | Qwen3.6-35B-A3B-NVFP4 (GB10) | **2.484s** |
| **本地快** | | **6.2x** |

各图耗时见 `results.json`。
