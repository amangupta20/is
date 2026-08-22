"""API endpoints for managing and generating versioned documents and spreadsheets."""

import base64
import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

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

router = APIRouter(prefix="/v1/artifacts", tags=["artifacts"])


def _serialize_artifact(
    art: Artifact,
    native_user_id: str,
    base_url: str,
    include_binary: bool = True,
    public_base_url: str | None = None,
) -> ArtifactResponse:
    versions = [
        ArtifactVersionResponse(
            version_num=v.version_num,
            content_sha256=v.content_sha256,
            file_size_bytes=v.file_size_bytes,
            mime_type=v.mime_type,
            change_summary=v.change_summary,
            created_at=(v.created_at or datetime.now(UTC)).isoformat(),
        )
        for v in art.versions
    ]
    target_v = next((v for v in art.versions if v.version_num == art.current_version_num), None)
    base64_data = None
    mime_type = target_v.mime_type if target_v else None
    if include_binary and target_v and target_v.binary_data:
        base64_data = base64.b64encode(target_v.binary_data).decode("ascii")

    effective_base = public_base_url.rstrip("/") if public_base_url else base_url.rstrip("/")

    return ArtifactResponse(
        id=str(art.id),
        native_user_id=native_user_id,
        title=art.title,
        slug=art.slug,
        artifact_type=art.artifact_type,
        current_version_num=art.current_version_num,
        created_at=(art.created_at or datetime.now(UTC)).isoformat(),
        updated_at=(art.updated_at or datetime.now(UTC)).isoformat(),
        native_project_id=art.native_project_id,
        native_folder_id=art.native_folder_id,
        versions=versions,
        download_url=f"{effective_base}/v1/artifacts/{art.id}/download",
        base64_data=base64_data,
        mime_type=mime_type,
    )


@router.post("/create", response_model=ArtifactResponse, status_code=status.HTTP_201_CREATED)
async def create_artifact(
    request_data: CreateArtifactRequest,
    request: Request,
    _: None = Depends(require_adapter_signature),
) -> ArtifactResponse:
    """Generate a new versioned document or spreadsheet."""
    async with request.app.state.session_factory() as session:
        repo = ArtifactRepository(session)
        try:
            artifact, _version = await repo.create_artifact(request_data)
            await session.commit()
            base_url = str(request.base_url)
            public_base = getattr(
                getattr(request.app.state, "settings", None), "public_base_url", None
            )
            return _serialize_artifact(
                artifact, request_data.native_user_id, base_url, public_base_url=public_base
            )
        except Exception as exc:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to generate artifact: {exc}",
            ) from exc


@router.get("/{artifact_id}", response_model=ArtifactResponse)
async def get_artifact_details(
    artifact_id: uuid.UUID,
    request: Request,
    native_user_id: Annotated[str, Query(description="Native User ID requesting the artifact")],
    _: None = Depends(require_adapter_signature),
) -> ArtifactResponse:
    """Get metadata and revision history for an artifact."""
    async with request.app.state.session_factory() as session:
        repo = ArtifactRepository(session)
        artifact = await repo.get_artifact(artifact_id)
        if not artifact:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")

        base_url = str(request.base_url)
        public_base = getattr(getattr(request.app.state, "settings", None), "public_base_url", None)
        return _serialize_artifact(artifact, native_user_id, base_url, public_base_url=public_base)


@router.get("/{artifact_id}/download")
async def download_artifact(
    artifact_id: uuid.UUID,
    request: Request,
    v: Annotated[
        int | None, Query(description="Specific version number; defaults to current")
    ] = None,
) -> Response:
    """Download the binary file for an artifact directly from PostgreSQL."""
    async with request.app.state.session_factory() as session:
        repo = ArtifactRepository(session)
        art = await repo.get_artifact(artifact_id)
        if not art or art.tombstoned_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")

        target_version_num = v if v is not None else art.current_version_num
        target_v = next(
            (ver for ver in art.versions if ver.version_num == target_version_num), None
        )
        if not target_v:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Version {target_version_num} not found",
            )

        ext = art.artifact_type
        filename = f"{art.slug}-v{target_v.version_num}.{ext}"
        return Response(
            content=target_v.binary_data,
            media_type=target_v.mime_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )


