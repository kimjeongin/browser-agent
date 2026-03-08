"""OpenTelemetry 설정 팩토리 - 모든 서비스에서 공유."""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from opentelemetry import metrics as metrics_api
from opentelemetry import trace as trace_api
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.sdk import trace as trace_sdk
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from openinference.instrumentation.langchain import LangChainInstrumentor

from shared.logging_config import configure_logging

if TYPE_CHECKING:
    from fastapi import FastAPI

_logger = logging.getLogger(__name__)
_DEFAULT_ENDPOINT = "http://otel-collector:4318"

# 모듈 수준 MeterProvider - shutdown_telemetry에서 정리
_meter_provider: MeterProvider | None = None


def _resolve_base_endpoint() -> str:
    """OTEL_EXPORTER_OTLP_ENDPOINT를 베이스 URL로 정규화.

    레거시 Phoenix 직접 연결 포맷(http://host:port/v1/traces)도 허용.
    """
    raw = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", _DEFAULT_ENDPOINT).rstrip("/")
    # 레거시 포맷 호환: /v1/traces 접미사 제거
    if raw.endswith("/v1/traces"):
        _logger.warning(
            "OTEL_EXPORTER_OTLP_ENDPOINT has legacy '/v1/traces' suffix. "
            "Stripping to base URL — logs and metrics will also route to this host. "
            "Update to OTel Collector URL: http://otel-collector:4318"
        )
        raw = raw[: -len("/v1/traces")]
    return raw


def setup_telemetry(
    service_name: str,
    app: "FastAPI | None" = None,
) -> tuple[trace_sdk.TracerProvider, LoggerProvider]:
    """FastAPI 서비스의 OTel 트레이싱 + 로깅 + 메트릭 초기화.

    lifespan 시작 시 호출, yield 후 shutdown_telemetry() 호출.
    """
    global _meter_provider

    base = _resolve_base_endpoint()
    traces_endpoint = f"{base}/v1/traces"
    logs_endpoint = f"{base}/v1/logs"
    metrics_endpoint = f"{base}/v1/metrics"

    resource = Resource({SERVICE_NAME: service_name})

    # Traces → OTel Collector → Phoenix
    tracer_provider = trace_sdk.TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=traces_endpoint))
    )
    trace_api.set_tracer_provider(tracer_provider)

    # Logs → OTel Collector → Loki
    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=logs_endpoint))
    )
    set_logger_provider(logger_provider)
    LoggingInstrumentor().instrument()

    # Metrics → OTel Collector → Prometheus (15s 간격)
    _meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[
            PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=metrics_endpoint),
                export_interval_millis=15_000,
            )
        ],
    )
    metrics_api.set_meter_provider(_meter_provider)

    # System metrics: CPU, 메모리, GC (opentelemetry-instrumentation-system-metrics)
    try:
        from opentelemetry.instrumentation.system_metrics import SystemMetricsInstrumentor
        SystemMetricsInstrumentor().instrument()
    except ImportError:
        pass
    except Exception:
        _logger.warning("SystemMetricsInstrumentor failed to instrument", exc_info=True)

    # LangChain + LangGraph 자동 계측 (단일 호출로 양쪽 커버)
    LangChainInstrumentor().instrument(tracer_provider=tracer_provider)

    # httpx: W3C traceparent 자동 주입 → 서비스 간 분산 트레이스 연결
    HTTPXClientInstrumentor().instrument()

    # FastAPI 엔드포인트 Span + 메트릭 (request count, latency)
    if app is not None:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(app, tracer_provider=tracer_provider)

    # Must be called after LoggingInstrumentor so OTel injects trace context first
    configure_logging(service_name)

    _logger.info("OTel initialized: service=%s base=%s", service_name, base)
    return tracer_provider, logger_provider


def shutdown_telemetry(
    tracer_provider: trace_sdk.TracerProvider,
    logger_provider: LoggerProvider,
) -> None:
    """pending span/log/metric flush 후 provider 종료. lifespan 정리 시 호출."""
    global _meter_provider
    try:
        LangChainInstrumentor().uninstrument()
        HTTPXClientInstrumentor().uninstrument()
        LoggingInstrumentor().uninstrument()
    except Exception:
        _logger.debug("uninstrument failed", exc_info=True)
    tracer_provider.shutdown()
    logger_provider.shutdown()
    if _meter_provider is not None:
        _meter_provider.shutdown()
        _meter_provider = None
