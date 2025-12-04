import sys
import time
from typing import List, Tuple, Optional, Dict, Any

import requests
from dotenv import load_dotenv
from openpyxl import load_workbook
from tkinter import Tk, filedialog

from upload_contacts_dedup import get_required_env, get_access_token


# Значения ниже можно легко менять под свои задачи
LEAD_DEFAULT_NAME = "ФИО не указано"
CALL_PROJECT_ID = "30000191458"  # Северная Столица (перехват)
DEFAULT_TAGS: List[str] = ["столица_перехват"]

# Настройки ограничения скорости и повторов запросов
REQUEST_DELAY_SECONDS = 0.3  # Пауза между запросами, чтобы не упираться в лимиты API
MAX_RETRIES = 3  # Сколько раз пробовать повторить запрос при ошибках
RETRY_BACKOFF_SECONDS = 1.0  # Базовая пауза перед повтором, будет расти с каждой попыткой


def select_xlsx_file() -> str:
    """
    Открываем диалог выбора файла и возвращаем путь к .xlsx.
    Если пользователь закрыл окно или ничего не выбрал — выходим.
    """
    root = Tk()
    root.withdraw()  # не показываем пустое окно Tk
    root.update()

    file_path = filedialog.askopenfilename(
        title="Выберите Excel-файл с лидами",
        filetypes=[("Excel files", "*.xlsx")],
    )

    root.destroy()

    if not file_path:
        print("Файл не выбран. Завершение работы.")
        sys.exit(0)

    return file_path


def normalize_phone(phone_value) -> str:
    """
    Приводим телефон к строке и добавляем плюс, если его нет.
    Ожидается формат типа 79393474999 или +79393474999.
    """
    if phone_value is None:
        return ""

    phone_str = str(phone_value).strip()
    if not phone_str:
        return ""

    # Удаляем пробелы внутри номера
    phone_str = phone_str.replace(" ", "")

    if not phone_str.startswith("+"):
        phone_str = f"+{phone_str}"

    return phone_str


def upload_leads_from_xlsx(
    leads: List[Tuple[int, str, str]],
    project_id: str,
    access_token: str,
    default_name: str,
    tags: List[str],
    total_rows: int,
    skipped_empty_phone: int,
) -> None:
    """
    Принимает уже разобранные строки из XLSX и создаёт лиды в Скрозвоне.

    leads — список кортежей (excel_row_index, phone, comment):
        excel_row_index — номер строки в Excel (для логов),
        phone — нормализованный телефон (со знаком +),
        comment — строка из столбца B (застройщик / комментарий).
    """
    url = "https://api.skorozvon.ru/api/v2/leads"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    created_count = 0
    created_leads: List[Tuple[str, Optional[str]]] = []
    errors: List[Dict[str, Any]] = []

    for excel_row_index, phone, comment in leads:
        payload = {
            "type": "Lead",
            "name": default_name,
            "phones": [phone],
            "call_project_id": int(project_id) if str(project_id).isdigit() else project_id,
            "comment": comment,
            "tags": tags,
        }

        success, lead_id, error_info = send_lead_with_retry(
            url=url,
            payload=payload,
            headers=headers,
            phone=phone,
            row_index=excel_row_index,
        )

        if success:
            created_count += 1
            created_leads.append((phone, lead_id))
        else:
            errors.append(error_info)

        # Небольшая пауза между запросами, чтобы не спровоцировать лимиты API
        time.sleep(REQUEST_DELAY_SECONDS)

    # Итоговый вывод
    print("\n====== Итоговая статистика ======")
    print(
        f"Всего строк (без заголовка): {total_rows}\n"
        f"Лидов успешно создано: {created_count}\n"
        f"Строк без телефона (пропущено): {skipped_empty_phone}\n"
        f"Ошибок при создании лидов: {len(errors)}"
    )

    if created_leads:
        print("\nСписок созданных лидов:")
        for phone, lead_id in created_leads:
            phone_clean = phone.lstrip("+")
            lead_id_str = lead_id if lead_id is not None else "id не найден в ответе"
            print(
                f"{phone_clean} в проекте ID {project_id} создан лид ID {lead_id_str}"
            )

    if errors:
        print("\nСписок ошибок при создании лидов:")
        for err in errors:
            print(
                f"Строка {err.get('row')}, телефон {err.get('phone')}: "
                f"status={err.get('status')} message={err.get('message')}"
            )


