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


# Group metadata: updated values with our group's information
GROUP_INFO = {
    "group_id": "group_A",
    "group_members": ["Anat Levin", "Roni Cohen", "Raissa Chut Steinberg", "Atara Keynan"],
}


# Tuned constants. Replace with the best values printed by tune_hyperparameters.py.
# Search space (per that script):
#   tau_base   in [25, 45]       tau_penalty in [0, 15]
#   beta       in [0.02, 0.25]   alpha_1 in [0.001, 0.05]   alpha_2 in [0.0001, 0.01]
#   delta_slow in [0, 1]         kappa   in [0, 1]
DEFAULT_PARAMS = {
    "tau_base": 28.7816,      # base "safe window" (min) before a PAC-3 wait turns costly
    "tau_penalty": 6.7764,    # how much an incoming surge shrinks that safe window
    "beta": 0.02131,          # steepness of PAC-3 escalation-risk growth with wait
    "alpha_1": 0.0240136,     # renege-risk sensitivity for PAC-1 waits
    "alpha_2": 0.00137581,    # renege-risk sensitivity for PAC-2 waits
    "delta_slow": 0.6569,     # PAC-3 cost multiplier when the free doctor is "Slow"
    "kappa": 0.0,             # c-mu throughput exponent; 0 = pure cost (re-tune to raise)
}

# Cold-start priors for the per-(tier, pac) service-time EMA. Tier bases are the
# generic guesses; the pac factor reflects that higher acuity usually runs longer.
_TIER_BASE = {"Fast": 15.0, "Medium": 20.0, "Slow": 30.0}
_PAC_FACTOR = {1: 0.8, 2: 1.0, 3: 1.3}

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
        self.delta_slow = float(p["delta_slow"])
        # kappa is optional so older 6-param dicts still load unchanged.
        self.kappa = float(p.get("kappa", 0.0))

        cw = shift_config.cost_weights
        self.w1 = cw.w1
        self.w2 = cw.w2
        self.w3 = cw.w3
        # Dynamic scaling: fold the shift's actual penalties into the linear
        # renege term and the exponential escalation term, so one parameter set
        # adapts across configs with very different penalty magnitudes.
        self.r_factor = cw.penalty_renege / 1000.0
        self.e_factor = cw.penalty_escalation / 10000.0

        # doctor_id -> speed_tier, so the completion hook can attribute the
        # realized duration to the right tier's running estimate.
        self.doctor_tier = {d.doctor_id: d.speed_tier for d in shift_config.doctor_roster}

        # Online EMA of realized service time per speed tier (reset every shift).
        self.expected_service_time = {"Fast": 15.0, "Medium": 20.0, "Slow": 30.0}

        # Online EMA of realized service time per (speed_tier, pac_scale), used
        # by the c-mu throughput divisor. Reset every shift, learned in-run.
        self.service_ema = {
            t: {pac: _TIER_BASE[t] * _PAC_FACTOR[pac] for pac in (1, 2, 3)}
            for t in _TIER_BASE
        }
        # patient_id -> pac_scale for patients currently in consultation, so the
        # completion hook can attribute the realized duration to the right cell.
        self._pending_pac = {}

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
        surge = self._surge_multiplier(state.shift_elapsed_min)
        tau_dynamic = self.tau_base - surge * self.tau_penalty

        w1 = self.w1
        w2 = self.w2
        w3 = self.w3
        a1r = self.alpha_1 * self.r_factor
        a2r = self.alpha_2 * self.r_factor
        e_factor = self.e_factor
        beta = self.beta
        exp = math.exp

        tier = state.available_doctor.speed_tier

        # Slow-doctor protection: discount PAC-3 so a Slow doctor prefers to
        # clear PAC-1/2 instead of getting stuck on a 90+ min high-acuity case.
        pac3_mult = self.delta_slow if tier == "Slow" else 1.0

        # c-mu throughput weighting: divide each cost by the expected service
        # time of *that* (tier, pac) job, S^kappa. This is non-uniform across
        # pac (long PAC-3 jobs get deprioritized), so it changes the argmax.
        # kappa == 0 -> divisor is 1.0 everywhere -> pure marginal-cost ranking.
        kappa = self.kappa
        if kappa != 0.0:
            se = self.service_ema[tier]
            inv1 = se[1] ** (-kappa)
            inv2 = se[2] ** (-kappa)
            inv3 = se[3] ** (-kappa)
        else:
            inv1 = inv2 = inv3 = 1.0

        best_pid = -1
        best_pac = 0
        best_score = -math.inf
        for wp in state.waiting_patients:
            pac = wp.pac_scale
            w_i = wp.elapsed_wait_min
            if pac == 3:
                arg = beta * (w_i - tau_dynamic)
                if arg > _MAX_EXP_ARG:
                    arg = _MAX_EXP_ARG
                c_i = (w3 + e_factor * exp(arg)) * pac3_mult * inv3
            elif pac == 1:
                c_i = (w1 + a1r * w_i) * inv1
            else:  # pac == 2
                c_i = (w2 + a2r * w_i) * inv2

            pid = wp.patient_id
            # Deterministic tie-break: highest cost, then lowest patient_id.
            if c_i > best_score or (c_i == best_score and pid < best_pid):
                best_score = c_i
                best_pid = pid
                best_pac = pac

        # Remember the winner's acuity so the completion hook can update the
        # per-(tier, pac) service-time estimate.
        if best_pid >= 0:
            self._pending_pac[best_pid] = best_pac
        return best_pid

    def on_consultation_complete(self, doctor_id: str, patient_id: int, duration_min: float) -> None:
        tier = self.doctor_tier.get(doctor_id)
        pac = self._pending_pac.pop(patient_id, None)
        if tier is not None:
            self.expected_service_time[tier] = (
                0.8 * self.expected_service_time[tier] + 0.2 * duration_min
            )
            if pac is not None:
                se = self.service_ema[tier]
                se[pac] = 0.8 * se[pac] + 0.2 * duration_min
