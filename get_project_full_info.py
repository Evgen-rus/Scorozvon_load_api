import sys
from typing import Any, Dict, List, Tuple

import requests
from dotenv import load_dotenv

from upload_contacts_dedup import get_required_env, get_access_token


CALL_PROJECTS_URL = "https://api.skorozvon.ru/api/v2/call_projects"
LEADS_URL = "https://api.skorozvon.ru/api/v2/leads"


def extract_project(payload: Any) -> Dict[str, Any]:
    """
    Аккуратно вытащить один проект из ответа API.

    В разных версиях API данные могут лежать либо в корне,
    либо в ключе "data".
    """
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            return data
        return payload
    return {}


def fetch_project_by_id(project_id: str, access_token: str) -> Dict[str, Any]:
    """
    Получить подробную информацию о проекте по его ID.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{CALL_PROJECTS_URL}/{project_id}"
    print(f"REQUEST: GET {url}")
    resp = requests.get(url, headers=headers, timeout=30)
    print(f"RESPONSE: status={resp.status_code} body={resp.text}")

    if resp.status_code == 404:
        # Проект не найден — возвращаем пустой словарь, чтобы main() вывел понятное сообщение.
        return {}
    if resp.status_code != 200:
        print(
            f"Ошибка при получении проекта: {resp.status_code} {resp.text}",
            file=sys.stderr,
        )
        return {}

    return extract_project(resp.json())


def fetch_project_leads_preview(
    project_id: str, access_token: str, limit: int = 3
) -> Tuple[int, List[Dict[str, Any]]]:
    """
    Получить общее количество лидов в проекте и первые N лидов.

    Если лидов больше, чем limit, вернём только первые limit, как вы и просили.
    """
    headers = {"Authorization": f"Bearer {access_token}"}

    params = {"call_project_id": project_id, "page": 1, "per_page": limit}
    print(f"REQUEST: GET {LEADS_URL} params={params}")
    resp = requests.get(LEADS_URL, headers=headers, params=params, timeout=30)
    print(f"RESPONSE: status={resp.status_code} body={resp.text}")

    if resp.status_code != 200:
        print(
            f"Ошибка при получении лидов проекта: {resp.status_code} {resp.text}",
            file=sys.stderr,
        )
        return 0, []

    data = resp.json()
    pagination = data.get("pagination") or {}
    total = pagination.get("total", 0)

    items: List[Dict[str, Any]] = []
    raw_items = data.get("data")
    if isinstance(raw_items, list):
        items = raw_items

    # На всякий случай ограничим результат сверху, даже если API вернуло больше.
    if len(items) > limit:
        items = items[:limit]

    return total, items


def print_project_info(project: Dict[str, Any]) -> None:
    """
    Печать подробной информации о проекте в человекочитаемом виде.
    """
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


def print_leads_preview(total: int, leads: List[Dict[str, Any]]) -> None:
    """
    Печать информации о лидах проекта:
    - сколько всего лидов;
    - подробности по первым трём (или меньше, если их меньше в принципе).
    """
    print("\n=== Лиды проекта ===")
    print(f"Всего лидов в проекте: {total}")

    if not leads:
        print("Лиды в проекте не найдены.")
        return

    print("\nПервые лиды (максимум 3):")
    for idx, lead in enumerate(leads, start=1):
        name = lead.get("name", "без имени")
        phones = lead.get("phones") or lead.get("lead_phones") or ""
        comment = lead.get("comment") or "—"
        tags = lead.get("tags") or []

        print(f"\nЛид #{idx}")
        print(f"  ID: {lead.get('id')}")
        print(f"  Имя: {name}")
        print(f"  Телефон(ы): {phones}")
        print(f"  Комментарий: {comment}")
        print(f"  Теги: {tags}")

    if total > len(leads):
        print(
            f"\nПоказаны только первые {len(leads)} лида из {total}. "
            f"Чтобы получить все, нужно отдельный скрипт/экспорт."
        )


def main() -> None:
    """
    Точка входа:
    - берём ID проекта из аргумента командной строки или спрашиваем у пользователя;
    - получаем токен;
    - выводим полную инфу о проекте и максимум трёх лидов.
    """
    load_dotenv()

    username = get_required_env("SKOROZVON_LOGIN")
    api_key = get_required_env("SKOROZVON_API_KEY")
    client_id = get_required_env("SKOROZVON_APP_ID")
    client_secret = get_required_env("SKOROZVON_APP_KEY")

    token = get_access_token(username, api_key, client_id, client_secret)

    if len(sys.argv) >= 2:
        project_id = sys.argv[1]
    else:
        project_id = input("Введите ID проекта: ").strip()

    if not project_id:
        print("ID проекта не указан, работа завершена.")
        return

    print(f"\nИщу проект с ID {project_id}...")
    project = fetch_project_by_id(project_id, token)
    if not project:
        print(f"Проект с ID {project_id} не найден или нет доступа.")
        return

    print_project_info(project)

    total_leads, leads_preview = fetch_project_leads_preview(project_id, token, limit=3)
    print_leads_preview(total_leads, leads_preview)


if __name__ == "__main__":
    main()



