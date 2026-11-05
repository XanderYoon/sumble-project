"""Contract for safe structured logs and rotating persistent non-200 history."""

from pathlib import Path


def configure_logging(log_dir: Path) -> None:
    """Configure JSON stdout logs and rotating non-200 files.

    Args:
        log_dir: Persistent directory for errors.jsonl and three rotated backups.

    Returns:
        None; file-sink failures must fall back to stdout diagnostics.

    Raises:
        NotImplementedError: Logging is not implemented in the skeleton.
    """
    raise NotImplementedError("Safe logging is scheduled for implementation.")
