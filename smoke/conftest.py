"""pytest 全局 fixtures：资源占用监控。

每个测试开始前记录基线，结束后比对：
  - CPU 使用率（采样 0.2s）超过 LIMIT_CPU_PCT 则 WARN
  - 进程 RSS 增量超过 LIMIT_RSS_DELTA_MB 则 FAIL

阈值可通过环境变量覆盖：
  SMOKE_CPU_LIMIT=80   （%，默认 80）
  SMOKE_RSS_DELTA=150  （MB，默认 150）
"""
from __future__ import annotations

import os
import time

import psutil
import pytest

_CPU_LIMIT = float(os.getenv("SMOKE_CPU_LIMIT", "80"))
_RSS_DELTA_LIMIT_MB = float(os.getenv("SMOKE_RSS_DELTA", "150"))


@pytest.fixture(autouse=True)
def resource_guard(request):
    proc = psutil.Process()

    # 预热一次，避免首次调用 cpu_percent 总返回 0.0
    proc.cpu_percent(interval=None)
    rss_before = proc.memory_info().rss

    yield

    cpu = proc.cpu_percent(interval=0.2)
    rss_after = proc.memory_info().rss
    rss_delta_mb = (rss_after - rss_before) / 1024 / 1024

    if cpu > _CPU_LIMIT:
        pytest.xfail(
            f"[perf] CPU {cpu:.1f}% > limit {_CPU_LIMIT}% "
            f"in {request.node.name}"
        )

    assert rss_delta_mb < _RSS_DELTA_LIMIT_MB, (
        f"[perf] RSS 增量 {rss_delta_mb:.1f} MB 超过上限 {_RSS_DELTA_LIMIT_MB} MB "
        f"in {request.node.name}"
    )
