"""Storage backend for local persistent volume artifact binaries."""

import hashlib
import shutil
import tempfile
import uuid
from pathlib import Path


class ArtifactStorageError(RuntimeError):
    """Raised on storage I/O failures."""


class LocalStorageBackend:
    """Stores artifact version binaries in a dedicated persistent directory."""

    def __init__(self, base_dir: str = "/data/artifacts") -> None:
        self.base_dir = Path(base_dir)

    def save(
        self,
        user_id: uuid.UUID,
        artifact_id: uuid.UUID,
        version_num: int,
        ext: str,
        data: bytes,
    ) -> tuple[str, str, int]:
        """Atomically save binary data to disk and return (storage_path, sha256, size)."""
        clean_ext = ext.lstrip(".")
        content_sha256 = hashlib.sha256(data).hexdigest()
        file_size = len(data)

        target_dir = self.base_dir / str(user_id) / str(artifact_id)
        target_file = target_dir / f"v{version_num}.{clean_ext}"

        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            # Write to temp file first to ensure atomic replacement
            with tempfile.NamedTemporaryFile(
                dir=target_dir, delete=False, prefix=".tmp_v_"
            ) as tmp:
                tmp.write(data)
                tmp_path = Path(tmp.name)

            tmp_path.replace(target_file)
            return str(target_file.resolve()), content_sha256, file_size
        except Exception as exc:
            raise ArtifactStorageError(f"Failed to save artifact binary: {exc}") from exc

    def read(self, storage_path: str) -> bytes:
        """Read binary data from disk path."""
        p = Path(storage_path)
        if not p.is_file():
            raise ArtifactStorageError(f"Artifact file not found: {storage_path}")
        try:
            return p.read_bytes()
        except Exception as exc:
            raise ArtifactStorageError(f"Failed to read artifact binary: {exc}") from exc

    def delete_artifact_tree(self, user_id: uuid.UUID, artifact_id: uuid.UUID) -> None:
        """Remove all versions for an artifact."""
        target_dir = self.base_dir / str(user_id) / str(artifact_id)
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
