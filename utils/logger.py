import logging
import sys

LOGGER_NAME = "scraper-novela"


def setup_logger(
    name: str = LOGGER_NAME,
    *,
    verbose: bool = False,
    debug: bool = False,
) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    level = logging.DEBUG if debug else (logging.INFO if verbose else logging.WARNING)
    logger.setLevel(level)

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)

    fmt = logging.Formatter(
        "[%(levelname).1s] %(message)s" if not debug
        else "[%(levelname)s %(name)s:%(lineno)d] %(message)s"
    )
    handler.setFormatter(fmt)
    logger.addHandler(handler)
    return logger
