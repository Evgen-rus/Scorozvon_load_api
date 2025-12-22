import os
import sys
from typing import Any, Dict, List, Tuple, Optional, Set

import requests
from dotenv import load_dotenv

from upload_contacts_dedup import get_required_env, get_access_token
from get_project_full_info import fetch_project_by_id


CALLS_URL = "https://api.skorozvon.ru/api/v2/calls"
SCENARIOS_URL = "https://api.skorozvon.ru/api/v2/scenarios"

# По умолчанию нацелимся на эти два бизнес-статуса:
#  - Покупка более чем через 3 мес
#  - Заявка
DEFAULT_RESULT_IDS = {30000200933, 30000200254}


def fetch_scenario_results_map(
    scenario_id: int, access_token: str
) -> Dict[int, Tuple[str, Optional[str]]]:
    """
    Получить словарь result_id -> (name, group_title) для указанного сценария.
    Использует endpoint /api/v2/scenarios/{id}/results.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{SCENARIOS_URL}/{scenario_id}/results"
    print(f"REQUEST: GET {url}")
    resp = requests.get(url, headers=headers, timeout=30)
    print(f"RESPONSE: status={resp.status_code} body={resp.text}")

    if resp.status_code != 200:
        print(
            f"Не удалось получить результаты сценария: {resp.status_code} {resp.text}",
            file=sys.stderr,
        )
        return {}

    try:
        data = resp.json()
    except ValueError:
        return {}

    items = data.get("data")
    if not isinstance(items, list):
        return {}

    result_map: Dict[int, Tuple[str, Optional[str]]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        rid = item.get("id")
        name = item.get("name")
        group_title = item.get("group_title")
        if isinstance(rid, int) and isinstance(name, str):
            result_map[rid] = (name, group_title if isinstance(group_title, str) else None)

    return result_map


def extract_calls_and_pagination(payload: Any) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Вспомогательная функция для разбора ответа /api/v2/calls.
    Ожидаем словарь с ключами 'data' и 'pagination'.
    """
    calls: List[Dict[str, Any]] = []
    pagination: Dict[str, Any] = {}

    if not isinstance(payload, dict):
        return calls, pagination

    raw_calls = payload.get("data")
    if isinstance(raw_calls, list):
        calls = [c for c in raw_calls if isinstance(c, dict)]

    raw_pagination = payload.get("pagination")
    if isinstance(raw_pagination, dict):
        pagination = raw_pagination

    return calls, pagination


def collect_calls_for_project_and_results(
    project_id: str,
    access_token: str,
    target_result_ids: Set[int],
    max_pages: int = 1000,
) -> List[Dict[str, Any]]:
    """
    Собираем все звонки по проекту, у которых result_id входит в target_result_ids
    и есть recording_url.
    """
    headers = {"Authorization": f"Bearer {access_token}"}

    page = 1
    length = 100
    matched_calls: List[Dict[str, Any]] = []

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

        try:
            payload = resp.json()
        except ValueError:
            break

        calls, pagination = extract_calls_and_pagination(payload)
        if not calls:
            break

        for call in calls:
            result_id = call.get("result_id")
            recording_url = call.get("recording_url")
            if (
                isinstance(result_id, int)
                and result_id in target_result_ids
                and isinstance(recording_url, str)
                and recording_url.strip()
            ):
                matched_calls.append(call)

        total_pages = pagination.get("total_pages")
        if isinstance(total_pages, int) and total_pages > 0:
            if page >= total_pages:
                break
        else:
            if len(calls) < length:
                break

        page += 1

    return matched_calls


def build_recording_url_with_token(recording_url: str, access_token: str) -> str:
    """
    Для скачивания записей, согласно документации, нужно передать access_token
    как параметр URL, а не через Authorization.
    """
    separator = "&" if "?" in recording_url else "?"
    return f"{recording_url}{separator}access_token={access_token}"


