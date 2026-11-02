"""Which judge sits behind the gate in this process (T-08b, D-085).

Lives in ``services`` because it needs the chat client that ``services.backend`` builds.
"""

from vigia.config import Settings
from vigia.domain.gate import Judge, mock_judge
from vigia.model.spark_judge import SparkJudge
from vigia.services.backend import make_chat


def make_judge(config: Settings) -> Judge:
    """The judge wired into the app, the webhook and a complete eval run.

    ``dev`` always gets the mock, so a local run spends nothing on judging; elsewhere
    (``eval``/``prod``) it is Spark, or the mock without a model key.

    Args:
        config: Process settings.

    Returns:
        A :data:`~vigia.domain.gate.Judge`.
    """
    if config.mode == "dev":
        return mock_judge
    spark = make_chat(config)
    return SparkJudge(spark) if spark else mock_judge
