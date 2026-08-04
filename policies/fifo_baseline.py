"""
policies/fifo_baseline.py
--------------------------
A plain First-Come-First-Served baseline: always sends whichever waiting
patient has been waiting the longest, ignoring acuity entirely. This is a
reference point, not a strong policy -- use it to sanity-check that the
runner works, and as a floor to beat.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from interface import DoctorQueuePolicy, HospitalState


class FIFOPolicy(DoctorQueuePolicy):
    def choose_patient(self, state: HospitalState) -> int:
        longest_waiting = max(state.waiting_patients, key=lambda p: p.elapsed_wait_min)
        return longest_waiting.patient_id
