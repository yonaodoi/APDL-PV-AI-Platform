"""Single entry point for AI text generation.

When an AI service is enabled under Administration > Licences & integrations,
its provider, model, credential, timeout, retries and spending limit are used.
Otherwise the environment settings below apply (see .env).

Documents contain patient identifiers, so they are sent to the enabled AI
service only when that service is marked "may read documents"; otherwise the
document-reading settings from .env are used (by default the local model).
Every call is recorded in pv.ai_usage (tokens, estimated cost, duration,
status; never the prompt or the answer).

    AI_PROVIDER=anthropic | ollama
        Default: anthropic when ANTHROPIC_API_KEY is set, otherwise ollama.
    AI_EXTRACTION_PROVIDER=anthropic | ollama
        Provider for reading uploaded documents (which contain patient
        identifiers). Defaults to AI_PROVIDER.
    ANTHROPIC_API_KEY=...            (required for anthropic)
    ANTHROPIC_MODEL=claude-sonnet-5-5
    OLLAMA_URL=http://127.0.0.1:11434/api/generate
    OLLAMA_MODEL=qwen2.5:7b
    OLLAMA_NUM_CTX=16384     (how much text the local model can read at once)
    OLLAMA_TIMEOUT=900       (seconds; local models are slower)
"""

import os

import requests


ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5-5"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
DEFAULT_OLLAMA_MODEL = "qwen2.5:7b"
DEFAULT_OLLAMA_NUM_CTX = 16384
DEFAULT_OLLAMA_TIMEOUT = 900

PURPOSE_ASSESSMENT = "assessment"
PURPOSE_EXTRACTION = "extraction"


class AIUnavailableError(RuntimeError):
    """The configured AI provider could not produce a response."""


class AITransientError(AIUnavailableError):
    """A failure worth retrying (timeout, connection, rate limit, overload)."""


def provider_for(purpose=PURPOSE_ASSESSMENT):
    default = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "ollama"
    provider = os.environ.get("AI_PROVIDER", default).strip().lower()
    if purpose == PURPOSE_EXTRACTION:
        provider = (
            os.environ.get("AI_EXTRACTION_PROVIDER", provider).strip().lower()
        )
    if provider not in {"anthropic", "ollama"}:
        raise AIUnavailableError(
            f"Unknown AI provider '{provider}'. Use 'anthropic' or 'ollama'."
        )
    return provider


def model_for(provider):
    if provider == "anthropic":
        return os.environ.get("ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL)
    return os.environ.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)


def describe_model(purpose=PURPOSE_ASSESSMENT):
    """Human-readable provider and model, for audit records."""
    config = effective_config(purpose)
    label = "Claude API" if config["provider"] == "anthropic" else "Local Ollama"
    return f"{label}: {config['model']}"


def _env_config(purpose):
    provider = provider_for(purpose)
    return {
        "source": "environment",
        "provider": provider,
        "model": model_for(provider),
        "endpoint": ANTHROPIC_URL if provider == "anthropic" else os.environ.get("OLLAMA_URL", DEFAULT_OLLAMA_URL),
        "api_key": os.environ.get("ANTHROPIC_API_KEY") if provider == "anthropic" else None,
        "timeout": None,
        "retries": int(os.environ.get("AI_MAX_RETRIES", "1") or 1),
        "monthly_limit": None,
        "price_in": None,
        "price_out": None,
        "currency": "USD",
    }


def effective_config(purpose=PURPOSE_ASSESSMENT):
    """The settings a call will use: the enabled AI integration, else .env."""
    try:
        from app.services.integrations import active_ai_config

        integration = active_ai_config()
    except Exception:
        integration = None
    if integration and (purpose != PURPOSE_EXTRACTION or integration.get("use_for_documents")):
        config = dict(integration)
        if config["provider"] == "anthropic":
            config["endpoint"] = config.get("endpoint") or ANTHROPIC_URL
            config["model"] = config.get("model") or DEFAULT_ANTHROPIC_MODEL
        else:
            config["endpoint"] = config.get("endpoint") or DEFAULT_OLLAMA_URL
            config["model"] = config.get("model") or DEFAULT_OLLAMA_MODEL
        return config
    return _env_config(purpose)


