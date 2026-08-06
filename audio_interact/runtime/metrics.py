"""账本二（可观测性）：Prometheus 指标 + 可选 OTel span 导出。

设计 §4 的落地取舍：Tencent 生产机只有 3.6G 内存，跑不起 Tempo/Collector 全栈，
所以 SLO 指标用 prometheus_client 直接暴露（/metrics，Prometheus+Grafana 轻量可跑）；
OTel trace 埋点走 try-import + 环境变量门控（OTEL_EXPORTER_OTLP_ENDPOINT 未设即为
no-op），代码在位、后端随时可接，不给热路径增加任何强依赖。
"""
from __future__ import annotations

from typing import Any

try:  # metrics are best-effort: the service must run fine without the dep
    from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
    _ENABLED = True
except ImportError:  # pragma: no cover
    _ENABLED = False
    CONTENT_TYPE_LATEST = "text/plain"

_LATENCY_BUCKETS = (0.05, 0.1, 0.2, 0.3, 0.4, 0.6, 0.8, 1.2, 1.8, 2.5, 4.0, 8.0)

if _ENABLED:
    ASR_LATENCY = Histogram("audio_asr_latency_seconds", "speech_end → asr.final", buckets=_LATENCY_BUCKETS)
    ROUTE_LATENCY = Histogram("audio_route_latency_seconds", "asr.final → envelope done", buckets=_LATENCY_BUCKETS)
    TTS_FIRST_CHUNK = Histogram("audio_tts_first_chunk_seconds", "tts request → first audio chunk", buckets=_LATENCY_BUCKETS)
    E2E_RESPONSE = Histogram("audio_e2e_response_seconds", "speech_end → first tts chunk on the wire", buckets=_LATENCY_BUCKETS)
    COMMIT_DELAY = Histogram("audio_bargein_commit_delay_seconds", "speech_start(during_tts) → bargein.commit", buckets=_LATENCY_BUCKETS)
    CANCEL_DELAY = Histogram("audio_bargein_cancel_delay_seconds", "user speech → speaker silent (clock-synced)", buckets=_LATENCY_BUCKETS)
    POST_CANCEL_TAIL = Histogram("audio_bargein_tail_seconds", "cancel received → playback stop on edge", buckets=(0.0, 0.05, 0.1, 0.15, 0.25, 0.5, 1.0))
    BARGEIN_TOTAL = Counter("audio_bargein_total", "committed barge-ins", ["stop_source"])
    TURNS_TOTAL = Counter("audio_turns_total", "completed turns", ["status", "wake_status"])
    SESSIONS_TOTAL = Counter("audio_sessions_total", "flushed sessions", ["retention_tier"])


def _observe(hist: Any, ms: Any) -> None:
    if ms is None:
        return
    try:
        hist.observe(max(0.0, float(ms) / 1000.0))
    except (TypeError, ValueError):
        pass


def record_turn(result: dict[str, Any]) -> None:
    if not _ENABLED:
        return
    _observe(ASR_LATENCY, result.get("asr_elapsed_ms"))
    _observe(ROUTE_LATENCY, result.get("route_elapsed_ms"))
    TURNS_TOTAL.labels(
        status=str(result.get("status") or "unknown")[:40],
        wake_status=str(result.get("wake_status") or "unknown")[:40],
    ).inc()


def record_tts_first_chunk(first_ms: Any, e2e_ms: Any = None) -> None:
    if not _ENABLED:
        return
    _observe(TTS_FIRST_CHUNK, first_ms)
    _observe(E2E_RESPONSE, e2e_ms)


def record_bargein_commit(commit_delay_ms: Any) -> None:
    if not _ENABLED:
        return
    _observe(COMMIT_DELAY, commit_delay_ms)


def record_bargein_case(case: dict[str, Any]) -> None:
    """Called when edge telemetry completes a barge-in case (cross-clock ready)."""
    if not _ENABLED:
        return
    _observe(CANCEL_DELAY, case.get("cancel_delay_ms"))
    _observe(POST_CANCEL_TAIL, case.get("post_cancel_tail_ms"))
    BARGEIN_TOTAL.labels(stop_source=str(case.get("stop_source") or "unknown")).inc()


def record_session_flush(retention_tier: str) -> None:
    if not _ENABLED:
        return
    SESSIONS_TOTAL.labels(retention_tier=retention_tier or "unknown").inc()


def metrics_payload() -> tuple[bytes, str]:
    if not _ENABLED:
        return b"# prometheus_client not installed\n", CONTENT_TYPE_LATEST
    return generate_latest(), CONTENT_TYPE_LATEST


# --- optional OTel tracing (no-op unless endpoint configured) ----------------

_tracer = None


def init_tracing(service_name: str = "audio-interact") -> None:
    """Enable OTLP span export only when OTEL_EXPORTER_OTLP_ENDPOINT is set."""
    global _tracer
    import os
    if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return
    try:  # pragma: no cover - requires optional deps + endpoint
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer(service_name)
    except Exception:  # noqa: BLE001
        _tracer = None


class turn_span:
    """Context manager for an interact.turn span; no-op without tracing."""

    def __init__(self, session_id: str, turn_id: str) -> None:
        self.attrs = {"session_id": session_id, "turn_id": turn_id}
        self._cm = None

    def __enter__(self):
        if _tracer is not None:  # pragma: no cover
            self._cm = _tracer.start_as_current_span("interact.turn", attributes=self.attrs)
            return self._cm.__enter__()
        return None

    def __exit__(self, *exc):  # pragma: no cover
        if self._cm is not None:
            return self._cm.__exit__(*exc)
        return False
