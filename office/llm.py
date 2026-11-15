"""Model routing. An employee asks for a TIER (fast / smart), never a model name; the tier is a fallback list in
config/models.yaml. Providers without a key, or Ollama servers that are down or lack the model, are skipped.

Providers: ollama (this machine's Ollama, OLLAMA_URL), ollama_pc / ollama_mac (the named machine's Ollama from
config/office.yaml, over Tailscale), groq, gemini, openrouter, nvidia, litellm. Stdlib only."""
import json, os, re, time, urllib.request, urllib.error
from . import config

config.load_env()

ENDPOINTS = {
    "groq": ("https://api.groq.com/openai/v1/chat/completions", "GROQ_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "GEMINI_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1/chat/completions", "OPENROUTER_API_KEY"),
    "nvidia": ("https://integrate.api.nvidia.com/v1/chat/completions", "NVIDIA_API_KEY"),
    "litellm": ("http://127.0.0.1:4000/v1/chat/completions", "LITELLM_MASTER_KEY"),
}
RETRYABLE = {429, 500, 502, 503, 504}
BACKOFF = (3, 9)
_sleep = time.sleep
_tags_cache = {}   # url -> (ts, set(models))


def ollama_url(provider):
    if provider == "ollama":
        return (os.environ.get("OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/")
    name = provider.split("_", 1)[1]
    url = (config.office().get("machines") or {}).get(name, {}).get("ollama")
    return url.rstrip("/") if url else None


def ollama_models(url, ttl=60):
    """Set of model names an Ollama server has, cached; empty set when unreachable."""
    now = time.time()
    hit = _tags_cache.get(url)
    if hit and now - hit[0] < ttl:
        return hit[1]
    try:
        with urllib.request.urlopen(url + "/api/tags", timeout=3) as r:
            names = {m["name"] for m in json.load(r).get("models", [])}
    except (OSError, ValueError, KeyError):
        names = set()
    _tags_cache[url] = (now, names)
    return names


def available(provider, model=None):
    if provider.startswith("ollama"):
        url = ollama_url(provider)
        if not url:
            return False
        have = ollama_models(url)
        if not have:
            return False
        return model is None or model in have or (model + ":latest") in have
    keyvar = ENDPOINTS.get(provider, (None, None))[1]
    if not (keyvar and os.environ.get(keyvar)):
        return False
    return _cloud_ok(provider)


_cloud_cache = {}   # provider -> (ts, ok)


def _cloud_ok(provider, ttl=600):
    """A dead key (401/403) or an unreachable endpoint is skipped for ten minutes instead of costing every call a
    failed round trip. The probe is one GET of the provider's models list with a short timeout."""
    now = time.time()
    hit = _cloud_cache.get(provider)
    if hit and now - hit[0] < ttl:
        return hit[1]
    url, keyvar = ENDPOINTS[provider]
    req = urllib.request.Request(url.rsplit("/chat/completions", 1)[0] + "/models",
                                 headers={"Authorization": f"Bearer {os.environ.get(keyvar, '')}"})
    try:
        with urllib.request.urlopen(req, timeout=5):
            ok = True
    except urllib.error.HTTPError as e:
        ok = e.code not in (401, 403)
    except (OSError, ValueError):
        ok = False
    _cloud_cache[provider] = (now, ok)
    return ok


def _strip_think(text):
    return re.sub(r"<think>.*?</think>\s*", "", text or "", flags=re.S).strip()


def _ollama(url, model, messages, timeout, max_tokens):
    payload = {"model": model, "messages": messages, "stream": False, "think": False}
    if max_tokens:
        payload["options"] = {"num_predict": max_tokens}
    req = urllib.request.Request(url + "/api/chat", json.dumps(payload).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 400:   # some models reject the think flag
            payload.pop("think", None)
            req = urllib.request.Request(url + "/api/chat", json.dumps(payload).encode(), {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.load(r)
        else:
            raise
    text = _strip_think(data["message"]["content"])
    return text, data.get("prompt_eval_count", 0) + data.get("eval_count", 0)


def _openai(provider, model, messages, timeout, max_tokens):
    url, keyvar = ENDPOINTS[provider]
    key = os.environ.get(keyvar)
    if not key:
        raise RuntimeError(f"{keyvar} not set")
    payload = {"model": model, "messages": messages}
    if max_tokens:
        payload["max_tokens"] = max_tokens
    req = urllib.request.Request(url, json.dumps(payload).encode(),
                                 {"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    return _strip_think(data["choices"][0]["message"]["content"]), data.get("usage", {}).get("total_tokens", 0)


def _call(provider, model, messages, timeout, max_tokens):
    if provider.startswith("ollama"):
        return _ollama(ollama_url(provider), model, messages, max(timeout, 900), max_tokens)
    return _openai(provider, model, messages, timeout, max_tokens)


def chain(tier, prefer=None):
    cfg = config.models()
    tiers = cfg.get("tiers") or {}
    items = list(tiers.get(tier) or tiers.get("fast") or [])
    if prefer and prefer.get("model"):
        items = [prefer] + [m for m in items if m != prefer]
    return items


def chat(tier, messages, prefer=None, timeout=180, max_tokens=None):
    """Returns (text, meta). meta = {provider, model, tokens, secs, tried}. Raises RuntimeError when nothing answered."""
    errors = []
    for m in chain(tier, prefer):
        prov, model = m["provider"], m["model"]
        if not available(prov, model if prov.startswith("ollama") else None):
            errors.append(f"{prov}/{model}: unavailable")
            continue
        for attempt in range(len(BACKOFF) + 1):
            t0 = time.time()
            try:
                text, tokens = _call(prov, model, messages, timeout, max_tokens)
                return text, {"provider": prov, "model": model, "tokens": tokens, "secs": round(time.time() - t0, 1), "tried": errors}
            except urllib.error.HTTPError as e:
                errors.append(f"{prov}/{model}: HTTP {e.code}")
                if e.code in RETRYABLE and attempt < len(BACKOFF):
                    _sleep(BACKOFF[attempt])
                    continue
                break
            except (OSError, ValueError, KeyError) as e:
                errors.append(f"{prov}/{model}: {type(e).__name__}: {str(e)[:120]}")
                break
    raise RuntimeError("no model answered: " + "; ".join(errors[-6:]))


def json_from(text):
    """Best-effort JSON object out of a model reply (handles ```json fences and chatter around the object)."""
    if not text:
        raise ValueError("empty reply")
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        raise ValueError("no JSON object in reply")
    return json.loads(m.group(0))
