from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from datetime import datetime, timezone
from urllib.parse import urlparse

from cloud_av_agent_lab.network.client import encode_json_payload

from .errors import TencentCloudConfigError


def build_tc3_headers(
    secret_id: str,
    secret_key: str,
    endpoint: str,
    action: str,
    version: str,
    region: str,
    payload: Mapping[str, object],
    timestamp: int,
) -> dict[str, str]:
    host = _endpoint_host(endpoint)
    service = host.split(".", maxsplit=1)[0]
    algorithm = "TC3-HMAC-SHA256"
    content_type = "application/json; charset=utf-8"
    signed_headers = "content-type;host"
    canonical_headers = f"content-type:{content_type}\nhost:{host}\n"
    hashed_payload = hashlib.sha256(encode_json_payload(payload)).hexdigest()
    canonical_request = "\n".join(
        [
            "POST",
            "/",
            "",
            canonical_headers,
            signed_headers,
            hashed_payload,
        ]
    )
    request_date = datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d")
    credential_scope = f"{request_date}/{service}/tc3_request"
    string_to_sign = "\n".join(
        [
            algorithm,
            str(timestamp),
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    signature = _sign_tc3(secret_key, request_date, service, string_to_sign)
    authorization = (
        f"{algorithm} Credential={secret_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    return {
        "Authorization": authorization,
        "Content-Type": content_type,
        "Host": host,
        "X-TC-Action": action,
        "X-TC-Version": version,
        "X-TC-Timestamp": str(timestamp),
        "X-TC-Region": region,
    }


def _sign_tc3(
    secret_key: str,
    request_date: str,
    service: str,
    string_to_sign: str,
) -> str:
    secret_date = _hmac_sha256(("TC3" + secret_key).encode("utf-8"), request_date)
    secret_service = _hmac_sha256(secret_date, service)
    secret_signing = _hmac_sha256(secret_service, "tc3_request")
    return hmac.new(
        secret_signing,
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _hmac_sha256(key: bytes, value: str) -> bytes:
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()


def _endpoint_host(endpoint: str) -> str:
    parsed = urlparse(endpoint)
    if not parsed.netloc:
        raise TencentCloudConfigError(f"invalid Tencent Cloud endpoint: {endpoint}")
    return parsed.netloc
