import os
import sys
from typing import Any, Dict

import requests
from dotenv import load_dotenv


TOKEN_URL = "https://api.skorozvon.ru/oauth/token"
CALL_PROJECTS_URL = "https://api.skorozvon.ru/api/v2/call_projects"
LEADS_URL = "https://api.skorozvon.ru/api/v2/leads"


def get_required_env(name: str) -> str:
    """Получение обязательной переменной окружения."""
    value = os.getenv(name)
    if not value:
        print(f"Отсутствует переменная окружения {name}. Проверьте файл .env", file=sys.stderr)
        sys.exit(1)
    return value


def get_access_token(username: str, api_key: str, client_id: str, client_secret: str) -> str:
    """Получение access_token по данным учетной записи."""
    payload = {
        "grant_type": "password",
        "username": username,
        "api_key": api_key,
        "client_id": client_id,
        "client_secret": client_secret,
    }
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


def extract_project(payload: Any) -> Dict[str, Any]:
    """Аккуратно вытащить один проект из ответа API."""
    if isinstance(payload, dict):
        if "data" in payload and isinstance(payload["data"], dict):
            return payload["data"]
        return payload
    return {}


def fetch_project_by_id(project_id: str, access_token: str) -> Dict[str, Any]:
    """Получить информацию о проекте по его ID.

    Сначала пробуем прямой запрос /call_projects/{id}.
    Если формат API отличается, можно будет доработать под фактический ответ.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{CALL_PROJECTS_URL}/{project_id}"
    print(f"REQUEST: GET {url}")
    resp = requests.get(url, headers=headers, timeout=30)
    print(f"RESPONSE: status={resp.status_code} body={resp.text}")

    if resp.status_code == 404:
        return {}
    if resp.status_code != 200:
        print(f"Ошибка при получении проекта: {resp.status_code} {resp.text}", file=sys.stderr)
        return {}

    return extract_project(resp.json())


def get_leads_summary(project_id: str, access_token: str) -> Dict[str, Any]:
    """Краткая сводка по лидам проекта: сколько всего и первые несколько штук."""
    headers = {"Authorization": f"Bearer {access_token}"}

    params = {"call_project_id": project_id, "page": 1, "per_page": 1}
    print(f"REQUEST: GET {LEADS_URL} params={params}")
    resp = requests.get(LEADS_URL, headers=headers, params=params, timeout=30)
    print(f"RESPONSE: status={resp.status_code} body={resp.text}")
    if resp.status_code != 200:
        return {"total": 0, "sample": []}

    data = resp.json()
    pagination = data.get("pagination") or {}
    total = pagination.get("total", 0)

    # Берем первые несколько лидов для примера, если они есть
    sample: list[Dict[str, Any]] = []
    if total > 0:
        params = {"call_project_id": project_id, "page": 1, "per_page": min(5, total)}
        print(f"REQUEST: GET {LEADS_URL} params={params}")
        resp = requests.get(LEADS_URL, headers=headers, params=params, timeout=30)
        print(f"RESPONSE: status={resp.status_code} body={resp.text}")
        if resp.status_code == 200:
            items = data.get("data") or []
            if isinstance(items, list):
                sample = items

    return {"total": total, "sample": sample}


def print_project_info(project: Dict[str, Any]) -> None:
    """Вывести проект в читаемом виде."""
    print("\n=== Информация о проекте ===")
    print(f"ID: {project.get('id')}")
    print(f"UUID: {project.get('uuid')}")
    print(f"Название: {project.get('title')}")
    print(f"Состояние: {project.get('state')} / {project.get('substate')}")
    print(f"Описание: {project.get('description') or '—'}")
    print(f"Тип: {project.get('type')}")
    print(f"Телефон проекта: {project.get('phone') or '—'}")
    print(f"ID сценария: {project.get('scenario_id')}")
    print(f"Режим дозвона: {project.get('dialing_mode')}")
    print(f"Таймаут ожидания ответа: {project.get('pickup_timeout')}")
    print(f"Макс. попыток дозвона: {project.get('max_attempts_to_client')}")

    client_schedule = project.get("client_schedule") or {}
    project_schedule = project.get("project_schedule") or {}
    if client_schedule:
        print(f"Окно звонков для клиента: {client_schedule}")
    if project_schedule:
        print(f"Расписание проекта: {project_schedule}")


def print_leads_summary(summary: Dict[str, Any]) -> None:
    """Вывести краткую сводку по лидам проекта."""
    total = summary.get("total", 0)
    print("\n=== Лиды проекта ===")
    print(f"Всего лидов в проекте: {total}")
    sample = summary.get("sample") or []
    if not sample:
        return

    print("\nПервые лиды (пример):")
    for idx, lead in enumerate(sample, start=1):
        name = lead.get("name", "без имени")
        phones = lead.get("phones", "")
        comment = lead.get("comment") or "—"
        tags = lead.get("tags") or []
        print(f"\nЛид #{idx}")
        print(f"  ID: {lead.get('id')}")
        print(f"  Имя: {name}")
        print(f"  Телефон(ы): {phones}")
        print(f"  Комментарий: {comment}")
        print(f"  Теги: {tags}")


def main() -> None:
    load_dotenv()

    username = get_required_env("SKOROZVON_LOGIN")
    api_key = get_required_env("SKOROZVON_API_KEY")
    client_id = get_required_env("SKOROZVON_APP_ID")
    client_secret = get_required_env("SKOROZVON_APP_KEY")

    token = get_access_token(username, api_key, client_id, client_secret)

    # Здесь вы просто меняете значение на ID, который прислал клиент
    project_id = "30000191458"

    print(f"\nИщу проект с ID {project_id}...")
    project = fetch_project_by_id(project_id, token)
    if not project:
        print(f"Проект с ID {project_id} не найден или нет доступа.")
        return

    print_project_info(project)

    summary = get_leads_summary(project_id, token)
    print_leads_summary(summary)


if __name__ == "__main__":
    main()