@router.get("/{artifact_id}/versions/{version_num}/raw")
async def get_raw_version_binary(
    artifact_id: uuid.UUID,
    version_num: int,
    request: Request,
) -> Response:
    """Raw endpoint for OnlyOffice Document Server to fetch the file from PostgreSQL."""
    async with request.app.state.session_factory() as session:
        repo = ArtifactRepository(session)
        art = await repo.get_artifact(artifact_id)
        if not art or art.tombstoned_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")

        target_v = next((ver for ver in art.versions if ver.version_num == version_num), None)
        if not target_v:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")

        return Response(content=target_v.binary_data, media_type=target_v.mime_type)


@router.post("/{artifact_id}/revise", response_model=ArtifactResponse)
async def revise_artifact(
    artifact_id: uuid.UUID,
    request_data: ReviseArtifactRequest,
    request: Request,
    _: None = Depends(require_adapter_signature),
) -> ArtifactResponse:
    """Append a new version N+1 to an existing artifact."""
    async with request.app.state.session_factory() as session:
        repo = ArtifactRepository(session)
        try:
            artifact, _version = await repo.add_version(artifact_id, request_data)
            await session.commit()
            base_url = str(request.base_url)
            public_base = getattr(
                getattr(request.app.state, "settings", None), "public_base_url", None
            )
            return _serialize_artifact(
                artifact, request_data.native_user_id, base_url, public_base_url=public_base
            )
        except Exception as exc:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to revise artifact: {exc}",
            ) from exc


@router.post("/{artifact_id}/onlyoffice/session")
async def open_onlyoffice_session(
    artifact_id: uuid.UUID,
    request: Request,
    native_user_id: Annotated[str, Query()],
    _: None = Depends(require_adapter_signature),
) -> dict[str, Any]:
    """Create an OnlyOffice editor session configuration."""
    settings = request.app.state.settings
    if not settings.onlyoffice_url:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="OnlyOffice Document Server is not configured (ASSISTANT_ONLYOFFICE_URL)",
        )

    async with request.app.state.session_factory() as session:
        repo = ArtifactRepository(session)
        art = await repo.get_artifact(artifact_id)
        if not art or art.tombstoned_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")

        target_v = next((v for v in art.versions if v.version_num == art.current_version_num), None)
        if not target_v:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Active version not found"
            )

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
@router.post("/onlyoffice/callback")
async def handle_onlyoffice_callback(
    request: Request,
    artifact_id: uuid.UUID | None = None,
    key: Annotated[str | None, Query()] = None,
    query_artifact_id: Annotated[uuid.UUID | None, Query(alias="artifact_id")] = None,
) -> dict[str, Any]:
    """Callback webhook invoked by OnlyOffice Document Server upon save/close/editing."""
    target_artifact_id = artifact_id or query_artifact_id
    if not target_artifact_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing artifact_id in path or query parameters",
        )

    try:
        raw_payload = await request.json()
        payload = raw_payload if isinstance(raw_payload, dict) else {}
    except (json.JSONDecodeError, ValueError):
        payload = {}

    settings = request.app.state.settings
    async with request.app.state.session_factory() as session:
        repo = ArtifactRepository(session)
        manager = OnlyOfficeManager(session, repo, settings)

        # Enforce JWT validation if secret is configured
        if settings.onlyoffice_jwt_secret:
            auth_header = request.headers.get("authorization")
            token: str | None = None
            if auth_header and auth_header.startswith("Bearer "):
                token = auth_header.removeprefix("Bearer ").strip()
            elif payload.get("token"):
                token = str(payload["token"])

            if not token:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Missing OnlyOffice JWT authorization token",
                )

            try:
                verified = manager.verify_callback_jwt(token)
                if isinstance(verified, dict) and "status" in verified:
                    payload = verified
            except jwt.PyJWTError as exc:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=f"Invalid OnlyOffice JWT signature: {exc}",
                ) from exc
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=f"OnlyOffice JWT verification failed: {exc}",
                ) from exc

        session_key = key or payload.get("key")
        if not session_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing session key",
            )

        res = await manager.handle_callback(
            artifact_id=target_artifact_id, session_key=session_key, payload=payload
        )
        await session.commit()
        return res


@router.delete("/{artifact_id}")
async def delete_artifact(
    artifact_id: uuid.UUID,
    request: Request,
    native_user_id: Annotated[str, Query(description="Open WebUI User ID")],
    _: None = Depends(require_adapter_signature),
) -> dict[str, str]:
    """Soft delete an artifact."""
    async with request.app.state.session_factory() as session:
        repo = ArtifactRepository(session)
        user = await repo.get_or_create_user(native_user_id)
        success = await repo.tombstone_artifact(artifact_id, user.id)
        if not success:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found or unauthorized"
            )
        await session.commit()
        return {"status": "deleted", "artifact_id": str(artifact_id)}
