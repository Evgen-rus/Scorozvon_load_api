import os
import sys
from typing import List, Any, Dict

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


def digits_only(s: str) -> str:
    return "".join(ch for ch in s if ch.isdigit())


def phone_variants(phone: str) -> List[str]:
    raw = phone
    digits = digits_only(phone)
    plus = "+" + digits if not phone.startswith("+") else phone
    variants: List[str] = []
    for v in (raw, digits, plus):
        if v and v not in variants:
            variants.append(v)
    return variants


def extract_list(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "items", "leads", "results"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


def phones_equal(a: str, b: str) -> bool:
    da = digits_only(a)
    db = digits_only(b)
    if not da or not db:
        return False
    if da == db:
        return True
    # Сравнение по последним 10 цифрам (учёт 7/8 и форматов)
    return da[-10:] == db[-10:]


def lead_exists_in_project(project_id: str, phone: str, headers: Dict[str, str]) -> bool:
    base_leads = "https://api.skorozvon.ru/api/v2/leads"

    # Стратегия: для ускорения проверяем ТОЛЬКО последние несколько страниц проекта,
    # считая, что новые лиды находятся в конце.
    per_page = 200

    def page_has_phone(page: int) -> bool:
        """Проверка, есть ли номер на конкретной странице лидов."""
        params = {"call_project_id": project_id, "page": page, "per_page": per_page}
        print(f"REQUEST: GET {base_leads} params={params}")
        resp = requests.get(base_leads, headers=headers, params=params, timeout=30)
        print(f"RESPONSE: status={resp.status_code} body={resp.text}")
        if resp.status_code != 200:
            return False
        data = resp.json()
        items = extract_list(data)
        for item in items:
            candidates: List[str] = []
            if isinstance(item.get("phones"), str):
                candidates.append(item["phones"])  # строка
            if isinstance(item.get("lead_phones"), list):
                candidates.extend([str(x) for x in item["lead_phones"]])
            for cand in candidates:
                if phones_equal(cand, phone):
                    return True
        return False

    # Сначала делаем один запрос, чтобы узнать общее количество страниц
    params_first = {"call_project_id": project_id, "page": 1, "per_page": per_page}
    print(f"REQUEST: GET {base_leads} params={params_first}")
    resp_first = requests.get(base_leads, headers=headers, params=params_first, timeout=30)
    print(f"RESPONSE: status={resp_first.status_code} body={resp_first.text}")
    if resp_first.status_code != 200:
        return False
    data_first = resp_first.json()
    pagination = data_first.get("pagination") or {}
    total_pages = pagination.get("total_pages") or 1

    # Если страниц мало — проверяем все, если много — только последние 5
    if total_pages <= 5:
        # Используем уже полученную первую страницу
        if page_has_phone(1):
            return True
        for page in range(2, total_pages + 1):
            if page_has_phone(page):
                return True
        return False

    # Берём последние 5 страниц: total_pages, total_pages-1, ...,
    # потому что новые лиды обычно в конце.
    start_page = max(1, total_pages - 5 + 1)
    for page in range(start_page, total_pages + 1):
        if page_has_phone(page):
            return True
    return False


def upload_contacts_dedup(project_id: str, phones: List[str], access_token: str) -> None:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    url = "https://api.skorozvon.ru/api/v2/leads"

    created_count = 0
    skipped_count = 0

    for phone in phones:
        if lead_exists_in_project(project_id, phone, headers):
            print(f"Пропускаю {phone}: номер уже есть в проекте {project_id}.")
            skipped_count += 1
            continue

        # Извлекаем номер телефона без плюса для названия
        phone_for_name = phone.lstrip("+")

        payload = {
            "type": "Lead",
            "name": "ФИО не указано",
            "phones": [phone if phone.startswith("+") else f"+{phone}"],
            "call_project_id": int(project_id) if project_id.isdigit() else project_id,
            "comment": "Тест интеграции",  # Описание
            "tags": ["столица_перехват"],  # Теги
        }
        print(f"REQUEST: POST {url} json={payload}")
        resp = requests.post(url, json=payload, headers=headers, timeout=60)
        print(f"RESPONSE: status={resp.status_code} body={resp.text}")
        if resp.status_code not in (200, 201):
            print(
                f"Ошибка при создании лида для {phone}: {resp.status_code} {resp.text}",
                file=sys.stderr,
            )
            sys.exit(1)
        created_count += 1

    total = len(phones)
    print(f"Итог: всего номеров={total}, создано={created_count}, дубликаты (пропущено)={skipped_count}.")


def main() -> None:
    load_dotenv()

    username = get_required_env("SKOROZVON_LOGIN")
    api_key = get_required_env("SKOROZVON_API_KEY")
    client_id = get_required_env("SKOROZVON_APP_ID")
    client_secret = get_required_env("SKOROZVON_APP_KEY")

    # Используйте ваш проект
    project_id = "30000191458"   #Северная Столица (перехват) 

    token = get_access_token(username, api_key, client_id, client_secret)

    test_phones = [
        "+79231920440",
    ]
    upload_contacts_dedup(project_id, test_phones, token)


if __name__ == "__main__":
    main()


