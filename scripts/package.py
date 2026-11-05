"""Contract for packaging an explicit submission artifact allowlist."""

from pathlib import Path


def build_submission(output: Path) -> Path:
    """Package approved deliverable files without runtime or workflow artifacts.

    Args:
        output: Destination ZIP path.

    Returns:
        The completed ZIP path.

    Raises:
        FileNotFoundError: A required source artifact is missing.
        OSError: The archive cannot be written.
        NotImplementedError: Packaging is not implemented in the skeleton.
    """
    raise NotImplementedError("Submission packaging is scheduled for implementation.")