def generate_text(
    prompt,
    *,
    system=None,
    max_tokens=2000,
    temperature=0.2,
    json_output=False,
    timeout=180,
    purpose=PURPOSE_ASSESSMENT,
    on_progress=None,
    config=None,
):
    """Return the model's text response, or raise AIUnavailableError.

    ``on_progress(text_so_far)`` is called periodically while a local model
    is still writing, so long generations can report progress. Transient
    failures are retried; nothing is changed between attempts, so a retry can
    never duplicate work.
    """
    import time

    config = dict(config or effective_config(purpose))
    if config["provider"] not in {"anthropic", "ollama"}:
        raise AIUnavailableError(f"Unknown AI provider '{config['provider']}'. Use 'anthropic' or 'ollama'.")
    call_timeout = int(config.get("timeout") or timeout)
    _check_spending_limit(config)

    attempts = 1 + max(0, int(config.get("retries") or 0))
    for attempt in range(1, attempts + 1):
        started = time.monotonic()
        try:
            if config["provider"] == "anthropic":
                text, usage = _anthropic(prompt, system, max_tokens, temperature, json_output, call_timeout, config)
            else:
                text, usage = _ollama(prompt, system, max_tokens, temperature, json_output, call_timeout, on_progress, config)
            if not text.strip():
                raise AIUnavailableError("The AI model returned an empty response.")
        except AITransientError as error:
            _record(config, purpose, None, started, "failed", str(error), attempt)
            if attempt < attempts:
                time.sleep(min(2 ** attempt, 20))
                continue
            raise AIUnavailableError(f"{error} (tried {attempts} time{'s' if attempts != 1 else ''}).") from error
        except AIUnavailableError as error:
            _record(config, purpose, None, started, "failed", str(error), attempt)
            raise
        _record(config, purpose, usage, started, "ok", None, attempt)
        return text.strip()
    raise AIUnavailableError("The AI service did not answer.")


def _check_spending_limit(config):
    limit = config.get("monthly_limit")
    if not limit:
        return
    from app.services.integrations import month_spend

    spent, _ = month_spend()
    if spent >= limit:
        raise AIUnavailableError(
            f"The monthly AI spending limit ({limit} {config.get('currency', 'USD')}) has been reached "
            f"({spent:.2f} used). An administrator can change it under Administration › Licences & integrations. "
            "Everything else in the platform keeps working."
        )


def _record(config, purpose, usage, started, status, error, attempt):
    import time

    try:
        from app.services.integrations import record_ai_usage
        from app.services.secrets_store import redact
    except Exception:
        return
    usage = usage or {}
    tokens_in, tokens_out = usage.get("input_tokens"), usage.get("output_tokens")
    cost = None
    if config.get("price_in") is not None and tokens_in is not None:
        cost = (tokens_in * config["price_in"] + (tokens_out or 0) * (config.get("price_out") or 0)) / 1_000_000
    record_ai_usage(
        purpose, config.get("provider"), config.get("model"), tokens_in, tokens_out, cost,
        int((time.monotonic() - started) * 1000), status,
        redact(error, [config.get("api_key")]) if error else None, attempt,
    )


