"""
Header Decryption for orion-mcp.

Decrypts JSON payloads from encrypted HTTP request headers sent by clients
(e.g., BugZooka). Uses AES-256-GCM with a shared symmetric key.

This module is generic - it decrypts the header and returns the JSON dict.
Callers decide which fields to extract (es_server, api_keys, etc.).
"""
import base64
import json
import logging
import os
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from utils.constants import AES_GCM_KEY_LENGTH_BYTES, AES_GCM_NONCE_LENGTH_BYTES

logger = logging.getLogger(__name__)

# Generic encrypted payload header name
HEADER_NAME = "X-Encrypted-Context"


def get_decrypted_context(headers: dict) -> Optional[dict]:
    """
    Extract and decrypt JSON context from request headers.

    Looks for X-Encrypted-Context header (case-insensitive),
    decrypts it using HEADER_SYMMETRIC_KEY, and returns the JSON dict.

    This function is generic - it does not validate specific fields.
    Callers should extract and validate the fields they need.

    :param headers: HTTP request headers dict
    :return: Decrypted context dict, or None if header not present
    :raises ValueError: If decryption fails or JSON is invalid

    Example:
        >>> headers = {"X-Encrypted-Context": "AQAAAACKzJ8R7vN...=="}
        >>> context = get_decrypted_context(headers)
        >>> print(context)
        {
            "es_server": "https://es-prod.example.com:9200",
            "es_metadata_index": "perf_scale_ci*",
            "api_key": "some-other-secret"
        }
        >>> # Caller extracts what it needs:
        >>> es_server = context.get("es_server")
    """
    if not headers:
        logger.debug("No headers provided")
        return None

    # Check for encrypted header (case-insensitive)
    encrypted_blob = None
    for key, value in headers.items():
        if key.lower() == HEADER_NAME.lower():
            encrypted_blob = value
            logger.debug("Found %s header", HEADER_NAME)
            break

    if not encrypted_blob:
        logger.debug("No %s header found in request", HEADER_NAME)
        return None

    logger.info("Found encrypted context in headers, decrypting...")

    try:
        decrypted_json = decrypt_payload(encrypted_blob)

        # Parse JSON dict
        context = json.loads(decrypted_json)

        if not isinstance(context, dict):
            raise ValueError(f"Expected dict, got {type(context)}")

        logger.info("Successfully decrypted context from header")
        logger.debug("Decrypted context keys: %s", list(context.keys()))
        return context

    except json.JSONDecodeError as e:
        logger.error("Failed to parse context JSON: %s", str(e))
        raise ValueError(f"Invalid context JSON format: {str(e)}") from e
    except Exception as e:
        logger.error("Failed to decrypt context from headers: %s", str(e))
        raise ValueError(f"Invalid encrypted context: {str(e)}") from e


def decrypt_payload(encrypted_blob: str) -> str:
    """
    Decrypt  payload from base64-encoded encrypted blob.

    Format: base64(nonce + ciphertext + authentication_tag)
    - nonce: 12 bytes (first 12 bytes)
    - ciphertext + tag: remaining bytes

    :param encrypted_blob: Base64-encoded encrypted data
    :return: Decrypted payload as string (typically JSON)
    :raises ValueError: If decryption fails or HEADER_SYMMETRIC_KEY not set

    Example:
        >>> encrypted = "AQAAAACKzJ8R7vN...base64blob...=="
        >>> payload_json = decrypt_payload(encrypted)
        >>> print(payload_json)
        {"es_server": "https://...", "other_field": "value"}
    """
    # Shared symmetric key (base64-encoded 32 bytes)
    encryption_key_b64 = os.environ.get("HEADER_SYMMETRIC_KEY")
    if not encryption_key_b64:
        raise ValueError(
            "HEADER_SYMMETRIC_KEY environment variable not set. "
            "This key must match the one used by the client (e.g., BugZooka)."
        )

    # Decode base64 key to bytes
    try:
        encryption_key = base64.b64decode(encryption_key_b64)
    except Exception as e:
        raise ValueError(
            f"Invalid HEADER_SYMMETRIC_KEY format (must be base64): {e}"
        ) from e

    if len(encryption_key) != AES_GCM_KEY_LENGTH_BYTES:
        raise ValueError(
            "HEADER_SYMMETRIC_KEY must decode to "
            f"{AES_GCM_KEY_LENGTH_BYTES} bytes (AES-256), got {len(encryption_key)} bytes"
        )

    # Decode encrypted blob from base64
    try:
        encrypted_data = base64.b64decode(encrypted_blob)
    except Exception as e:
        raise ValueError(f"Invalid encrypted blob format (must be base64): {e}") from e

    if len(encrypted_data) < AES_GCM_NONCE_LENGTH_BYTES:
        raise ValueError(
            "Encrypted data too short "
            f"(minimum {AES_GCM_NONCE_LENGTH_BYTES} bytes for nonce), "
            f"got {len(encrypted_data)} bytes"
        )

    nonce = encrypted_data[:AES_GCM_NONCE_LENGTH_BYTES]
    ciphertext_with_tag = encrypted_data[AES_GCM_NONCE_LENGTH_BYTES:]

    # Decrypt using AES-GCM
    aesgcm = AESGCM(encryption_key)
    try:
        plaintext = aesgcm.decrypt(nonce, ciphertext_with_tag, associated_data=None)
    except Exception as e:
        raise ValueError(
            f"Decryption failed (wrong key or corrupted data): {e}. "
            "Ensure HEADER_SYMMETRIC_KEY matches the client's key."
        ) from e

    # Decode to string
    payload_str = plaintext.decode('utf-8')

    logger.debug(
        "Decrypted %d bytes payload",
        len(encrypted_data)
    )

    return payload_str


# ============================================================================
# Convenience functions for specific use cases (ES config, etc.)
# These extract specific fields from the decrypted context.
# ============================================================================

def get_es_config_from_headers(headers: dict) -> Optional[dict]:
    """
    Convenience function to extract ES configuration from headers.

    Decrypts the X-Encrypted-Context header and extracts ES-specific fields.
    This is a wrapper around get_decrypted_context() for ES use case.

    :param headers: HTTP request headers dict
    :return: Dict with es_server (required), es_metadata_index, es_benchmark_index (optional)
             Returns None if header not present
    :raises ValueError: If decryption fails or es_server is missing

    Example:
        >>> headers = {"X-Encrypted-Context": "...encrypted..."}
        >>> es_config = get_es_config_from_headers(headers)
        >>> print(es_config["es_server"])
        "https://es-prod.example.com:9200"
    """
    context = get_decrypted_context(headers)

    if context is None:
        return None

    # Validate ES-specific required field
    if "es_server" not in context:
        raise ValueError(
            "Decrypted context missing required field 'es_server'. "
            f"Available fields: {list(context.keys())}"
        )

    # Return only ES-related fields (filter out other secrets)
    es_config = {
        "es_server": context["es_server"]
    }

    # Include optional ES fields if present
    if "es_metadata_index" in context:
        es_config["es_metadata_index"] = context["es_metadata_index"]
    if "es_benchmark_index" in context:
        es_config["es_benchmark_index"] = context["es_benchmark_index"]

    logger.info("Extracted ES config from decrypted context")
    return es_config
