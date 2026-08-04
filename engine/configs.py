"""
engine/configs.py
------------------
Scenario configurations organized into three tiers by stress level:

  BASIC       -- low stress, forgiving. Any reasonable policy should do fine.
                 Use these to confirm your code runs and behaves sensibly.
  MODERATE    -- realistic, real-data-anchored baseline with controlled
                 single-factor stress. This is where policy quality matters.
  ADVERSARIAL -- deliberately stacked stress combinations that surface
                 genuine trade-offs in queue management.
"""

import math
from dataclasses import dataclass
from typing import Callable, Dict, List, Tuple


# Arrival rate cycle functions (deterministic, policy-independent)
# These multiply the base arrival rate based on simulation time.
# Convention: minute 0 = Monday 00:00 (midnight)

def hour_of_day_multiplier(elapsed_min: float, amplitude: float = 0.5) -> float:
    """
    Hour-of-day cycle (repeats every 1440 min = 24 hours).
    Peak in evening (18:00-21:00), trough overnight (00:00-06:00).
    Amplitude controls range around 1.0 (e.g., amplitude=0.5 -> 0.5–1.5).
    """
    hour = (elapsed_min % 1440) / 60.0  # hour of day [0, 24)
    phase = (hour - 19.5) * math.pi / 12  # 12-hour half-period
    return 1.0 + amplitude * math.sin(phase)


def day_of_week_multiplier(elapsed_min: float, amplitude: float = 0.2) -> float:
    """
    Day-of-week cycle (repeats every 10080 min = 7 days).
    Busier on weekends, quieter mid-week.
    Amplitude controls range around 1.0 (e.g., amplitude=0.2 -> 0.8–1.2).
    """
    day = (elapsed_min % 10080) / 1440.0  # day of week [0, 7)
    # Peak Friday evening (day ~4.75-5), Saturday-Sunday (days 5-6)
    # Trough Tuesday-Wednesday (days 1.5-2.5)
    phase = (day - 4.5) * math.pi / 3.5  # 3.5-day half-period
    return 1.0 + amplitude * math.sin(phase)


_REAL_ARRIVAL_RATE_PER_MIN = 0.267        # 21,127 cases over Oct 1 - Nov 24, 2012
_REAL_TRIAGE_TO_DOCTOR_WAIT_MIN = 29.0    # documented average, core-backbone cases
_REAL_CONSULT_DURATION_MIN = 46.0         # documented average, top-10-variant subset
_REAL_DOCTOR_SPEED_SPREAD = 2.5           # documented ratio, slowest vs. fastest doctor
_REAL_PAC3_SHARE = 0.57                   # documented share of pac_scale == 3 rows
# Exact case-level split between pac_scale 1 and 2 is not broken out in the
# documentation; we assign the remainder with a reasonable lean toward 2.
_REAL_PAC_SCALE_PROBS = (0.15, 0.28, _REAL_PAC3_SHARE)


@dataclass(frozen=True)
class DoctorSpec:
    doctor_id: str
    speed_tier: str  # "Fast" | "Medium" | "Slow"


@dataclass(frozen=True)
class ScenarioConfig:
    name: str
    tier: str          # "basic" | "moderate" | "adversarial"
    notes: str          # one-line, factual description

    # --- Known to the policy at shift start ---
    shift_length_min: int
    doctor_roster: Tuple[DoctorSpec, ...]
    w1: float
    w2: float
    w3: float
    penalty_renege: float
    penalty_escalation: float

    # --- Hidden from the policy; used only by the simulation engine ---
    arrival_rate_per_min: float
    pac_scale_probs: Tuple[float, float, float]
    triage_duration_mean_min: float
    consult_duration_mean_min: Dict[int, float]
    consult_duration_sigma: float
    speed_multiplier_range: Dict[str, Tuple[float, float]]
    renege_hazard_per_min: Dict[int, float]
    escalation_hazard_per_min: float
    hourly_cycle_amplitude: float = 0.5  # Range of hour-of-day multiplier around 1.0
    weekly_cycle_amplitude: float = 0.2  # Range of day-of-week multiplier around 1.0


def _roster(n_fast: int, n_medium: int, n_slow: int) -> Tuple[DoctorSpec, ...]:
    roster: List[DoctorSpec] = []
    idx = 1
    for tier, n in (("Fast", n_fast), ("Medium", n_medium), ("Slow", n_slow)):
        for _ in range(n):
            roster.append(DoctorSpec(doctor_id=f"Doctor_{idx}", speed_tier=tier))
            idx += 1
    return tuple(roster)


# Speed ranges are chosen so that (Slow midpoint / Fast midpoint) reproduces
# the documented ~2.5x real spread at MODERATE tier; BASIC narrows this
# dimension deliberately, ADVERSARIAL widens it.
_SPEED_RANGES_NARROW = {"Fast": (0.85, 0.95), "Medium": (0.95, 1.05), "Slow": (1.05, 1.15)}
_SPEED_RANGES_MODERATE = {"Fast": (0.60, 0.75), "Medium": (0.95, 1.10), "Slow": (1.55, 1.80)}
_SPEED_RANGES_WIDE = {"Fast": (0.45, 0.60), "Medium": (0.90, 1.15), "Slow": (1.90, 2.30)}

