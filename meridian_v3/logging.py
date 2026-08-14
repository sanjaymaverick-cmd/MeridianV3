from __future__ import annotations

from loguru import logger

from meridian_v3.config import get_settings


def setup_logging() -> None:
    settings = get_settings()
    settings.ensure_dirs()
    log_file = settings.log_dir / "meridian_v3.log"
    logger.remove()
    logger.add(
        log_file,
        rotation="5 MB",
        retention="14 days",
        enqueue=True,
        backtrace=False,
        diagnose=False,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<7} | {message}",
    )
    logger.add(lambda msg: None)  # keep import side-effect free for tests
