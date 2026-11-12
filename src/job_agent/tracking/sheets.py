"""Tracking file in Google Sheets (cloud mode), append only, like the Excel file.

Same sheets and columns as the Excel file. The agent only ADDS rows at the
bottom (values.append): it never rewrites an existing row nor the user's
columns (status, application date, notes...).

The spreadsheet is created by the agent the first time (Google permission
`drive.file`: the agent sees only the files it created), in French and in
the Paris time zone, with frozen headers, filters and the status drop-down.
Its id is kept in the agent's memory, or given in
`tracking.google_sheets.spreadsheet_id`.
"""

import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from job_agent.processing.filters import CONTRACT_LABELS
from job_agent.processing.verification import LABELS as VERIFICATION_LABELS
from job_agent.storage import Storage
from job_agent.tracking.excel import (
    AGENT_COLUMNS,
    DESCRIPTION_COLUMNS,
    REJECTED_COLUMNS,
    REPLY_COLUMNS,
    SHEET_DESCRIPTIONS,
    SHEET_REJECTED,
    SHEET_REPLIES,
    SHEET_TRACKING,
    SHEET_VERIFICATIONS,
    STATUS_CHOICES,
    USER_COLUMNS,
    VERIFICATION_COLUMNS,
    WriteResult,
    _local,
    _tracking_row,
)

STATE_KEY = "google_sheets_id"
TITLE = "Suivi des candidatures (job-agent)"
MAX_CELL = 45000  # Google Sheets refuses cells above 50 000 characters
SHEETS = {
    SHEET_TRACKING: AGENT_COLUMNS + USER_COLUMNS,
    SHEET_DESCRIPTIONS: DESCRIPTION_COLUMNS,
    SHEET_REJECTED: REJECTED_COLUMNS,
    SHEET_VERIFICATIONS: VERIFICATION_COLUMNS,
    SHEET_REPLIES: REPLY_COLUMNS,
}


class SheetsUnavailable(Exception):
    """The spreadsheet cannot be written now. Message in French, without secret."""


def build_service():
    from googleapiclient.discovery import build

    from job_agent.gmail.auth import SHEETS_SCOPES, load_credentials

    return build("sheets", "v4", credentials=load_credentials(SHEETS_SCOPES), cache_discovery=False)


def _cell(value) -> object:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y %H:%M")
    if isinstance(value, str) and len(value) > MAX_CELL:
        return value[:MAX_CELL] + " [texte coupé : voir la base de l'agent]"
    return value


def _rows(values: list[list[object]]) -> list[list[object]]:
    return [[_cell(value) for value in row] for row in values]


def create_spreadsheet(service, sheets: dict = SHEETS) -> str:
    """New spreadsheet with its sheets, headers, frozen first row, filters and status list."""
    body = {
        "properties": {"title": TITLE, "locale": "fr_FR", "timeZone": "Europe/Paris"},
        "sheets": [{"properties": {"title": name, "gridProperties": {"frozenRowCount": 1}}} for name in sheets],
    }
    created = service.spreadsheets().create(body=body).execute()
    spreadsheet_id = created["spreadsheetId"]
    ids = {sheet["properties"]["title"]: sheet["properties"]["sheetId"] for sheet in created["sheets"]}
    requests = []
    for name, columns in sheets.items():
        requests.append({"setBasicFilter": {"filter": {"range": {"sheetId": ids[name]}}}})
        requests.append(
            {
                "repeatCell": {
                    "range": {"sheetId": ids[name], "startRowIndex": 0, "endRowIndex": 1},
                    "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                    "fields": "userEnteredFormat.textFormat.bold",
                }
            }
        )
        for index, (_title, width) in enumerate(columns):
            requests.append(
                {
                    "updateDimensionProperties": {
                        "range": {
                            "sheetId": ids[name],
                            "dimension": "COLUMNS",
                            "startIndex": index,
                            "endIndex": index + 1,
                        },
                        "properties": {"pixelSize": int(width * 7)},
                        "fields": "pixelSize",
                    }
                }
            )
    status_column = len(AGENT_COLUMNS)
    requests.append(
        {
            "setDataValidation": {
                "range": {
                    "sheetId": ids[SHEET_TRACKING],
                    "startRowIndex": 1,
                    "startColumnIndex": status_column,
                    "endColumnIndex": status_column + 1,
                },
                "rule": {
                    "condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": s} for s in STATUS_CHOICES]},
                    "strict": False,
                    "showCustomUi": True,
                },
            }
        }
    )
    service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()
    for name, columns in sheets.items():
        append_rows(service, spreadsheet_id, name, [[title for title, _ in columns]])
    return spreadsheet_id


