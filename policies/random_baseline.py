"""
policies/random_baseline.py
-----------------------------
A second reference baseline: picks uniformly at random among waiting
patients. Useful as a lower-bound sanity check -- any reasonable policy
should beat this by a wide margin.
"""

import random
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from interface import DoctorQueuePolicy, HospitalState


class RandomPolicy(DoctorQueuePolicy):
    def choose_patient(self, state: HospitalState) -> int:
        return random.choice(state.waiting_patients).patient_id
