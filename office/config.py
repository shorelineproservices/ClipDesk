"""Config and secrets. YAML files in config/, secrets from .env (never committed).

Secrets are looked up in this order and the first file that defines a key wins: AgentOffice/.env, then the sibling
projects' .env files (AgentStack, AgentFactory) so one key works for every project on this machine. On the Mac the
NVIDIA key is read from ~/.config/homebase/keys/nvidia.key (where homebase keeps it) when NVIDIA_API_KEY is unset."""
import os, threading
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
_loaded = False
_lock = threading.Lock()


def load_env():
    """Thread-safe: the server's first requests arrive from several threads at once, and a caller must never see
    the loaded flag before the variables are actually set (that handed out a random token once)."""
    global _loaded
    if _loaded:
        return
    with _lock:
        if _loaded:
            return
        _load_env_files()
        _loaded = True


def _load_env_files():
    candidates = [ROOT / ".env", ROOT.parent / "AgentStack" / ".env", ROOT.parent / "AgentFactory" / ".env"]
    for f in candidates:
        if not f.is_file():
            continue
        for line in f.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if v and not os.environ.get(k):
                os.environ[k] = v
    nv = Path.home() / ".config" / "homebase" / "keys" / "nvidia.key"
    if not os.environ.get("NVIDIA_API_KEY") and nv.is_file():
        os.environ["NVIDIA_API_KEY"] = nv.read_text().strip()


def _yaml(name, default=None):
    f = CONFIG / name
    if not f.is_file():
        return default if default is not None else {}
    return yaml.safe_load(f.read_text(encoding="utf-8")) or (default if default is not None else {})


def office():
    return _yaml("office.yaml")


def models():
    return _yaml("models.yaml")


def schedule():
    return _yaml("schedule.yaml", [])


def business():
    return _yaml("business.yaml")


def pricing():
    return _yaml("pricing.yaml")


def machine_name():
    """Which machine is this? OFFICE_MACHINE wins, else a guess from the OS (pc on Windows, mac on Linux)."""
    return os.environ.get("OFFICE_MACHINE") or ("pc" if os.name == "nt" else "mac")


def token():
    """Shared secret for every write to the office API. Generated once into data/token when nothing sets OFFICE_TOKEN."""
    load_env()
    t = os.environ.get("OFFICE_TOKEN")
    if t:
        return t
    f = Path(os.environ.get("OFFICE_DATA") or (ROOT / "data")) / "token"
    if f.is_file():
        return f.read_text().strip()
    import secrets
    f.parent.mkdir(parents=True, exist_ok=True)
    t = secrets.token_urlsafe(24)
    f.write_text(t)
    try:
        os.chmod(f, 0o600)
    except OSError:
        pass
    return t