def download_recording(
    url: str,
    dest_path: str,
) -> bool:
    """
    Скачивает запись по URL и сохраняет в dest_path.
    Возвращает True при успехе.
    """
    try:
        print(f"DOWNLOAD: GET {url} -> {dest_path}")
        with requests.get(url, stream=True, timeout=300) as resp:
            if resp.status_code != 200:
                print(
                    f"Ошибка скачивания {url}: {resp.status_code} {resp.text}",
                    file=sys.stderr,
                )
                return False
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            with open(dest_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if not chunk:
                        continue
                    f.write(chunk)
        return True
    except requests.RequestException as exc:
        print(f"Сетевая ошибка при скачивании {url}: {exc}", file=sys.stderr)
        return False


def choose_call_indexes(count: int) -> List[int]:
    """
    Позволяет пользователю выбрать, какие записи скачивать:
    - 'all' или пустой ввод — все;
    - список номеров через запятую (1,2,5).
    Возвращает список индексов (0-based).
    """
    if count == 0:
        return []

    print(
        "\nЧто скачивать?\n"
        "  - Нажмите Enter или введите 'all', чтобы скачать все записи.\n"
        "  - Или введите номера через запятую (например: 1,3,5)."
    )
    raw = input("Ваш выбор: ").strip().lower()

    if not raw or raw == "all":
        return list(range(count))

    result: List[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            print(f"Игнорирую некорректный номер: {part}")
            continue
        idx = int(part) - 1
        if 0 <= idx < count:
            result.append(idx)
        else:
            print(f"Номер вне диапазона: {part}")

    return result


def main() -> None:
    """
    Скрипт:
    - Находит проект и его scenario_id.
    - Получает статусы сценария (result_id -> name).
    - Фильтрует звонки по проекту и нужным result_id.
    - Показывает список подходящих записей.
    - По выбору пользователя скачивает их.
    """
    load_dotenv()

    username = get_required_env("SKOROZVON_LOGIN")
    api_key = get_required_env("SKOROZVON_API_KEY")
    client_id = get_required_env("SKOROZVON_APP_ID")
    client_secret = get_required_env("SKOROZVON_APP_KEY")

    access_token = get_access_token(username, api_key, client_id, client_secret)

    if len(sys.argv) >= 2:
        project_id = sys.argv[1]
    else:
        project_id = input("Введите ID проекта: ").strip()

    if not project_id:
        print("ID проекта не указан, работа завершена.")
        return

    print(f"\nИщу проект с ID {project_id}...")
    project = fetch_project_by_id(project_id, access_token)
    if not project:
        print(f"Проект с ID {project_id} не найден или нет доступа.")
        return

    project_title = project.get("title") or project.get("name") or "(без названия)"
    scenario_id = project.get("scenario_id")
    if not isinstance(scenario_id, int):
        print("У проекта не получилось определить scenario_id.")
        print(f"project.get('scenario_id') -> {scenario_id!r}")
        return

    print(f"Проект: {project_id} — {project_title}")
    print(f"scenario_id={scenario_id}")

    # Получаем карту result_id -> (name, group_title)
    results_map = fetch_scenario_results_map(scenario_id, access_token)
    if not results_map:
        print("Не удалось получить список статусов сценария, остановка.")
        return

    print("\nДоступные результаты сценария:")
    for rid, (name, group) in sorted(results_map.items()):
        group_part = f" [{group}]" if group else ""
        print(f"  {rid}: {name}{group_part}")

    # Предлагаем использовать значения по умолчанию или ввести свои
    print(
        "\nПо умолчанию будут использованы статусы:\n"
        "  30000200254: Заявка\n"
        "  30000200933: Покупка более чем через 3 мес"
    )
    use_default = input("Использовать эти статусы? (Y/n): ").strip().lower()

    target_ids: Set[int] = set()
    if use_default in ("", "y", "yes", "д", "да"):
        target_ids = set(DEFAULT_RESULT_IDS)
    else:
        raw_ids = input(
            "Введите нужные result_id через запятую (например: 30000200254,30000200933): "
        ).strip()
        for part in raw_ids.split(","):
            part = part.strip()
            if not part:
                continue
            if not part.isdigit():
                print(f"Игнорирую некорректный id: {part}")
                continue
            target_ids.add(int(part))

    if not target_ids:
        print("Не выбрано ни одного result_id, работа остановлена.")
        return

    print("\nИтоговый список result_id для поиска записей:")
    for rid in sorted(target_ids):
        name, group = results_map.get(rid, ("(неизвестно)", None))
        group_part = f" [{group}]" if group else ""
        print(f"  {rid}: {name}{group_part}")

    print("\nИщу звонки по проекту и выбранным статусам...")
    calls = collect_calls_for_project_and_results(project_id, access_token, target_ids)

    if not calls:
        print("Не найдено ни одного звонка с записями для выбранных статусов.")
        return

    print(f"\nНайдено звонков с записями: {len(calls)}")
    print("\nСписок записей (только просмотр, пока без скачивания):")

    for idx, call in enumerate(calls, start=1):
        call_id = call.get("id")
        result_id = call.get("result_id")
        recording_url = call.get("recording_url") or ""
        started_at = call.get("started_at") or call.get("created_at") or ""
        lead = call.get("lead") or {}
        lead_id = lead.get("id") if isinstance(lead, dict) else call.get("lead_id")
        lead_name = lead.get("name") if isinstance(lead, dict) else None

        result_name, group = results_map.get(result_id, ("(неизвестно)", None))
        group_part = f" [{group}]" if group else ""

        print(f"\n#{idx}")
        print(f"  call_id: {call_id}")
        print(f"  lead_id: {lead_id}  name: {lead_name or '—'}")
        print(f"  дата/время: {started_at}")
        print(f"  result_id: {result_id} — {result_name}{group_part}")
        print(f"  recording_url: {recording_url}")

    indexes = choose_call_indexes(len(calls))
    if not indexes:
        print("Ничего не выбрано для скачивания. Завершение работы.")
        return

    default_dir = f"recordings_project_{project_id}"
    dest_dir = input(
        f"\nВведите папку для сохранения записей (Enter — {default_dir}): "
    ).strip() or default_dir

    downloaded = 0
    for idx in indexes:
        call = calls[idx]
        call_id = call.get("id")
        result_id = call.get("result_id")
        recording_url = call.get("recording_url")
        if not isinstance(recording_url, str) or not recording_url.strip():
            print(f"Пропуск звонка #{idx+1}: нет recording_url.")
            continue

        result_name, _ = results_map.get(result_id, ("unknown", None))
        safe_result_name = result_name.replace(" ", "_")
        filename = f"{project_id}_call_{call_id}_{result_id}_{safe_result_name}.mp3"
        dest_path = os.path.join(dest_dir, filename)

        url_with_token = build_recording_url_with_token(recording_url, access_token)
        if download_recording(url_with_token, dest_path):
            downloaded += 1

    print(f"\nГотово. Всего выбрано: {len(indexes)}, успешно скачано: {downloaded}.")


if __name__ == "__main__":
    main()



