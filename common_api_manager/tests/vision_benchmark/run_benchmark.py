"""Run vision benchmark against lv_server VL and Spark vision APIs.

Usage:
    python run_benchmark.py [--lv URL] [--spark URL] [--out results.json]

Defaults:
    --lv    https://www.wangyutang.cn/common/api/vision/lv/analyze-json
    --spark https://www.wangyutang.cn/common/api/vision/spark/analyze-json
"""

from __future__ import annotations

import argparse
import base64
import json
import time
from pathlib import Path
from typing import Any

import requests

IMAGES_DIR = Path(__file__).parent / "images"

QUESTIONS: dict[str, str] = {
    "01_solid_red": "这张图片是什么颜色？",
    "02_solid_blue": "这张图片是什么颜色？",
    "03_solid_green": "这张图片是什么颜色？",
    "04_solid_yellow": "这张图片是什么颜色？",
    "05_solid_white": "这张图片是什么颜色？",
    "06_solid_black": "这张图片是什么颜色？",
    "07_gradient_red_blue": "描述这张图片的颜色变化。",
    "08_gradient_green_yellow": "描述这张图片的颜色变化。",
    "09_basic_shapes": "图中有哪些几何形状？各是什么颜色？",
    "10_checkerboard": "描述这张图片的图案。",
    "11_stripes_vertical": "描述这张图片的图案和颜色。",
    "12_stripes_horizontal": "描述这张图片的图案和颜色。",
    "13_text_mixed": "读出图中所有可见文字。",
    "14_bar_chart": "这是什么类型的图表？描述图表内容。",
    "15_low_contrast": "图片中有什么内容？",
    "16_dark_night_scene": "描述这张图片的场景。",
    "17_radial_gradient": "描述这张图片的颜色分布。",
    "18_mixed_content": "读出图中文字，并描述图形内容。",
}


def call_api(url: str, image_path: Path, question: str, timeout: int = 30) -> dict[str, Any]:
    data = image_path.read_bytes()
    b64 = base64.b64encode(data).decode()
    payload = {"image_base64": b64, "question": question}
    t0 = time.perf_counter()
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        elapsed = time.perf_counter() - t0
        resp.raise_for_status()
        body = resp.json()
        answer = body.get("text") or body.get("result") or ""
        model = body.get("model", "")
        return {"ok": True, "answer": answer, "model": model, "elapsed_s": round(elapsed, 3)}
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        return {"ok": False, "error": str(exc), "elapsed_s": round(elapsed, 3)}


def run(lv_url: str, spark_url: str, out_path: Path) -> None:
    images = sorted(IMAGES_DIR.glob("*.jpg"))
    if not images:
        print("No images found. Run generate_images.py first.")
        return

    results: list[dict] = []
    print(f"{'Image':<30} {'LV(s)':>7} {'Spark(s)':>9}  LV answer (truncated)")
    print("-" * 90)

    for img_path in images:
        stem = img_path.stem
        question = QUESTIONS.get(stem, "描述这张图片。")
        lv = call_api(lv_url, img_path, question)
        spark = call_api(spark_url, img_path, question)

        lv_ans = (lv.get("answer") or lv.get("error", ""))[:60]
        spark_ans = (spark.get("answer") or spark.get("error", ""))[:60]
        lv_t = lv["elapsed_s"]
        spark_t = spark["elapsed_s"]
        speedup = round(spark_t / lv_t, 1) if lv_t > 0 else 0

        print(f"{stem:<30} {lv_t:>7.3f} {spark_t:>9.3f}  {lv_ans}")

        results.append({
            "image": stem,
            "question": question,
            "lv": lv,
            "spark": spark,
            "speedup_lv_vs_spark": speedup,
        })

    lv_times = [r["lv"]["elapsed_s"] for r in results if r["lv"]["ok"]]
    spark_times = [r["spark"]["elapsed_s"] for r in results if r["spark"]["ok"]]
    summary = {
        "total_images": len(results),
        "lv_avg_s": round(sum(lv_times) / len(lv_times), 3) if lv_times else None,
        "spark_avg_s": round(sum(spark_times) / len(spark_times), 3) if spark_times else None,
        "speedup_avg": round(
            (sum(spark_times) / len(spark_times)) / (sum(lv_times) / len(lv_times)), 1
        ) if lv_times and spark_times else None,
        "lv_url": lv_url,
        "spark_url": spark_url,
        "run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    print("-" * 90)
    print(f"LV avg: {summary['lv_avg_s']}s   Spark avg: {summary['spark_avg_s']}s   "
          f"Speedup: {summary['speedup_avg']}x")

    out = {"summary": summary, "results": results}
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--lv", default="https://www.wangyutang.cn/common/api/vision/lv/analyze-json")
    parser.add_argument("--spark", default="https://www.wangyutang.cn/common/api/vision/spark/analyze-json")
    parser.add_argument("--out", default=str(Path(__file__).parent / "results.json"))
    args = parser.parse_args()
    run(args.lv, args.spark, Path(args.out))