def send_lead_with_retry(
    url: str,
    payload: Dict[str, Any],
    headers: Dict[str, str],
    phone: str,
    row_index: int,
) -> Tuple[bool, Optional[str], Dict[str, Any]]:
    """
    Отправляет запрос на создание лида с ретраями.

    Возвращает:
        success: удалось ли создать лид
        lead_id: строковый id лида, если удалось извлечь его из ответа
        error_info: словарь с описанием ошибки (если success=False)
    """
    last_status: Optional[int] = None
    last_message: str = ""

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            print(f"REQUEST (attempt {attempt}): POST {url} json={payload}")
            resp = requests.post(url, json=payload, headers=headers, timeout=60)
            print(f"RESPONSE: status={resp.status_code} body={resp.text}")

            last_status = resp.status_code

            if resp.status_code in (200, 201):
                lead_id = extract_lead_id(resp)
                return True, lead_id, {}

            # Коды, при которых есть смысл попробовать ещё раз
            if resp.status_code in (429,) or 500 <= resp.status_code < 600:
                last_message = f"временная ошибка или лимит API: {resp.status_code} {resp.text}"
            else:
                # Некорректный запрос или постоянная ошибка — нет смысла ретраить
                last_message = f"ошибка создания лида: {resp.status_code} {resp.text}"
                break

        except requests.RequestException as exc:
            last_message = f"сетевое исключение: {exc}"

        # Если это не последняя попытка — ждём и повторяем
        if attempt < MAX_RETRIES:
            sleep_time = RETRY_BACKOFF_SECONDS * attempt
            print(f"Ожидаю {sleep_time} сек перед повторной попыткой...")
            time.sleep(sleep_time)

    error_info = {
        "row": row_index,
        "phone": phone,
        "status": last_status,
        "message": last_message,
    }
    print(
        f"Не удалось создать лид для телефона {phone} (строка {row_index}) "
        f"после {MAX_RETRIES} попыток: {last_message}",
        file=sys.stderr,
    )
    return False, None, error_info


def extract_lead_id(resp: requests.Response) -> Optional[str]:
    """
    Пытается вытащить id лида из JSON-ответа.
    Структура ответа может отличаться, поэтому используем несколько вариантов.
    """
    try:
        data = resp.json()
    except ValueError:
        return None

    # Прямой id в корне
    for key in ("id", "lead_id"):
        value = data.get(key)
        if isinstance(value, (int, str)):
            return str(value)

    # Вложенные структуры
    for container_key in ("data", "result", "lead"):
        inner = data.get(container_key)
        if isinstance(inner, dict):
            for key in ("id", "lead_id"):
                value = inner.get(key)
                if isinstance(value, (int, str)):
                    return str(value)

    return None


def read_leads_from_xlsx(
    file_path: str,
) -> Tuple[List[Tuple[int, str, str]], int, int]:
    """
    Читает XLSX-файл и возвращает:
        - список лидов (строка, телефон, комментарий),
        - общее количество строк (без заголовка),
        - количество строк без телефона (пропущено).
    """
    wb = load_workbook(file_path)
    ws = wb.active

    leads: List[Tuple[int, str, str]] = []
    total_rows = 0
    skipped_empty_phone = 0

    # values_only=True — получаем сразу значения ячеек, без объектов
    # max_col=2 — берём только первые два столбца (телефон и комментарий),
    # чтобы не было ошибки "too many values to unpack", если в файле есть лишние колонки.
    for excel_row_index, (phone_cell, comment_cell) in enumerate(
        ws.iter_rows(min_row=2, max_col=2, values_only=True), start=2
    ):
        total_rows += 1

        phone = normalize_phone(phone_cell)
        if not phone:
            skipped_empty_phone += 1
            continue

        comment = str(comment_cell).strip() if comment_cell is not None else ""
        leads.append((excel_row_index, phone, comment))

    return leads, total_rows, skipped_empty_phone


def main() -> None:
    # 1. Загружаем переменные окружения и получаем токен
    load_dotenv()

    username = get_required_env("SKOROZVON_LOGIN")
    api_key = get_required_env("SKOROZVON_API_KEY")
    client_id = get_required_env("SKOROZVON_APP_ID")
    client_secret = get_required_env("SKOROZVON_APP_KEY")

    token = get_access_token(username, api_key, client_id, client_secret)

    # 2. Даём пользователю выбрать Excel-файл
    xlsx_path = select_xlsx_file()
    print(f"Выбран файл: {xlsx_path}")

    # 3. Читаем лиды из файла и показываем краткую информацию
    leads, total_rows, skipped_empty_phone = read_leads_from_xlsx(xlsx_path)

    print(f"\nПрочитано {total_rows} лидов из файла")
    print(f"\nНайдено {len(leads)} лидов с телефонами.")

    if leads:
        print("\nПример первых 3 лидов:")
        for _, phone, comment in leads[:3]:
            phone_clean = phone.lstrip("+")
            print(f"- Телефон: {phone_clean}\n")
            print(f"  Застройщик: {comment}")
            print("--------------------------------------------------")

    answer = input("Начать загрузку лидов в Скорозвон? (y/n): ").strip().lower()
    if answer not in ("y", "yes", "д", "да"):
        print("Загрузка отменена пользователем.")
        sys.exit(0)

    # 4. Загружаем лиды из файла без проверки дублей
    upload_leads_from_xlsx(
        leads=leads,
        project_id=CALL_PROJECT_ID,
        access_token=token,
        default_name=LEAD_DEFAULT_NAME,
        tags=DEFAULT_TAGS,
        total_rows=total_rows,
        skipped_empty_phone=skipped_empty_phone,
    )


if __name__ == "__main__":
    main()


