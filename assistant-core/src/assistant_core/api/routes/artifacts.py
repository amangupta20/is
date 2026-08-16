"""API endpoints for managing and generating versioned documents and spreadsheets."""

import os
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse

from assistant_core.api.dependencies import require_adapter_signature
from assistant_core.artifacts.models import Artifact
from assistant_core.artifacts.onlyoffice import OnlyOfficeManager
from assistant_core.artifacts.repository import ArtifactRepository
from assistant_core.artifacts.schemas import (
    ArtifactResponse,
    ArtifactVersionResponse,
    CreateArtifactRequest,
    ReviseArtifactRequest,
)
from assistant_core.artifacts.storage import LocalStorageBackend

router = APIRouter(prefix="/v1/artifacts", tags=["artifacts"])


def _serialize_artifact(art: Artifact, native_user_id: str, base_url: str) -> ArtifactResponse:
    """Convert Artifact ORM model to ArtifactResponse schema."""
    now_iso = datetime.now(UTC).isoformat()
    versions = [
        ArtifactVersionResponse(
            version_num=v.version_num,
            content_sha256=v.content_sha256,
            file_size_bytes=v.file_size_bytes,
            mime_type=v.mime_type,
            change_summary=v.change_summary,
            created_at=v.created_at.isoformat() if v.created_at else now_iso,
        )
        for v in art.versions
    ]

    download_url = f"{base_url.rstrip('/')}/v1/artifacts/{art.id}/download"

    return ArtifactResponse(
        id=str(art.id),
        native_user_id=native_user_id,
        title=art.title,
        slug=art.slug,
        artifact_type=art.artifact_type,
        current_version_num=art.current_version_num,
        created_at=art.created_at.isoformat() if art.created_at else now_iso,
        updated_at=art.updated_at.isoformat() if art.updated_at else now_iso,
        versions=versions,
        download_url=download_url,
    )


@router.post("/create", response_model=ArtifactResponse, status_code=status.HTTP_201_CREATED)
async def create_artifact(
    request_data: CreateArtifactRequest,
    request: Request,
    _: None = Depends(require_adapter_signature),
) -> ArtifactResponse:
    """Generate a new versioned document or spreadsheet."""
    storage = LocalStorageBackend(base_dir=request.app.state.settings.artifacts_dir)
    async with request.app.state.session_factory() as session:
        repo = ArtifactRepository(session, storage)
        try:
            artifact, _version = await repo.create_artifact(request_data)
            await session.commit()
            base_url = str(request.base_url)
            return _serialize_artifact(artifact, request_data.native_user_id, base_url)
        except Exception as exc:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to generate artifact: {exc}",
            ) from exc


