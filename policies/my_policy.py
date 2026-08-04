"""
policies/my_policy.py
---------------------
Deterministic Dynamic Priority Index policy for the ED Doctor-Queue Challenge.

When a doctor becomes free, every waiting patient is scored by its *marginal
cost of continued waiting* C_i (acuity-aware, surge-anticipating). The patient
with the highest score is served. All learning (per-tier service-time EMA)
happens online, within a single shift only -- no cross-run state, no I/O, no
randomness in the decision path.

This file is self-contained and safe to submit on its own.
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from interface import DoctorQueuePolicy, HospitalState


# Group metadata: update with your group's information.
GROUP_INFO = {
    "group_id": "group_X",
    "group_members": ["Jorden", "Tom", "Arik"],
}


# Tuned constants. Replace these with the best values printed by
# tune_hyperparameters.py. Search space (per that script):
#   tau_base   in [25, 45]      tau_penalty in [0, 15]
#   beta       in [0.01, 0.3]   alpha_1/2   in [0.0001, 0.05]
#   gamma      in [0, 2]
DEFAULT_PARAMS = {
    "tau_base": 35.0,      # base "safe window" (min) before a PAC-3 wait turns costly
    "tau_penalty": 8.0,    # how much an incoming surge shrinks that safe window
    "beta": 0.08,          # steepness of PAC-3 escalation-risk growth with wait
    "alpha_1": 0.008,      # renege-risk sensitivity for PAC-1 waits
    "alpha_2": 0.015,      # renege-risk sensitivity for PAC-2 waits
    "gamma": 0.5,          # end-of-shift throughput urgency boost
}

# Guard against math.exp overflow for pathologically long PAC-3 waits.
_MAX_EXP_ARG = 50.0


class Submission(DoctorQueuePolicy):
    """Deterministic Dynamic Priority Index policy."""

    def __init__(self, shift_config, params=None):
        super().__init__(shift_config)

        p = DEFAULT_PARAMS if params is None else params
        self.tau_base = float(p["tau_base"])
        self.tau_penalty = float(p["tau_penalty"])
        self.beta = float(p["beta"])
        self.alpha_1 = float(p["alpha_1"])
        self.alpha_2 = float(p["alpha_2"])
        self.gamma = float(p["gamma"])

        cw = shift_config.cost_weights
        self.w1 = cw.w1
        self.w2 = cw.w2
        self.w3 = cw.w3
        # penalty_renege / penalty_escalation act as the marginal-risk scale
        # (P_renege / P_escalation) folded into the cost model.
        self.penalty_renege = cw.penalty_renege
        self.penalty_escalation = cw.penalty_escalation
        self.shift_length_min = shift_config.shift_length_min

        # doctor_id -> speed_tier, so the completion hook can attribute the
        # realized duration to the right tier's running estimate.
        self.doctor_tier = {d.doctor_id: d.speed_tier for d in shift_config.doctor_roster}

        # Online EMA of realized service time per speed tier (reset every shift).
        self.expected_service_time = {"Fast": 15.0, "Medium": 20.0, "Slow": 30.0}

    def _surge_multiplier(self, elapsed_min: float) -> float:
        """Deterministic surge signal in [0, 1]: peaks at ~19:30, higher on weekends."""
        hour_of_day = (elapsed_min % 1440.0) / 60.0
        day_of_week = (elapsed_min % 10080.0) / 1440.0
        # Cosine bell centred on 19.5h, 24h period -> 1.0 at peak, 0.0 at 07:30.
        hour_surge = 0.5 * (1.0 + math.cos((hour_of_day - 19.5) * math.pi / 12.0))
        if day_of_week >= 5.0:
            # Weekend: raise the floor so surge is always >= a busy weekday.
            return 0.5 + 0.5 * hour_surge
        return 0.85 * hour_surge

    def choose_patient(self, state: HospitalState) -> int:
        elapsed = state.shift_elapsed_min

        surge = self._surge_multiplier(elapsed)
        tau_dynamic = self.tau_base - surge * self.tau_penalty

        # Per-call constants (identical for every candidate this call): the
        # doctor's efficiency and the end-of-shift urgency boost. They scale
        # all scores uniformly, so they don't change which patient wins, but
        # are computed here to match the Efficiency / Final Score definition.
        e_s = self.expected_service_time.get(state.available_doctor.speed_tier, 20.0)
        t_rem = self.shift_length_min - elapsed
        urgency = 1.0 - t_rem / 120.0
        time_factor = 1.0 + self.gamma * (urgency if urgency > 0.0 else 0.0)
        scale = time_factor / e_s if e_s > 0.0 else time_factor

        # Hoist everything out of the hot loop for speed.
        w1 = self.w1
        w2 = self.w2
        w3 = self.w3
        a1r = self.alpha_1 * self.penalty_renege
        a2r = self.alpha_2 * self.penalty_renege
        p_esc = self.penalty_escalation
        beta = self.beta
        exp = math.exp

        best_pid = -1
        best_score = -math.inf
        for wp in state.waiting_patients:
            pac = wp.pac_scale
            w_i = wp.elapsed_wait_min
            if pac == 3:
                arg = beta * (w_i - tau_dynamic)
                if arg > _MAX_EXP_ARG:
                    arg = _MAX_EXP_ARG
                c_i = w3 + p_esc * exp(arg)
            elif pac == 1:
                c_i = w1 + a1r * w_i
            else:  # pac == 2
                c_i = w2 + a2r * w_i

            score = c_i * scale
            pid = wp.patient_id
            # Deterministic tie-break: highest score, then lowest patient_id.
            if score > best_score or (score == best_score and pid < best_pid):
                best_score = score
                best_pid = pid

        return best_pid

    def on_consultation_complete(self, doctor_id: str, patient_id: int, duration_min: float) -> None:
        tier = self.doctor_tier.get(doctor_id)
        if tier is not None:
            self.expected_service_time[tier] = (
                0.8 * self.expected_service_time[tier] + 0.2 * duration_min
            )
