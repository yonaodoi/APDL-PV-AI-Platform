"""Encryption of stored credentials (API keys for dictionaries and AI services).

Credentials are encrypted before they reach the database and are only
decrypted in memory when a connection is made. They are never shown again in
full, written to logs, the audit trail or exports: screens show a hint such as
"••••3f9a".

The key comes from PV_SECRETS_KEY in .env (recommended), falling back to the
application secret key (APDL_PV_SECRET_KEY). If that key changes, stored
credentials cannot be read and must be entered again.

When the ``cryptography`` package is installed, Fernet (AES-128-CBC with
HMAC-SHA256) is used. Without it, an encrypt-then-MAC construction built only
on HMAC-SHA256 from the standard library is used: a keystream from
HMAC-SHA256(key, nonce || counter) and an HMAC-SHA256 tag over the nonce and
ciphertext. Both formats are recognised when reading.
"""

import base64
import hashlib
import hmac
import os

from flask import current_app


class SecretError(Exception):
    pass


def _master_key():
    key = os.environ.get("PV_SECRETS_KEY") or current_app.config.get("SECRET_KEY")
    if not key:
        raise SecretError("No encryption key is configured (set PV_SECRETS_KEY or the application secret key).")
    return hashlib.sha256(("pv-secrets:" + str(key)).encode("utf-8")).digest()


def _derive(label):
    return hmac.new(_master_key(), label.encode("ascii"), hashlib.sha256).digest()


def encrypt(plaintext):
    if plaintext is None or plaintext == "":
        return None
    data = plaintext.encode("utf-8")
    try:
        from cryptography.fernet import Fernet

        token = Fernet(base64.urlsafe_b64encode(_derive("fernet"))).encrypt(data)
        return "f1:" + token.decode("ascii")
    except ImportError:
        pass
    nonce = os.urandom(16)
    stream = _keystream(_derive("enc"), nonce, len(data))
    cipher = bytes(a ^ b for a, b in zip(data, stream))
    tag = hmac.new(_derive("mac"), b"h1" + nonce + cipher, hashlib.sha256).digest()
    return "h1:" + base64.urlsafe_b64encode(nonce + cipher + tag).decode("ascii")


def decrypt(token):
    if not token:
        return None
    try:
        if token.startswith("f1:"):
            from cryptography.fernet import Fernet, InvalidToken

            try:
                return Fernet(base64.urlsafe_b64encode(_derive("fernet"))).decrypt(token[3:].encode("ascii")).decode("utf-8")
            except InvalidToken:
                raise SecretError("The stored credential cannot be read (the encryption key has changed). Enter it again.")
        if token.startswith("h1:"):
            raw = base64.urlsafe_b64decode(token[3:].encode("ascii"))
            nonce, cipher, tag = raw[:16], raw[16:-32], raw[-32:]
            expected = hmac.new(_derive("mac"), b"h1" + nonce + cipher, hashlib.sha256).digest()
            if not hmac.compare_digest(tag, expected):
                raise SecretError("The stored credential cannot be read (the encryption key has changed). Enter it again.")
            stream = _keystream(_derive("enc"), nonce, len(cipher))
            return bytes(a ^ b for a, b in zip(cipher, stream)).decode("utf-8")
    except ImportError:
        raise SecretError("This credential was stored with the 'cryptography' package, which is no longer installed.")
    raise SecretError("Unrecognised credential format.")


def _keystream(key, nonce, length):
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:length])


def hint(plaintext):
    """'••••3f9a' – enough to recognise a key, never enough to use it."""
    text = (plaintext or "").strip()
    if not text:
        return None
    return "••••" + (text[-4:] if len(text) >= 8 else "")


def redact(text, secrets):
    """Remove any of ``secrets`` from a message before it is shown or logged."""
    out = str(text or "")
    for secret in secrets or []:
        if secret and len(secret) >= 4:
            out = out.replace(secret, "[credential]")
    return out
