"""L3 expert inspection/control surface for lightassay.

This module defines the deliberate L3 expert layer.  It is NOT part of
the ordinary top-level package exports.  It is reached only through
``DiagnosticsHandle.open_expert()`` — the deliberate L2→L3 escalation
path.

The expert surface provides:

- **Deep inspection** of workbook source, config bindings, and run
  artifacts at a level of detail that goes beyond L2 summaries.
- **One bounded low-level control** (``rebind_config``) that updates
  session config bindings for the current handle without mutating files.

Callers who only use L1 or L2 do not need this module.  Expert types
can be imported for annotations::

    from lightassay.expert import ExpertHandle, WorkbookSourceView
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    pass


# ── Expert view types ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class DirectionView:
    """Deep view of a single direction from the workbook."""

    direction_id: str
    body: str
    human_instruction: str


@dataclass(frozen=True)
class CaseView:
    """Deep view of a single case from the workbook."""

    case_id: str
    input: str
    target_directions: list[str]
    expected_behavior: str
    context: str | None
    notes: str | None
    human_instruction: str


@dataclass(frozen=True)
class RunReadinessView:
    """Deep view of workbook run-readiness state."""

    run_ready: bool
    readiness_note: str


@dataclass(frozen=True)
class ArtifactReferencesView:
    """Deep view of workbook artifact reference paths."""

    run: str | None
    analysis: str | None
    compare: str | None


@dataclass(frozen=True)
class WorkbookSourceView:
    """Deep inspection of the workbook source and parsed model.

    Includes the raw markdown text and all parsed structural elements
    that L1/L2 summaries intentionally hide from the ordinary caller.
    """

    workbook_path: str
    raw_text: str
    brief: str
    directions: list[DirectionView]
    cases: list[CaseView]
    run_readiness: RunReadinessView
    artifact_references: ArtifactReferencesView


@dataclass(frozen=True)
class ConfigBindingEntry:
    """Validity report for a single config binding.

    ``bound`` is ``True`` when the session has a path for this config
    type.  ``file_exists`` and ``valid`` are ``None`` when unbound.
    ``validation_error`` carries the exact error string when
    ``valid is False``.
    """

    config_type: str  # "preparation" | "workflow" | "semantic"
    path: str | None
    bound: bool
    file_exists: bool | None
    valid: bool | None
    validation_error: str | None


@dataclass(frozen=True)
class ConfigBindingsView:
    """Deep view of all session config bindings and their validity."""

    bindings: list[ConfigBindingEntry]


@dataclass(frozen=True)
class CaseRecordView:
    """Deep view of a single case execution record from a run artifact."""

    case_id: str
    input: str
    expected_behavior: str
    status: str
    duration_ms: int
    raw_response: str | None
    execution_error: str | None


@dataclass(frozen=True)
class RunArtifactView:
    """Deep inspection of a run artifact with case-level detail.

    Goes beyond the L1 ``RunResult`` summary by exposing individual
    case records, timing, and raw responses.
    """

    run_id: str
    workflow_id: str
    provider: str | None
    model: str | None
    status: str
    started_at: str
    finished_at: str
    total_cases: int
    completed_cases: int
    failed_cases: int
    total_duration_ms: int
    cases: list[CaseRecordView]


# ── Expert handle ──────────────────────────────────────────────────────────


class ExpertHandle:
    """L3 expert inspection/control handle.

    Reached only through ``DiagnosticsHandle.open_expert()``.

    Provides deep inspection primitives:

    - ``inspect_workbook_source()`` — raw workbook text and full parsed
      model including directions, cases, feedback, and readiness state
    - ``inspect_config_bindings()`` — all bound config paths with
      existence and validity checks
    - ``inspect_run_artifact(path)`` — case-level detail from a run
      artifact JSON file

    Provides one bounded low-level control:

    - ``rebind_config(...)`` — update session config bindings for the
      current handle without mutating any files

    The expert surface owns:

    - eval session inspection
    - config binding validity
    - artifact content inspection

    The expert surface does NOT own:

    - install/bootstrap of external dependencies
    - hidden repair of broken workflow contracts
    - business logic of the tested workflow
    """

    __slots__ = (
        "_workbook_path",
        "_get_config_paths",
        "_set_config_paths",
    )

    def __init__(
        self,
        workbook_path: str,
        get_config_paths: Callable[[], dict],
        set_config_paths: Callable[[dict], None],
    ) -> None:
        self._workbook_path = workbook_path
        self._get_config_paths = get_config_paths
        self._set_config_paths = set_config_paths

    # ── deep inspection ────────────────────────────────────────────────

    def inspect_workbook_source(self) -> WorkbookSourceView:
        """Deep inspection of the workbook file.

        Reads the workbook from disk, returns both raw text and the full
        parsed model including directions, cases, human feedback, and
        readiness state.

        Raises ``EvalError`` if the workbook file is missing or unparseable.
        """
        from .errors import EvalError, WorkbookParseError
        from .workbook_parser import parse

        if not os.path.isfile(self._workbook_path):
            raise EvalError(f"Workbook file not found: {self._workbook_path!r}")

        with open(self._workbook_path, encoding="utf-8") as fh:
            raw_text = fh.read()

        try:
            workbook = parse(raw_text)
        except WorkbookParseError as exc:
            raise EvalError(f"Workbook parse failed: {exc}") from exc

        directions = [
            DirectionView(
                direction_id=d.direction_id,
                body=d.body,
                human_instruction=d.human_instruction.text,
            )
            for d in workbook.directions
        ]

        cases = [
            CaseView(
                case_id=c.case_id,
                input=c.input,
                target_directions=list(c.target_directions),
                expected_behavior=c.expected_behavior,
                context=c.context,
                notes=c.notes,
                human_instruction=c.human_instruction.text,
            )
            for c in workbook.cases
        ]

        return WorkbookSourceView(
            workbook_path=self._workbook_path,
            raw_text=raw_text,
            brief=workbook.brief,
            directions=directions,
            cases=cases,
            run_readiness=RunReadinessView(
                run_ready=workbook.run_readiness.run_ready,
                readiness_note=workbook.run_readiness.readiness_note,
            ),
            artifact_references=ArtifactReferencesView(
                run=workbook.artifact_references.run,
                analysis=workbook.artifact_references.analysis,
                compare=workbook.artifact_references.compare,
            ),
        )

    def inspect_config_bindings(self) -> ConfigBindingsView:
        """Deep inspection of all session config bindings.

        For each config type (preparation, workflow, semantic), reports:
        - whether a path is bound
        - whether the file exists on disk
        - whether the config parses and validates successfully
        - the exact validation error if it does not
        """
        paths = self._get_config_paths()
        bindings: list[ConfigBindingEntry] = []

        for config_type, path in [
            ("preparation", paths.get("preparation_config")),
            ("workflow", paths.get("workflow_config")),
            ("semantic", paths.get("semantic_config")),
        ]:
            if path is None:
                bindings.append(
                    ConfigBindingEntry(
                        config_type=config_type,
                        path=None,
                        bound=False,
                        file_exists=None,
                        valid=None,
                        validation_error=None,
                    )
                )
                continue

            file_exists = os.path.isfile(path)
            if not file_exists:
                bindings.append(
                    ConfigBindingEntry(
                        config_type=config_type,
                        path=path,
                        bound=True,
                        file_exists=False,
                        valid=False,
                        validation_error=f"File not found: {path!r}",
                    )
                )
                continue

            # Attempt to load/validate.
            valid, validation_error = self._validate_config(config_type, path)

            bindings.append(
                ConfigBindingEntry(
                    config_type=config_type,
                    path=path,
                    bound=True,
                    file_exists=True,
                    valid=valid,
                    validation_error=validation_error,
                )
            )

        return ConfigBindingsView(bindings=bindings)

    def inspect_run_artifact(self, artifact_path: str) -> RunArtifactView:
        """Deep inspection of a run artifact with case-level detail.

        Loads the run artifact JSON and returns a structured view
        including individual case records with timing, raw responses,
        and execution errors.

        Raises ``EvalError`` if the file is missing or malformed.
        """
        from .errors import EvalError, RunError
        from .run_artifact_io import load_run_artifact

        if not os.path.isfile(artifact_path):
            raise EvalError(f"Run artifact file not found: {artifact_path!r}")

        try:
            artifact = load_run_artifact(artifact_path)
        except RunError as exc:
            raise EvalError(f"Run artifact invalid: {exc}") from exc

        cases = [
            CaseRecordView(
                case_id=c.case_id,
                input=c.input,
                expected_behavior=c.expected_behavior,
                status=c.status,
                duration_ms=c.duration_ms,
                raw_response=c.raw_response,
                execution_error=c.execution_error,
            )
            for c in artifact.cases
        ]

        return RunArtifactView(
            run_id=artifact.run_id,
            workflow_id=artifact.workflow_id,
            provider=artifact.provider,
            model=artifact.model,
            status=artifact.status,
            started_at=artifact.started_at,
            finished_at=artifact.finished_at,
            total_cases=artifact.aggregate.total_cases,
            completed_cases=artifact.aggregate.completed_cases,
            failed_cases=artifact.aggregate.failed_cases,
            total_duration_ms=artifact.aggregate.total_duration_ms,
            cases=cases,
        )

    # ── bounded low-level control ──────────────────────────────────────

    def rebind_config(
        self,
        *,
        preparation_config: str | None = None,
        workflow_config: str | None = None,
        semantic_config: str | None = None,
    ) -> ConfigBindingsView:
        """Rebind session config paths for the current handle.

        Updates only the config paths that are explicitly provided
        (non-``None``).  Paths set to ``None`` via keyword are left
        unchanged; to unbind a config, the caller must set it to the
        empty string ``""``, which is then stored as ``None`` internally.

        This does NOT mutate any files.  It only changes the in-memory
        config bindings on the session that produced this expert handle.

        Returns the new ``ConfigBindingsView`` after rebinding so the
        caller can verify the result.

        Raises ``EvalError`` if the expert handle's session has been
        released.
        """

        current = self._get_config_paths()

        updates: dict = {}
        for key, value in [
            ("preparation_config", preparation_config),
            ("workflow_config", workflow_config),
            ("semantic_config", semantic_config),
        ]:
            if value is not None:
                # Empty string means "unbind".
                updates[key] = None if value == "" else value

        if updates:
            new_paths = {**current, **updates}
            self._set_config_paths(new_paths)

        return self.inspect_config_bindings()

    # ── private helpers ────────────────────────────────────────────────

    @staticmethod
    def _validate_config(config_type: str, path: str) -> tuple:
        """Validate a config file.  Returns (valid: bool, error: str|None)."""
        try:
            if config_type == "preparation":
                from .preparation_config import load_preparation_config

                load_preparation_config(path)
            elif config_type == "workflow":
                from .workflow_config import load_workflow_config

                load_workflow_config(path)
            elif config_type == "semantic":
                from .semantic_config import load_semantic_config

                load_semantic_config(path)
            return True, None
        except Exception as exc:
            return False, str(exc)

    def __repr__(self) -> str:
        return f"ExpertHandle(workbook={self._workbook_path!r})"
