from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, cast

from codex_pro_release_checks import collect_current_release_evidence
from codex_pro_release_evidence import write_evidence_artifacts
from codex_pro_runtime_receipt_io import (
    RuntimeReceiptError,
    read_runtime_receipts,
)
from codex_pro_runtime_receipt_models import RuntimeReceiptSet
from codex_pro_resident_identity import (
    DEFAULT_RESIDENT_IDENTITY_KEY_PATH,
    DEFAULT_RESIDENT_IDENTITY_PATH,
    ResidentIdentityError,
    ResidentRuntimeIdentity,
    read_current_resident_identity,
)


DEFAULT_OUTPUT = Path(".release-evidence/pro-release-evidence.json")


class _ParsedArgs(Protocol):
    repo_root: Path
    output: Path
    runtime_receipts: Path | None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect fail-closed pre-restart evidence for the !pro release path."
    )
    _ = parser.add_argument("--repo-root", type=Path, default=Path(__file__).parent)
    _ = parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    _ = parser.add_argument(
        "--runtime-receipts",
        type=Path,
        help="Validated public-safe receipts captured by live and post-restart QA.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = cast(_ParsedArgs, cast(object, _parser().parse_args(argv)))
    repo_root = args.repo_root.resolve()
    output = args.output
    if not output.is_absolute():
        output = repo_root / output
    runtime_receipts: RuntimeReceiptSet | None = None
    current_resident: ResidentRuntimeIdentity | None = None
    if args.runtime_receipts is not None:
        receipt_path = args.runtime_receipts
        if not receipt_path.is_absolute():
            receipt_path = repo_root / receipt_path
        try:
            runtime_receipts = read_runtime_receipts(receipt_path)
        except RuntimeReceiptError as exc:
            print(f"Runtime receipts invalid: {exc}", file=sys.stderr)
            return 2
        try:
            current_resident = read_current_resident_identity(
                repo_root / DEFAULT_RESIDENT_IDENTITY_PATH,
                repo_root / DEFAULT_RESIDENT_IDENTITY_KEY_PATH,
            )
        except ResidentIdentityError as exc:
            print(f"Resident identity invalid: {exc}", file=sys.stderr)
            return 2
    evidence = collect_current_release_evidence(
        repo_root,
        current_resident=current_resident,
    )
    json_path, summary_path = write_evidence_artifacts(
        evidence,
        output,
        runtime_receipts,
    )
    print(evidence.summary(runtime_receipts), end="")
    print(f"JSON: {json_path}")
    print(f"Summary: {summary_path}")
    if runtime_receipts is None:
        return 0 if evidence.pre_restart_ready else 1
    return 0 if evidence.release_readiness(runtime_receipts).ready else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
