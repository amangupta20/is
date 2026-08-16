"""Unit tests for LocalStorageBackend."""

import uuid
from pathlib import Path

from assistant_core.artifacts.storage import LocalStorageBackend


def test_local_storage_backend_save_read_delete(tmp_path: Path) -> None:
    backend = LocalStorageBackend(base_dir=str(tmp_path))
    user_id = uuid.uuid4()
    art_id = uuid.uuid4()
    sample_bytes = b"Hello, this is a test document binary."

    storage_path, sha256_hash, file_size = backend.save(
        user_id=user_id,
        artifact_id=art_id,
        version_num=1,
        ext="docx",
        data=sample_bytes,
    )

    assert storage_path.endswith("v1.docx")
    assert file_size == len(sample_bytes)
    assert len(sha256_hash) == 64

    # Read back
    read_bytes = backend.read(storage_path)
    assert read_bytes == sample_bytes

    # Version 2
    v2_bytes = b"Version 2 updated content."
    v2_path, _v2_hash, _v2_size = backend.save(
        user_id=user_id,
        artifact_id=art_id,
        version_num=2,
        ext="docx",
        data=v2_bytes,
    )
    assert v2_path.endswith("v2.docx")
    assert backend.read(v2_path) == v2_bytes
    assert backend.read(storage_path) == sample_bytes  # v1 untouched!

    # Delete tree
    backend.delete_artifact_tree(user_id=user_id, artifact_id=art_id)
    assert not Path(storage_path).exists()
    assert not Path(v2_path).exists()
