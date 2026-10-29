"""Which judge sits behind the gate in this process (T-08b, D-085).

Lives in ``services`` because it chooses between two adapters (Spark and Jev), and an
adapter module never imports another (specs/09 §19).
"""

from vigia.config import Settings
from vigia.domain.gate import Judge, mock_judge
from vigia.model.jev import make_dev_judge
from vigia.model.spark_judge import SparkJudge
from vigia.services.backend import make_chat


def make_judge(config: Settings) -> Judge:
    """The judge wired into the app, the webhook and a complete eval run.

    Outside ``dev`` (``eval``/``prod``) this is always Spark or the mock, never Jev: the
    config already refuses ``JEV_BACKEND=real`` there (D-085). In ``dev`` it defers to
    Jev's own factory: a developer's local choice, never a production path.

    Args:
        config: Process settings.

    Returns:
        A :data:`~vigia.domain.gate.Judge`.
    """
    if config.mode == "dev":
        return make_dev_judge(config)
    spark = make_chat(config)
    return SparkJudge(spark) if spark else mock_judge
