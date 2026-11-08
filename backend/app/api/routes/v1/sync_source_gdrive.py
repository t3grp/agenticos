"""Google Drive browsing for the sync-source setup."""

import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.deps import Auth, McpConnectionSvc, require
from app.core.exceptions import NotFoundError
from app.core.permissions import Perm
from app.repositories import mcp_connection as mcp_connection_repo
from app.schemas.gdrive_folder import (
    GoogleDriveFolderPickerResponse,
    GoogleDrivePickerItem,
)
from app.services.portals.google_oauth import DRIVE
from app.services.rag.drive_api import DriveClient
from app.services.rag.remote_names import checked_drive_folder_id

router = APIRouter(
    dependencies=[Depends(require(Perm.CONNECTIONS_VIEW))],
)
_FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
_DRIVE_READONLY = "https://www.googleapis.com/auth/drive.readonly"


def _folders(
    drive: DriveClient, *, parent: str, corpora: str, drive_id: str | None
) -> list[GoogleDrivePickerItem]:
    parent_id = checked_drive_folder_id(parent)
    query = f"mimeType='{_FOLDER_MIME_TYPE}' and '{parent_id}' in parents and trashed=false"
    folders: list[GoogleDrivePickerItem] = []
    page_token = None
    while True:
        page = drive.list_page(
            q=query,
            fields="nextPageToken, files(id, name)",
            page_size=100,
            page_token=page_token,
            corpora=corpora,
            drive_id=drive_id,
        )
        folders.extend(
            GoogleDrivePickerItem(id=checked_drive_folder_id(item.id), name=item.name)
            for item in page.files
        )
        page_token = page.next_page_token
        if not page_token:
            return folders


def _shared_drives(drive: DriveClient) -> list[GoogleDrivePickerItem]:
    drives: list[GoogleDrivePickerItem] = []
    page_token = None
    while True:
        page = drive.list_drives_page(
            fields="nextPageToken, drives(id, name)",
            page_size=100,
            page_token=page_token,
        )
        drives.extend(
            GoogleDrivePickerItem(id=checked_drive_folder_id(item.id), name=item.name)
            for item in page.drives
        )
        page_token = page.next_page_token
        if not page_token:
            return drives


@router.get(
    "/gdrive/folders",
    response_model=GoogleDriveFolderPickerResponse,
)
async def list_google_drive_folders(
    service: McpConnectionSvc,
    ctx: Auth,
    account: UUID = Query(...),
    parent: str = Query("root"),
) -> GoogleDriveFolderPickerResponse:
    """List Shared Drives and My Drive folders for an org account."""
    account_row = await mcp_connection_repo.get_account(service.db, account)
    if (
        account_row is None
        or account_row.scope != "org"
        or account_row.organization_id != ctx.organization_id
        or account_row.catalog_key != "google"
        or account_row.oauth_payload is None
    ):
        raise NotFoundError(message="Google account not found")

    grant = await service.account_grant(account_row, organization_id=ctx.organization_id)
    if grant is None or not {DRIVE, _DRIVE_READONLY}.intersection(grant.scopes):
        raise NotFoundError(message="Google account not found")

    shared_drive_id: str | None = None
    if parent == "root":
        parent_id, corpora = "root", "user"
    elif parent.startswith("drive:"):
        shared_drive_id = checked_drive_folder_id(parent.removeprefix("drive:"))
        parent_id, corpora = shared_drive_id, "drive"
    else:
        parent_id, corpora = checked_drive_folder_id(parent), "allDrives"

    drive = DriveClient.from_oauth_grant(
        client_id=grant.client_id,
        client_secret=grant.client_secret,
        refresh_token=grant.refresh_token,
    )

    def read() -> GoogleDriveFolderPickerResponse:
        with drive:
            folders = _folders(
                drive,
                parent=parent_id,
                corpora=corpora,
                drive_id=shared_drive_id,
            )
            shared_drives = _shared_drives(drive) if parent == "root" else []
        return GoogleDriveFolderPickerResponse(drives=shared_drives, folders=folders)

    return await asyncio.to_thread(read)
