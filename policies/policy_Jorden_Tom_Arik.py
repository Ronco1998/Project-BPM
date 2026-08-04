"""
policies/policy_Jorden_Tom_Arik.py
-----------------------------------
This is the ONLY code file you submit. Implement your policy below.

You may add helper methods, imports from any libraries, and
use the optional on_consultation_complete / on_patient_exit hooks defined
in interface.py if you want your policy to track running statistics or state
over the course of a shift (e.g. an empirical estimate of how fast a
particular doctor seems to be running today).

**This script should not generate any artifacts or print anything on submission.**
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from interface import DoctorQueuePolicy, HospitalState


# Group metadata: update these values with your group's information
GROUP_INFO = {
    "group_id": "group_X",
    "group_members": ["Jorden", "Tom", "Arik"],
}


class Submission(DoctorQueuePolicy):

    def __init__(self, shift_config):
        super().__init__(shift_config)
        # Add any per-shift tracking state you want here, e.g.:
        # self.observed_durations = {d.doctor_id: [] for d in shift_config.doctor_roster}

    def choose_patient(self, state: HospitalState) -> int:
        # TODO: replace this with your own policy.
        # This placeholder just falls back to plain FCFS so the file runs
        # out of the box -- it is not intended to be a good policy.
        longest_waiting = max(state.waiting_patients, key=lambda p: p.elapsed_wait_min)
        return longest_waiting.patient_id

    # def on_consultation_complete(self, doctor_id, patient_id, duration_min):
    #     self.observed_durations[doctor_id].append(duration_min)

    # def on_patient_exit(self, patient_id, outcome):
    #     pass
