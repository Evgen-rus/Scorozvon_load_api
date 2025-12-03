import os
import sys
from typing import Any, Dict

import requests
from dotenv import load_dotenv


TOKEN_URL = "https://api.skorozvon.ru/oauth/token"
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


def print_lead(lead: Dict[str, Any]) -> None:
    """Красивый вывод данных по лиду."""
    print("\n=== Информация о лиде ===")
    print(f"ID: {lead.get('id')}")
    print(f"Имя: {lead.get('name')}")
    print(f"Комментарий: {lead.get('comment')}")
    print(f"Город: {lead.get('city')}")
    print(f"Бизнес/компания: {lead.get('business')}")
    print(f"Адрес: {lead.get('address')}")
    print(f"Ответственный ID: {lead.get('responsible_id')}")

    phones = lead.get("phones")
    lead_phones = lead.get("lead_phones") or []
    print(f"\nОсновной телефон: {phones or '—'}")
    if lead_phones:
        print(f"Доп. телефоны ({len(lead_phones)}):")
        for p in lead_phones:
            print(f"  - {p}")

    tags = lead.get("tags") or []
    print(f"\nТеги: {tags}")

    created_at = lead.get("created_at")
    updated_at = lead.get("updated_at")
    print(f"\nСоздан: {created_at}")
    print(f"Обновлён: {updated_at}")

    # Если есть кастомные поля — тоже покажем
    custom_fields = lead.get("custom_fields") or {}
    if custom_fields:
        print("\nПользовательские поля (custom_fields):")
        for key, val in custom_fields.items():
            print(f"  {key}: {val}")


def main() -> None:
    """Получить и показать информацию о лиде по его ID."""
    load_dotenv()

    username = get_required_env("SKOROZVON_LOGIN")
    api_key = get_required_env("SKOROZVON_API_KEY")
    client_id = get_required_env("SKOROZVON_APP_ID")
    client_secret = get_required_env("SKOROZVON_APP_KEY")

    token = get_access_token(username, api_key, client_id, client_secret)

    raw_lead_id = input("Введите ID лида (например 30922761989): ").strip()
    if not raw_lead_id:
        print("ID лида не введён, выхожу.")
        return

    # Собираем URL вида /api/v2/leads/{id}
    url = f"{LEADS_URL}/{raw_lead_id}"
    headers = {"Authorization": f"Bearer {token}"}

    print(f"\nREQUEST: GET {url}")
    resp = requests.get(url, headers=headers, timeout=30)
    print(f"RESPONSE: status={resp.status_code} body={resp.text}")

    if resp.status_code == 404:
        print(f"Лид с ID {raw_lead_id} не найден.")
        return
    if resp.status_code != 200:
        print(f"Ошибка при получении лида: {resp.status_code} {resp.text}", file=sys.stderr)
        return

    data = resp.json()
    # На случай, если API оборачивает в {data: {...}}
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        lead = data["data"]
    else:
        lead = data

    print_lead(lead)


if __name__ == "__main__":
    main()


