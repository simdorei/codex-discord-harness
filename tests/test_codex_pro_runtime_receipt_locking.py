from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from codex_pro_runtime_receipt_io import (
    publish_runtime_receipts,
    read_runtime_receipts,
    write_runtime_receipts,
)
from tests.pro_runtime_receipt_support import complete_runtime_receipts

_WORKER = r"""
import sys
import time
from pathlib import Path
from codex_pro_runtime_receipt_io import publish_runtime_receipts, read_runtime_receipts

source = Path(sys.argv[1])
target = Path(sys.argv[2])
gate = Path(sys.argv[3])
label = sys.argv[4]
deadline = time.monotonic() + 10
while not gate.exists():
    if time.monotonic() >= deadline:
        raise SystemExit("gate timeout")
    time.sleep(0.01)
try:
    publish_runtime_receipts(read_runtime_receipts(source), target)
except Exception as exc:
    print(f"error:{label}:{type(exc).__name__}")
else:
    print(f"ok:{label}")
"""


def test_cross_process_publish_has_one_canonical_winner_and_no_stale_lock(
    tmp_path: Path,
) -> None:
    first = complete_runtime_receipts(datetime.now(UTC))
    second = complete_runtime_receipts(
        datetime.now(UTC) + timedelta(minutes=1)
    )
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    target = tmp_path / "published.json"
    gate = tmp_path / "go"
    _ = write_runtime_receipts(first, first_path)
    _ = write_runtime_receipts(second, second_path)
    workers = (
        _worker(first_path, target, gate, "first"),
        _worker(second_path, target, gate, "second"),
    )

    _ = gate.write_text("go", encoding="utf-8")
    outputs = tuple(
        worker.communicate(timeout=15)[0].strip() for worker in workers
    )

    winners = tuple(value for value in outputs if value.startswith("ok:"))
    losers = tuple(value for value in outputs if value.startswith("error:"))
    assert len(winners) == 1
    assert len(losers) == 1
    expected = first if winners[0] == "ok:first" else second
    assert read_runtime_receipts(target) == expected
    lock_path = target.with_name(target.name + ".lock")
    assert lock_path.exists()

    target.unlink()
    assert publish_runtime_receipts(first, target) == target
    assert read_runtime_receipts(target) == first


def _worker(
    source: Path,
    target: Path,
    gate: Path,
    label: str,
) -> subprocess.Popen[str]:
    return subprocess.Popen(
        (
            sys.executable,
            "-c",
            _WORKER,
            str(source),
            str(target),
            str(gate),
            label,
        ),
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )
