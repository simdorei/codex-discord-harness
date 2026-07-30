from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class OperationRequest(BaseModel):
    """Immutable input for one thread-bound project capability."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class ProjectRulesRequest(OperationRequest):
    kind: Literal["project_rules"] = "project_rules"


class ProjectStatusRequest(OperationRequest):
    kind: Literal["project_status"] = "project_status"


class CodeSearchRequest(OperationRequest):
    kind: Literal["code_search"] = "code_search"
    query: str = Field(min_length=1, max_length=500)
    max_results: int = Field(default=100, ge=1, le=200)


class FileApplyPatchRequest(OperationRequest):
    kind: Literal["file_apply_patch"] = "file_apply_patch"
    patch: str = Field(min_length=1, max_length=10_485_760)
    precondition_hashes: dict[str, str] = Field(default_factory=dict)


class FileCreateRequest(OperationRequest):
    kind: Literal["file_create"] = "file_create"
    path: str = Field(min_length=1, max_length=1_000)
    content: str = Field(max_length=1_048_576)
    overwrite: bool = False


class CommandListRequest(OperationRequest):
    kind: Literal["command_list"] = "command_list"


class CommandRunRequest(OperationRequest):
    kind: Literal["command_run"] = "command_run"
    command_id: str = Field(min_length=1, max_length=300)
    timeout_seconds: int = Field(default=60, ge=1, le=300)


class RepoStatusRequest(OperationRequest):
    kind: Literal["repo_status"] = "repo_status"


class RepoDiffRequest(OperationRequest):
    kind: Literal["repo_diff"] = "repo_diff"


class GitCommitRequest(OperationRequest):
    kind: Literal["git_commit"] = "git_commit"
    message: str = Field(min_length=1, max_length=500)
    paths: tuple[str, ...] = Field(min_length=1, max_length=200)


class GitPushRequest(OperationRequest):
    kind: Literal["git_push"] = "git_push"
    remote: str = Field(default="origin", pattern=r"^[A-Za-z0-9._-]+$")
    branch: str | None = Field(
        default=None,
        max_length=250,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$",
    )


class SaveImageRequest(OperationRequest):
    kind: Literal["save_image"] = "save_image"
    path: str = Field(min_length=1, max_length=1_000)
    data_base64: str = Field(min_length=4, max_length=7_000_000)
    overwrite: bool = False


class SaveImageFromUrlRequest(OperationRequest):
    kind: Literal["save_image_from_url"] = "save_image_from_url"
    path: str = Field(min_length=1, max_length=1_000)
    url: HttpUrl
    overwrite: bool = False


class ListImagesRequest(OperationRequest):
    kind: Literal["list_images"] = "list_images"


class RetrieveImageRequest(OperationRequest):
    kind: Literal["retrieve_image"] = "retrieve_image"
    path: str = Field(min_length=1, max_length=1_000)


class CheckpointListRequest(OperationRequest):
    kind: Literal["checkpoint_list"] = "checkpoint_list"


class CheckpointShowRequest(OperationRequest):
    kind: Literal["checkpoint_show"] = "checkpoint_show"
    checkpoint_id: str = Field(pattern=r"^cp_[a-f0-9]{16}$")


class CheckpointRestoreRequest(OperationRequest):
    kind: Literal["checkpoint_restore"] = "checkpoint_restore"
    checkpoint_id: str = Field(pattern=r"^cp_[a-f0-9]{16}$")


ProjectOperation = Annotated[
    ProjectRulesRequest
    | ProjectStatusRequest
    | CodeSearchRequest
    | FileApplyPatchRequest
    | FileCreateRequest
    | CommandListRequest
    | CommandRunRequest
    | RepoStatusRequest
    | RepoDiffRequest
    | GitCommitRequest
    | GitPushRequest
    | SaveImageRequest
    | SaveImageFromUrlRequest
    | ListImagesRequest
    | RetrieveImageRequest
    | CheckpointListRequest
    | CheckpointShowRequest
    | CheckpointRestoreRequest,
    Field(discriminator="kind"),
]
