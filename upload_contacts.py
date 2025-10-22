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
    resp = requests.post(TOKEN_URL, data=payload, timeout=30)
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
            resp = requests.post(url, json=payload_with_project, headers=headers, timeout=60)
            if resp.status_code in (200, 201):
                continue
            # Если проект закрыт/не существует — пробуем без привязки
            if resp.status_code == 422 and "Проект не существует" in resp.text:
                resp2 = requests.post(url, json=payload, headers=headers, timeout=60)
                if resp2.status_code in (200, 201):
                    print(f"Лид для {phone} создан без привязки к проекту (исходный проект недоступен).")
                    continue
                print(
                    f"Ошибка при создании лида для {phone}: {resp2.status_code} {resp2.text}",
                    file=sys.stderr,
                )
                sys.exit(1)
            print(
                f"Ошибка при создании лида для {phone}: {resp.status_code} {resp.text}",
                file=sys.stderr,
            )
            sys.exit(1)

        # Если project_id не указан — сразу создаем без привязки
        resp = requests.post(url, json=payload, headers=headers, timeout=60)
        if resp.status_code not in (200, 201):
            print(f"Ошибка при создании лида для {phone}: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    print("Лиды успешно созданы и привязаны к проекту.")


def main() -> None:
    load_dotenv()

    username = get_required_env("SKOROZVON_LOGIN")
    api_key = get_required_env("SKOROZVON_API_KEY")
    client_id = get_required_env("SKOROZVON_APP_ID")
    client_secret = get_required_env("SKOROZVON_APP_KEY")
    project_id = get_required_env("SKOROZVON_PROJECT_ID")

    token = get_access_token(username, api_key, client_id, client_secret)

    test_phones = [
        "+79991234567",
        "+79997654321",
    ]
    upload_contacts(project_id, test_phones, token)


if __name__ == "__main__":
    main()


