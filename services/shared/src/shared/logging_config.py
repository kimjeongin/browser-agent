"""Unified JSON log format shared by all services.
configure_logging(service_name) is called inside setup_telemetry() —
no individual service or logger call needs to change.
"""
from __future__ import annotations

import logging
import os
import socket

from pythonjsonlogger import jsonlogger


class _ServiceJsonFormatter(jsonlogger.JsonFormatter):
    """Single canonical log format for all microservices."""

    def __init__(self, service_name: str) -> None:
        self._service_name = service_name
        self._hostname = socket.gethostname()
        super().__init__(
            fmt="%(asctime)s %(levelname)s %(name)s %(filename)s %(lineno)d %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
            rename_fields={
                "levelname": "level",
                "name": "logger",
                "asctime": "timestamp",
            },
        )

    def add_fields(
        self,
        log_record: dict,
        record: logging.LogRecord,
        message_dict: dict,
    ) -> None:
        super().add_fields(log_record, record, message_dict)
        log_record["service"] = self._service_name
        log_record["hostname"] = self._hostname
        # Combine filename + lineno into a single location field
        filename = log_record.pop("filename", None)
        lineno = log_record.pop("lineno", None)
        if filename is not None and lineno is not None:
            log_record["location"] = f"{filename}:{lineno}"
        # OTel trace context injected by LoggingInstrumentor
        trace_id = getattr(record, "otelTraceID", "") or ""
        span_id = getattr(record, "otelSpanID", "") or ""
        if trace_id and trace_id != "0" * 32:
            log_record["trace_id"] = trace_id
            log_record["span_id"] = span_id


def configure_logging(service_name: str) -> None:
    """Install unified JSON log format on the root logger.

    Must be called AFTER LoggingInstrumentor().instrument() so OTel
    injects otelTraceID/otelSpanID into LogRecords before our formatter reads them.
    """
    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    formatter = _ServiceJsonFormatter(service_name)
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # uvicorn installs its own handlers — clear them so root handler takes over
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True
