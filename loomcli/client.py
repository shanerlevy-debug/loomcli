"""HTTP client over the Powerloom control plane.

Thin httpx wrapper that:
  - Injects the bearer token from RuntimeConfig on every request
  - Surfaces HTTP errors as PowerloomApiError so command code can
    catch a single exception type regardless of status code
  - Returns parsed JSON (or None for 204)

Synchronous on purpose — the CLI's operations are inherently serial
(parse manifest → GET current state → diff → POST/PATCH per resource)
and an async layer would complicate command code without real speedup.
Plan/apply latency is dominated by network round trips, not by
concurrency opportunities.
"""
from __future__ import annotations

from typing import Any

import httpx

from loomcli.config import RuntimeConfig


class PowerloomApiError(Exception):
    def __init__(
        self,
        status_code: int,
        message: str,
        body: Any | None = None,
        method: str | None = None,
        path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body
        self.method = method
        self.path = path

    def to_dict(self) -> dict[str, Any]:
        """Convert error to a standardized dictionary for agent-friendly output."""
        return {
            "status": "error",
            "code": self.status_code,
            "message": str(self),
            "details": self.body,
            "method": self.method,
            "path": self.path,
        }


class PowerloomClient:
    def __init__(self, cfg: RuntimeConfig) -> None:
        self._cfg = cfg
        headers: dict[str, str] = {"Accept": "application/json"}
        if cfg.access_token:
            headers["Authorization"] = f"Bearer {cfg.access_token}"
        # v0.5.3 — approval-gate support. When the user's org has a
        # policy that requires a justification, the API returns 409
        # with code='justification_required' unless this header is
        # present. --justification (CLI flag) or POWERLOOM_APPROVAL_JUSTIFICATION
        # (env var) provide the value.
        if cfg.approval_justification:
            headers["X-Approval-Justification"] = cfg.approval_justification
        # v0.6.1 — version negotiation. Send the loomcli SCHEMA_VERSION
        # so the engine can 426 us if the major's unsupported and we
        # surface a clear "upgrade your CLI" message instead of a generic
        # 4xx. Engine: powerloom_api/core/schema_version_check.py.
        from loomcli.schema import SCHEMA_VERSION  # local import to avoid cycle

        headers["X-Powerloom-Schema-Version"] = SCHEMA_VERSION
        self._http = httpx.Client(
            base_url=cfg.api_base_url,
            headers=headers,
            timeout=cfg.request_timeout_seconds,
        )

    # Context-manager friendly — commands open + close.
    def __enter__(self) -> "PowerloomClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self._http.close()

    def close(self) -> None:
        self._http.close()

    # -------- core methods --------
    def get(self, path: str, **params: Any) -> Any:
        return self._request("GET", path, params=params or None)

    def post(self, path: str, body: Any | None = None) -> Any:
        return self._request("POST", path, json=body)

    def patch(self, path: str, body: Any | None = None) -> Any:
        return self._request("PATCH", path, json=body)

    def delete(self, path: str) -> Any:
        return self._request("DELETE", path)

    def post_multipart(
        self,
        path: str,
        *,
        file_name: str,
        file_bytes: bytes,
        file_field: str = "file",
        content_type: str = "application/octet-stream",
    ) -> Any:
        """POST a multipart/form-data upload. Used by `weave skill upload`
        for archive uploads to `/skills/{id}/versions`.

        Returns the parsed JSON response on success.
        """
        try:
            res = self._http.post(
                path,
                files={file_field: (file_name, file_bytes, content_type)},
            )
        except httpx.HTTPError as e:
            raise PowerloomApiError(
                0, f"network error: {e}", method="POST", path=path
            ) from e
        if res.status_code == 204:
            return None
        if res.status_code >= 400:
            try:
                parsed = res.json()
            except Exception:
                parsed = {"raw": res.text}
            detail = _extract_detail(parsed) or res.text[:200]
            raise PowerloomApiError(
                res.status_code,
                f"HTTP {res.status_code} POST {path}: {detail}",
                body=parsed,
                method="POST",
                path=path,
            )
        try:
            return res.json()
        except Exception:
            return res.text

    # -------- login helper (unauthenticated) --------
    def dev_login(self, email: str) -> str:
        """Dev-mode impersonation. Returns the access_token. Only
        works when the control plane runs POWERLOOM_AUTH_MODE=dev."""
        # Unauthenticated request — don't inject bearer.
        res = httpx.get(
            f"{self._cfg.api_base_url}/auth/login",
            params={"as": email},
            timeout=self._cfg.request_timeout_seconds,
        )
        if res.status_code != 200:
            raise PowerloomApiError(
                res.status_code,
                f"dev login failed ({res.status_code}): {res.text[:200]}",
                method="GET",
                path="/auth/login",
            )
        body = res.json()
        token = body.get("tokens", {}).get("access_token")
        if not token:
            raise PowerloomApiError(
                200,
                "dev login returned no access_token — is POWERLOOM_AUTH_MODE=dev?",
                body=body,
                method="GET",
                path="/auth/login",
            )
        return token

    # -------- internal --------
    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
    ) -> Any:
        try:
            res = self._http.request(method, path, params=params, json=json)
        except httpx.HTTPError as e:
            raise PowerloomApiError(
                0,
                f"network error: {e}",
                method=method,
                path=path,
            ) from e
        if res.status_code == 204:
            return None
        if res.status_code >= 400:
            try:
                parsed = res.json()
            except Exception:
                parsed = {"raw": res.text}
            detail = _extract_detail(parsed) or res.text[:200]
            # v0.6.1 — 426 Upgrade Required. Engine emits this when the
            # CLI's SCHEMA_VERSION major is not supported. Format a
            # clearer message that surfaces the supported list so the
            # user knows what to install instead of seeing a raw 426.
            if res.status_code == 426:
                detail = _format_version_mismatch(parsed) or detail
            raise PowerloomApiError(
                res.status_code,
                f"HTTP {res.status_code} {method} {path}: {detail}",
                body=parsed,
                method=method,
                path=path,
            )
        try:
            return res.json()
        except Exception:
            return res.text


def _format_version_mismatch(body: Any) -> str | None:
    """Render a 426 schema-version-unsupported body into a CLI-friendly
    line. Returns None if `body` doesn't have the engine's expected
    `error.detail.{supported_versions, client_sent}` shape."""
    if not isinstance(body, dict):
        return None
    err = body.get("error")
    if not isinstance(err, dict):
        return None
    detail = err.get("detail")
    if not isinstance(detail, dict):
        return None
    supported = detail.get("supported_versions")
    sent = detail.get("client_sent")
    if not isinstance(supported, list) or not isinstance(sent, str):
        return None
    return (
        f"engine rejected schema version {sent!r}; supported: {supported}. "
        f"upgrade weave/loomcli to a version whose SCHEMA_VERSION major is "
        f"in that list."
    )


def _extract_detail(body: Any) -> str | None:
    """Pull a human-readable error out of a control-plane error payload.

    Matches the shape core.errors produces: {error: {message, code, ...}}
    as well as FastAPI's default {detail: "..."}.
    """
    if not isinstance(body, dict):
        return None
    err = body.get("error")
    if isinstance(err, dict):
        msg = err.get("message")
        if isinstance(msg, str):
            return msg
    if isinstance(body.get("detail"), str):
        return body["detail"]
    return None