@router.get("", response_model=list[ArtifactResponse])
async def list_artifacts(
    request: Request,
    native_user_id: str = Query(..., description="Open WebUI User ID"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _: None = Depends(require_adapter_signature),
) -> list[ArtifactResponse]:
    """List all active artifacts for a user."""
    storage = LocalStorageBackend(base_dir=request.app.state.settings.artifacts_dir)
    async with request.app.state.session_factory() as session:
        repo = ArtifactRepository(session, storage)
        arts = await repo.list_user_artifacts(native_user_id, limit=limit, offset=offset)
        base_url = str(request.base_url)
        return [_serialize_artifact(a, native_user_id, base_url) for a in arts]


@router.get("/{artifact_id}", response_model=ArtifactResponse)
async def get_artifact_details(
    artifact_id: uuid.UUID,
    request: Request,
    native_user_id: str = Query(..., description="Open WebUI User ID"),
    _: None = Depends(require_adapter_signature),
) -> ArtifactResponse:
    """Get metadata and complete version history of an artifact."""
    storage = LocalStorageBackend(base_dir=request.app.state.settings.artifacts_dir)
    async with request.app.state.session_factory() as session:
        repo = ArtifactRepository(session, storage)
        art = await repo.get_artifact(artifact_id)
        if not art or art.tombstoned_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
        base_url = str(request.base_url)
        return _serialize_artifact(art, native_user_id, base_url)


@router.get("/{artifact_id}/download")
async def download_artifact(
    artifact_id: uuid.UUID,
    request: Request,
    v: int | None = Query(None, description="Optional version number (defaults to latest)"),
) -> FileResponse:
    """Download the binary file for an artifact."""
    storage = LocalStorageBackend(base_dir=request.app.state.settings.artifacts_dir)
    async with request.app.state.session_factory() as session:
        repo = ArtifactRepository(session, storage)
        art = await repo.get_artifact(artifact_id)
        if not art or art.tombstoned_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")

        target_version_num = v if v is not None else art.current_version_num
        target_v = next((ver for ver in art.versions if ver.version_num == target_version_num), None)
        if not target_v:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Version {target_version_num} not found")

        if not os.path.isfile(target_v.storage_path):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Binary file missing on disk")

        ext = art.artifact_type
        filename = f"{art.slug}-v{target_v.version_num}.{ext}"
        return FileResponse(
            path=target_v.storage_path,
            media_type=target_v.mime_type,
            filename=filename,
        )


@router.get("/{artifact_id}/versions/{version_num}/raw")
async def get_raw_version_binary(
    artifact_id: uuid.UUID,
    version_num: int,
    request: Request,
) -> FileResponse:
    """Raw endpoint for OnlyOffice Document Server to fetch the file."""
    storage = LocalStorageBackend(base_dir=request.app.state.settings.artifacts_dir)
    async with request.app.state.session_factory() as session:
        repo = ArtifactRepository(session, storage)
        art = await repo.get_artifact(artifact_id)
        if not art or art.tombstoned_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")

        target_v = next((ver for ver in art.versions if ver.version_num == version_num), None)
        if not target_v or not os.path.isfile(target_v.storage_path):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")

        return FileResponse(path=target_v.storage_path, media_type=target_v.mime_type)


@router.post("/{artifact_id}/revise", response_model=ArtifactResponse)
async def revise_artifact(
    artifact_id: uuid.UUID,
    request_data: ReviseArtifactRequest,
    request: Request,
    _: None = Depends(require_adapter_signature),
) -> ArtifactResponse:
    """Append a new version N+1 to an existing artifact."""
    storage = LocalStorageBackend(base_dir=request.app.state.settings.artifacts_dir)
    async with request.app.state.session_factory() as session:
        repo = ArtifactRepository(session, storage)
        try:
            artifact, _version = await repo.add_version(artifact_id, request_data)
            await session.commit()
            base_url = str(request.base_url)
            return _serialize_artifact(artifact, request_data.native_user_id, base_url)
        except Exception as exc:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to revise artifact: {exc}",
            ) from exc


@router.post("/{artifact_id}/onlyoffice/session")
async def create_onlyoffice_session(
    artifact_id: uuid.UUID,
    request: Request,
    native_user_id: str = Query(..., description="Open WebUI User ID"),
    _: None = Depends(require_adapter_signature),
) -> dict[str, Any]:
    """Generate OnlyOffice Document Server config for web editing."""
    storage = LocalStorageBackend(base_dir=request.app.state.settings.artifacts_dir)
    settings = request.app.state.settings
    async with request.app.state.session_factory() as session:
        repo = ArtifactRepository(session, storage)
        art = await repo.get_artifact(artifact_id)
        if not art or art.tombstoned_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")

        target_v = next((v for v in art.versions if v.version_num == art.current_version_num), None)
        if not target_v:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Active version not found")

        manager = OnlyOfficeManager(session, repo, settings)
        base_url = str(request.base_url)
        config = await manager.create_editor_session(
            artifact=art,
            current_version=target_v,
            native_user_id=native_user_id,
            base_service_url=base_url,
        )
        await session.commit()
        return {
            "onlyoffice_url": settings.onlyoffice_url,
            "config": config,
        }


@router.post("/{artifact_id}/onlyoffice/callback")
async def onlyoffice_callback(
    artifact_id: uuid.UUID,
    payload: dict[str, Any],
    request: Request,
    key: str = Query(..., description="Session Key"),
) -> dict[str, Any]:
    """Save webhook from OnlyOffice Document Server."""
    storage = LocalStorageBackend(base_dir=request.app.state.settings.artifacts_dir)
    settings = request.app.state.settings
    async with request.app.state.session_factory() as session:
        repo = ArtifactRepository(session, storage)
        manager = OnlyOfficeManager(session, repo, settings)
        res = await manager.handle_callback(artifact_id=artifact_id, session_key=key, payload=payload)
        await session.commit()
        return res


@router.delete("/{artifact_id}")
async def delete_artifact(
    artifact_id: uuid.UUID,
    request: Request,
    native_user_id: str = Query(..., description="Open WebUI User ID"),
    _: None = Depends(require_adapter_signature),
) -> dict[str, str]:
    """Soft delete an artifact."""
    storage = LocalStorageBackend(base_dir=request.app.state.settings.artifacts_dir)
    async with request.app.state.session_factory() as session:
        repo = ArtifactRepository(session, storage)
        success = await repo.tombstone_artifact(artifact_id, native_user_id)
        if not success:
            await session.rollback()
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found or unauthorized")
        await session.commit()
        return {"status": "deleted", "artifact_id": str(artifact_id)}
