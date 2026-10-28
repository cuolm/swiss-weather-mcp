"""Set up logging for the server and client entry points."""
import logging
import logging.config
from typing import Literal, get_args

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
LOG_LEVELS = list(get_args(LogLevel))


def setup_logging(log_level: str = "INFO") -> None:
    """
    Send the log records of the whole program to stderr through one handler on the root logger.

    Libraries that log every request, such as httpx2, keep the level WARNING, so a lower log_level
    such as DEBUG does not flood the output with them.
    """
    logger_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            }
        },
        "handlers": {
            "stderr_handler": {
                "level": log_level,
                "class": "logging.StreamHandler",
                "formatter": "standard",
                "stream": "ext://sys.stderr",  # stdout carries the MCP protocol on the stdio transport
            }
        },
        "loggers": {
            "": {  # "" corresponds to root logger
                "handlers": ["stderr_handler"],
                "level": log_level,
                "propagate": False,
            },
            "httpx2": {  # silence the HTTP request logs of httpx2, which the OpenAI SDK uses
                "level": "WARNING",
            },
            "urllib3": {  # connection retries
                "level": "WARNING",
            },
            "mcp.server": {  # a second line for each tool failure, and HTTP session messages
                "level": "WARNING",
            },
        },
    }
    logging.config.dictConfig(logger_config)
