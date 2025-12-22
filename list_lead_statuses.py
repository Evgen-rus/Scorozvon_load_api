import sys
from typing import Any, Dict, List, Tuple, Set

import requests
from dotenv import load_dotenv

from upload_contacts_dedup import get_required_env, get_access_token
from get_project_full_info import fetch_project_by_id


SCENARIOS_URL = "https://api.skorozvon.ru/api/v2/scenarios"


def extract_statuses_from_payload(payload: Any) -> List[Tuple[int, str]]:
    """
    Пытаемся найти в ответе API элементы, похожие на "статусы лидов":
    объекты с целочисленным id и строковым name/title.

    Это сделано специально "широко", чтобы подойти к разным форматам ответа:
    - /scenarios/{id}, если он сразу отдаёт результаты;
    - /scenario_results?... и т.п.
    """
    statuses: List[Tuple[int, str]] = []

    def try_add(obj: Dict[str, Any]) -> None:
        if not isinstance(obj, dict):
            return
        raw_id = obj.get("id")
        if not isinstance(raw_id, int):
            return
        name = obj.get("name") or obj.get("title")
        if not isinstance(name, str) or not name.strip():
            return
        statuses.append((raw_id, name.strip()))

    if isinstance(payload, dict):
        # Вариант 1: список лежит в data
        data = payload.get("data")
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    try_add(item)

        # Вариант 2: перебираем все списки верхнего уровня
        for value in payload.values():
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        try_add(item)

    elif isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                try_add(item)

    # Убираем дубли по id
    seen: Set[int] = set()
    unique_statuses: List[Tuple[int, str]] = []
    for sid, name in statuses:
        if sid in seen:
            continue
        seen.add(sid)
        unique_statuses.append((sid, name))

    return unique_statuses


def fetch_scenario_statuses_from_scenarios(
    scenario_id: int, access_token: str
) -> List[Tuple[int, str]]:
    """
    Пытаемся получить статусы через /api/v2/scenarios/{id}.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{SCENARIOS_URL}/{scenario_id}"
    print(f"REQUEST: GET {url}")
    resp = requests.get(url, headers=headers, timeout=30)
    print(f"RESPONSE: status={resp.status_code} body={resp.text}")

    if resp.status_code != 200:
        return []

    payload = resp.json()
    return extract_statuses_from_payload(payload)


def fetch_scenario_statuses_from_results(
    scenario_id: int, access_token: str
) -> List[Tuple[int, str]]:
    """
    Запасной вариант: пробуем /api/v2/scenarios/{id}/results.

    Это более точный вариант согласно документации:
    результаты сценария отдаются как вложенный ресурс сценария.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{SCENARIOS_URL}/{scenario_id}/results"
    print(f"REQUEST: GET {url}")
    resp = requests.get(url, headers=headers, timeout=30)
    print(f"RESPONSE: status={resp.status_code} body={resp.text}")

    if resp.status_code != 200:
        return []

    payload = resp.json()
    return extract_statuses_from_payload(payload)


def main() -> None:
    """
    Скрипт показывает бизнес-статусы (результаты сценария) для проекта.

    Алгоритм:
    - Берём ID проекта (аргументом или через input).
    - Получаем токен из .env.
    - Через REST /call_projects/{id} узнаём scenario_id.
    - Пробуем:
        1) /scenarios/{scenario_id}
        2) /scenario_results?scenario_id={scenario_id}
      и вытаскиваем оттуда пары (id, name),
      где name — как раз строки вроде "Заявка", "Покупка более чем через 3 мес".
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

    scenario_id = project.get("scenario_id")
    if not isinstance(scenario_id, int):
        print("У проекта не получилось определить scenario_id.")
        print(f"project.get('scenario_id') -> {scenario_id!r}")
        return

    print(f"Найден scenario_id={scenario_id}. Пытаюсь получить статусы сценария...")

    statuses = fetch_scenario_statuses_from_scenarios(scenario_id, token)
    if not statuses:
        print(
            "Через /scenarios/{id} статусы не найдены, пробую /scenario_results..."
        )
        statuses = fetch_scenario_statuses_from_results(scenario_id, token)

    if not statuses:
        print(
            "Не удалось получить список статусов сценария через доступные REST-методы.\n"
            "Возможно, в вашем аккаунте используется другой endpoint или права ограничены."
        )
        return

    print("\n=== Статусы (результаты сценария) для этого проекта ===")
    for sid, name in sorted(statuses, key=lambda x: x[0]):
        print(f"{sid}: {name}")

    print(
        "\nЭти id и названия можно будет использовать для фильтрации звонков и "
        "скачивания записей по нужным статусам лидов."
    )


if __name__ == "__main__":
    main()



