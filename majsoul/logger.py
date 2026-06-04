from __future__ import annotations

import loguru
from loguru import logger as main_logger
from datetime import datetime
from pathlib import Path

# Log to: "./logs/majsoul_<timestamp>.log"
log_path: Path = Path().cwd() / "logs" / f"majsoul_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logger: loguru.Logger = main_logger.bind(module="majsoul")
main_logger.add(log_path, level="DEBUG", filter=lambda record: record["extra"].get("module") == "majsoul")
