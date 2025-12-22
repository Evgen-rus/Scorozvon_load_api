import sys
from typing import Any, Dict, List, Set, Tuple

import requests
from dotenv import load_dotenv

from upload_contacts_dedup import get_required_env, get_access_token


CALLS_URL = "https://api.skorozvon.ru/api/v2/calls"


def extract_calls(payload: Any) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Аккуратно вытащить список звонков и блок pagination из ответа API.
    """
    items: List[Dict[str, Any]] = []
    pagination: Dict[str, Any] = {}

    if not isinstance(payload, dict):
        return items, pagination

    raw_items = payload.get("data")
    if isinstance(raw_items, list):
        items = raw_items

    raw_pagination = payload.get("pagination")
    if isinstance(raw_pagination, dict):
        pagination = raw_pagination

    return items, pagination


def extract_status_label(call: Dict[str, Any]) -> str:
    """
    Попробовать вытащить человекочитаемый "статус" звонка.

    В разных аккаунтах структура может отличаться, поэтому:
    - в первую очередь используем result_id и возможное имя результата;
    - если имени нет, подставляем reason (SIP-описание завершения);
    - если совсем ничего нет, возвращаем 'UNKNOWN'.
    """
    result_id = call.get("result_id")

    result_name: str | None = None
    # Возможные варианты, как может называться человекочитаемый результат
    for key in ("result_name", "result", "scenario_result", "scenario_result_group"):
        value = call.get(key)
        if isinstance(value, dict):
            name = value.get("name") or value.get("title")
            if isinstance(name, str) and name.strip():
                result_name = name.strip()
                break
        elif isinstance(value, str) and value.strip():
            result_name = value.strip()
            break

    reason = call.get("reason")

    parts: List[str] = []
    if result_id is not None:
        parts.append(f"id={result_id}")
    if result_name:
        parts.append(f"name={result_name}")
    if reason and not result_name:
        parts.append(f"reason={reason}")

    if not parts:
        return "UNKNOWN"

    return " | ".join(parts)


def collect_statuses_for_project(
    project_id: str, access_token: str, max_pages: int = 100
) -> Set[str]:
    """
    Обойти список звонков по проекту и собрать множество разных статусов.

    Важно:
    - если в проекте очень много звонков, обойдём не больше max_pages страниц,
      чтобы не крутиться бесконечно;
    - размер страницы (length) можно при необходимости подправить.
    """
    headers = {"Authorization": f"Bearer {access_token}"}

    page = 1
    length = 100  # сколько звонков за один запрос просим у API

    statuses: Set[str] = set()

    while page <= max_pages:
        params = {
            "call_project_id": project_id,
            "page": page,
            "length": length,
        }
        print(f"REQUEST: GET {CALLS_URL} params={params}")
        resp = requests.get(CALLS_URL, headers=headers, params=params, timeout=60)
        print(f"RESPONSE: status={resp.status_code}")

        if resp.status_code != 200:
            print(
                f"Ошибка при получении звонков: {resp.status_code} {resp.text}",
                file=sys.stderr,
            )
            break

        data = resp.json()
        calls, pagination = extract_calls(data)
        if not calls:
            # Звонков больше нет
            break

        for call in calls:
            label = extract_status_label(call)
            statuses.add(label)

        total_pages = pagination.get("total_pages")
        if isinstance(total_pages, int) and total_pages > 0:
            if page >= total_pages:
                break
        else:
            # Если API не отдало total_pages, просто идём, пока не встретится пустая страница
            if len(calls) < length:
                break

        page += 1

    return statuses


def main() -> None:
    """
    Точка входа:
    - берём ID проекта из аргумента или спрашиваем у пользователя;
    - получаем токен;
    - собираем и печатаем список разных статусов звонков в этом проекте.
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

    print(f"\nСобираю статусы звонков по проекту {project_id}...")
    statuses = collect_statuses_for_project(project_id, token)

    if not statuses:
        print("Не удалось найти звонки или статусы для указанного проекта.")
        return

    print("\n=== Найденные статусы звонков в проекте ===")
    for idx, status in enumerate(sorted(statuses), start=1):
        print(f"{idx}. {status}")


if __name__ == "__main__":
    main()



