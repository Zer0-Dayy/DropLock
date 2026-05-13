from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from session_models import CloseGateResult, LockerSession


@dataclass(frozen=True, slots=True)
class CloseGateConfig:
    """
    Configuration for evaluating whether a locker session may proceed to CLOSE.
    """

    require_signature: bool = True
    require_weight: bool = True
    require_door_closed: bool = False

    # Absolute allowed difference in grams between expected and measured weight.
    # Example:
    # expected=1500, measured=1480, tolerance=100 -> accepted
    weight_tolerance_grams: int = 200


class CloseGates:
    """
    Evaluates whether all close conditions are satisfied for a LockerSession.

    Current supported gates:
    - signature captured
    - weight measured and accepted
    - optional door closed confirmation

    This module is decision-only.
    It does not:
    - talk to MQTT
    - write Firebase
    - modify session state
    """

    def __init__(self, config: Optional[CloseGateConfig] = None) -> None:
        self._config = config or CloseGateConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(
        self,
        session: LockerSession,
        *,
        door_closed: Optional[bool] = None,
    ) -> CloseGateResult:
        """
        Return whether CLOSE is allowed right now, plus blocking reasons.

        Parameters:
            session:
                Current locker session snapshot.
            door_closed:
                Optional controller-derived door status.
                Only used if require_door_closed=True.
        """
        blocking_reasons: list[str] = []

        self._evaluate_signature_gate(session, blocking_reasons)
        self._evaluate_weight_gate(session, blocking_reasons)
        self._evaluate_door_gate(door_closed, blocking_reasons)

        return CloseGateResult(
            can_close=(len(blocking_reasons) == 0),
            blocking_reasons=blocking_reasons,
        )

    def evaluate_weight_only(self, session: LockerSession) -> tuple[bool, str]:
        """
        Helper for orchestrator use when a WEIGHT_MEASURED event arrives.

        Returns:
            (accepted, reason)
        """
        expected = session.weight_expected_grams
        measured = session.weight_measured_grams

        if not self._config.require_weight:
            return True, "WEIGHT_NOT_REQUIRED"

        if expected is None:
            return False, "WEIGHT_EXPECTED_MISSING"

        if measured is None:
            return False, "WEIGHT_NOT_MEASURED"

        if expected < 0:
            return False, "WEIGHT_EXPECTED_INVALID"

        if measured < 0:
            return False, "WEIGHT_MEASURED_INVALID"

        diff = abs(expected - measured)
        if diff > self._config.weight_tolerance_grams:
            return False, "WEIGHT_MISMATCH"

        return True, "OK"

    # ------------------------------------------------------------------
    # Internal gate evaluation
    # ------------------------------------------------------------------

    def _evaluate_signature_gate(
        self,
        session: LockerSession,
        blocking_reasons: list[str],
    ) -> None:
        if not self._config.require_signature:
            return

        if session.signature_captured_at is None:
            blocking_reasons.append("SIGNATURE_REQUIRED")

        if not session.signature_path:
            blocking_reasons.append("SIGNATURE_FILE_MISSING")

    def _evaluate_weight_gate(
        self,
        session: LockerSession,
        blocking_reasons: list[str],
    ) -> None:
        if not self._config.require_weight:
            return

        expected = session.weight_expected_grams
        measured = session.weight_measured_grams

        if expected is None:
            blocking_reasons.append("WEIGHT_EXPECTED_MISSING")
            return

        if measured is None:
            blocking_reasons.append("WEIGHT_NOT_MEASURED")
            return

        if expected < 0:
            blocking_reasons.append("WEIGHT_EXPECTED_INVALID")
            return

        if measured < 0:
            blocking_reasons.append("WEIGHT_MEASURED_INVALID")
            return

        diff = abs(expected - measured)

        if diff > self._config.weight_tolerance_grams:
            blocking_reasons.append("WEIGHT_MISMATCH")

        if session.weight_accepted is False:
            blocking_reasons.append("WEIGHT_REJECTED")

    def _evaluate_door_gate(
        self,
        door_closed: Optional[bool],
        blocking_reasons: list[str],
    ) -> None:
        if not self._config.require_door_closed:
            return

        if door_closed is None:
            blocking_reasons.append("DOOR_STATUS_UNKNOWN")
            return

        if not door_closed:
            blocking_reasons.append("DOOR_NOT_CLOSED")
