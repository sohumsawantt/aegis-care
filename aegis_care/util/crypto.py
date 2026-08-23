"""Integrity primitives: SHA-256 commitments, Ed25519 signatures, HKDF tokens.

Section 12.1 pins "SHA-256 commitments and Ed25519 signatures via standard
libraries". This module is the only place that touches key material.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any, Dict, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


# --------------------------------------------------------------------------
# Canonical encoding
# --------------------------------------------------------------------------
def canonical_bytes(payload: Any) -> bytes:
    """Deterministic serialisation so commitments are stable across processes."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def commit(payload: Any, *, domain: str = "aegis") -> str:
    """Opaque commitment to a payload. Used for seeds, parents, and capsules."""
    return sha256_hex(domain.encode("utf-8") + b"\x00" + canonical_bytes(payload))


def commit_text(text: str, *, domain: str = "content") -> str:
    return sha256_hex(domain.encode("utf-8") + b"\x00" + text.encode("utf-8"))


# --------------------------------------------------------------------------
# Receiver-scoped pseudonyms (Section 5.4: "receiver-specific keyed token")
# --------------------------------------------------------------------------
def hkdf(key: bytes, info: bytes, length: int = 32) -> bytes:
    """RFC-5869 HKDF-Expand with an all-zero salt extract step."""
    prk = hmac.new(b"\x00" * 32, key, hashlib.sha256).digest()
    okm, block, counter = b"", b"", 1
    while len(okm) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        okm += block
        counter += 1
    return okm[:length]


def receiver_scoped_token(patient_id: str, recipient: str, incident_key: bytes) -> str:
    """A patient pseudonym that a recipient can match locally but cannot link
    across recipients or across incidents."""
    material = hkdf(incident_key, f"patient-token|{recipient}|{patient_id}".encode("utf-8"), 16)
    return "pt_" + material.hex()


# --------------------------------------------------------------------------
# Signing
# --------------------------------------------------------------------------
@dataclass
class SigningIdentity:
    """An Ed25519 identity belonging to one principal (runtime or coordinator)."""

    name: str
    _private: Ed25519PrivateKey

    @classmethod
    def generate(cls, name: str) -> "SigningIdentity":
        return cls(name=name, _private=Ed25519PrivateKey.generate())

    @classmethod
    def from_seed(cls, name: str, seed: bytes) -> "SigningIdentity":
        """Deterministic identity so a whole experiment can be reproduced."""
        material = hkdf(seed, f"ed25519|{name}".encode("utf-8"), 32)
        return cls(name=name, _private=Ed25519PrivateKey.from_private_bytes(material))

    @property
    def public_key(self) -> Ed25519PublicKey:
        return self._private.public_key()

    def public_key_b64(self) -> str:
        raw = self.public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return base64.b64encode(raw).decode("ascii")

    def sign(self, payload: Any) -> str:
        return base64.b64encode(self._private.sign(canonical_bytes(payload))).decode("ascii")


class KeyRing:
    """Registry of principal public keys, plus the private identities this
    process is allowed to sign with."""

    def __init__(self, root_seed: bytes = b"aegis-care-root") -> None:
        self._root_seed = root_seed
        self._identities: Dict[str, SigningIdentity] = {}
        self._public: Dict[str, str] = {}

    def identity(self, name: str) -> SigningIdentity:
        if name not in self._identities:
            ident = SigningIdentity.from_seed(name, self._root_seed)
            self._identities[name] = ident
            self._public[name] = ident.public_key_b64()
        return self._identities[name]

    def public_key_b64(self, name: str) -> str:
        self.identity(name)
        return self._public[name]

    def sign(self, principal: str, payload: Any) -> str:
        return self.identity(principal).sign(payload)

    def verify(self, principal: str, payload: Any, signature: str) -> bool:
        try:
            raw = base64.b64decode(self.public_key_b64(principal))
            Ed25519PublicKey.from_public_bytes(raw).verify(
                base64.b64decode(signature), canonical_bytes(payload)
            )
            return True
        except (InvalidSignature, ValueError, TypeError):
            return False

    def incident_key(self, incident_id: str) -> bytes:
        return hkdf(self._root_seed, f"incident|{incident_id}".encode("utf-8"), 32)

    def known_principals(self) -> Dict[str, str]:
        return dict(self._public)


__all__ = [
    "canonical_bytes", "sha256_hex", "commit", "commit_text", "hkdf",
    "receiver_scoped_token", "SigningIdentity", "KeyRing",
]
