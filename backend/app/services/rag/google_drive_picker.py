"""Paginated Google Drive listings for the sync-source folder picker."""

from app.schemas.gdrive_folder import GoogleDrivePickerItem
from app.services.rag.drive_api import DriveClient
from app.services.rag.remote_names import checked_drive_folder_id

_FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"


def list_folder_items(
    drive: DriveClient, *, parent: str, corpora: str, drive_id: str | None
) -> list[GoogleDrivePickerItem]:
    """List every folder page beneath the selected Drive parent."""
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


def list_shared_drive_items(drive: DriveClient) -> list[GoogleDrivePickerItem]:
    """List every page of shared drives visible to the connected account."""
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
