from __future__ import annotations

from typing import assert_never

from remote_mcp_server.simdorei_mcp.broker_errors import (
    BridgeProtocolError,
    RemoteOperationError,
)
from simdorei_mcp_common.messages import (
    BridgeResult,
    ListFilesOutput,
    ListFilesResult,
    OperationErrorResult,
    ProjectInfoOutput,
    ProjectInfoResult,
    ReadFileOutput,
    ReadFileResult,
    WriteFileOutput,
    WriteFileResult,
)


def project_info_output(result: BridgeResult) -> ProjectInfoOutput:
    match result:
        case ProjectInfoResult(output=output):
            return output
        case OperationErrorResult(message=message):
            raise RemoteOperationError(message)
        case ListFilesResult() | ReadFileResult() | WriteFileResult():
            raise BridgeProtocolError("local bridge returned the wrong result type")
        case unreachable:
            assert_never(unreachable)


def list_files_output(result: BridgeResult) -> ListFilesOutput:
    match result:
        case ListFilesResult(output=output):
            return output
        case OperationErrorResult(message=message):
            raise RemoteOperationError(message)
        case ProjectInfoResult() | ReadFileResult() | WriteFileResult():
            raise BridgeProtocolError("local bridge returned the wrong result type")
        case unreachable:
            assert_never(unreachable)


def read_file_output(result: BridgeResult) -> ReadFileOutput:
    match result:
        case ReadFileResult(output=output):
            return output
        case OperationErrorResult(message=message):
            raise RemoteOperationError(message)
        case ProjectInfoResult() | ListFilesResult() | WriteFileResult():
            raise BridgeProtocolError("local bridge returned the wrong result type")
        case unreachable:
            assert_never(unreachable)


def write_file_output(result: BridgeResult) -> WriteFileOutput:
    match result:
        case WriteFileResult(output=output):
            return output
        case OperationErrorResult(message=message):
            raise RemoteOperationError(message)
        case ProjectInfoResult() | ListFilesResult() | ReadFileResult():
            raise BridgeProtocolError("local bridge returned the wrong result type")
        case unreachable:
            assert_never(unreachable)
