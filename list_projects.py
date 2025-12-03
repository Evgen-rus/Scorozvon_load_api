import os
import sys
from typing import List, Dict, Any

import requests
from dotenv import load_dotenv


TOKEN_URL = "https://api.skorozvon.ru/oauth/token"
CALL_PROJECTS_URL = "https://api.skorozvon.ru/api/v2/call_projects"


def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        print(f"Отсутствует переменная окружения {name}. Проверьте файл .env", file=sys.stderr)
        sys.exit(1)
    return value


def get_access_token(username: str, api_key: str, client_id: str, client_secret: str) -> str:
    payload = {
        "grant_type": "password",
        "username": username,
        "api_key": api_key,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    # Логи в терминал
    print(f"AUTH REQUEST: POST {TOKEN_URL} data={payload}")
    resp = requests.post(TOKEN_URL, data=payload, timeout=30)
    print(f"AUTH RESPONSE: status={resp.status_code} body={resp.text}")
    try:
        resp.raise_for_status()
    except requests.HTTPError:
        print(f"Не удалось получить токен: {resp.status_code} {resp.text}", file=sys.stderr)
        sys.exit(1)
    data = resp.json()
    token = data.get("access_token")
    if not token:
        print(f"В ответе нет access_token: {data}", file=sys.stderr)
        sys.exit(1)
    return token


def extract_projects(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "items", "projects"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


def get_display_name(project: Dict[str, Any]) -> str:
    for key in ("name", "title", "project_name"):
        if project.get(key):
            return str(project[key])
    return "(без названия)"


def list_all_projects(access_token: str, per_page: int = 10) -> List[Dict[str, Any]]:
    headers = {"Authorization": f"Bearer {access_token}"}
    page = 1
    result: List[Dict[str, Any]] = []
    while True:
        params = {"page": page, "per_page": per_page}
        print(f"REQUEST: GET {CALL_PROJECTS_URL} params={params}")
        resp = requests.get(CALL_PROJECTS_URL, headers=headers, params=params, timeout=30)
        print(f"RESPONSE: status={resp.status_code} body={resp.text}")
        if resp.status_code != 200:
            print(f"Ошибка при получении проектов: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
        chunk = extract_projects(resp.json())
        if not chunk:
            break
        result.extend(chunk)
        if len(chunk) < per_page:
            break
        page += 1
    return result


def main() -> None:
    load_dotenv()
    username = get_required_env("SKOROZVON_LOGIN")
    api_key = get_required_env("SKOROZVON_API_KEY")
    client_id = get_required_env("SKOROZVON_APP_ID")
    client_secret = get_required_env("SKOROZVON_APP_KEY")

    token = get_access_token(username, api_key, client_id, client_secret)
    projects = list_all_projects(token)

    if not projects:
        print("Проекты не найдены.")
        return

    print("Список проектов (id — имя):")
    for p in projects:
        pid = p.get("id", p.get("uuid", "?"))
        name = get_display_name(p)
        print(f"{pid} — {name}")


if __name__ == "__main__":
    main()


