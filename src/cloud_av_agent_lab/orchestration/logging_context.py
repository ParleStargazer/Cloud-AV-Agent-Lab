from __future__ import annotations

import logging
import sys
from contextlib import contextmanager
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Iterator

current_instance_id: ContextVar[str] = ContextVar("instance_id", default="-")
current_run_id: ContextVar[str] = ContextVar("run_id", default="-")


class RunContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.instance_id = current_instance_id.get()
        record.run_id = current_run_id.get()
        return True


@contextmanager
def run_log_context(instance_id: str, run_id: str) -> Iterator[None]:
    instance_token: Token[str] = current_instance_id.set(instance_id)
    run_token: Token[str] = current_run_id.set(run_id)
    try:
        yield
    finally:
        current_run_id.reset(run_token)
        current_instance_id.reset(instance_token)


@contextmanager
def configure_run_logging(run_dir: Path, level: int = logging.INFO) -> Iterator[None]:
    logger = logging.getLogger("cloud_av_agent_lab")
    previous_level = logger.level
    previous_propagate = logger.propagate
    formatter = logging.Formatter("[%(instance_id)s][%(run_id)s] %(message)s")
    context_filter = RunContextFilter()

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(level)
    stdout_handler.setFormatter(formatter)
    stdout_handler.addFilter(context_filter)

    run_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(run_dir / "run.log", encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(context_filter)

    logger.setLevel(level)
    logger.propagate = False
    logger.addHandler(stdout_handler)
    logger.addHandler(file_handler)
    try:
        yield
    finally:
        logger.removeHandler(stdout_handler)
        logger.removeHandler(file_handler)
        stdout_handler.close()
        file_handler.close()
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate
