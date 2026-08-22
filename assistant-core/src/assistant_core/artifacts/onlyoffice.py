"""OnlyOffice Document Server integration, signed configuration, and save callbacks."""

import hashlib
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_core.artifacts.models import Artifact, ArtifactVersion, OnlyOfficeSession
from assistant_core.artifacts.repository import ArtifactRepository
from assistant_core.config import Settings

logger = logging.getLogger(__name__)

DOCUMENT_TYPE_MAP = {
    "docx": "word",
    "doc": "word",
    "txt": "word",
    "odt": "word",
    "xlsx": "cell",
    "xls": "cell",
    "csv": "cell",
    "ods": "cell",
    "pptx": "slide",
    "ppt": "slide",
    "odp": "slide",
}


class OnlyOfficeManager:
    """Handles OnlyOffice session creation, JWT signing, and webhook callbacks."""

    def __init__(self, session: AsyncSession, repo: ArtifactRepository, settings: Settings) -> None:
        self.session = session
        self.repo = repo
        self.settings = settings

    def verify_callback_jwt(self, token: str) -> dict[str, Any]:
        """Verify and decode OnlyOffice JWT token using configured secret."""
        if not self.settings.onlyoffice_jwt_secret:
            raise ValueError("OnlyOffice JWT secret is not configured")
        secret = self.settings.onlyoffice_jwt_secret.get_secret_value()
        decoded = jwt.decode(token, secret, algorithms=["HS256"])
        if isinstance(decoded, dict):
            if "payload" in decoded and isinstance(decoded["payload"], dict):
                return decoded["payload"]
            return decoded
        return {}

    async def create_editor_session(
        self,
        artifact: Artifact,
        current_version: ArtifactVersion,
        native_user_id: str,
        base_service_url: str,
    ) -> dict[str, Any]:
        """Generate a complete DocsAPI.DocEditor configuration dictionary."""
        user = await self.repo.get_or_create_user(native_user_id)
        session_key = hashlib.sha256(
            f"{artifact.id}-{current_version.version_num}-{datetime.now(UTC).timestamp()}".encode()
        ).hexdigest()[:20]

        # Record session in database
        oo_session = OnlyOfficeSession(
            id=uuid.uuid4(),
            artifact_id=artifact.id,
            user_id=user.id,
            session_key=session_key,
            status="active",
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
        self.session.add(oo_session)
        await self.session.flush()

        doc_type = DOCUMENT_TYPE_MAP.get(artifact.artifact_type, "word")
        ext = artifact.artifact_type
        title_with_ext = (
            f"{artifact.title}.{ext}" if not artifact.title.endswith(f".{ext}") else artifact.title
        )

        # Build raw download & callback URLs
        download_url = f"{base_service_url.rstrip('/')}/v1/artifacts/{artifact.id}/versions/{current_version.version_num}/raw"
        callback_url = f"{base_service_url.rstrip('/')}/v1/artifacts/{artifact.id}/onlyoffice/callback?key={session_key}"

        config: dict[str, Any] = {
            "document": {
                "fileType": ext,
                "key": session_key,
                "title": title_with_ext,
                "url": download_url,
                "permissions": {
                    "download": True,
                    "edit": True,
                    "print": True,
                },
            },
            "documentType": doc_type,
            "editorConfig": {
                "callbackUrl": callback_url,
                "user": {
                    "id": str(user.id),
                    "name": native_user_id,
                },
                "customization": {
                    "autosave": True,
                    "forcesave": True,
                },
            },
        }

        # If JWT Secret is configured, sign the payload
        jwt_secret = (
            self.settings.onlyoffice_jwt_secret.get_secret_value()
            if self.settings.onlyoffice_jwt_secret
            else None
        )
        if jwt_secret:
            token = jwt.encode(config, jwt_secret, algorithm="HS256")
            config["token"] = token

        return config

    async def handle_callback(
        self,
        artifact_id: uuid.UUID,
        session_key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Handle Document Server save/status webhook."""
        # Look up session
        stmt = select(OnlyOfficeSession).where(
            OnlyOfficeSession.artifact_id == artifact_id,
            OnlyOfficeSession.session_key == session_key,
        )
        res = await self.session.execute(stmt)
        oo_session = res.scalar_one_or_none()
        if not oo_session:
            return {"error": 1, "message": "Invalid session key"}

        status = payload.get("status")

        # Status 1: Document is being edited
        if status == 1:
            oo_session.status = "editing"
            await self.session.flush()
            return {"error": 0, "status": "editing"}

        # Status 4: Document is closed with no changes
        if status == 4:
            oo_session.status = "closed"
            await self.session.flush()
            return {"error": 0, "status": "closed"}

        # Status 3: Saving error (non-destructive)
        if status == 3:
            logger.warning(
                "OnlyOffice document saving error for artifact %s, session %s: %s",
                artifact_id,
                session_key,
                payload,
            )
            oo_session.status = "error"
            await self.session.flush()
            return {"error": 0, "status": "error_recorded"}

        # Status 2 (Ready for saving) or Status 6 (Force save)
        if status in (2, 6):
            download_url = payload.get("url")
            if not download_url:
                oo_session.status = "error"
                await self.session.flush()
                return {"error": 1, "message": "Missing download url in callback"}

            # Download binary from Document Server
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(download_url)
                if resp.status_code != 200 or not resp.content:
                    oo_session.status = "error"
                    await self.session.flush()
                    return {
                        "error": 1,
                        "message": f"Failed to download edited document: status {resp.status_code}",
                    }
                edited_bytes = resp.content

            # Save new immutable version
            _, new_version = await self.repo.add_raw_binary_version(
                artifact_id=artifact_id,
                user_id=oo_session.user_id,
                raw_data=edited_bytes,
                change_summary="OnlyOffice Web Edit",
            )

            oo_session.status = "saved"
            await self.session.flush()

            return {"error": 0, "saved_version": new_version.version_num}

        # Status 7: Corrupted force save error
        if status == 7:
            logger.warning(
                "OnlyOffice document force save error for artifact %s, session %s: %s",
                artifact_id,
                session_key,
                payload,
            )
            oo_session.status = "error"
            await self.session.flush()
            return {"error": 0, "status": "error_recorded"}

        return {"error": 0, "status": "acknowledged"}
