"""
interface.py
------------
This file defines the ONLY contract between your policy and the simulator.
You do not need to modify this file. Read it carefully, then implement your
own policy in `my_policy.py` by subclassing `DoctorQueuePolicy`.

Nothing outside of what is defined here is ever passed to your policy.
"""

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class DoctorInfo:
    """A doctor scheduled for the current shift."""
    doctor_id: str
    speed_tier: str  # one of "Fast", "Medium", "Slow" -- known ahead of time,
                      # the way a charge nurse has a general sense of who's
                      # typically quick. The exact numeric speed a doctor
                      # turns out to have on a given shift is NOT disclosed.


@dataclass(frozen=True)
class CostWeights:
    """The hospital's own stated priorities. Published, fixed, known upfront."""
    w1: float                  # weight on wait time for pac_scale == 1
    w2: float                  # weight on wait time for pac_scale == 2
    w3: float                  # weight on wait time for pac_scale == 3
    penalty_renege: float      # added to cost if a patient leaves before being seen
    penalty_escalation: float  # added to cost if a high-acuity patient's outcome worsens


@dataclass(frozen=True)
class ShiftConfig:
    """
    Everything a hospital manager would know before a shift starts.
    Handed to your policy exactly once, in __init__.
    """
    shift_length_min: int
    doctor_roster: List[DoctorInfo]
    cost_weights: CostWeights


@dataclass(frozen=True)
class WaitingPatient:
    """One patient currently waiting to see a doctor."""
    patient_id: int
    pac_scale: int              # 1, 2, or 3 (acuity/triage scale; 3 = most severe)
    elapsed_wait_min: float     # minutes since this patient finished triage


@dataclass(frozen=True)
class HospitalState:
    """
    The full picture your policy sees at the moment ONE specific doctor
    (`available_doctor`) has just become free and needs a next patient.
    """
    shift_elapsed_min: float
    waiting_patients: List[WaitingPatient]   # always non-empty when choose_patient is called
    available_doctor: DoctorInfo
    n_doctors_busy: int
    n_doctors_free: int


class DoctorQueuePolicy:
    """
    Base class. Subclass this exactly once, in my_policy.py, as:

        class Submission(DoctorQueuePolicy):
            def choose_patient(self, state: HospitalState) -> int:
                ...

    A fresh instance of your policy is constructed once at the start of
    EVERY simulated shift. Nothing persists between shifts.
    """

    def __init__(self, shift_config: ShiftConfig):
        self.shift_config = shift_config

    def choose_patient(self, state: HospitalState) -> int:
        """
        REQUIRED. Called every time a doctor becomes free while at least
        one patient is waiting. Must return the patient_id of exactly one
        patient found in state.waiting_patients.
        """
        raise NotImplementedError("choose_patient must be implemented by your policy")

    # ---- Optional hooks (default: do nothing). These let you track
    # running information over the course of a shift, e.g.
    # a per-doctor running average of realized consultation duration.
    # Implement these if you want to maintain state during a shift. ----

    def on_consultation_complete(self, doctor_id: str, patient_id: int, duration_min: float) -> None:
        """Fired when a consultation ends, with its realized duration."""
        pass

    def on_patient_exit(self, patient_id: int, outcome: str) -> None:
        """Fired whenever a patient's case concludes for any reason."""
        pass
