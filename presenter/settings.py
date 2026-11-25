"""Runtime settings for model endpoints (LLM + speech recognition).

Defaults come from .env; changes made in the UI are persisted to data/settings.json
and take precedence. API keys stay on the server and are only ever returned masked.
"""
from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
FILE = Path(os.getenv("STAGE_SETTINGS_FILE") or BASE / "data" / "settings.json")  # override for tests
MODELS = BASE / "models"
_lock = threading.Lock()
_cache: dict | None = None

KEEP = "__keep__"    # api_key sentinel: leave the stored key unchanged
CLEAR = "__clear__"  # api_key sentinel: remove the stored key

LLM_PRESETS = [
    {"id": "ollama", "label": "Ollama (local)", "base_url": "http://localhost:11434/v1", "model": "llama3.1:8b"},
    {"id": "lmstudio", "label": "LM Studio (local)", "base_url": "http://localhost:1234/v1", "model": ""},
    {"id": "ovms", "label": "OpenVINO Model Server", "base_url": "http://localhost:9000/v3", "model": ""},
    {"id": "nvidia", "label": "NVIDIA NIM", "base_url": "https://integrate.api.nvidia.com/v1",
     "model": "deepseek-ai/deepseek-v4-pro", "extra_body": '{"chat_template_kwargs":{"thinking":false}}'},
    {"id": "openai", "label": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    {"id": "azure", "label": "Azure OpenAI (v1 API)", "base_url": "https://YOUR-RESOURCE.openai.azure.com/openai/v1", "model": ""},
]
STT_PRESETS = [
    {"id": "openai", "label": "OpenAI Whisper", "base_url": "https://api.openai.com/v1", "model": "whisper-1"},
    {"id": "ovms", "label": "OpenVINO Model Server", "base_url": "http://localhost:9000/v3", "model": ""},
    {"id": "speaches", "label": "Speaches / faster-whisper-server", "base_url": "http://localhost:8001/v1",
     "model": "Systran/faster-whisper-small.en"},
    {"id": "groq", "label": "Groq", "base_url": "https://api.groq.com/openai/v1", "model": "whisper-large-v3-turbo"},
]


def _env_defaults() -> dict:
    def rel(v: str) -> str:
        return Path(v).name if v else ""

    llm_provider = os.getenv("LLM_PROVIDER", "").strip().lower()
    if not llm_provider:
        llm_provider = "openvino" if local_models("llm") else ("openai" if os.getenv("LLM_MODEL") else "none")
    return {
        "llm": {
            "provider": llm_provider,
            "ov_device": os.getenv("OV_LLM_DEVICE", "NPU").upper(),
            "ov_model": rel(os.getenv("OV_LLM_MODEL_DIR", "")),
            "base_url": os.getenv("LLM_BASE_URL", ""),
            "api_key": os.getenv("LLM_API_KEY", ""),
            "model": os.getenv("LLM_MODEL", ""),
            "extra_body": os.getenv("LLM_EXTRA_BODY", ""),
            "temperature": float(os.getenv("LLM_TEMPERATURE", "0.7")),
            "max_tokens": int(os.getenv("LLM_MAX_TOKENS", "700")),
            "claude_model": os.getenv("CLAUDE_MODEL", "claude-opus-5-5"),
            "claude_effort": os.getenv("CLAUDE_EFFORT", "medium"),
            # How Claude is reached: anthropic = Anthropic API key, bedrock = Amazon Bedrock (AWS credentials)
            "claude_access": os.getenv("CLAUDE_ACCESS", "").strip().lower() or _default_claude_access(),
            "bedrock_api": os.getenv("BEDROCK_API", "runtime").strip().lower(),   # runtime | mantle
            "bedrock_region": os.getenv("BEDROCK_REGION", ""),                    # blank = AWS_REGION
            "bedrock_model": os.getenv("BEDROCK_MODEL") or _env_bedrock_model(),  # blank = <geo>.anthropic.claude-opus-5-5
            "gemini_model": os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        },
        "stt": {
            "provider": os.getenv("STT_PROVIDER", "openvino" if local_models("whisper") else "none").lower(),
            "ov_device": os.getenv("OV_WHISPER_DEVICE", "GPU").upper(),
            "ov_model": rel(os.getenv("OV_WHISPER_MODEL_DIR", "")),
            "base_url": os.getenv("STT_BASE_URL", ""),
            "api_key": os.getenv("STT_API_KEY", ""),
            "model": os.getenv("STT_MODEL", "whisper-1"),
            "filler_prompt": True,
            "gemini_model": os.getenv("GEMINI_STT_MODEL", "gemini-2.5-flash"),
        },
        # Shared cloud credentials. Blank = fall back to the SDK's own env vars
        # (ANTHROPIC_API_KEY / GEMINI_API_KEY or GOOGLE_API_KEY; bedrock: AWS_BEARER_TOKEN_BEDROCK,
        # else the AWS credential chain).
        "keys": {"anthropic": "", "gemini": "", "bedrock": ""},
    }


def _default_claude_access() -> str:
    """Bedrock when CLAUDE_CODE_USE_BEDROCK=1, or when only AWS Bedrock credentials are present in the environment."""
    if os.getenv("CLAUDE_CODE_USE_BEDROCK", "").strip().lower() in ("1", "true", "yes"):
        return "bedrock"
    direct = os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")
    return "bedrock" if os.getenv("AWS_BEARER_TOKEN_BEDROCK") and not direct else "anthropic"


def _env_bedrock_model() -> str:
    """Claude Code's model variables, when they hold a Bedrock id (…anthropic.claude-…)."""
    for var in ("ANTHROPIC_DEFAULT_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL"):
        v = os.getenv(var, "").strip()
        if "anthropic.claude-" in v:
            return v
    return ""


def local_models(kind: str) -> list[str]:
    """Installed OpenVINO model folders under models/<kind>/."""
    d = MODELS / kind
    if not d.exists():
        return []
    markers = ("openvino_model.xml", "openvino_language_model.xml", "openvino_encoder_model.xml")
    return sorted(p.name for p in d.iterdir() if p.is_dir() and any((p / m).exists() for m in markers))


def resolve_model_dir(kind: str, name: str) -> str:
    """Model name (folder under models/<kind>) or absolute path -> existing directory, else first installed."""
    if name:
        p = Path(name)
        cand = p if p.is_absolute() else MODELS / kind / name
        if cand.is_dir():
            return str(cand)
    found = local_models(kind)
    return str(MODELS / kind / found[0]) if found else ""


def get() -> dict:
    global _cache
    with _lock:
        if _cache is None:
            data = _env_defaults()
            if FILE.exists():
                try:
                    saved = json.loads(FILE.read_text("utf-8"))
                    for sec in data:
                        data[sec].update({k: v for k, v in saved.get(sec, {}).items() if k in data[sec]})
                    # A Bedrock API key saved in the Anthropic field (before Bedrock access existed) belongs to Bedrock
                    if is_bedrock_key(data["keys"]["anthropic"]):
                        if not data["keys"]["bedrock"]:
                            data["keys"]["bedrock"] = data["keys"]["anthropic"]
                        data["keys"]["anthropic"] = ""
                        if "claude_access" not in saved.get("llm", {}):
                            data["llm"]["claude_access"] = "bedrock"
                except Exception:  # noqa: BLE001
                    pass
            _cache = data
        return copy.deepcopy(_cache)


def is_bedrock_key(key: str) -> bool:
    """Amazon Bedrock API keys start with ABSK (long-term) or bedrock-api-key- (short-term)."""
    return (key or "").strip().startswith(("ABSK", "bedrock-api-key-"))


def update(patch: dict) -> dict:
    """Merge a partial update. Returns the new settings."""
    global _cache
    cur = get()
    for k, v in (patch.get("keys") or {}).items():
        if k in cur["keys"] and v not in (None, "", KEEP):
            cur["keys"][k] = "" if v == CLEAR else str(v).strip()
    for sec in ("llm", "stt"):
        for k, v in (patch.get(sec) or {}).items():
            if k not in cur[sec]:
                continue
            if k == "api_key":
                if v in (None, "", KEEP):
                    continue
                v = "" if v == CLEAR else v.strip()
            elif isinstance(cur[sec][k], bool):
                v = bool(v)
            elif isinstance(cur[sec][k], float):
                v = float(v)
            elif isinstance(cur[sec][k], int):
                v = int(v)
            elif isinstance(v, str):
                v = v.strip()
            cur[sec][k] = v
    if cur["llm"]["extra_body"]:
        json.loads(cur["llm"]["extra_body"])  # validate; raises ValueError on bad JSON
    allowed = {"llm": ("openvino", "openai", "anthropic", "gemini", "none"),
               "stt": ("openvino", "openai", "gemini", "none")}
    if cur["stt"]["provider"] == "anthropic":
        raise ValueError("Claude does not accept audio input, so it cannot transcribe speech. "
                         "Use Claude as the language model and Whisper or Gemini for speech")
    if cur["llm"]["claude_effort"] not in ("low", "medium", "high", "xhigh", "max"):
        raise ValueError("Claude effort must be low, medium, high, xhigh or max")
    if is_bedrock_key(cur["keys"]["anthropic"]):
        raise ValueError("That is an Amazon Bedrock API key, not an Anthropic one: "
                         "set Access to Amazon Bedrock and paste it in the Bedrock API key field")
    if cur["llm"]["claude_access"] not in ("anthropic", "bedrock"):
        raise ValueError("Claude access must be anthropic or bedrock")
    if cur["llm"]["bedrock_api"] not in ("runtime", "mantle"):
        raise ValueError("Bedrock API must be runtime or mantle")
    for sec in ("llm", "stt"):
        if cur[sec]["provider"] not in allowed[sec]:
            raise ValueError(f"Unknown {sec} provider")
        if cur[sec]["ov_device"] not in ("NPU", "GPU", "CPU"):
            raise ValueError("Device must be NPU, GPU or CPU")
    with _lock:
        FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, indent=2), "utf-8")
        tmp.replace(FILE)
        _cache = cur
    return copy.deepcopy(cur)


