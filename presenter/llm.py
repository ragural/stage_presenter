"""LLM backends: local OpenVINO (NPU/GPU/CPU) or any OpenAI-compatible endpoint.

Configuration comes from presenter.settings (UI ⚙ Settings, defaults from .env).
"""
from __future__ import annotations

import gc
import json
import logging
import os
import threading
import time
from pathlib import Path

from . import settings

log = logging.getLogger(__name__)
BASE = Path(__file__).resolve().parent.parent


def _first_model_dir(sub: str) -> str:  # kept for backwards compatibility
    return settings.resolve_model_dir(sub, "")


def device_chain(pref: str) -> list[str]:
    """Preferred device first, then the remaining accelerators, CPU last."""
    order = [pref.upper()] + [d for d in ("NPU", "GPU", "CPU") if d != pref.upper()]
    try:
        import openvino as ov
        avail = {d.split(".")[0] for d in ov.Core().available_devices}
        order = [d for d in order if d in avail]
    except Exception:  # noqa: BLE001
        pass
    return order


def cfg() -> dict:
    return settings.get()["llm"]


def provider() -> str:
    return cfg()["provider"]


def ov_model_dir() -> str:
    return settings.resolve_model_dir("llm", cfg()["ov_model"])


class _OVLLM:
    def __init__(self) -> None:
        self.pipe = None
        self.device = None      # device actually in use
        self.model = None
        self.key = None         # (path, requested device) the pipe was built for
        self.error: str | None = None
        self.lock = threading.RLock()
        self.loading = False

    def wanted(self) -> tuple[str, str]:
        return ov_model_dir(), cfg()["ov_device"]

    def unload(self) -> None:
        with self.lock:
            if self.pipe is not None:
                log.info("Unloading LLM %s from %s", self.model, self.device)
            self.pipe, self.device, self.model, self.key = None, None, None, None
            gc.collect()

    def load(self):
        want = self.wanted()
        if self.pipe is not None and self.key == want:
            return self.pipe
        with self.lock:
            if self.pipe is not None and self.key == want:
                return self.pipe
            self.unload()
            import openvino_genai as ov_genai

            path, pref = want
            if not path:
                self.error = "No local model installed. Run: python download_models.py --only llm"
                raise RuntimeError(self.error)
            cache = BASE / "models" / "cache"
            cache.mkdir(parents=True, exist_ok=True)
            errors = []
            self.loading, self.error = True, None
            try:
                for dev in device_chain(pref):
                    opts: dict = {"CACHE_DIR": str(cache)}
                    if dev == "NPU":
                        opts.update({
                            "MAX_PROMPT_LEN": int(os.getenv("OV_NPU_MAX_PROMPT_LEN", "3072")),
                            "MIN_RESPONSE_LEN": int(os.getenv("OV_NPU_MIN_RESPONSE_LEN", "768")),
                        })
                    t0 = time.time()
                    try:
                        log.info("Loading %s on %s …", Path(path).name, dev)
                        self.pipe = ov_genai.LLMPipeline(path, dev, **opts)
                        self.device, self.model, self.key = dev, Path(path).name, want
                        log.info("LLM ready on %s in %.1fs", dev, time.time() - t0)
                        if dev != pref:
                            self.error = f"{pref} unavailable, fell back to {dev}"
                        return self.pipe
                    except Exception as e:  # noqa: BLE001
                        errors.append(f"{dev}: {e}")
                        log.warning("LLM on %s failed: %s", dev, e)
                self.error = "; ".join(errors)
                raise RuntimeError("Could not load OpenVINO LLM - " + self.error)
            finally:
                self.loading = False

    def chat(self, system: str, user: str, max_tokens: int, temperature: float) -> str:
        import openvino_genai as ov_genai

        with self.lock:  # one generation at a time on the accelerator
            pipe = self.load()
            if "qwen3" in (self.model or "").lower():
                user += " /no_think"
            gc_ = ov_genai.GenerationConfig()
            gc_.max_new_tokens = max_tokens
            if temperature > 0:
                gc_.do_sample, gc_.temperature, gc_.top_p = True, temperature, 0.9
            gc_.repetition_penalty = 1.05
            history = ov_genai.ChatHistory([{"role": "system", "content": system},
                                            {"role": "user", "content": user}])
            res = pipe.generate(history, gc_)
        return res.texts[0] if hasattr(res, "texts") else str(res)


OV = _OVLLM()


