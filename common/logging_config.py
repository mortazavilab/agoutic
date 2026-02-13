"""
Unified structured logging configuration for AGOUTIC.

Provides a single `setup_logging()` call that every server uses to configure
structlog with JSON-lines output to both a per-server file and a shared
unified log file (`agoutic.jsonl`).

Usage in any server:
    from common.logging_config import setup_logging, get_logger
    setup_logging("server1")
    logger = get_logger(__name__)
    logger.info("Server started", port=8000)
"""

import logging
import os
import sys
from pathlib import Path

import structlog


# ---------------------------------------------------------------------------
# Path resolution (matches server1/config.py and server3/config.py patterns)
# ---------------------------------------------------------------------------
AGOUTIC_CODE = Path(os.getenv("AGOUTIC_CODE", Path(__file__).resolve().parent.parent))
AGOUTIC_DATA = Path(os.getenv("AGOUTIC_DATA", AGOUTIC_CODE / "data"))
LOGS_DIR = AGOUTIC_DATA / "logs"

# Set to "dev" for coloured human-readable console output instead of JSON
LOG_FORMAT = os.getenv("AGOUTIC_LOG_FORMAT", "json")


def _ensure_logs_dir() -> None:
    """Create the logs directory if it doesn't exist."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


def setup_logging(
    server_name: str,
    log_level: str = "INFO",
) -> None:
    """
    Configure structured logging for an AGOUTIC server process.

    Creates two JSON-lines file handlers (per-server + unified) and a
    console handler.  All stdlib ``logging`` and ``uvicorn.*`` loggers are
    routed through the same pipeline so every log entry is structured.

    Args:
        server_name: Identifier for this process (e.g. "server1",
                     "server3-rest", "encode-mcp").  Bound into every
                     log record as ``server``.
        log_level:   Root log level (default ``"INFO"``).
    """
    _ensure_logs_dir()

    level = getattr(logging, log_level.upper(), logging.INFO)

    # --- Shared structlog processors (pre-format) ---
    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    # --- Formatter for file handlers (always JSON) ---
    json_formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
        foreign_pre_chain=shared_processors,
    )

    # --- Formatter for console handler ---
    if LOG_FORMAT == "dev":
        console_formatter = structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.dev.ConsoleRenderer(colors=True),
            ],
            foreign_pre_chain=shared_processors,
        )
    else:
        console_formatter = json_formatter

    # --- Handlers ---
    # Per-server JSON-lines file
    per_server_handler = logging.FileHandler(
        LOGS_DIR / f"{server_name}.jsonl", encoding="utf-8",
    )
    per_server_handler.setFormatter(json_formatter)

    # Unified JSON-lines file (all servers write here)
    unified_handler = logging.FileHandler(
        LOGS_DIR / "agoutic.jsonl", encoding="utf-8",
    )
    unified_handler.setFormatter(json_formatter)

    # Console (stderr)
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(console_formatter)

    # --- Root logger ---
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(per_server_handler)
    root.addHandler(unified_handler)
    root.addHandler(console_handler)
    root.setLevel(level)

    # --- Tame noisy third-party loggers ---
    for noisy in ("httpx", "httpcore", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # --- Route uvicorn's loggers through our handlers ---
    for uvi_name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        uvi_logger = logging.getLogger(uvi_name)
        uvi_logger.handlers.clear()
        uvi_logger.propagate = True  # inherit root handlers

    # --- Configure structlog itself ---
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Bind the server name so every entry is tagged
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(server=server_name)

    # Emit a startup marker
    _logger = structlog.get_logger("agoutic.logging")
    _logger.info(
        "Logging initialised",
        server=server_name,
        log_level=log_level,
        per_server_log=str(LOGS_DIR / f"{server_name}.jsonl"),
        unified_log=str(LOGS_DIR / "agoutic.jsonl"),
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger.

    Call this *after* ``setup_logging()`` has been invoked by the server
    entrypoint.  Module-level usage is fine because structlog lazily
    resolves the configuration on first use.

    Args:
        name: Logger name – typically ``__name__``.
    """
    return structlog.get_logger(name)
