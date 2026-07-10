"""ci_tests 共享 fixtures：远端服务地址 + 资源监控。"""
from __future__ import annotations

import os
import pytest
import psutil

# 远端服务基础地址，CI 中可通过环境变量覆盖
BASE_URL = os.getenv("CI_BASE_URL", "https://www.wangyutang.cn")
WS_URL   = BASE_URL.replace("https://", "wss://").replace("http://", "ws://")

_CPU_LIMIT       = float(os.getenv("SMOKE_CPU_LIMIT", "80"))
_RSS_DELTA_LIMIT = float(os.getenv("SMOKE_RSS_DELTA", "150"))


@pytest.fixture(autouse=True)
def resource_guard(request):
    proc = psutil.Process()
    proc.cpu_percent(interval=None)
    rss_before = proc.memory_info().rss
    yield
    cpu = proc.cpu_percent(interval=0.2)
    rss_after = proc.memory_info().rss
    rss_delta_mb = (rss_after - rss_before) / 1024 / 1024
    if cpu > _CPU_LIMIT:
        pytest.xfail(f"[perf] CPU {cpu:.1f}% > {_CPU_LIMIT}% in {request.node.name}")
    assert rss_delta_mb < _RSS_DELTA_LIMIT, (
        f"[perf] RSS +{rss_delta_mb:.1f}MB 超过 {_RSS_DELTA_LIMIT}MB in {request.node.name}"
    )
