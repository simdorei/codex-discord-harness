from __future__ import annotations

import base64
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Final

from codex_remote_mcp_checkpoints import (
    CheckpointTarget,
    begin_checkpoint,
    checkpoint_transaction,
    finish_checkpoint,
)
from codex_remote_mcp_files import ProjectFileAccess, ProjectFileError
from simdorei_mcp_common.operation_outputs import (
    ImageEntry,
    ImageListOutput,
    ImageRetrieveOutput,
    ImageSaveOutput,
)

MAX_IMAGE_BYTES: Final = 5_242_880
MAX_IMAGE_RESULTS: Final = 200
IMAGE_TYPES: Final = {
    ".gif": ("image/gif", (b"GIF87a", b"GIF89a")),
    ".jpg": ("image/jpeg", (b"\xff\xd8\xff",)),
    ".jpeg": ("image/jpeg", (b"\xff\xd8\xff",)),
    ".png": ("image/png", (b"\x89PNG\r\n\x1a\n",)),
    ".webp": ("image/webp", (b"RIFF",)),
}


class ProjectImageError(ProjectFileError):
    """Raised when project image input is invalid or unsafe."""


def save_image(
    root: Path,
    path: str,
    data_base64: str,
    *,
    overwrite: bool,
) -> ImageSaveOutput:
    """Decode, validate, and atomically store one project image."""
    try:
        content = base64.b64decode(data_base64, validate=True)
    except ValueError as exc:
        raise ProjectImageError(path, "image data is not valid base64") from exc
    return _save_bytes(root, path, content, overwrite=overwrite)


def save_image_from_url(
    root: Path,
    path: str,
    url: str,
    *,
    overwrite: bool,
) -> ImageSaveOutput:
    """Fetch one public HTTPS image and store it inside the project."""
    try:
        import httpx2

        from codex_remote_mcp_http import (
            PublicNetworkError,
            public_http_client,
            validate_public_url_shape,
        )
    except ModuleNotFoundError as exc:
        if exc.name not in {"httpcore2", "httpx2"}:
            raise
        raise ProjectImageError(
            "<image-url>",
            "public URL image support requires optional dependencies; run install.ps1",
        ) from exc

    content = bytearray()
    try:
        validate_public_url_shape(url)
        with (
            public_http_client() as client,
            client.stream("GET", url) as response,
        ):
            response.raise_for_status()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > MAX_IMAGE_BYTES:
                    raise ProjectImageError(
                        path,
                        f"image exceeds {MAX_IMAGE_BYTES} bytes",
                    )
    except ProjectImageError:
        raise
    except PublicNetworkError as exc:
        raise ProjectImageError("<image-url>", str(exc)) from exc
    except (OSError, httpx2.HTTPError) as exc:
        raise ProjectImageError(
            "<image-url>",
            f"image download failed: {type(exc).__name__}",
        ) from exc
    return _save_bytes(root, path, bytes(content), overwrite=overwrite)


def list_images(root: Path) -> ImageListOutput:
    """List bounded, validated image files visible inside the project."""
    access = ProjectFileAccess(root)
    entries: list[ImageEntry] = []
    for candidate in sorted(access.root.rglob("*")):
        if len(entries) >= MAX_IMAGE_RESULTS:
            break
        if not candidate.is_file() or candidate.suffix.casefold() not in IMAGE_TYPES:
            continue
        relative = candidate.relative_to(access.root).as_posix()
        try:
            target = access.resolve_path(relative)
            entries.append(_image_entry(target, relative))
        except ProjectFileError:
            continue
    return ImageListOutput(images=tuple(entries))


def retrieve_image(root: Path, path: str) -> ImageRetrieveOutput:
    """Return one bounded project image as base64."""
    access = ProjectFileAccess(root)
    target = access.resolve_path(path)
    content = target.read_bytes()
    _validate_image(path, content)
    image = _image_entry(target, target.relative_to(access.root).as_posix())
    return ImageRetrieveOutput(
        image=image,
        data_base64=base64.b64encode(content).decode("ascii"),
    )


def _save_bytes(
    root: Path,
    path: str,
    content: bytes,
    *,
    overwrite: bool,
) -> ImageSaveOutput:
    access = ProjectFileAccess(root)
    target = access.resolve_path(path, require_file=False)
    if target.exists() and not overwrite:
        raise ProjectImageError(path, "file already exists")
    _validate_image(path, content)
    draft = begin_checkpoint(
        root,
        "save image",
        (CheckpointTarget(path=path, absolute_path=target),),
    )
    with checkpoint_transaction(draft):
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(target, content)
        finish_checkpoint(draft)
    relative = target.relative_to(access.root).as_posix()
    return ImageSaveOutput(
        image=_image_entry(target, relative),
        sha256=hashlib.sha256(content).hexdigest(),
    )


def _validate_image(path: str, content: bytes) -> str:
    if len(content) > MAX_IMAGE_BYTES:
        raise ProjectImageError(path, f"image exceeds {MAX_IMAGE_BYTES} bytes")
    suffix = Path(path).suffix.casefold()
    image_type = IMAGE_TYPES.get(suffix)
    if image_type is None:
        raise ProjectImageError(path, "supported types are PNG, JPEG, GIF, and WebP")
    media_type, signatures = image_type
    if not any(content.startswith(signature) for signature in signatures):
        raise ProjectImageError(path, "file content does not match its image type")
    if suffix == ".webp" and content[8:12] != b"WEBP":
        raise ProjectImageError(path, "file content does not match WebP")
    return media_type


def _image_entry(target: Path, relative: str) -> ImageEntry:
    size = target.stat().st_size
    if size > MAX_IMAGE_BYTES:
        raise ProjectImageError(relative, f"image exceeds {MAX_IMAGE_BYTES} bytes")
    return ImageEntry(
        path=relative,
        media_type=_validate_image(relative, target.read_bytes()),
        size_bytes=size,
    )


def _atomic_write(path: Path, content: bytes) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
