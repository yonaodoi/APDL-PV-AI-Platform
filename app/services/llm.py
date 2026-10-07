"""Single entry point for AI text generation.

Provider is chosen by environment settings (see .env):

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
    provider = provider_for(purpose)
    label = "Claude API" if provider == "anthropic" else "Local Ollama"
    return f"{label}: {model_for(provider)}"


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
):
    """Return the model's text response, or raise AIUnavailableError.

    ``on_progress(text_so_far)`` is called periodically while a local model
    is still writing, so long generations can report progress.
    """
    provider = provider_for(purpose)
    if provider == "anthropic":
        text = _anthropic(prompt, system, max_tokens, temperature, json_output, timeout)
    else:
        text = _ollama(
            prompt, system, max_tokens, temperature, json_output, timeout,
            on_progress,
        )

    if not text.strip():
        raise AIUnavailableError("The AI model returned an empty response.")
    return text.strip()


def _anthropic(prompt, system, max_tokens, temperature, json_output, timeout):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise AIUnavailableError(
            "ANTHROPIC_API_KEY is not set. Add it to the .env file."
        )

    if json_output:
        prompt += "\n\nRespond with a single JSON object only, no other text."

    body = {
        "model": model_for("anthropic"),
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        body["system"] = system

    try:
        response = requests.post(
            ANTHROPIC_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            json=body,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise AIUnavailableError(
            "Could not reach the Claude API. Check the internet connection."
        ) from exc

    if response.status_code != 200:
        try:
            detail = response.json().get("error", {}).get("message", "")
        except ValueError:
            detail = response.text[:200]
        raise AIUnavailableError(
            f"Claude API error {response.status_code}: {detail}"
        )

    data = response.json()
    return "".join(
        block.get("text", "")
        for block in data.get("content", [])
        if block.get("type") == "text"
    )


def _ollama(
    prompt, system, max_tokens, temperature, json_output, timeout,
    on_progress=None,
):
    model = model_for("ollama")
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
            os.environ.get("OLLAMA_URL", DEFAULT_OLLAMA_URL),
            json=body,
            timeout=max(
                timeout,
                int(os.environ.get("OLLAMA_TIMEOUT", DEFAULT_OLLAMA_TIMEOUT)),
            ),
            stream=on_progress is not None,
        )
        response.raise_for_status()
        if on_progress is None:
            return response.json().get("response", "")
        return _read_ollama_stream(response, on_progress)
    except requests.RequestException as exc:
        raise AIUnavailableError(
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
