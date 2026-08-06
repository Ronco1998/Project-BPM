"""
tune_hyperparameters.py
-----------------------
Offline Optuna search for the Deterministic Dynamic Priority Index policy in
policies/my_policy.py. Maximises the challenge Overall Score:

    Overall = (1/3) * mean(moderate uplift) + (2/3) * mean(adversarial uplift)

where per-config uplift is measured against the FIFO baseline on the *same*
seeds, using total_cost_post_warmup (exactly as run_simulation.py scores it).

Nothing here is imported by the policy at submission time -- this is a pure
offline tool. Run it, then paste the printed best params into DEFAULT_PARAMS
in policies/my_policy.py.

Usage:
    pip install optuna
    python tune_hyperparameters.py
"""

import os
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine.simulator import simulate_shift
from policies.my_policy import Submission
from policies.fifo_baseline import FIFOPolicy


# ---------------------------------------------------------------------------
# Search settings -- tune these to trade search quality against wall-clock time.
# ---------------------------------------------------------------------------
N_TRIALS = 40          # Optuna trials.
N_SEEDS = 3            # Replications per config per evaluation (higher = less noise).
BASE_SEED = 67         # Matches run_simulation.py default for comparable scoring.
N_WORKERS = max(1, (os.cpu_count() or 2) - 1)
STUDY_SEED = 42        # Makes the TPE sampler reproducible.

# Scored configs, split by tier. Basic-tier configs are intentionally excluded
# (they don't count toward the Overall Score).
MODERATE_CONFIGS = ["classic_baseline", "surge_day", "wide_doctor_spread"]
ADVERSARIAL_CONFIGS = [
    "starvation_pressure",
    "escalation_pressure",
    "predictable_surge_pressure",
    "compound_pressure",
    "chronic_understaffing",
]
ALL_SCORED = MODERATE_CONFIGS + ADVERSARIAL_CONFIGS


# ---------------------------------------------------------------------------
# Worker (top-level so it is picklable for ProcessPoolExecutor on Windows).
# ---------------------------------------------------------------------------
def _shift_cost(task):
    """Run one shift and return its post-warmup cost."""
    kind, config_name, seed, params = task
    if kind == "fifo":
        result = simulate_shift(config_name, seed, FIFOPolicy)
    else:
        def factory(shift_config):
            return Submission(shift_config, params=params)

        result = simulate_shift(config_name, seed, factory)
    return result.total_cost_post_warmup


def _mean_cost_by_config(pool, kind, params, seeds):
    """Dispatch (config x seed) shifts in parallel and average by config."""
    tasks = [(kind, cfg, seed, params) for cfg in ALL_SCORED for seed in seeds]
    costs = list(pool.map(_shift_cost, tasks))
    means = {}
    idx = 0
    for cfg in ALL_SCORED:
        means[cfg] = statistics.mean(costs[idx:idx + len(seeds)])
        idx += len(seeds)
    return means


def _overall_score(policy_mean, fifo_mean):
    """Reproduce run_simulation.py's Overall Score from per-config mean costs."""
    def uplift(cfg):
        base = fifo_mean[cfg]
        if base <= 0.0:
            return 0.0
        return (base - policy_mean[cfg]) / base * 100.0

    mod = statistics.mean([uplift(c) for c in MODERATE_CONFIGS])
    adv = statistics.mean([uplift(c) for c in ADVERSARIAL_CONFIGS])
    overall = (1.0 / 3.0) * mod + (2.0 / 3.0) * adv
    return overall, mod, adv


def _sample_params(trial):
    return {
        "tau_base": trial.suggest_float("tau_base", 25.0, 45.0),
        "tau_penalty": trial.suggest_float("tau_penalty", 0.0, 15.0),
        "beta": trial.suggest_float("beta", 0.02, 0.25),
        "alpha_1": trial.suggest_float("alpha_1", 1e-3, 5e-2, log=True),
        "alpha_2": trial.suggest_float("alpha_2", 1e-4, 1e-2, log=True),
        "delta_slow": trial.suggest_float("delta_slow", 0.0, 1.0),
        "kappa": trial.suggest_float("kappa", 0.0, 1.0),
    }


def main():
    try:
        import optuna
        from optuna.samplers import TPESampler
    except ImportError:
        sys.exit("Optuna is required. Install it with:  pip install optuna")

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    seeds = [BASE_SEED + i for i in range(N_SEEDS)]

    print(f"Tuning over {len(ALL_SCORED)} configs x {N_SEEDS} seeds "
          f"using {N_WORKERS} worker(s), {N_TRIALS} trials.\n")

    with ProcessPoolExecutor(max_workers=N_WORKERS) as pool:
        # FIFO baseline is params-independent -> compute once and reuse.
        print("Computing FIFO baseline costs...")
        fifo_mean = _mean_cost_by_config(pool, "fifo", None, seeds)

        def objective(trial):
            params = _sample_params(trial)
            policy_mean = _mean_cost_by_config(pool, "policy", params, seeds)
            overall, mod, adv = _overall_score(policy_mean, fifo_mean)
            trial.set_user_attr("moderate", mod)
            trial.set_user_attr("adversarial", adv)
            return overall

        study = optuna.create_study(direction="maximize", sampler=TPESampler(seed=STUDY_SEED))
        study.optimize(objective, n_trials=N_TRIALS, show_progress_bar=True)

    best = study.best_trial
    print("\n" + "=" * 60)
    print(f"BEST Overall Score: {best.value:.2f}%")
    print(f"  moderate  : {best.user_attrs.get('moderate', float('nan')):.2f}%")
    print(f"  adversarial: {best.user_attrs.get('adversarial', float('nan')):.2f}%")
    print("=" * 60)
    print("\nPaste into DEFAULT_PARAMS in policies/my_policy.py:\n")
    print("DEFAULT_PARAMS = {")
    for key in ("tau_base", "tau_penalty", "beta", "alpha_1", "alpha_2", "delta_slow", "kappa"):
        print(f'    "{key}": {best.params[key]:.6g},')
    print("}")


if __name__ == "__main__":
    main()
