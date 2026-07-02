import logging


def setup_logging() -> None:
    """Configure project logging with a compact level prefix."""
    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s] %(message)s",
    )
