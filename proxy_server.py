"""Secure proxy for OpenAI-compatible chat APIs.

Run:
    python proxy_server.py

For home Wi-Fi use on iPhone/iPad:
    copy proxy.env.example to .env
    set PROXY_HOST=0.0.0.0
    then open http://YOUR_PC_IP:5051/

Environment variables:
    OLLAMA_API_ENDPOINT
    OPENAI_API_KEY
    OPENROUTER_API_KEY
    CUSTOM_LLM_API_KEY

Optional environment variables:
    OPENAI_API_ENDPOINT
    OPENROUTER_API_ENDPOINT
    CUSTOM_LLM_API_ENDPOINT
    OPENROUTER_HTTP_REFERER
    OPENROUTER_APP_TITLE
    PROXY_HOST
    PROXY_PORT
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

APP_DIR = Path(__file__).resolve().parent


def _relaunch_with_project_venv_if_needed() -> None:
    """Prefer the project virtualenv interpreter when available.

    This avoids common dependency errors when the script is launched with a
    global Python instead of `.venv`.
    """
    if os.environ.get("MYFIRSTWEB_VENV_BOOTSTRAPPED") == "1":
        return

    venv_python = APP_DIR / ".venv" / "Scripts" / "python.exe"
    if not venv_python.exists():
        return

    try:
        current_python = Path(sys.executable).resolve()
        target_python = venv_python.resolve()
    except OSError:
        return

    if current_python == target_python:
        return

    relaunch_env = os.environ.copy()
    relaunch_env["MYFIRSTWEB_VENV_BOOTSTRAPPED"] = "1"
    relaunch_cmd = [str(target_python), str(APP_DIR / "proxy_server.py"), *sys.argv[1:]]
    print(f"[proxy_server] Re-launching with project venv: {target_python}")
    raise SystemExit(subprocess.call(relaunch_cmd, cwd=str(APP_DIR), env=relaunch_env))


_relaunch_with_project_venv_if_needed()

import requests
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})


def _load_env_file() -> None:
    env_path = APP_DIR / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        normalized_key = key.strip()
        normalized_value = value.strip().strip('"').strip("'")
        if normalized_key:
            os.environ.setdefault(normalized_key, normalized_value)


_load_env_file()


PROVIDER_CONFIGS: Dict[str, Dict[str, Any]] = {
    "ollama": {
        "api_key_env": "",
        "endpoint_env": "OLLAMA_API_ENDPOINT",
        "default_endpoint": "http://127.0.0.1:11434/api/chat",
        "requires_api_key": False,
    },
    "openai": {
        "api_key_env": "OPENAI_API_KEY",
        "endpoint_env": "OPENAI_API_ENDPOINT",
        "default_endpoint": "https://api.openai.com/v1/chat/completions",
        "requires_api_key": True,
    },
    "openrouter": {
        "api_key_env": "OPENROUTER_API_KEY",
        "endpoint_env": "OPENROUTER_API_ENDPOINT",
        "default_endpoint": "https://openrouter.ai/api/v1/chat/completions",
        "requires_api_key": True,
    },
    "custom": {
        "api_key_env": "CUSTOM_LLM_API_KEY",
        "endpoint_env": "CUSTOM_LLM_API_ENDPOINT",
        "default_endpoint": "",
        "requires_api_key": True,
    },
}


def _extract_text(payload: Any) -> str:
    if not payload:
        return ""

    if isinstance(payload, str):
        return payload.strip()

    if isinstance(payload, dict) and isinstance(payload.get("content"), str):
        return payload["content"].strip()

    if isinstance(payload, dict):
        message = payload.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, str):
                        parts.append(item)
                    elif isinstance(item, dict) and isinstance(item.get("text"), str):
                        parts.append(item["text"])
                joined = "".join(parts).strip()
                if joined:
                    return joined

        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first_choice = choices[0]
            if isinstance(first_choice, dict):
                message = first_choice.get("message")
                if isinstance(message, dict):
                    content = message.get("content")
                    if isinstance(content, str):
                        return content.strip()
                    if isinstance(content, list):
                        parts = []
                        for item in content:
                            if isinstance(item, str):
                                parts.append(item)
                            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                                parts.append(item["text"])
                        joined = "".join(parts).strip()
                        if joined:
                            return joined

                text = first_choice.get("text")
                if isinstance(text, str):
                    return text.strip()

        output_text = payload.get("output_text")
        if isinstance(output_text, str):
            return output_text.strip()

        response_text = payload.get("response")
        if isinstance(response_text, str):
            return response_text.strip()

        content = payload.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    return item["text"].strip()

        output = payload.get("output")
        if isinstance(output, list) and output:
            first = output[0]
            if isinstance(first, dict):
                out_content = first.get("content")
                if isinstance(out_content, list):
                    parts = []
                    for item in out_content:
                        if isinstance(item, dict) and isinstance(item.get("text"), str):
                            parts.append(item["text"])
                    joined = "".join(parts).strip()
                    if joined:
                        return joined

    return ""


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _build_upstream_payload(provider: str, model: str, messages: Any, body: Dict[str, Any]) -> Dict[str, Any]:
    temperature = _to_float(body.get("temperature"), 0.7)
    top_p = _to_float(body.get("top_p"), 0.9)
    max_tokens = _to_int(body.get("max_tokens"), 240)

    if provider == "ollama":
        return {
            "model": model,
            "messages": messages,
            "stream": False,
            "keep_alive": "10m",
            "options": {
                "temperature": temperature,
                "top_p": top_p,
                "num_predict": max_tokens,
            },
        }

    return {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": max_tokens,
    }


def _build_headers(provider: str, api_key: str) -> Dict[str, str]:
    headers = {
        "Content-Type": "application/json",
    }

    if provider != "ollama":
        headers["Authorization"] = f"Bearer {api_key}"

    if provider == "openrouter":
        headers["HTTP-Referer"] = os.getenv("OPENROUTER_HTTP_REFERER", "http://localhost")
        headers["X-Title"] = os.getenv("OPENROUTER_APP_TITLE", "Chemistry Fundamentals 007")

    return headers


def _serve_workspace_file(filename: str) -> Any:
    return send_from_directory(APP_DIR, filename)


@app.get("/")
def serve_root() -> Any:
    return _serve_workspace_file("chemistry.html")


@app.get("/chemistry.html")
def serve_chemistry() -> Any:
    return _serve_workspace_file("chemistry.html")


@app.get("/index.html")
def serve_index() -> Any:
    return _serve_workspace_file("index.html")


@app.get("/subjects.html")
def serve_subjects() -> Any:
    return _serve_workspace_file("subjects.html")


@app.get("/philosophy_religion.html")
def serve_philosophy() -> Any:
    return _serve_workspace_file("philosophy_religion.html")


@app.get("/api/health")
def health() -> Any:
    providers = {}
    for name, config in PROVIDER_CONFIGS.items():
        key_name = str(config.get("api_key_env") or "")
        endpoint = os.getenv(str(config["endpoint_env"]), str(config["default_endpoint"])).strip()
        requires_api_key = bool(config.get("requires_api_key", True))
        configured = bool(endpoint)

        if requires_api_key:
            configured = bool(os.getenv(key_name, "").strip())

        providers[name] = {
            "configured": configured,
            "requiresApiKey": requires_api_key,
            "apiKeyEnv": key_name or None,
            "endpoint": endpoint,
        }

    return jsonify(
        {
            "status": "ok",
            "providers": providers,
        }
    )


@app.post("/api/chat")
def chat() -> Any:
    body = request.get_json(silent=True) or {}

    provider = str(body.get("provider") or "openai").strip().lower()
    if provider not in PROVIDER_CONFIGS:
        return jsonify({"error": f"Unsupported provider: {provider}"}), 400

    provider_config = PROVIDER_CONFIGS[provider]

    endpoint = str(body.get("endpoint") or "").strip() or os.getenv(
        provider_config["endpoint_env"], provider_config["default_endpoint"]
    )
    model = str(body.get("model") or "").strip()
    messages = body.get("messages")

    if not endpoint:
        return jsonify({"error": "Provider endpoint is missing."}), 400

    if not model:
        return jsonify({"error": "Model is required."}), 400

    if not isinstance(messages, list) or not messages:
        return jsonify({"error": "Messages must be a non-empty list."}), 400

    requires_api_key = bool(provider_config.get("requires_api_key", True))
    api_key = ""
    if requires_api_key:
        api_key = os.getenv(str(provider_config["api_key_env"]), "").strip()
        if not api_key:
            return (
                jsonify(
                    {
                        "error": (
                            f"Server environment variable {provider_config['api_key_env']} is not set. "
                            "Configure it before using proxy mode."
                        )
                    }
                ),
                500,
            )

    request_payload = _build_upstream_payload(provider, model, messages, body)
    headers = _build_headers(provider, api_key)

    try:
        upstream_response = requests.post(
            endpoint,
            headers=headers,
            json=request_payload,
            timeout=60,
        )
    except requests.RequestException as exc:
        return jsonify({"error": f"Upstream request failed: {exc}"}), 502

    raw_text = upstream_response.text
    try:
        upstream_payload = upstream_response.json()
    except ValueError:
        upstream_payload = {}

    if not upstream_response.ok:
        upstream_error = ""
        if isinstance(upstream_payload, dict):
            error_obj = upstream_payload.get("error")
            if isinstance(error_obj, dict):
                upstream_error = str(error_obj.get("message") or "").strip()
            elif isinstance(error_obj, str):
                upstream_error = error_obj.strip()

        if not upstream_error:
            upstream_error = raw_text[:280] or f"HTTP {upstream_response.status_code}"

        return (
            jsonify(
                {
                    "error": upstream_error,
                    "provider": provider,
                    "status": upstream_response.status_code,
                }
            ),
            upstream_response.status_code,
        )

    content = _extract_text(upstream_payload)
    if not content:
        return jsonify({"error": "Proxy could not parse text content from upstream response."}), 502

    usage = {}
    if isinstance(upstream_payload, dict) and isinstance(upstream_payload.get("usage"), dict):
        usage = upstream_payload["usage"]

    return jsonify(
        {
            "content": content,
            "provider": provider,
            "model": model,
            "usage": usage,
        }
    )


if __name__ == "__main__":
    host = os.getenv("PROXY_HOST", "127.0.0.1")
    port = _to_int(os.getenv("PROXY_PORT", "5051"), 5051)
    app.run(host=host, port=port, debug=False)
