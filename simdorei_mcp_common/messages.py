from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, NewType

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

DeviceId = NewType("DeviceId", str)
RequestId = NewType("RequestId", str)


class ProtocolModel(BaseModel):
    """Immutable base for values crossing the bridge boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class BindingUpsert(ProtocolModel):
    type: Literal["binding_upsert"] = "binding_upsert"
    binding_code: str = Field(min_length=24, max_length=128)
    thread_id: str = Field(min_length=1, max_length=200)
    project_name: str = Field(min_length=1, max_length=200)
    expires_at: datetime


class BindProjectOutput(ProtocolModel):
    project_name: str
    thread_id: str
    expires_at: datetime


class ProjectInfoOutput(ProtocolModel):
    root: str
    thread_id: str


class FileEntry(ProtocolModel):
    path: str
    size_bytes: int = Field(ge=0)


class ListFilesOutput(ProtocolModel):
    files: tuple[FileEntry, ...]
    truncated: bool


class ReadFileOutput(ProtocolModel):
    path: str
    content: str
    sha256: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=0)
    total_lines: int = Field(ge=0)
    truncated: bool


class WriteFileOutput(ProtocolModel):
    path: str
    sha256: str
    bytes_written: int = Field(ge=0)
    created: bool


class ProjectInfoCommand(ProtocolModel):
    type: Literal["project_info"] = "project_info"
    request_id: RequestId
    thread_id: str


class ListFilesCommand(ProtocolModel):
    type: Literal["list_files"] = "list_files"
    request_id: RequestId
    thread_id: str
    pattern: str = Field(min_length=1, max_length=500)
    limit: int = Field(ge=1, le=500)


class ReadFileCommand(ProtocolModel):
    type: Literal["read_file"] = "read_file"
    request_id: RequestId
    thread_id: str
    path: str = Field(min_length=1, max_length=1_000)
    start_line: int = Field(ge=1)
    max_lines: int = Field(ge=1, le=500)


class WriteFileCommand(ProtocolModel):
    type: Literal["write_file"] = "write_file"
    request_id: RequestId
    thread_id: str
    path: str = Field(min_length=1, max_length=1_000)
    content: str = Field(max_length=1_048_576)
    expected_sha256: str | None = Field(default=None, min_length=64, max_length=64)


class ProjectInfoResult(ProtocolModel):
    type: Literal["project_info_result"] = "project_info_result"
    request_id: RequestId
    output: ProjectInfoOutput


class ListFilesResult(ProtocolModel):
    type: Literal["list_files_result"] = "list_files_result"
    request_id: RequestId
    output: ListFilesOutput


class ReadFileResult(ProtocolModel):
    type: Literal["read_file_result"] = "read_file_result"
    request_id: RequestId
    output: ReadFileOutput


class WriteFileResult(ProtocolModel):
    type: Literal["write_file_result"] = "write_file_result"
    request_id: RequestId
    output: WriteFileOutput


class OperationErrorResult(ProtocolModel):
    type: Literal["operation_error"] = "operation_error"
    request_id: RequestId
    error_code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=1_000)


class BridgeHello(ProtocolModel):
    type: Literal["hello"] = "hello"
    device_id: DeviceId


class GatewayHello(ProtocolModel):
    type: Literal["hello_ack"] = "hello_ack"
    protocol_version: Literal[1] = 1


class BindingAck(ProtocolModel):
    type: Literal["binding_ack"] = "binding_ack"
    binding_code: str


GatewayCommand = Annotated[
    ProjectInfoCommand | ListFilesCommand | ReadFileCommand | WriteFileCommand,
    Field(discriminator="type"),
]
BridgeResult = Annotated[
    ProjectInfoResult
    | ListFilesResult
    | ReadFileResult
    | WriteFileResult
    | OperationErrorResult,
    Field(discriminator="type"),
]
BridgeInboundMessage = Annotated[
    BridgeHello
    | BindingUpsert
    | ProjectInfoResult
    | ListFilesResult
    | ReadFileResult
    | WriteFileResult
    | OperationErrorResult,
    Field(discriminator="type"),
]
GatewayInboundMessage = Annotated[
    GatewayHello
    | BindingAck
    | ProjectInfoCommand
    | ListFilesCommand
    | ReadFileCommand
    | WriteFileCommand,
    Field(discriminator="type"),
]

BRIDGE_INBOUND_ADAPTER: TypeAdapter[BridgeInboundMessage] = TypeAdapter(
    BridgeInboundMessage
)
GATEWAY_INBOUND_ADAPTER: TypeAdapter[GatewayInboundMessage] = TypeAdapter(
    GatewayInboundMessage
)


def parse_bridge_message(raw: str) -> BridgeInboundMessage:
    return BRIDGE_INBOUND_ADAPTER.validate_json(raw)


def parse_gateway_message(raw: str) -> GatewayInboundMessage:
    return GATEWAY_INBOUND_ADAPTER.validate_json(raw)
