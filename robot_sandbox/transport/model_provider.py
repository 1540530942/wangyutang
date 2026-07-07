from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import requests


class ModelProviderError(RuntimeError):
    pass


def nested_get(data: dict[str, Any], path: list[str]) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


class GenericHttpAudioProvider:
    """Provider-neutral HTTP adapter for the real ASR service."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.name = str(config.get("name") or config.get("active") or config.get("active_provider") or "generic_http")
        self.endpoint = str(config.get("endpoint") or "")
        self.model = str(config.get("model") or "")
        self.audio_field = str(config.get("audio_field") or "audio")
        self.model_field = str(config.get("model_field") or "model")
        self.timeout = float(config.get("timeout_seconds") or 45)
        self.response_text_path = list(config.get("response_text_path") or ["text"])

    def api_key(self) -> str:
        explicit = str(self.config.get("api_key") or "")
        env_name = str(self.config.get("api_key_env") or "")
        return explicit or (os.environ.get(env_name, "") if env_name else "")

    def transcribe(self, wav_path: str | Path) -> dict[str, Any]:
        if not self.endpoint:
            raise ModelProviderError("model_provider.endpoint is required")
        path = Path(wav_path)
        if not path.exists():
            raise ModelProviderError(f"audio file not found: {path}")

        headers = dict(self.config.get("headers") or {})
        key = self.api_key()
        if key and "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {key}"

        fields = dict(self.config.get("extra_fields") or {})
        if self.model:
            fields[self.model_field] = self.model

        with path.open("rb") as handle:
            files = {self.audio_field: (path.name, handle, "audio/wav")}
            response = requests.post(
                self.endpoint,
                headers=headers,
                data=fields,
                files=files,
                timeout=self.timeout,
            )
        response.raise_for_status()
        payload = response.json()
        text = nested_get(payload, self.response_text_path)
        if not isinstance(text, str) or not text.strip():
            return {
                "text": "",
                "raw": payload,
                "error": f"empty transcript at response path: {'.'.join(self.response_text_path)}",
            }
        return {"text": text.strip(), "raw": payload}


def resolve_provider_config(config: dict[str, Any]) -> dict[str, Any]:
    providers = config.get("providers")
    active = str(config.get("active") or config.get("active_provider") or config.get("provider") or "")
    if not isinstance(providers, dict) or not providers:
        return dict(config)

    if not active:
        active = next(iter(providers))
    selected = providers.get(active)
    if not isinstance(selected, dict):
        raise ModelProviderError(f"unknown ASR provider: {active}")

    base = {key: value for key, value in config.items() if key not in {"providers", "active", "active_provider", "provider"}}
    merged = {**base, **selected}
    merged["active"] = active
    return merged


def build_provider(config: dict[str, Any]) -> GenericHttpAudioProvider:
    config = resolve_provider_config(config)
    provider_type = str(config.get("type") or "generic_http")
    if provider_type == "generic_http":
        return GenericHttpAudioProvider(config)
    raise ModelProviderError(f"unsupported provider type without real ASR transport: {provider_type}")