def _anthropic(prompt, system, max_tokens, temperature, json_output, timeout, config):
    api_key = config.get("api_key")
    if not api_key:
        raise AIUnavailableError(
            "No Claude API key is set. Add it under Administration › Licences & integrations (or ANTHROPIC_API_KEY in .env)."
        )

    if json_output:
        prompt += "\n\nRespond with a single JSON object only, no other text."

    body = {
        "model": config.get("model") or DEFAULT_ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        body["system"] = system

    try:
        response = requests.post(
            config.get("endpoint") or ANTHROPIC_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            json=body,
            timeout=timeout,
        )
    except requests.Timeout as exc:
        raise AITransientError(f"The Claude API did not answer within {timeout} seconds.") from exc
    except requests.RequestException as exc:
        raise AITransientError(
            "Could not reach the Claude API. Check the internet connection."
        ) from exc

    if response.status_code != 200:
        try:
            detail = response.json().get("error", {}).get("message", "")
        except ValueError:
            detail = response.text[:200]
        message = f"Claude API error {response.status_code}: {detail}"
        if response.status_code in (408, 409, 429, 500, 502, 503, 504, 529):
            raise AITransientError(message)
        if response.status_code in (401, 403):
            raise AIUnavailableError(f"{message}. The API key was refused; check it.")
        raise AIUnavailableError(message)

    data = response.json()
    text = "".join(
        block.get("text", "")
        for block in data.get("content", [])
        if block.get("type") == "text"
    )
    usage = data.get("usage") or {}
    return text, {"input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens")}


def _ollama(
    prompt, system, max_tokens, temperature, json_output, timeout,
    on_progress=None, config=None,
):
    config = config or {}
    model = config.get("model") or model_for("ollama")
    body = {
        "model": model,
        "prompt": prompt,
        "stream": on_progress is not None,
        "keep_alive": "15m",
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
            "num_ctx": int(
                os.environ.get("OLLAMA_NUM_CTX", DEFAULT_OLLAMA_NUM_CTX)
            ),
        },
    }
    if system:
        body["system"] = system
    if json_output:
        body["format"] = "json"

    try:
        response = requests.post(
            config.get("endpoint") or os.environ.get("OLLAMA_URL", DEFAULT_OLLAMA_URL),
            json=body,
            timeout=max(
                timeout,
                int(os.environ.get("OLLAMA_TIMEOUT", DEFAULT_OLLAMA_TIMEOUT)),
            ),
            stream=on_progress is not None,
        )
        response.raise_for_status()
        if on_progress is None:
            data = response.json()
            return data.get("response", ""), {"input_tokens": data.get("prompt_eval_count"), "output_tokens": data.get("eval_count")}
        return _read_ollama_stream(response, on_progress), {}
    except requests.Timeout as exc:
        raise AITransientError(f"The local AI model did not finish within the time allowed ({model}).") from exc
    except requests.RequestException as exc:
        raise AITransientError(
            f"Local AI is unavailable. Ensure Ollama is running with model {model}."
        ) from exc


PROGRESS_INTERVAL_SECONDS = 2.0


def _read_ollama_stream(response, on_progress):
    """Collect a streamed Ollama reply, reporting progress every few seconds."""
    import json
    import time

    parts = []
    last_report = 0.0
    for line in response.iter_lines():
        if not line:
            continue
        chunk = json.loads(line)
        if chunk.get("error"):
            raise AIUnavailableError(f"Local AI error: {chunk['error']}")
        parts.append(chunk.get("response", ""))
        now = time.monotonic()
        if chunk.get("done") or now - last_report >= PROGRESS_INTERVAL_SECONDS:
            last_report = now
            on_progress("".join(parts))
    return "".join(parts)


# Fields removed before case data is sent for assessment. The assessment
# needs clinical facts, not who the patient or reporter is.
IDENTIFYING_CASE_FIELDS = {
    "patient_initials",
    "patient_date_of_birth",
    "patient_address",
    "patient_phone",
    "reporter_name",
    "reporter_phone",
    "reporter_email",
    "reporter_organisation",
}


def deidentify(record):
    """Copy of a case-like dict with direct identifiers removed."""
    if not record:
        return {}
    return {
        key: value
        for key, value in dict(record).items()
        if key not in IDENTIFYING_CASE_FIELDS
    }
