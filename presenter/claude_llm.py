"""Anthropic Claude backend (official `anthropic` SDK) for script writing and coaching.

Two ways to reach Claude:
  * Anthropic API      - an Anthropic API key, model ids like `claude-opus-5-5`
  * Amazon Bedrock     - AWS credentials, model ids like `us.anthropic.claude-opus-5-5`

Claude's Messages API takes text, images and PDFs - not audio - so Claude is offered as
the language model only; speech recognition stays with Whisper or Gemini.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import anthropic

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"
EFFORTS = ["low", "medium", "high", "xhigh", "max"]
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

ACCESS = ("anthropic", "bedrock")
# runtime = bedrock-runtime InvokeModel (inference-profile ids, e.g. us.anthropic.claude-opus-5-5)
# mantle  = bedrock-mantle Messages endpoint (ids like anthropic.claude-opus-5-5)
BEDROCK_APIS = ("runtime", "mantle")
_BEDROCK_MODELS = ("claude-opus-5-5", "claude-fable-5-2", "claude-sonnet-5-5", "claude-haiku-5-5")
_BEDROCK_KEY_ENV = ("AWS_BEARER_TOKEN_BEDROCK",)


def has_credentials(api_key: str) -> bool:
    return bool(api_key or os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))


def _client(api_key: str, timeout: float = 180.0) -> anthropic.Anthropic:
    # Blank key -> the SDK resolves ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / `ant auth login` profile.
    # Public endpoint: the SDK's HTTP client honours HTTPS_PROXY, which the corporate network needs.
    kw = {"api_key": api_key} if api_key else {}
    return anthropic.Anthropic(timeout=timeout, max_retries=2, **kw)


# ---------------------------------------------------------------- Amazon Bedrock
def bedrock_region(region: str = "") -> str:
    return (region or os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "").strip()


def bedrock_env_credentials() -> bool:
    """True if AWS credentials are available outside the app (Bedrock API key or the IAM credential chain)."""
    return bool(any(os.getenv(v) for v in _BEDROCK_KEY_ENV + ("AWS_ACCESS_KEY_ID", "AWS_PROFILE",
                                                               "AWS_WEB_IDENTITY_TOKEN_FILE"))
                or (Path.home() / ".aws" / "credentials").exists() or (Path.home() / ".aws" / "config").exists())


def bedrock_has_credentials(api_key: str) -> bool:
    return bool(api_key) or bedrock_env_credentials()


def _geo(region: str) -> str:
    """Geography prefix of Bedrock cross-region inference profiles for a region."""
    r = bedrock_region(region)
    return "us" if r.startswith("us-") else "eu" if r.startswith("eu-") else "apac" if r.startswith("ap-") else "global"


def bedrock_models(api: str, region: str = "") -> list[str]:
    """Suggested model ids (Bedrock has no Models API to list them)."""
    if api == "mantle":
        return [f"anthropic.{m}" for m in _BEDROCK_MODELS]
    prefixes = dict.fromkeys((_geo(region), "global"))
    return [f"{p}.anthropic.{m}" for p in prefixes for m in _BEDROCK_MODELS]


def bedrock_default_model(api: str, region: str = "") -> str:
    return bedrock_models(api, region)[0]


def _bedrock_fallbacks(model: str) -> list[dict]:
    """Refusal-fallback chain for a Bedrock model id, keeping its prefix (us.anthropic. / anthropic. / ...)."""
    head, sep, tail = model.rpartition("claude-")
    if not sep:
        return []
    name = "claude-" + tail.split(":")[0]
    if name.startswith("claude-sonnet-5-5"):
        chain = ["claude-sonnet-5"]
    elif name.startswith(("claude-opus-5-5", "claude-fable-5", "claude-mythos-5")):
        chain = ["claude-opus-5", "claude-opus-4-8"]
    elif name == "claude-opus-5":
        chain = ["claude-opus-4-8"]
    else:
        chain = []
    return [{"model": head + m} for m in chain]


def _bedrock_client(b: dict, timeout: float = 180.0):
    """b = {"api": runtime|mantle, "region": str, "key": str}. Blank key -> AWS_BEARER_TOKEN_BEDROCK, else the
    default AWS credential chain (SigV4 signing, needs `pip install "anthropic[bedrock]"`)."""
    region = bedrock_region(b.get("region", ""))
    if not region:
        raise RuntimeError("Claude on Bedrock: set the AWS region in Settings, e.g. us-east-2 (or AWS_REGION)")
    kw: dict = {"aws_region": region, "timeout": timeout, "max_retries": 2}
    if b.get("key"):
        kw["api_key"] = b["key"]
    # Bedrock has no server-side `fallbacks`; the SDK middleware retries a classifier refusal client-side.
    chain = _bedrock_fallbacks(b["model"])
    if chain:
        kw["middleware"] = [anthropic.BetaRefusalFallbackMiddleware(chain)]
    cls = anthropic.AnthropicBedrockMantle if b.get("api") == "mantle" else anthropic.AnthropicBedrock
    return cls(**kw)


def chat(system: str, user: str, *, api_key: str, model: str, effort: str, bedrock: dict | None = None) -> str:
    """One chat turn. `bedrock` = {"api", "region", "key", "model"} routes the call through Amazon Bedrock."""
    effort = effort if effort in EFFORTS else "medium"
    kw: dict = dict(
        max_tokens=16000,                    # thinking shares this budget; replies themselves are short
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": effort},    # Opus 5.5 thinking is always on; effort controls depth/latency
    )
    if bedrock:
        b = {**bedrock, "model": bedrock.get("model") or bedrock_default_model(bedrock.get("api", ""), bedrock.get("region", ""))}
        try:
            resp = _bedrock_client(b).beta.messages.create(model=b["model"], **kw)
        except ModuleNotFoundError as e:  # SigV4 signing needs boto3
            raise RuntimeError('Claude on Bedrock: IAM credentials need boto3 - run: pip install "anthropic[bedrock]" '
                               "(or paste a Bedrock API key in Settings)") from e
    else:
        if (api_key or "").startswith(("ABSK", "bedrock-api-key-")):
            raise RuntimeError("Claude: that is an Amazon Bedrock API key - set Access to Amazon Bedrock "
                               "and paste it in the Bedrock API key field")
        if not has_credentials(api_key):
            raise RuntimeError("Claude: no Anthropic API key. Add one, or if you reach Claude through AWS "
                               "set Access to Amazon Bedrock")
        resp = _client(api_key).beta.messages.create(
            model=model or DEFAULT_MODEL,
            betas=[_FALLBACK_BETA],
            fallbacks="default",             # on a classifier refusal, the API re-runs on a fallback model
            **kw,
        )
    if resp.stop_reason == "refusal":
        cat = getattr(resp.stop_details, "category", None) if resp.stop_details else None
        raise RuntimeError(f"Claude declined this request (refusal{': ' + cat if cat else ''})")
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    if resp.stop_reason == "max_tokens" and not text:
        raise RuntimeError("Claude hit max_tokens before answering - try a lower effort")
    log.info("Claude %s%s (effort %s) replied, request %s", resp.model, " via Bedrock" if bedrock else "", effort,
             resp._request_id)
    return text


def list_models(api_key: str) -> list[str]:
    if not has_credentials(api_key):
        raise RuntimeError("no Anthropic API key. Add one, or if you reach Claude through AWS set Access to Amazon Bedrock")
    return [m.id for m in _client(api_key, timeout=20).models.list()]


def _detail(e: Exception) -> str:
    """The service's own message (AWS names the missing permission / model), without the SDK's wrapper text."""
    body = getattr(e, "body", None)
    if isinstance(body, dict):
        inner = body.get("error") if isinstance(body.get("error"), dict) else body
        msg = inner.get("message") or inner.get("Message")
        if msg:
            return str(msg)[:300]
    return str(getattr(e, "message", e))[:300]


def friendly_error(e: Exception, bedrock: bool = False) -> str:
    who = "Claude on Bedrock" if bedrock else "Claude"
    aws = f" - {_detail(e)}" if bedrock else ""
    if isinstance(e, anthropic.AuthenticationError):
        return f"{who}: invalid AWS credentials or Bedrock API key{aws}" if bedrock else "Claude: invalid API key"
    if isinstance(e, anthropic.PermissionDeniedError):
        return f"{who}: access denied{aws}" if bedrock else "Claude: API key lacks permission for this model"
    if isinstance(e, anthropic.NotFoundError):
        return f"{who}: model not found - check the model id and region{aws}" if bedrock \
            else "Claude: model not found - use ↻ List to pick one"
    if isinstance(e, anthropic.RateLimitError):
        return f"{who}: rate limited - wait a moment and retry"
    if isinstance(e, anthropic.BadRequestError):
        return f"{who}: bad request - {_detail(e) if bedrock else e.message}"
    if isinstance(e, anthropic.APIStatusError):
        return f"{who}: API error {e.status_code} - {e.message}"
    if isinstance(e, anthropic.APIConnectionError):
        return f"{who}: cannot reach {'the Bedrock endpoint' if bedrock else 'api.anthropic.com'} (check network / HTTPS_PROXY)"
    return f"{who}: {e}"
