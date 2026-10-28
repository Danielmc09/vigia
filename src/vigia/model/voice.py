"""Muse Voice Transcribe over HTTP (adapter): the real :data:`~vigia.domain.reading.Transcriber`.

Endpoint and shapes verified in T-02 (specs/03 "Verificado" row 5,
``tests/fixtures/meta/asr_transcribe.json``). Plain ``httpx`` like :mod:`vigia.model.spark`.
Any failure is a :class:`~vigia.domain.rejection.Rejection`, never an exception.
"""

import json
import logging
from uuid import uuid4

import httpx

from vigia.config import Settings
from vigia.domain.reading import Transcript
from vigia.domain.rejection import Rejection
from vigia.model.spark import BASE

MODEL = "muse-voice-transcribe-1.0"
PRICE_HOUR = 0.18  # USD per hour of audio, Standard tier (specs/03 "Verificado" row 7)
TIMEOUT_S = 15.0  # verified calls took 1.5-2 s for 4 s of audio; 2 min must still fit

log = logging.getLogger(__name__)


class VoiceClient:
    """Sends a canonical WAV to Voice's batch endpoint and returns the transcript."""

    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        """Keep the key and the HTTP client.

        Args:
            api_key: Model API key, from settings only.
            client: HTTP client; tests pass one with ``httpx.MockTransport``.
        """
        self._key = api_key
        self._client = client or httpx.Client()

    def __call__(self, wav: bytes, language: str) -> Transcript | Rejection:
        """Transcribe one WAV (already checked by :func:`~vigia.domain.attachment.check_wav`).

        Args:
            wav: Canonical WAV bytes.
            language: Language bias, ``es`` or ``en``.

        Returns:
            The transcript billed by ``audioDurationMs``, or a retryable ``MODEL_UNAVAILABLE``.
        """
        request = {"model": MODEL, "audioEncoding": "WAV", "languageBias": [language]}
        try:
            response = self._client.post(
                f"{BASE}/asr/transcribe",
                params={"sessionId": str(uuid4())},
                headers={"Authorization": f"Bearer {self._key}"},
                files={
                    "request": (None, json.dumps(request), "application/json"),
                    "audio": ("audio.wav", wav, "audio/wav"),
                },
                timeout=TIMEOUT_S,
            )
            response.raise_for_status()
            data = response.json()
            audio_ms = int(data["audioDurationMs"])
            return Transcript(
                text=str(data["transcript"]),
                audio_ms=audio_ms,
                usd=audio_ms / 3_600_000 * PRICE_HOUR,
            )
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            log.warning("voice unavailable: %s", type(exc).__name__)
            return Rejection(
                error_code="MODEL_UNAVAILABLE", message=type(exc).__name__, retryable=True
            )


def make_voice(config: Settings) -> VoiceClient | None:
    """The Voice client for this process, or ``None`` without ``MODEL_API_KEY``.

    Args:
        config: Process settings.

    Returns:
        A client, or ``None`` (audio cases then cannot be transcribed).
    """
    if config.model_api_key is None:
        return None
    return VoiceClient(config.model_api_key.get_secret_value())
