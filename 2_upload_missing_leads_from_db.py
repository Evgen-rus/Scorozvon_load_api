import logging
from logging.handlers import TimedRotatingFileHandler
import os
import random
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2 import service_account

# ====== Настройки поведения ======
# Режим теста: создать только 1 лид и остановиться
# Включить: True (создаст 1 лид и завершит работу)
# Выключить: False (обработает все подходящие лиды)
TEST_MODE_ONE_LEAD = False

# Имя файла базы данных в корне проекта
DB_PATH = "lr164.db"

# Проверка только за последние N дней
DAYS_LOOKBACK = 3

# Значения для лида (можно менять под задачу)
LEAD_DEFAULT_NAME = "ФИО не указано"
DEFAULT_TAGS: List[str] = ["столица_перехват"]
SKOROZVON_PROJECT_ID = "30000191458"  # Северная Столица (перехват)

# Повторы при временных ошибках API
MAX_API_RETRIES = 4
API_RETRY_BACKOFF_SECONDS = 1.0

# Google Sheets API (нужна запись)
GOOGLE_SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Размер пачки обновления Google Sheets
BATCH_UPDATE_SIZE = 50

# Время в таблице указано в МСК (UTC+3)
MSK_TZ = timezone(timedelta(hours=3))

LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "upload_missing_leads_from_db.log")

os.makedirs(LOG_DIR, exist_ok=True)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

file_handler = TimedRotatingFileHandler(
    LOG_FILE,
    when="midnight",
    backupCount=7,
    encoding="utf-8",
)
file_handler.setFormatter(
    logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
)

console_handler = logging.StreamHandler()
console_handler.setFormatter(
    logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
)

logger.handlers.clear()
logger.addHandler(file_handler)
logger.addHandler(console_handler)


def get_env_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"В переменной окружения {name} пусто или нет значения")
    return value


def create_sheets_service(credentials_file: str):
    if not os.path.exists(credentials_file):
        raise FileNotFoundError(f"Файл credentials не найден: {credentials_file}")
    credentials = service_account.Credentials.from_service_account_file(
        credentials_file, scopes=GOOGLE_SHEETS_SCOPES
    )
    return build("sheets", "v4", credentials=credentials)


def execute_with_retries(request, action_name: str):
    last_error = None
    for attempt in range(1, MAX_API_RETRIES + 1):
        try:
            return request.execute()
        except HttpError as exc:
            status = exc.resp.status if exc.resp else None
            if status in (429, 500, 502, 503, 504):
                last_error = exc
            else:
                raise
        except OSError as exc:
            last_error = exc

        if attempt < MAX_API_RETRIES:
            base_delay = API_RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))
            sleep_time = random.uniform(0, base_delay)
            logger.warning(
                "Временная ошибка API (%s). Повтор через %.2f сек...",
                action_name,
                sleep_time,
            )
            time.sleep(sleep_time)

    raise RuntimeError(
        f"Не удалось выполнить запрос к API ({action_name}) "
        f"после {MAX_API_RETRIES} попыток: {last_error}"
    )


def get_sheet_headers(service, spreadsheet_id: str, sheet_name: str) -> List[str]:
    request = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=f"{sheet_name}!1:1")
    )
    result = execute_with_retries(request, "get_sheet_headers")
    rows = result.get("values", [])
    return rows[0] if rows else []


def normalize_header(header: str) -> str:
    return re.sub(r"\s+", " ", header.strip().lower())


def map_headers(headers: List[str]) -> Dict[str, int]:
    mapped = {}
    for idx, header in enumerate(headers):
        name = normalize_header(header)
        if name == "id":
            mapped["source_id"] = idx
        elif name == "дата":
            mapped["event_dt"] = idx
        elif name == "номера":
            mapped["phone"] = idx
        elif name == "канал":
            mapped["channel"] = idx
        elif name == "застройщик_проект":
            mapped["developer_project"] = idx
        elif "id проекта" in name and "скорозвоне" in name:
            mapped["skorozvon_info"] = idx
    return mapped


