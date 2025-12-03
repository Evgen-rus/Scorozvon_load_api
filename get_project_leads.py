import os
import sys
from typing import List, Dict, Any

import requests
from dotenv import load_dotenv


TOKEN_URL = "https://api.skorozvon.ru/oauth/token"
CALL_PROJECTS_URL = "https://api.skorozvon.ru/api/v2/call_projects"


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


def extract_projects(payload: Any) -> List[Dict[str, Any]]:
    """Извлечение списка проектов из ответа API."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "items", "projects"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


def get_display_name(project: Dict[str, Any]) -> str:
    """Получение отображаемого имени проекта."""
    for key in ("name", "title", "project_name"):
        if project.get(key):
            return str(project[key])
    return "(без названия)"


def list_all_projects(access_token: str, per_page: int = 10) -> List[Dict[str, Any]]:
    """Получение всех проектов с пагинацией."""
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


def select_project(projects: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Интерактивный выбор проекта из списка."""
    if not projects:
        print("Проекты не найдены.")
        sys.exit(1)

    print("\nСписок проектов:")
    for i, p in enumerate(projects, 1):
        pid = p.get("id", p.get("uuid", "?"))
        name = get_display_name(p)
        print(f"{i}. {pid} — {name}")

    while True:
        try:
            choice = input("\nВыберите номер проекта (1-" + str(len(projects)) + "): ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(projects):
                selected = projects[idx]
                pid = selected.get("id", selected.get("uuid", "?"))
                name = get_display_name(selected)
                print(f"Выбран проект: {pid} — {name}")
                return selected
            else:
                print(f"Неверный номер. Введите число от 1 до {len(projects)}")
        except ValueError:
            print("Введите корректное число")
        except KeyboardInterrupt:
            print("\nОтмена выбора")
            sys.exit(0)


def extract_leads(payload: Any) -> List[Dict[str, Any]]:
    """Извлечение списка лидов из ответа API."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "items", "leads", "results"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


def get_project_leads(project_id: str, access_token: str, per_page: int = 200) -> List[Dict[str, Any]]:
    """Получение всех лидов проекта с пагинацией."""
    headers = {"Authorization": f"Bearer {access_token}"}
    base_url = "https://api.skorozvon.ru/api/v2/leads"

    page = 1
    result: List[Dict[str, Any]] = []

    while True:
        params = {"call_project_id": project_id, "page": page, "per_page": per_page}
        print(f"REQUEST: GET {base_url} params={params}")
        resp = requests.get(base_url, headers=headers, params=params, timeout=30)
        print(f"RESPONSE: status={resp.status_code} body={resp.text}")

        if resp.status_code != 200:
            print(f"Ошибка при получении лидов: {resp.status_code} {resp.text}", file=sys.stderr)
            break

        leads = extract_leads(resp.json())
        if not leads:
            break

        result.extend(leads)

        # Проверяем, есть ли еще страницы
        if len(leads) < per_page:
            break

        page += 1

    return result


def display_lead_details(lead: Dict[str, Any], index: int) -> None:
    """Отображение детальной информации о лиде."""
    print(f"\n--- Лид #{index + 1} ---")

    # Основные поля
    lead_id = lead.get("id", "неизвестен")
    name = lead.get("name", "без имени")
    created_at = lead.get("created_at", "неизвестно")
    updated_at = lead.get("updated_at", "неизвестно")

    print(f"ID лида: {lead_id}")
    print(f"Имя: {name}")
    print(f"Создан: {created_at}")
    print(f"Обновлен: {updated_at}")

    # Телефоны
    phones = lead.get("phones", "")
    if phones:
        print(f"Основной телефон: {phones}")
    else:
        print("Основной телефон: не указан")

    # Дополнительные телефоны (если есть)
    lead_phones = lead.get("lead_phones", [])
    if lead_phones:
        print(f"Дополнительные телефоны ({len(lead_phones)}):")
        for phone in lead_phones:
            print(f"  - {phone}")

    # Email
    email = lead.get("email", "")
    if email:
        print(f"Email: {email}")
    else:
        print("Email: не указан")

    # Остальные поля
    print("\nВсе поля лида:")
    for key, value in lead.items():
        if key not in ["phones", "lead_phones", "created_at", "updated_at"]:  # Уже показали
            print(f"  {key}: {value}")


def main() -> None:
    load_dotenv()

    # Получение учетных данных
    username = get_required_env("SKOROZVON_LOGIN")
    api_key = get_required_env("SKOROZVON_API_KEY")
    client_id = get_required_env("SKOROZVON_APP_ID")
    client_secret = get_required_env("SKOROZVON_APP_KEY")

    # Аутентификация
    token = get_access_token(username, api_key, client_id, client_secret)

    # Получение списка проектов
    projects = list_all_projects(token)

    # Выбор проекта
    selected_project = select_project(projects)
    project_id = str(selected_project.get("id", selected_project.get("uuid", "")))

    if not project_id:
        print("Не удалось получить ID проекта", file=sys.stderr)
        sys.exit(1)

    # Получение лидов проекта
    print(f"\nЗагружаю лиды проекта {project_id}...")
    leads = get_project_leads(project_id, token)

    if not leads:
        print("В выбранном проекте нет лидов.")
        return

    print(f"\nНайдено лидов: {len(leads)}")

    # Отображение информации о каждом лиде
    for i, lead in enumerate(leads):
        display_lead_details(lead, i)


if __name__ == "__main__":
    main()
