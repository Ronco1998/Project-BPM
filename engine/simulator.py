"""
engine/simulator.py
--------------------
The discrete-event simulation engine. You are not expected to modify or
need to fully understand this file to complete the project.
"""

import csv
import heapq
import itertools
import math
import os
import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from engine.configs import ScenarioConfig, get_config, hour_of_day_multiplier, day_of_week_multiplier
from interface import (
    CostWeights,
    DoctorInfo,
    HospitalState,
    ShiftConfig,
    WaitingPatient,
)

# Wall-clock budget per choose_patient() call, in seconds.
SOFT_CALL_TIME_BUDGET_SEC = 2

# Warmup period: discard the first full week (10080 minutes) from results to let the
# system reach steady state and clear one full day-of-week cycle. Only outcomes finalized after this time count.
WARMUP_MIN = 10080


@dataclass
class _Patient:
    patient_id: int
    pac_scale: int
    triage_end_time: float = 0.0
    escalated: bool = False
    status: str = "waiting"        # waiting -> in_consultation -> done
    outcome: Optional[str] = None
    wait_time_min: Optional[float] = None
    assigned_doctor: Optional[str] = None


@dataclass
class _Doctor:
    doctor_id: str
    speed_tier: str
    speed_multiplier: float
    free: bool = True


@dataclass
class ShiftResult:
    config_name: str
    seed: int
    n_patients_arrived: int = 0
    n_completed: int = 0
    n_reneged: int = 0
    n_escalated: int = 0
    n_still_waiting_at_end: int = 0
    n_policy_violations: int = 0
    total_cost: float = 0.0
    log_path: Optional[str] = None
    # Stats after warmup (first 1440 min / first day discarded)
    n_patients_arrived_post_warmup: int = 0
    n_completed_post_warmup: int = 0
    n_reneged_post_warmup: int = 0
    n_escalated_post_warmup: int = 0
    total_cost_post_warmup: float = 0.0


_EVENT_SEQ = itertools.count()


def _sample_lognormal(rng: random.Random, mean_min: float, sigma: float) -> float:
    # Parameterized so the *median* of the draw is close to mean_min.
    mu = math.log(max(mean_min, 0.1))
    return rng.lognormvariate(mu, sigma)


@dataclass
class _PreGeneratedPatient:
    """Pre-drawn stochastic quantities for one patient, independent of policy."""
    patient_id: int
    pac_scale: int
    arrival_time: float
    triage_duration: float
    intrinsic_consultation_duration: float  # Lognormal draw, before doctor speed multiplier
    renege_timer: Optional[float] = None  # Time-to-renege from triage end (pac_scale 1/2)
    escalation_timer: Optional[float] = None  # Time-to-escalation (pac_scale 3)


