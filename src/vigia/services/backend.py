"""Who answers the chat turns of the analyst and the judge (T-09b, D-086).

Wiring, so it lives in ``services``: ``MODEL_BACKEND`` picks Muse Spark on the Meta Model API
or Muse Spark Open on Baseten. Readers (PDF, image), SAM, Voice and the web search stay on
Meta: Baseten serves chat completions only.
"""

from vigia.config import Settings
from vigia.model.spark import SPARK_OPEN, SparkClient, make_spark


def has_chat(config: Settings) -> bool:
    """Whether :func:`make_chat` would return a client, without building one.

    Args:
        config: Process settings.

    Returns:
        ``True`` when the configured backend has its key.
    """
    key = config.model_api_key if config.model_backend == "spark" else config.baseten_api_key
    return key is not None


def make_chat(config: Settings) -> SparkClient | None:
    """The chat client for the analyst and the judge, or ``None`` without its key.

    Args:
        config: Process settings.

    Returns:
        A :class:`SparkClient` on Meta (``spark``) or Baseten (``spark_open``).
    """
    if config.model_backend == "spark":
        return make_spark(config)
    if config.baseten_api_key is None:
        return None
    return SparkClient(config.baseten_api_key.get_secret_value(), endpoint=SPARK_OPEN)
