"""Push to the phone through ntfy.sh (NTFY_TOPIC from .env, same topic AgentStack uses). Silent no-op when unset."""
import os, urllib.request
from . import config


def push(title, body, priority="default", tags=None):
    config.load_env()
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return False
    server = (os.environ.get("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")
    headers = {"Title": title.encode("ascii", "ignore").decode(), "Priority": priority}
    if tags:
        headers["Tags"] = ",".join(tags)
    req = urllib.request.Request(f"{server}/{topic}", body.encode("utf-8"), headers)
    try:
        with urllib.request.urlopen(req, timeout=10):
            return True
    except OSError:
        return False