# Consultation duration means, by pac_scale, chosen to average out near the
# documented ~46 min real figure once blended across the real pac_scale mix.
_CONSULT_MEAN_MODERATE = {1: 34.0, 2: 44.0, 3: 58.0}

_STANDARD_COST_WEIGHTS = dict(w1=1.0, w2=2.0, w3=4.0, penalty_renege=150.0, penalty_escalation=300.0)

CONFIGS: Dict[str, ScenarioConfig] = {}


def _register(cfg: ScenarioConfig) -> None:
    CONFIGS[cfg.name] = cfg


# ===========================================================================
# BASIC -- low stress, forgiving. Confirms your code runs and behaves sensibly.
# ===========================================================================

_register(ScenarioConfig(
    name="sanity_check",
    tier="basic",
    notes="Very light load, an oversized roster, and no reneging/escalation risk. "
          "Use this as a sanity check that your policy runs correctly.",
    shift_length_min=131040,
    doctor_roster=_roster(n_fast=8, n_medium=8, n_slow=4),
    **_STANDARD_COST_WEIGHTS,
    arrival_rate_per_min=0.06,
    pac_scale_probs=_REAL_PAC_SCALE_PROBS,
    triage_duration_mean_min=6.0,
    consult_duration_mean_min=_CONSULT_MEAN_MODERATE,
    consult_duration_sigma=0.30,
    speed_multiplier_range=_SPEED_RANGES_NARROW,
    renege_hazard_per_min={1: 0.0, 2: 0.0},
    escalation_hazard_per_min=0.0,
))

_register(ScenarioConfig(
    name="gentle_day",
    tier="basic",
    notes="Below-average demand with a comfortably sized roster. Real acuity mix, "
          "light reneging/escalation risk.",
    shift_length_min=131040,
    doctor_roster=_roster(n_fast=6, n_medium=10, n_slow=4),
    **_STANDARD_COST_WEIGHTS,
    arrival_rate_per_min=_REAL_ARRIVAL_RATE_PER_MIN * 0.6,
    pac_scale_probs=_REAL_PAC_SCALE_PROBS,
    triage_duration_mean_min=6.0,
    consult_duration_mean_min=_CONSULT_MEAN_MODERATE,
    consult_duration_sigma=0.30,
    speed_multiplier_range=_SPEED_RANGES_NARROW,
    renege_hazard_per_min={1: 0.002, 2: 0.001},
    escalation_hazard_per_min=0.001,
))

# ===========================================================================
# MODERATE -- real-data-anchored baseline, Policy quality starts to matter here.
# ===========================================================================

_register(ScenarioConfig(
    name="classic_baseline",
    tier="moderate",
    notes="",
    shift_length_min=131040,
    doctor_roster=_roster(n_fast=5, n_medium=10, n_slow=5),
    **_STANDARD_COST_WEIGHTS,
    arrival_rate_per_min=_REAL_ARRIVAL_RATE_PER_MIN,
    pac_scale_probs=_REAL_PAC_SCALE_PROBS,
    triage_duration_mean_min=6.0,
    consult_duration_mean_min=_CONSULT_MEAN_MODERATE,
    consult_duration_sigma=0.35,
    speed_multiplier_range=_SPEED_RANGES_MODERATE,
    renege_hazard_per_min={1: 0.006, 2: 0.004},
    escalation_hazard_per_min=0.003,
))

_register(ScenarioConfig(
    name="surge_day",
    tier="moderate",
    notes="",
    shift_length_min=131040,
    doctor_roster=_roster(n_fast=5, n_medium=10, n_slow=5),
    **_STANDARD_COST_WEIGHTS,
    arrival_rate_per_min=_REAL_ARRIVAL_RATE_PER_MIN * 1.5,
    pac_scale_probs=_REAL_PAC_SCALE_PROBS,
    triage_duration_mean_min=6.0,
    consult_duration_mean_min=_CONSULT_MEAN_MODERATE,
    consult_duration_sigma=0.35,
    speed_multiplier_range=_SPEED_RANGES_MODERATE,
    renege_hazard_per_min={1: 0.006, 2: 0.004},
    escalation_hazard_per_min=0.003,
))

_register(ScenarioConfig(
    name="wide_doctor_spread",
    tier="moderate",
    notes="",
    shift_length_min=131040,
    doctor_roster=_roster(n_fast=3, n_medium=8, n_slow=9),
    **_STANDARD_COST_WEIGHTS,
    arrival_rate_per_min=_REAL_ARRIVAL_RATE_PER_MIN,
    pac_scale_probs=_REAL_PAC_SCALE_PROBS,
    triage_duration_mean_min=6.0,
    consult_duration_mean_min=_CONSULT_MEAN_MODERATE,
    consult_duration_sigma=0.35,
    speed_multiplier_range=_SPEED_RANGES_WIDE,
    renege_hazard_per_min={1: 0.006, 2: 0.004},
    escalation_hazard_per_min=0.003,
))

