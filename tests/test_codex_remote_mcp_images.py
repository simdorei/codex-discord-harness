from __future__ import annotations

import base64
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import assert_never

import pytest
from pydantic import HttpUrl

from codex_remote_mcp_dispatch import LocalProjectDispatcher
from simdorei_mcp_common.messages import (
    ListFilesResult,
    OperationErrorResult,
    ProjectInfoResult,
    ProjectOperationCommand,
    ProjectOperationResult,
    ReadFileResult,
    RequestId,
    WriteFileResult,
)
from simdorei_mcp_common.operation_outputs import (
    ImageListOutput,
    ImageRetrieveOutput,
    ImageSaveOutput,
)
from simdorei_mcp_common.operation_requests import (
    ListImagesRequest,
    RetrieveImageRequest,
    SaveImageFromUrlRequest,
    SaveImageRequest,
)
from tests.remote_mcp_dispatch_support import (
    TEST_PROJECT_SESSION_ID,
    activate_test_session,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\nremote-image"


def test_non_url_features_load_when_http_dependencies_are_missing() -> None:
    # Given
    repository = Path(__file__).resolve().parents[1]
    script = """
import builtins
from pathlib import Path

real_import = builtins.__import__

def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name.split(".", 1)[0] in {"httpcore2", "httpx2"}:
        raise ModuleNotFoundError(f"No module named {name!r}", name=name)
    return real_import(name, globals, locals, fromlist, level)

builtins.__import__ = guarded_import
import codex_remote_mcp_dispatch
import codex_remote_mcp_images

try:
    codex_remote_mcp_images.save_image_from_url(
        Path.cwd(),
        "assets/test.png",
        "https://example.com/test.png",
        overwrite=False,
    )
except codex_remote_mcp_images.ProjectImageError as exc:
    assert "run install.ps1" in str(exc)
else:
    raise AssertionError("URL image support unexpectedly loaded")
"""

    # When
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repository,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    # Then
    assert result.returncode == 0, result.stderr


def test_image_save_writes_validated_image(tmp_path: Path) -> None:
    # Given
    root, dispatcher = _bound_project(tmp_path)
    encoded = base64.b64encode(PNG_BYTES).decode("ascii")

    # When
    result = dispatcher.execute(
        _command(
            "save",
            SaveImageRequest(path="assets/test.png", data_base64=encoded),
        )
    )

    # Then
    match result:
        case ProjectOperationResult(output=ImageSaveOutput(image=image)):
            assert image.media_type == "image/png"
            assert (root / image.path).read_bytes() == PNG_BYTES
        case ProjectOperationResult() | OperationErrorResult():
            raise AssertionError(f"unexpected result: {result.type}")
        case unreachable:
            assert_never(unreachable)


def test_image_list_finds_project_images(tmp_path: Path) -> None:
    # Given
    root, dispatcher = _bound_project(tmp_path)
    assets = root / "assets"
    assets.mkdir()
    (assets / "test.png").write_bytes(PNG_BYTES)

    # When
    result = dispatcher.execute(_command("list", ListImagesRequest()))

    # Then
    match result:
        case ProjectOperationResult(output=ImageListOutput(images=images)):
            assert tuple(image.path for image in images) == ("assets/test.png",)
        case ProjectOperationResult() | OperationErrorResult():
            raise AssertionError(f"unexpected result: {result.type}")
        case unreachable:
            assert_never(unreachable)


def test_image_retrieve_returns_base64_bytes(tmp_path: Path) -> None:
    # Given
    root, dispatcher = _bound_project(tmp_path)
    assets = root / "assets"
    assets.mkdir()
    (assets / "test.png").write_bytes(PNG_BYTES)

    # When
    result = dispatcher.execute(
        _command("retrieve", RetrieveImageRequest(path="assets/test.png"))
    )

    # Then
    match result:
        case ProjectOperationResult(output=ImageRetrieveOutput(data_base64=data)):
            assert base64.b64decode(data) == PNG_BYTES
        case ProjectOperationResult() | OperationErrorResult():
            raise AssertionError(f"unexpected result: {result.type}")
        case unreachable:
            assert_never(unreachable)


def test_image_url_rejects_loopback_destination(tmp_path: Path) -> None:
    # Given
    _, dispatcher = _bound_project(tmp_path)

    # When
    result = dispatcher.execute(
        _command(
            "url",
            SaveImageFromUrlRequest(
                path="assets/test.png",
                url=HttpUrl("https://127.0.0.1/test.png"),
            ),
        )
    )

    # Then
    match result:
        case OperationErrorResult(message=message):
            assert "public network" in message
        case (
            ProjectInfoResult()
            | ListFilesResult()
            | ReadFileResult()
            | WriteFileResult()
            | ProjectOperationResult()
        ):
            raise AssertionError(f"unexpected result: {result.type}")
        case unreachable:
            assert_never(unreachable)


def test_image_save_rolls_back_when_checkpoint_persist_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, dispatcher = _bound_project(tmp_path)
    monkeypatch.setattr(
        "codex_remote_mcp_images.finish_checkpoint",
        lambda _draft: (_ for _ in ()).throw(OSError("disk failure")),
    )

    with pytest.raises(OSError, match="disk failure"):
        dispatcher.execute(
            _command(
                "rollback",
                SaveImageRequest(
                    path="assets/test.png",
                    data_base64=base64.b64encode(PNG_BYTES).decode("ascii"),
                ),
            )
        )

    assert not (root / "assets/test.png").exists()


def _bound_project(tmp_path: Path) -> tuple[Path, LocalProjectDispatcher]:
    root = tmp_path / "project"
    root.mkdir()
    dispatcher = LocalProjectDispatcher()
    dispatcher.upsert(
        "thread-a",
        root,
        datetime.now(UTC) + timedelta(minutes=10),
    )
    activate_test_session(dispatcher)
    return root, dispatcher


def _command(
    suffix: str,
    operation: (
        SaveImageRequest
        | SaveImageFromUrlRequest
        | ListImagesRequest
        | RetrieveImageRequest
    ),
) -> ProjectOperationCommand:
    return ProjectOperationCommand(
        request_id=RequestId(f"request-{suffix}"),
        thread_id="thread-a",
        computer_session_id=TEST_PROJECT_SESSION_ID,
        operation=operation,
    )