def _hint(key: str) -> str:
    return ("••••" + key[-4:]) if len(key) > 8 else ("••••" if key else "")


def masked(s: dict | None = None) -> dict:
    s = copy.deepcopy(s or get())
    keys = s.pop("keys")
    for sec in s.values():
        key = sec.pop("api_key", "")
        sec["api_key_set"] = bool(key)
        sec["api_key_hint"] = _hint(key)
    env = {"anthropic": bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")),
           "gemini": bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")),
           "bedrock": _bedrock_env()}
    s["keys"] = {k: {"set": bool(v), "hint": _hint(v), "env": env[k]} for k, v in keys.items()}
    return s


def _bedrock_env() -> bool:
    from . import claude_llm

    return claude_llm.bedrock_env_credentials()


def devices() -> list[dict]:
    try:
        import openvino as ov

        core = ov.Core()
        out = []
        for d in core.available_devices:
            base = d.split(".")[0]
            if base in ("NPU", "GPU", "CPU") and base not in [x["id"] for x in out]:
                out.append({"id": base, "name": core.get_property(d, "FULL_DEVICE_NAME")})
        return sorted(out, key=lambda x: ["NPU", "GPU", "CPU"].index(x["id"]))
    except Exception:  # noqa: BLE001
        return [{"id": "CPU", "name": "CPU"}]