# ===========================================================================
# ADVERSARIAL -- deliberately stacked stress combinations
# ===========================================================================

_register(ScenarioConfig(
    name="starvation_pressure",
    tier="adversarial",
    notes="",
    shift_length_min=131040,
    doctor_roster=_roster(n_fast=2, n_medium=6, n_slow=7),
    w1=1.0, w2=1.2, w3=1.2, penalty_renege=5000.0, penalty_escalation=300.0,
    arrival_rate_per_min=_REAL_ARRIVAL_RATE_PER_MIN,
    pac_scale_probs=(0.20, 0.20, 0.60),
    triage_duration_mean_min=6.0,
    consult_duration_mean_min=_CONSULT_MEAN_MODERATE,
    consult_duration_sigma=0.35,
    speed_multiplier_range=_SPEED_RANGES_MODERATE,
    renege_hazard_per_min={1: 0.015, 2: 0.010},
    escalation_hazard_per_min=0.003,
))

_register(ScenarioConfig(
    name="escalation_pressure",
    tier="adversarial",
    notes="",
    shift_length_min=131040,
    doctor_roster=_roster(n_fast=3, n_medium=7, n_slow=5),
    **_STANDARD_COST_WEIGHTS,
    arrival_rate_per_min=_REAL_ARRIVAL_RATE_PER_MIN * 1.3,
    pac_scale_probs=(0.10, 0.25, 0.65),
    triage_duration_mean_min=6.0,
    consult_duration_mean_min=_CONSULT_MEAN_MODERATE,
    consult_duration_sigma=0.35,
    speed_multiplier_range=_SPEED_RANGES_MODERATE,
    renege_hazard_per_min={1: 0.006, 2: 0.004},
    escalation_hazard_per_min=0.012,
))

_register(ScenarioConfig(
    name="predictable_surge_pressure",
    tier="adversarial",
    notes="",
    shift_length_min=131040,
    doctor_roster=_roster(n_fast=4, n_medium=8, n_slow=4),
    **_STANDARD_COST_WEIGHTS,
    arrival_rate_per_min=_REAL_ARRIVAL_RATE_PER_MIN,
    pac_scale_probs=_REAL_PAC_SCALE_PROBS,
    triage_duration_mean_min=6.0,
    consult_duration_mean_min=_CONSULT_MEAN_MODERATE,
    consult_duration_sigma=0.35,
    speed_multiplier_range=_SPEED_RANGES_MODERATE,
    renege_hazard_per_min={1: 0.004, 2: 0.002},
    escalation_hazard_per_min=0.002,
    hourly_cycle_amplitude=1.0,  
    weekly_cycle_amplitude=0.1,  
))

_register(ScenarioConfig(
    name="compound_pressure",
    tier="adversarial",
    notes="",
    shift_length_min=131040,
    doctor_roster=_roster(n_fast=4, n_medium=8, n_slow=4),
    w1=1.0, w2=1.5, w3=3.0, penalty_renege=2000.0, penalty_escalation=800.0,
    arrival_rate_per_min=_REAL_ARRIVAL_RATE_PER_MIN * 1.15,
    pac_scale_probs=(0.33, 0.33, 0.34),
    triage_duration_mean_min=6.0,
    consult_duration_mean_min=_CONSULT_MEAN_MODERATE,
    consult_duration_sigma=0.35,
    speed_multiplier_range=_SPEED_RANGES_MODERATE,
    renege_hazard_per_min={1: 0.012, 2: 0.008},
    escalation_hazard_per_min=0.008,
))

_register(ScenarioConfig(
    name="chronic_understaffing",
    tier="adversarial",
    notes="",
    shift_length_min=131040,
    doctor_roster=_roster(n_fast=2, n_medium=5, n_slow=3),
    **_STANDARD_COST_WEIGHTS,
    arrival_rate_per_min=_REAL_ARRIVAL_RATE_PER_MIN,
    pac_scale_probs=_REAL_PAC_SCALE_PROBS,
    triage_duration_mean_min=6.0,
    consult_duration_mean_min={1: 18.0, 2: 42.0, 3: 95.0},
    consult_duration_sigma=0.35,
    speed_multiplier_range=_SPEED_RANGES_MODERATE,
    renege_hazard_per_min={1: 0.008, 2: 0.005},
    escalation_hazard_per_min=0.004,
))


def get_config(name: str) -> ScenarioConfig:
    if name not in CONFIGS:
        raise KeyError(f"Unknown config '{name}'. Available: {sorted(CONFIGS)}")
    return CONFIGS[name]


def available_configs() -> List[str]:
    return sorted(CONFIGS)


def configs_by_tier() -> Dict[str, List[str]]:
    tiers: Dict[str, List[str]] = {"basic": [], "moderate": [], "adversarial": []}
    for cfg in CONFIGS.values():
        tiers[cfg.tier].append(cfg.name)
    return {tier: sorted(names) for tier, names in tiers.items()}
