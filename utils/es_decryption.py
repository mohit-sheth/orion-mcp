"""
ES Server Decryption for orion-mcp.

Decrypts ES configuration (server, indices) from an encrypted HTTP request header
sent by BugZooka. Uses AES-256-GCM with a shared symmetric key (same key for all
encrypted header payloads).
"""
import base64
import json
import logging
import os
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from utils.constants import AES_GCM_KEY_LENGTH_BYTES, AES_GCM_NONCE_LENGTH_BYTES

logger = logging.getLogger(__name__)

# Encrypted payload header
HEADER_NAME = "X-Encrypted-Context"


def get_es_server_from_headers(headers: dict) -> Optional[dict]:
    """
    Extract and decrypt ES configuration from request headers.

    Looks for X-Encrypted-Context header (case-insensitive),
    decrypts it using HEADER_ENCRYPTION_KEY, and returns ES config dict.

    :param headers: HTTP request headers dict
    :return: Decrypted ES config dict with keys:
             - es_server: ES_SERVER URL
             - es_metadata_index: (optional) metadata index pattern
             - es_benchmark_index: (optional) benchmark index pattern
             Returns None if header not present
    :raises ValueError: If decryption fails

    Example:
        >>> headers = {"X-Encrypted-Context": "AQAAAACKzJ8R7vN...=="}
        >>> es_config = get_es_server_from_headers(headers)
        >>> print(es_config)
        {
            "es_server": "https://es-prod.example.com:9200",
            "es_metadata_index": "perf_scale_ci*",
            "es_benchmark_index": "ripsaw-kube-burner-*"
        }
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

    logger.info("Found encrypted ES config in headers, decrypting...")

    try:
        decrypted_json = decrypt_es_server(encrypted_blob)

        # Parse JSON dict
        es_config = json.loads(decrypted_json)

        # Validate that we got a dict with es_server
        if not isinstance(es_config, dict):
            raise ValueError(f"Expected dict, got {type(es_config)}")

        if "es_server" not in es_config:
            raise ValueError("Missing required field 'es_server' in ES config")

        logger.info("Successfully decrypted ES config from encrypted header")
        return es_config
    except json.JSONDecodeError as e:
        logger.error("Failed to parse ES config JSON: %s", str(e))
        raise ValueError(f"Invalid ES config JSON format: {str(e)}") from e
    except Exception as e:
        logger.error("Failed to decrypt ES config from headers: %s", str(e))
        raise ValueError(f"Invalid encrypted ES config: {str(e)}") from e


def decrypt_es_server(encrypted_blob: str) -> str:
    """
    Decrypt ES configuration JSON from base64-encoded encrypted blob.

    Decrypts the full ES config which includes es_server, es_metadata_index,
    and es_benchmark_index as a JSON string.

    Format: base64(nonce + ciphertext + authentication_tag)
    - nonce: 12 bytes (first 12 bytes)
    - ciphertext + tag: remaining bytes

    :param encrypted_blob: Base64-encoded encrypted data
    :return: Decrypted ES config as JSON string
    :raises ValueError: If decryption fails or HEADER_ENCRYPTION_KEY not set

    Example:
        >>> encrypted = "AQAAAACKzJ8R7vN...base64blob...=="
        >>> config_json = decrypt_es_server(encrypted)
        >>> print(config_json)
        {"es_server": "https://es-prod.example.com:9200", "es_metadata_index": "...", ...}
    """
    # Shared symmetric key (base64-encoded 32 bytes); same value used to encrypt on BugZooka
    encryption_key_b64 = os.environ.get("HEADER_ENCRYPTION_KEY")
    if not encryption_key_b64:
        raise ValueError(
            "HEADER_ENCRYPTION_KEY environment variable not set. "
            "This key must match the one used in BugZooka."
        )

    # Decode base64 key to bytes
    try:
        encryption_key = base64.b64decode(encryption_key_b64)
    except Exception as e:
        raise ValueError(
            f"Invalid HEADER_ENCRYPTION_KEY format (must be base64): {e}"
        ) from e

    if len(encryption_key) != AES_GCM_KEY_LENGTH_BYTES:
        raise ValueError(
            "HEADER_ENCRYPTION_KEY must decode to "
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
            "Ensure HEADER_ENCRYPTION_KEY matches the one in BugZooka."
        ) from e

    # Decode to string (JSON config)
    es_config_json = plaintext.decode('utf-8')

    logger.debug(
        "Decrypted %d bytes to ES config JSON",
        len(encrypted_data)
    )

    return es_config_json
