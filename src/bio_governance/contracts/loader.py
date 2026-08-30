"""Loading contract definitions from YAML.

The loader's whole job is to turn a file into a :class:`DataContract` or to fail
with a message that names the file and the problem. A contract that loads is a
contract every rule in the validator can rely on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from bio_governance.contracts.models import DataContract


class ContractError(Exception):
    """A contract definition could not be read, parsed or understood."""


def load_contract(path: Path) -> DataContract:
    """Read and validate the contract definition at ``path``.

    Raises :class:`ContractError` for a missing file, malformed YAML, a document
    that is not a mapping, or a mapping that does not describe a contract.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContractError(f"cannot read contract {path}: {exc.strerror or exc}") from exc

    try:
        document: Any = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ContractError(f"{path} is not valid YAML: {_yaml_detail(exc)}") from exc

    if document is None:
        raise ContractError(f"{path} is empty: expected a contract definition")
    if not isinstance(document, dict):
        raise ContractError(
            f"{path} must contain a mapping at the top level, got {type(document).__name__}"
        )

    try:
        return DataContract.model_validate(document)
    except ValidationError as exc:
        raise ContractError(f"{path} is not a valid contract:\n{_field_errors(exc)}") from exc


def _yaml_detail(exc: yaml.YAMLError) -> str:
    """Reduce a PyYAML error to its problem and location."""
    if isinstance(exc, yaml.MarkedYAMLError) and exc.problem_mark is not None:
        mark = exc.problem_mark
        return f"{exc.problem or 'parse error'} (line {mark.line + 1}, column {mark.column + 1})"
    return str(exc).replace("\n", " ").strip()


def _field_errors(exc: ValidationError) -> str:
    """Render Pydantic's errors as one indented ``field: message`` line each."""
    lines = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "<contract>"
        lines.append(f"  {location}: {error['msg']}")
    return "\n".join(lines)
