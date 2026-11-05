"""Build the explicitly allowlisted delivery archive after codebase approval."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

SOURCE_ROOT = Path(__file__).resolve().parents[1]
DELIVERY_FILES = (
    "Dockerfile",
    "docker-compose.yml",
    ".dockerignore",
    ".gitignore",
    "README.md",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "docs/database.md",
    "docs/validation.md",
    "docs/code-flow.md",
    "scripts/package.py",
    "src/sumble_api/__init__.py",
    "src/sumble_api/app.py",
    "src/sumble_api/compiler.py",
    "src/sumble_api/database.py",
    "src/sumble_api/models.py",
    "src/sumble_api/observability.py",
    "src/sumble_api/query.py",
    "tests/conftest.py",
    "tests/test_api.py",
    "tests/test_compiler.py",
    "tests/test_concurrency.py",
    "tests/test_database.py",
    "tests/test_observability.py",
    "tests/test_query.py",
)


def build_submission(output: Path) -> Path:
    """Write a new ZIP containing only required, regular, non-symlink inputs.

    Call only after the user approves the entire codebase. No discovery/globbing
    includes runtime files, secrets, Git metadata, workflow files, or DB data.
    Existing outputs are never overwritten. Validation completes before writing.

    Args:
        output: Destination archive path, normally under dist/.

    Returns:
        The absolute completed archive path.

    Raises:
        FileNotFoundError: A required input is absent.
        ValueError: An input or its ancestor is a symlink or is not a regular file.
        OSError: The output already exists or cannot be written.
    """
    inputs: list[tuple[Path, str]] = []
    for relative in DELIVERY_FILES:
        source = SOURCE_ROOT / relative
        for component in (source, *source.relative_to(SOURCE_ROOT).parents):
            candidate = component if component.is_absolute() else SOURCE_ROOT / component
            if candidate.is_symlink():
                raise ValueError(f"Delivery input must not be a symlink: {relative}")
        if not source.exists():
            raise FileNotFoundError(source)
        if not source.is_file():
            raise ValueError(f"Delivery input must be a regular file: {relative}")
        inputs.append((source, relative))
    output = output.absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with ZipFile(output, mode="x", compression=ZIP_DEFLATED) as archive:
            for source, relative in inputs:
                archive.write(source, arcname=relative)
    except FileExistsError:
        raise
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return output
