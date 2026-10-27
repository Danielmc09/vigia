"""Envelope encryption of PII: per-business DEK wrapped by the KEK, keyed lookup hashes.

The only module that touches AES-GCM or HMAC (specs/16). Values leave it as ``Encrypted``
or ``Hashed`` bytes. The store takes plaintext plus a ``Vault`` and writes PII columns only
with what ``Vault.seal``/``Vault.hash`` return; the types mark that, they do not enforce it
(``store`` is outside ``mypy --strict``). The enforcement is the raw-SELECT test.
"""

import base64
import binascii
import hashlib
import hmac
import os
from typing import NewType, Self
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from vigia.config import Settings

Encrypted = NewType("Encrypted", bytes)
Hashed = NewType("Hashed", bytes)

_KEY_BYTES = 32
_NONCE_BYTES = 12


def _seal(key: bytes, plaintext: bytes, aad: bytes) -> bytes:
    """AES-256-GCM encrypt; returns nonce || ciphertext || tag."""
    nonce = os.urandom(_NONCE_BYTES)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def _open(key: bytes, blob: bytes, aad: bytes) -> bytes:
    """Inverse of ``_seal``; raises ``InvalidTag`` on wrong key or AAD."""
    return AESGCM(key).decrypt(blob[:_NONCE_BYTES], blob[_NONCE_BYTES:], aad)


class Vault:
    """One business's DEK, opened in memory for the current transaction.

    The AAD binds every ciphertext to its business and column, so a value copied to
    another business or column fails to decrypt.
    """

    def __init__(self, business_id: UUID, dek: bytes, keyring: "Keyring") -> None:
        """Hold an unwrapped DEK.

        Args:
            business_id: Business the DEK belongs to.
            dek: Raw 32-byte data key.
            keyring: Keyring that produced it; provides lookup hashes.
        """
        self.business_id = business_id
        self._dek = dek
        self._keyring = keyring

    def _aad(self, column: str) -> bytes:
        """Bind a ciphertext to its business and column."""
        return f"{self.business_id}|{column}".encode()

    def seal(self, column: str, value: str) -> Encrypted:
        """Encrypt a value for one column of this business.

        Args:
            column: ``table.column`` it will be stored in, e.g. ``members.phone``.
            value: Plaintext.

        Returns:
            ``nonce || ciphertext || tag``.
        """
        return Encrypted(_seal(self._dek, value.encode(), self._aad(column)))

    def open(self, column: str, blob: bytes) -> str:
        """Decrypt a value sealed for the same business and column.

        Args:
            column: ``table.column`` it was sealed for.
            blob: Stored ciphertext.

        Returns:
            The plaintext.

        Raises:
            cryptography.exceptions.InvalidTag: Wrong business, column or key.
        """
        return _open(self._dek, blob, self._aad(column)).decode()

    def hash(self, value: str) -> Hashed:
        """Lookup hash of a value (same key for every business); see ``Keyring.hash``."""
        return self._keyring.hash(value)


class Keyring:
    """Process-wide keys: the KEK (wraps DEKs) and the HMAC key (lookup hashes)."""

    def __init__(self, kek: bytes, hmac_key: bytes) -> None:
        """Hold the process keys.

        Args:
            kek: 32-byte key-encryption key.
            hmac_key: 32-byte key for lookup hashes, separate from the KEK.
        """
        self._kek = kek
        self._hmac_key = hmac_key

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        """Build the keyring from ``VIGIA_KEK`` and ``PII_HMAC_KEY`` (base64, 32 bytes).

        Args:
            settings: Process settings.

        Returns:
            The keyring.

        Raises:
            ValueError: If a key is missing, not base64 or not 32 bytes.
        """
        return cls(
            _decode_key("VIGIA_KEK", settings.kek and settings.kek.get_secret_value()),
            _decode_key(
                "PII_HMAC_KEY", settings.pii_hmac_key and settings.pii_hmac_key.get_secret_value()
            ),
        )

    def hash(self, value: str) -> Hashed:
        """HMAC-SHA256 of a normalized value, to find it without storing it.

        Args:
            value: Normalized phone, email, account or code.

        Returns:
            32-byte digest.
        """
        return Hashed(hmac.new(self._hmac_key, value.encode(), hashlib.sha256).digest())

    def new_business(self, business_id: UUID) -> tuple[Vault, bytes]:
        """Generate the DEK of a new business.

        Args:
            business_id: Id the business will be stored under.

        Returns:
            The open vault and the DEK wrapped by the KEK, to store in ``businesses.dek_enc``.
        """
        dek = AESGCM.generate_key(bit_length=_KEY_BYTES * 8)
        return Vault(business_id, dek, self), _seal(self._kek, dek, _dek_aad(business_id))

    def open_business(self, business_id: UUID, wrapped: bytes) -> Vault:
        """Unwrap a stored DEK.

        Args:
            business_id: Business the DEK was wrapped for.
            wrapped: Content of ``businesses.dek_enc``.

        Returns:
            The open vault.

        Raises:
            cryptography.exceptions.InvalidTag: Wrong KEK or business.
        """
        return Vault(business_id, _open(self._kek, wrapped, _dek_aad(business_id)), self)


def _dek_aad(business_id: UUID) -> bytes:
    """Bind a wrapped DEK to its business."""
    return f"dek|{business_id}".encode()


def _decode_key(name: str, value: str | None) -> bytes:
    """Decode a base64 32-byte key from settings."""
    try:
        key = base64.b64decode(value or "", validate=True)
    except binascii.Error as exc:
        raise ValueError(f"{name} is not base64") from exc
    if len(key) != _KEY_BYTES:
        raise ValueError(f"{name} must be {_KEY_BYTES} bytes, base64-encoded")
    return key
