import os
import json
import time
from pathlib import Path
import requests
from dotenv import load_dotenv

load_dotenv()

APP_CLIENT_ID = os.getenv("ID_Connect")
APP_SECRET = os.getenv("Protect_key")
SERVICE_KEY = os.getenv("Service_key")

TOKEN_CACHE_FILE = Path(__file__).parent / "sbis_token.json"


class SbisAuthError(Exception):
    pass


def _fetch_new_token() -> dict:
    """
    Получает новый сервисный токен Saby через:
    POST https://online.sbis.ru/oauth/service/
    """
    if not (APP_CLIENT_ID and APP_SECRET and SERVICE_KEY):
        raise SbisAuthError("Заполни ID_Connect / Protect_key / Service_key в .env")

    url = "https://online.sbis.ru/oauth/service/"

    payload = {
        "app_client_id": APP_CLIENT_ID,
        "app_secret": APP_SECRET,
        "secret_key": SERVICE_KEY,
    }

    resp = requests.post(url, json=payload, timeout=15)
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        raise SbisAuthError(
            f"Ошибка получения токена: {e}, тело: {resp.text}"
        ) from e

    data = resp.json()
    token = data.get("token")
    if not token:
        raise SbisAuthError(f"Ответ без token: {data}")

    # срок жизни токена возьмём 1 час — запас 1 минута
    exp_ts = int(time.time()) + 3600 - 60

    token_data = {
        "token": token,
        "exp": exp_ts,
    }

    try:
        with open(TOKEN_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(token_data, f, ensure_ascii=False, indent=2)
    except:
        pass

    return token_data


def _load_cached_token() -> dict | None:
    if not TOKEN_CACHE_FILE.exists():
        return None
    try:
        with open(TOKEN_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "token" in data and "exp" in data:
            return data
    except:
        return None
    return None


def get_token() -> str:
    cached = _load_cached_token()
    now = int(time.time())

    if cached and cached.get("exp", 0) > now:
        return cached["token"]

    token_data = _fetch_new_token()
    return token_data["token"]


def get_auth_headers() -> dict:
    """Готовые заголовки для API Saby (Retail)."""
    token = get_token()
    return {
        "X-SBISAccessToken": token
    }


if __name__ == "__main__":
    t = get_token()
    print("Токен:", t[:10], "...", t[-10:])