def status() -> dict:
    c = cfg()
    p = c["provider"]
    if p == "openvino":
        path = ov_model_dir()
        return {"provider": "openvino", "enabled": bool(path),
                "model": OV.model or (Path(path).name if path else None),
                "device": OV.device or c["ov_device"],
                "loaded": OV.pipe is not None and OV.key == OV.wanted(),
                "loading": OV.loading, "error": OV.error}
    if p == "anthropic" and c["claude_access"] == "bedrock":
        from . import claude_llm
        region = claude_llm.bedrock_region(c["bedrock_region"])
        ok = bool(region) and claude_llm.bedrock_has_credentials(settings.get()["keys"]["bedrock"])
        return {"provider": "anthropic", "enabled": ok, "device": "Claude · Bedrock", "loaded": ok, "loading": False,
                "model": c["bedrock_model"] or claude_llm.bedrock_default_model(c["bedrock_api"], region),
                "error": None if ok else "Set the AWS region in Settings, e.g. us-east-2 (or AWS_REGION)" if not region
                else "Add a Bedrock API key in Settings (or set AWS_BEARER_TOKEN_BEDROCK / AWS credentials)"}
    if p == "anthropic":
        from . import claude_llm
        ok = claude_llm.has_credentials(settings.get()["keys"]["anthropic"])
        return {"provider": "anthropic", "enabled": ok, "model": c["claude_model"] or claude_llm.DEFAULT_MODEL,
                "device": "Claude API", "loaded": ok, "loading": False,
                "error": None if ok else "Add an Anthropic API key in Settings (or set ANTHROPIC_API_KEY)"}
    if p == "gemini":
        from . import gemini_ai
        ok = gemini_ai.has_credentials(settings.get()["keys"]["gemini"])
        return {"provider": "gemini", "enabled": ok, "model": c["gemini_model"] or gemini_ai.DEFAULT_MODEL,
                "device": "Gemini API", "loaded": ok, "loading": False,
                "error": None if ok else "Add a Gemini API key in Settings (or set GEMINI_API_KEY)"}
    if p == "openai":
        return {"provider": "openai", "enabled": bool(c["model"] and c["base_url"]),
                "model": c["model"], "device": "remote", "loaded": True, "loading": False,
                "error": None if c["model"] and c["base_url"] else "Endpoint URL and model are required"}
    return {"provider": "none", "enabled": False, "model": None, "device": None, "loaded": False, "loading": False, "error": None}


def enabled() -> bool:
    return status()["enabled"]


def _is_local(url: str) -> bool:
    """localhost / private-LAN / *.local hosts must bypass corporate HTTP(S)_PROXY settings."""
    import ipaddress
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    if host in ("localhost", "") or host.endswith((".local", ".localhost", ".lan", ".internal")):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_loopback or ip.is_private or ip.is_link_local
    except ValueError:
        return False


def openai_client(base_url: str, api_key: str, timeout: float = 180):
    import openai

    kw = {}
    if base_url and _is_local(base_url):
        kw["http_client"] = openai.DefaultHttpx2Client(trust_env=False, timeout=timeout)
    return openai.OpenAI(base_url=base_url or None, api_key=api_key or "not-needed", timeout=timeout,
                         max_retries=0, **kw)


def chat(system: str, user: str, max_tokens: int | None = None, temperature: float | None = None,
         override: dict | None = None) -> str:
    """Run one chat turn. `override` lets the settings 'Test' button try unsaved values."""
    c = {**cfg(), **(override or {})}
    keys = c.get("_keys") or settings.get()["keys"]
    temp = c["temperature"] if temperature is None else temperature
    max_tokens = max_tokens or c["max_tokens"]
    if c["provider"] == "anthropic":
        from . import claude_llm
        bedrock = None
        if c["claude_access"] == "bedrock":
            bedrock = {"api": c["bedrock_api"], "region": c["bedrock_region"], "key": keys["bedrock"],
                       "model": c["bedrock_model"]}
        try:  # Opus 5.5 takes no temperature/max-token tuning here: effort is the control
            return claude_llm.chat(system, user, api_key=keys["anthropic"], model=c["claude_model"],
                                   effort=c["claude_effort"], bedrock=bedrock)
        except RuntimeError:
            raise
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(claude_llm.friendly_error(e, bedrock=bool(bedrock))) from e
    if c["provider"] == "gemini":
        from . import gemini_ai
        try:
            return gemini_ai.chat(system, user, api_key=keys["gemini"], model=c["gemini_model"],
                                  max_tokens=max_tokens, temperature=temp)
        except RuntimeError:
            raise
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(gemini_ai.friendly_error(e)) from e
    if c["provider"] == "openvino":
        return OV.chat(system, user, max_tokens, temp)
    if c["provider"] == "openai":
        if not c["base_url"] or not c["model"]:
            raise RuntimeError("Endpoint URL and model are required")
        extra = (c.get("extra_body") or "").strip()
        resp = openai_client(c["base_url"], c["api_key"]).chat.completions.create(
            model=c["model"],
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=temp, max_tokens=max_tokens,
            **({"extra_body": json.loads(extra)} if extra else {}),
        )
        return resp.choices[0].message.content or ""
    raise RuntimeError("No LLM configured")
