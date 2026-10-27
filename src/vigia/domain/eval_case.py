"""Vocabulary of an eval case (specs/06) and how it maps to a channel message and Label (domain).

The eval file keeps the Spanish values of specs/06; these are the only definitions.
"""

from typing import Literal, get_args

from vigia.domain.case import ChannelKind
from vigia.domain.verdict import Label

EvalKind = Literal["texto", "imagen", "audio", "pdf", "correo", "conversacion"]
Expected = Literal["estafa", "legitimo", "dudoso"]
EvalOrigin = Literal["sintetico", "publico_anonimizado", "propio_anonimizado", "incidente", "ciego"]
Category = Literal[
    "banco",
    "billetera",
    "estado",
    "transportadora",
    "telco",
    "premio",
    "familiar",
    "bec_proveedor",
    "bec_jefe",
    "compraventa",
    "comercio",
    "legitimo_banco",
    "legitimo_transportadora",
    "legitimo_proveedor",
    "legitimo_otro",
]

# Positional: both Literals list scam, legit, doubtful in the same order (test_medir pins it).
EXPECTED_LABEL: dict[Expected, Label] = dict(zip(get_args(Expected), get_args(Label), strict=True))
"""Label the system must return for each expected value."""

CHANNEL_KIND: dict[EvalKind, ChannelKind] = {
    "texto": "text",
    "imagen": "image",
    "audio": "audio",
    "pdf": "document",
    "correo": "email",
    "conversacion": "text",  # an exported chat is text (specs/15)
}
"""Channel kind a forwarded message of each eval type arrives as; ingest normalizes it."""
