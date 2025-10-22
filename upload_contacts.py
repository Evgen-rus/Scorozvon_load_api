import os
import sys
from typing import List

import requests
from dotenv import load_dotenv


TOKEN_URL = "https://api.skorozvon.ru/oauth/token"


def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        print(f"Отсутствует переменная окружения {name}. Проверьте файл .env", file=sys.stderr)
        sys.exit(1)
    return value


def get_access_token(username: str, api_key: str, client_id: str, client_secret: str) -> str:
    """Получение access_token по данным учетной записи.

    В некоторых инсталляциях Скорозвона используется password grant, где API-ключ выступает как пароль.
    Если ваш PDF регламентирует иную схему (например, иные поля/урл), замените формирование payload/URL ниже.
    """
    payload = {
        "grant_type": "password",
        "username": username,
        "api_key": api_key,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    # Логи запроса авторизации
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


def upload_contacts(project_id: str, phones: List[str], access_token: str) -> None:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    url = "https://api.skorozvon.ru/api/v2/leads"

    for phone in phones:
        payload = {
            "type": "Lead",
            "name": f"Тестовый лид {phone}",
            "phones": [phone if phone.startswith("+") else f"+{phone}"],
        }
        # Пробуем с привязкой к проекту, если указан
        if project_id:
            payload_with_project = dict(payload)
            payload_with_project["call_project_id"] = int(project_id) if project_id.isdigit() else project_id
            print(f"REQUEST: POST {url} json={payload_with_project}")
            resp = requests.post(url, json=payload_with_project, headers=headers, timeout=60)
            print(f"RESPONSE: status={resp.status_code} body={resp.text}")
            if resp.status_code in (200, 201):
                continue
            print(
                f"Не удалось привязать лид {phone} к проекту {project_id}: {resp.status_code} {resp.text}",
                file=sys.stderr,
            )
            print("Лид не создан, так как создание без привязки к проекту отключено.", file=sys.stderr)
            sys.exit(1)

        # Если project_id не указан — прерываем (запрещено создавать без проекта)
        print("project_id не указан — создание лидов без привязки к проекту отключено.", file=sys.stderr)
        sys.exit(1)
    print("Лиды успешно созданы и привязаны к проекту.")


def main() -> None:
    load_dotenv()

    username = get_required_env("SKOROZVON_LOGIN")
    api_key = get_required_env("SKOROZVON_API_KEY")
    client_id = get_required_env("SKOROZVON_APP_ID")
    client_secret = get_required_env("SKOROZVON_APP_KEY")

    # Укажите ID проекта Скорозвон, в который загружать номера
    project_id = "194408"

    token = get_access_token(username, api_key, client_id, client_secret)

    test_phones = [
        "+79991234567",
    ]
    upload_contacts(project_id, test_phones, token)


if __name__ == "__main__":
    main()


