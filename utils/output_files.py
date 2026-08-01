from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Iterator
from uuid import uuid4


@contextmanager
def reserved_output(
    output_dir: Path,
    extension: str,
    report_date: date | None = None,
) -> Iterator[tuple[Path, Path]]:
    """Reserve a numbered output name and provide a sibling temporary path."""

    output_dir.mkdir(parents=True, exist_ok=True)
    day = (report_date or date.today()).isoformat()
    suffix = extension if extension.startswith(".") else f".{extension}"
    number = 1

    while True:
        final_path = output_dir / f"SpaceWeekly_{day}_{number:03d}{suffix}"

        try:
            descriptor = os.open(
                final_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError:
            number += 1
            continue

        os.close(descriptor)
        break

    temp_path = output_dir / (
        f".{final_path.stem}.{uuid4().hex}.tmp{final_path.suffix}"
    )

    try:
        yield final_path, temp_path
    except BaseException:
        temp_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        raise
    else:
        try:
            os.replace(temp_path, final_path)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            final_path.unlink(missing_ok=True)
            raise