def simulate_shift(
    config_name: str,
    seed: int,
    policy_class,
    export_log_path: Optional[str] = None,
) -> ShiftResult:
    """Run exactly one simulated shift and return an aggregate result."""
    cfg: ScenarioConfig = get_config(config_name)
    rng = random.Random(seed)

    # ---- Pre-generate doctor speed multipliers (deterministic, independent of policy) ----
    doctors: Dict[str, _Doctor] = {}
    for spec in cfg.doctor_roster:
        lo, hi = cfg.speed_multiplier_range[spec.speed_tier]
        doctors[spec.doctor_id] = _Doctor(
            doctor_id=spec.doctor_id,
            speed_tier=spec.speed_tier,
            speed_multiplier=rng.uniform(lo, hi),
        )

    shift_config = ShiftConfig(
        shift_length_min=cfg.shift_length_min,
        doctor_roster=[DoctorInfo(doctor_id=d.doctor_id, speed_tier=d.speed_tier) for d in doctors.values()],
        cost_weights=CostWeights(
            w1=cfg.w1, w2=cfg.w2, w3=cfg.w3,
            penalty_renege=cfg.penalty_renege, penalty_escalation=cfg.penalty_escalation,
        ),
    )
    policy = policy_class(shift_config)

    patients: Dict[int, _Patient] = {}
    waiting: List[int] = []  # patient_ids currently waiting for a doctor
    events: List[tuple] = []  # heap of (time, seq, kind, payload)
    log_rows: List[dict] = []
    pre_generated: Dict[int, _PreGeneratedPatient] = {}  # patient_id -> pre-drawn data

    result = ShiftResult(config_name=config_name, seed=seed)

    def push(t: float, kind: str, payload: dict) -> None:
        heapq.heappush(events, (t, next(_EVENT_SEQ), kind, payload))

    def log_event(case_id: int, activity: str, t: float, resource: str, pac_scale: int) -> None:
        if export_log_path is not None:
            log_rows.append({
                "case_id": case_id,
                "activity": activity,
                "timestamp_min": round(t, 2),
                "resource": resource,
                "pac_scale": pac_scale,
            })

    # ---- Pre-generate all patient arrivals and their stochastic quantities ----
    # This ensures the world is identical across policies with the same (config, seed)
    # Use thinning (acceptance-rejection) to handle time-varying arrival rate based on day/week cycles
    max_multiplier = (1.0 + cfg.hourly_cycle_amplitude) * (1.0 + cfg.weekly_cycle_amplitude)
    max_rate = cfg.arrival_rate_per_min * max_multiplier

    t = 0.0
    next_patient_id = 1
    while t < cfg.shift_length_min:
        t += rng.expovariate(max_rate)
        if t >= cfg.shift_length_min:
            break

        # Accept this candidate arrival with probability proportional to local rate
        local_multiplier = hour_of_day_multiplier(t, cfg.hourly_cycle_amplitude) * day_of_week_multiplier(t, cfg.weekly_cycle_amplitude)
        local_rate = cfg.arrival_rate_per_min * local_multiplier
        if rng.random() >= local_rate / max_rate:
            # Reject this candidate; move to next
            continue

        pac_scale = rng.choices([1, 2, 3], weights=list(cfg.pac_scale_probs))[0]

        # Pre-generate all stochastic quantities for this patient
        triage_dur = max(1.0, rng.gauss(cfg.triage_duration_mean_min, 1.5))
        base_mean = cfg.consult_duration_mean_min[pac_scale]
        intrinsic_consult = _sample_lognormal(rng, base_mean, cfg.consult_duration_sigma)

        renege_timer = None
        escalation_timer = None
        if pac_scale in (1, 2):
            hazard = cfg.renege_hazard_per_min[pac_scale]
            if hazard > 0:
                renege_timer = rng.expovariate(hazard)
        elif pac_scale == 3:
            hazard = cfg.escalation_hazard_per_min
            if hazard > 0:
                escalation_timer = rng.expovariate(hazard)

        pre_generated[next_patient_id] = _PreGeneratedPatient(
            patient_id=next_patient_id,
            pac_scale=pac_scale,
            arrival_time=t,
            triage_duration=triage_dur,
            intrinsic_consultation_duration=intrinsic_consult,
            renege_timer=renege_timer,
            escalation_timer=escalation_timer,
        )
        push(t, "ARRIVAL", {"patient_id": next_patient_id, "pac_scale": pac_scale})
        next_patient_id += 1

    def try_assign(now: float) -> None:
        # Process free doctors in a fixed, disclosed order (ascending doctor_id),
        # one decision at a time, for as long as both a free doctor and a
        # waiting patient exist.
        while waiting:
            free_ids = sorted(d.doctor_id for d in doctors.values() if d.free)
            if not free_ids:
                return
            doc = doctors[free_ids[0]]

            state = HospitalState(
                shift_elapsed_min=now,
                waiting_patients=[
                    WaitingPatient(
                        patient_id=pid,
                        pac_scale=patients[pid].pac_scale,
                        elapsed_wait_min=now - patients[pid].triage_end_time,
                    )
                    for pid in waiting
                ],
                available_doctor=DoctorInfo(doctor_id=doc.doctor_id, speed_tier=doc.speed_tier),
                n_doctors_busy=sum(1 for d in doctors.values() if not d.free),
                n_doctors_free=sum(1 for d in doctors.values() if d.free),
            )

            t0 = time.process_time()
            chosen_id = policy.choose_patient(state)
            if chosen_id not in waiting:
                raise ValueError(f"choose_patient returned patient_id {chosen_id!r} which is not waiting")
            elapsed = time.process_time() - t0
            if elapsed > SOFT_CALL_TIME_BUDGET_SEC:
                raise RuntimeError(f"choose_patient took {elapsed:.2f}s, exceeding soft budget "
                                   f"{SOFT_CALL_TIME_BUDGET_SEC}s -- policy is too slow")

            waiting.remove(chosen_id)
            doc.free = False
            p = patients[chosen_id]
            p.status = "in_consultation"
            p.wait_time_min = now - p.triage_end_time
            p.assigned_doctor = doc.doctor_id

            # Use pre-generated intrinsic duration, multiply by doctor's speed multiplier
            intrinsic_duration = pre_generated[chosen_id].intrinsic_consultation_duration
            duration = intrinsic_duration * doc.speed_multiplier
            log_event(chosen_id, "Doctor Consultation Start", now, doc.doctor_id, p.pac_scale)
            push(now + duration, "CONSULT_END", {"patient_id": chosen_id, "doctor_id": doc.doctor_id, "duration": duration})

    def finalize_patient(p: _Patient, now: float, outcome: str) -> None:
        p.status = "done"
        p.outcome = outcome
        w = {1: cfg.w1, 2: cfg.w2, 3: cfg.w3}[p.pac_scale]
        cost = w * (p.wait_time_min if p.wait_time_min is not None else (now - p.triage_end_time))
        if outcome == "Renege":
            cost += cfg.penalty_renege
            result.n_reneged += 1
        if outcome == "Escalated":
            cost += cfg.penalty_escalation
            result.n_escalated += 1
        if outcome not in ("Renege",):
            result.n_completed += 1
        result.total_cost += cost

        # Track post-warmup stats separately (outcomes finalized after first day)
        if now > WARMUP_MIN:
            result.n_completed_post_warmup += (1 if outcome not in ("Renege",) else 0)
            result.n_reneged_post_warmup += (1 if outcome == "Renege" else 0)
            result.n_escalated_post_warmup += (1 if outcome == "Escalated" else 0)
            result.total_cost_post_warmup += cost

        policy.on_patient_exit(p.patient_id, outcome)

    # ---- Main event loop ----
    while events:
        now, _, kind, payload = heapq.heappop(events)

        if kind == "ARRIVAL":
            pid = payload["patient_id"]
            pac = payload["pac_scale"]
            patients[pid] = _Patient(patient_id=pid, pac_scale=pac)
            result.n_patients_arrived += 1
            if now > WARMUP_MIN:
                result.n_patients_arrived_post_warmup += 1
            # Use pre-generated triage duration
            triage_duration = pre_generated[pid].triage_duration
            log_event(pid, "Arrival", now, "N/A", pac)
            push(now + triage_duration, "TRIAGE_END", {"patient_id": pid})

        elif kind == "TRIAGE_END":
            pid = payload["patient_id"]
            p = patients[pid]
            p.triage_end_time = now
            waiting.append(pid)
            log_event(pid, "Triage End", now, "N/A", p.pac_scale)

            # Use pre-generated renege/escalation timers
            if p.pac_scale in (1, 2) and pre_generated[pid].renege_timer is not None:
                push(now + pre_generated[pid].renege_timer, "RENEGE_CHECK", {"patient_id": pid})
            elif p.pac_scale == 3 and pre_generated[pid].escalation_timer is not None:
                push(now + pre_generated[pid].escalation_timer, "ESCALATION_CHECK", {"patient_id": pid})

            try_assign(now)

        elif kind == "RENEGE_CHECK":
            pid = payload["patient_id"]
            p = patients[pid]
            if p.status == "waiting" and pid in waiting:
                waiting.remove(pid)
                log_event(pid, "Discharge", now, "N/A", p.pac_scale)
                p.wait_time_min = now - p.triage_end_time
                finalize_patient(p, now, "Renege")

        elif kind == "ESCALATION_CHECK":
            pid = payload["patient_id"]
            p = patients[pid]
            if p.status == "waiting" and not p.escalated:
                p.escalated = True

        elif kind == "CONSULT_END":
            pid = payload["patient_id"]
            doc_id = payload["doctor_id"]
            duration = payload["duration"]
            p = patients[pid]
            doctors[doc_id].free = True
            log_event(pid, "Doctor Consultation End", now, doc_id, p.pac_scale)
            log_event(pid, "Discharge", now, doc_id, p.pac_scale)
            policy.on_consultation_complete(doc_id, pid, duration)
            outcome = "Escalated" if p.escalated else "Discharged"
            finalize_patient(p, now, outcome)
            try_assign(now)

    result.n_still_waiting_at_end = sum(1 for p in patients.values() if p.status == "waiting")

    if export_log_path is not None:
        os.makedirs(os.path.dirname(export_log_path) or ".", exist_ok=True)
        with open(export_log_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["case_id", "activity", "timestamp_min", "resource", "pac_scale"])
            writer.writeheader()
            for row in sorted(log_rows, key=lambda r: (r["timestamp_min"], r["case_id"])):
                writer.writerow(row)
        result.log_path = export_log_path

    return result