def spreadsheet_id(service, storage: Storage, configured: str) -> str:
    """The spreadsheet to write: configured, remembered, or created now."""
    if configured:
        return configured
    known = storage.get_state(STATE_KEY)
    if known:
        return known
    created = create_spreadsheet(service)
    storage.set_state(STATE_KEY, created)
    return created


def append_rows(service, spreadsheet_id: str, sheet: str, rows: list[list[object]]) -> None:
    if not rows:
        return
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"'{sheet}'!A1",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": _rows(rows)},
    ).execute()


def append_offers(
    service,
    spreadsheet_id: str,
    pending: list[sqlite3.Row],
    members_of,
    keep_rejected: bool,
    timezone_name: str = "Europe/Paris",
    events: list[sqlite3.Row] = (),
    replies: list[sqlite3.Row] = (),
) -> WriteResult:
    """Append pending offers and verification changes. Raises SheetsUnavailable."""
    timezone = ZoneInfo(timezone_name)
    tracking, descriptions, rejected_rows, verification_rows = [], [], [], []
    written: list[int] = []
    kept = rejected = 0
    for main in pending:
        if main["kept"]:
            tracking.append(_tracking_row(main, members_of(main["group_id"]), timezone))
            descriptions.append([main["id"], main["company"], main["title"], main["description"]])
            kept += 1
        elif keep_rejected:
            rejected_rows.append(
                [
                    main["id"],
                    main["company"],
                    main["title"],
                    main["location"],
                    CONTRACT_LABELS.get(main["contract_type"] or "", ""),
                    main["source_name"],
                    _local(main["published_at"], timezone),
                    _local(main["first_seen_at"], timezone),
                    "; ".join(json.loads(main["reasons"])),
                    main["apply_url"] or main["url"],
                ]
            )
            rejected += 1
        written.append(main["id"])
    for event in events:
        verification_rows.append(
            [
                _local(event["at"], timezone),
                event["group_id"],
                event["company"] or event["company_key"],
                event["title"],
                VERIFICATION_LABELS.get(event["old_status"] or "", ""),
                VERIFICATION_LABELS.get(event["new_status"], event["new_status"]),
                event["detail"],
            ]
        )
    reply_rows = []
    for row in replies:
        reply = json.loads(row["reply"])
        received = datetime.fromisoformat(reply["received_at"]).astimezone(timezone).replace(tzinfo=None)
        reply_rows.append(
            [
                received,
                reply["offer_id"],
                reply["company"],
                reply["title"],
                reply["sender_name"],
                reply["subject"],
                reply["meaning"],
                reply["suggested_status"],
            ]
        )
    try:
        append_rows(service, spreadsheet_id, SHEET_TRACKING, tracking)
        append_rows(service, spreadsheet_id, SHEET_DESCRIPTIONS, descriptions)
        append_rows(service, spreadsheet_id, SHEET_REJECTED, rejected_rows)
        append_rows(service, spreadsheet_id, SHEET_VERIFICATIONS, verification_rows)
        append_rows(service, spreadsheet_id, SHEET_REPLIES, reply_rows)
    except Exception as error:  # quota, network, permission: retried at the next run
        raise SheetsUnavailable(f"écriture Google Sheets impossible ({type(error).__name__})") from None
    return WriteResult(written, kept, rejected, [event["id"] for event in events])


def import_excel(service, spreadsheet_id: str, excel_path) -> dict[str, int]:
    """Copy every row of the local Excel tracking file (the user's columns
    included) into the spreadsheet, once, when moving to the cloud."""
    from openpyxl import load_workbook

    workbook = load_workbook(excel_path, read_only=True)
    copied = {}
    for name in SHEETS:
        if name not in workbook.sheetnames:
            continue
        rows = [
            list(row)
            for row in workbook[name].iter_rows(min_row=2, values_only=True)
            if any(value not in (None, "") for value in row)
        ]
        append_rows(service, spreadsheet_id, name, rows)
        copied[name] = len(rows)
    return copied