def col_index_to_letter(index: int) -> str:
    """
    Индекс 1 -> A, 2 -> B, ..., 27 -> AA.
    """
    result = ""
    while index > 0:
        index, rem = divmod(index - 1, 26)
        result = chr(65 + rem) + result
    return result


def normalize_phone(phone_value: str) -> str:
    phone_str = str(phone_value).strip()
    if not phone_str:
        return ""
    phone_str = phone_str.replace(" ", "")
    if not phone_str.startswith("+"):
        phone_str = f"+{phone_str}"
    return phone_str


def parse_event_dt(value: str) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y",
    ]
    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.replace(tzinfo=MSK_TZ)
        except ValueError:
            continue
    return None


def get_access_token(username: str, api_key: str, client_id: str, client_secret: str) -> str:
    payload = {
        "grant_type": "password",
        "username": username,
        "api_key": api_key,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    logger.info("AUTH REQUEST: POST https://api.skorozvon.ru/oauth/token")
    resp = requests.post(
        "https://api.skorozvon.ru/oauth/token", data=payload, timeout=30
    )
    logger.info("AUTH RESPONSE: status=%s", resp.status_code)
    resp.raise_for_status()
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise ValueError("В ответе нет access_token")
    return token


def send_lead_with_retry(
    url: str,
    payload: Dict[str, object],
    headers: Dict[str, str],
    source_id: str,
) -> Tuple[bool, Optional[str]]:
    last_error = None
    for attempt in range(1, MAX_API_RETRIES + 1):
        try:
            logger.info("REQUEST (attempt %s): POST %s", attempt, url)
            resp = requests.post(url, json=payload, headers=headers, timeout=60)
            logger.info("RESPONSE: status=%s", resp.status_code)
            if resp.status_code in (200, 201):
                return True, extract_lead_id(resp)
            if resp.status_code in (429,) or 500 <= resp.status_code < 600:
                last_error = f"{resp.status_code} {resp.text}"
            else:
                last_error = f"{resp.status_code} {resp.text}"
                break
        except requests.RequestException as exc:
            last_error = str(exc)

        if attempt < MAX_API_RETRIES:
            base_delay = API_RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))
            sleep_time = random.uniform(0, base_delay)
            logger.warning(
                "Временная ошибка API (create_lead). Повтор через %.2f сек...",
                sleep_time,
            )
            time.sleep(sleep_time)

    logger.error(
        "Не удалось создать лид (source_id=%s) после %s попыток: %s",
        source_id,
        MAX_API_RETRIES,
        last_error,
    )
    return False, None


def extract_lead_id(resp: requests.Response) -> Optional[str]:
    try:
        data = resp.json()
    except ValueError:
        return None
    for key in ("id", "lead_id"):
        value = data.get(key)
        if isinstance(value, (int, str)):
            return str(value)
    for container_key in ("data", "result", "lead"):
        inner = data.get(container_key)
        if isinstance(inner, dict):
            for key in ("id", "lead_id"):
                value = inner.get(key)
                if isinstance(value, (int, str)):
                    return str(value)
    return None


def batch_update_sheet_cells(
    service,
    spreadsheet_id: str,
    updates: List[Tuple[str, List[List[str]]]],
) -> None:
    if not updates:
        return
    data = [{"range": rng, "values": values} for rng, values in updates]
    request = (
        service.spreadsheets()
        .values()
        .batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "RAW", "data": data},
        )
    )
    execute_with_retries(request, "batch_update_sheet_cells")


