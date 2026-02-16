import logging
import sys


def setup_logger(
    name: str = "scraper-novela",
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