def load_leads_for_processing(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    rows = cur.execute(
        """
        SELECT source_id, event_dt, phone, channel, developer_project,
               skorozvon_info, sheet_name, sheet_row
        FROM leads
        ORDER BY event_dt ASC
        """
    ).fetchall()
    return rows


def main() -> None:
    load_dotenv()

    username = get_env_required("SKOROZVON_LOGIN")
    api_key = get_env_required("SKOROZVON_API_KEY")
    client_id = get_env_required("SKOROZVON_APP_ID")
    client_secret = get_env_required("SKOROZVON_APP_KEY")
    spreadsheet_id = get_env_required("GOOGLE_SHEET_ID")
    sheet_name = get_env_required("GOOGLE_SHEET_NAME")
    credentials_file = get_env_required("GOOGLE_CREDENTIALS_FILE")

    service = create_sheets_service(credentials_file)
    headers = get_sheet_headers(service, spreadsheet_id, sheet_name)
    header_map = map_headers(headers)
    if "skorozvon_info" not in header_map:
        raise ValueError(
            "Не найден столбец 'ID проекта и ID созданного в скорозвоне лида'"
        )

    pending_updates: List[Tuple[str, List[List[str]]]] = []
    pending_total = 0

    conn = sqlite3.connect(DB_PATH)
    try:
        rows = load_leads_for_processing(conn)
        if not rows:
            logger.info("Нет лидов для обработки.")
            return

        token = get_access_token(username, api_key, client_id, client_secret)
        headers_api = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        url = "https://api.skorozvon.ru/api/v2/leads"

        created_count = 0
        synced_count = 0
        skipped_no_row = 0

        def enqueue_update(row_index: int, value: str) -> None:
            nonlocal pending_total
            col_letter = col_index_to_letter(header_map["skorozvon_info"] + 1)
            cell = f"{sheet_name}!{col_letter}{row_index}"
            pending_updates.append((cell, [[value]]))
            pending_total += 1
            if pending_total >= BATCH_UPDATE_SIZE:
                batch_update_sheet_cells(service, spreadsheet_id, pending_updates)
                pending_updates.clear()
                pending_total = 0
        cutoff = datetime.now(MSK_TZ) - timedelta(days=DAYS_LOOKBACK)
        skipped_old = 0
        skipped_bad_date = 0
        for row in rows:
            event_dt = parse_event_dt(row["event_dt"])
            if not event_dt:
                skipped_bad_date += 1
                continue
            if event_dt < cutoff:
                skipped_old += 1
                continue

            if not row["sheet_row"]:
                skipped_no_row += 1
                continue

            if row["skorozvon_info"] and str(row["skorozvon_info"]).strip():
                enqueue_update(int(row["sheet_row"]), str(row["skorozvon_info"]))
                synced_count += 1
                continue

            phone = normalize_phone(row["phone"])
            if not phone:
                logger.warning("Пустой телефон, source_id=%s", row["source_id"])
                continue

            payload = {
                "type": "Lead",
                "name": LEAD_DEFAULT_NAME,
                "phones": [phone],
                "call_project_id": (
                    int(SKOROZVON_PROJECT_ID)
                    if SKOROZVON_PROJECT_ID.isdigit()
                    else SKOROZVON_PROJECT_ID
                ),
                "comment": row["developer_project"] or "",
                "tags": DEFAULT_TAGS,
            }

            success, lead_id = send_lead_with_retry(
                url=url,
                payload=payload,
                headers=headers_api,
                source_id=row["source_id"],
            )
            if not success:
                continue

            phone_clean = phone.lstrip("+")
            lead_id_str = lead_id if lead_id else "id не найден в ответе"
            info_text = (
                f"{phone_clean} в проекте ID {SKOROZVON_PROJECT_ID} "
                f"создан лид ID {lead_id_str}"
            )

            conn.execute(
                "UPDATE leads SET skorozvon_info = ? WHERE source_id = ?",
                (info_text, row["source_id"]),
            )
            conn.commit()

            enqueue_update(int(row["sheet_row"]), info_text)

            created_count += 1
            logger.info("Создан лид и обновлены БД/таблица: source_id=%s", row["source_id"])

            if TEST_MODE_ONE_LEAD and created_count >= 1:
                logger.info("Тестовый режим: создан 1 лид, остановка.")
                break
    finally:
        if pending_updates:
            batch_update_sheet_cells(service, spreadsheet_id, pending_updates)
        conn.close()

    logger.info(
        "Итог: создано лидов=%s, синхронизировано=%s, без sheet_row=%s, "
        "старые даты=%s, не распознали дату=%s",
        created_count,
        synced_count,
        skipped_no_row,
        skipped_old,
        skipped_bad_date,
    )


if __name__ == "__main__":
    main()
