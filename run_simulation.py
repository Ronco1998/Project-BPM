"""
run_simulation.py
------------------
This is the script you actually run. Edit the settings inside the
`if __name__ == "__main__":` block at the bottom of this file, then run:

    python run_simulation.py

It will simulate your policy across whichever configs and replication counts you list, in parallel, and print a
summary. It can optionally write one CSV event log per simulated shift to a
directory of your choice, in the same case_id / activity / timestamp /
resource / pac_scale shape you already worked with in HW3 -- useful if
you want to look for bottlenecks your own policy is creating.

You do not need to read or modify engine/ or interface.py to use this
script. See README.md for setup and usage instructions.
"""

import importlib
import os
import random
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine.simulator import simulate_shift, ShiftResult
from engine.configs import available_configs, get_config


# Available policies: map policy name -> (module_path, class_name)
# Add your own policy by creating a .py file in policies/ and adding an entry here.
AVAILABLE_POLICIES = {
    "my_policy": ("policies.my_policy", "Submission"),
    "fifo": ("policies.fifo_baseline", "FIFOPolicy"),
    "random": ("policies.random_baseline", "RandomPolicy"),
}


def _load_policy_class(module_name: str, class_name: str):
    module = importlib.import_module(module_name)
    return getattr(module, class_name)


def _worker(task):
    """Worker for one replication. Catches crashes and returns a result with violation marker."""
    config_name, seed, policy_module, policy_class_name, log_path = task
    try:
        policy_class = _load_policy_class(policy_module, policy_class_name)
        return simulate_shift(config_name, seed, policy_class, export_log_path=log_path)
    except Exception as e:
        # Crash during this replication: return a result marked with a violation
        # This signals that the config as a whole failed and gets -1000% uplift
        from engine.simulator import ShiftResult
        result = ShiftResult(config_name=config_name, seed=seed)
        result.n_policy_violations = 1  # Mark as crashed
        return result


@dataclass
class RunSpec:
    config: str
    n_simulations: int


def run_all(
    runs: List[RunSpec],
    policy_module: str,
    policy_class: str,
    output_dir: Optional[str] = None,
    n_workers: int = 1,
    base_seed: Optional[int] = None,
) -> dict:
    """Runs every (config, replication) pair, in parallel, and returns
    {config_name: [ShiftResult, ...]}.

    If base_seed is None: uses random, non-reproducible seeds each run.
    If base_seed is an int: uses base_seed, base_seed+1, base_seed+2, ... (reproducible).
    """

    tasks = []
    for spec in runs:
        for i in range(spec.n_simulations):
            # Generate seed based on base_seed parameter
            if base_seed is None:
                seed = random.randint(1, 2**31 - 1)
            else:
                seed = base_seed + i

            log_path = None
            if output_dir is not None:
                log_path = os.path.join(output_dir, f"{spec.config}_seed{seed}.csv")
            tasks.append((spec.config, seed, policy_module, policy_class, log_path))

    # Cap n_workers to available CPU count to avoid oversubscription
    available_cpus = os.cpu_count() or 1
    n_workers = min(n_workers, available_cpus)

    results_by_config: dict = {spec.config: [] for spec in runs}

    if n_workers <= 1:
        for task in tasks:
            results_by_config[task[0]].append(_worker(task))
    else:
        with ProcessPoolExecutor(max_workers=n_workers) as pool:
            futures = {pool.submit(_worker, task): task for task in tasks}
            for future in as_completed(futures):
                config_name = futures[future][0]
                results_by_config[config_name].append(future.result())

    return results_by_config


CRASH_SENTINEL = -1000.0


def print_summary(results_by_config: dict, config_seeds_used: dict = None) -> None:
    """Print summary statistics for all configs, including uplift vs FIFO baseline.

    Args:
        results_by_config: {config_name: [ShiftResult, ...]}
        config_seeds_used: {config_name: [seeds_list]} used to score this policy
    """
    if not results_by_config:
        print("\nNo results to display.\n")
        return

    if config_seeds_used is None:
        config_seeds_used = {}

    # Compute policy stats for all configs
    all_rows = []
    scored_moderate_uplift = []
    scored_adversarial_uplift = []

    for config_name, results in results_by_config.items():
        if not results:
            continue

        crashed = any(r.n_policy_violations > 0 for r in results)
        n = len(results)
        costs = [r.total_cost_post_warmup for r in results]
        mean_cost = statistics.mean(costs) if costs else 0.0
        std_cost = statistics.stdev(costs) if n > 1 and costs else 0.0
        arrived = sum(r.n_patients_arrived_post_warmup for r in results)
        renege_pct = 100.0 * sum(r.n_reneged_post_warmup for r in results) / max(arrived, 1)
        esc_pct = 100.0 * sum(r.n_escalated_post_warmup for r in results) / max(arrived, 1)

        all_rows.append((config_name, n, mean_cost, std_cost, renege_pct, esc_pct, crashed))

    if not all_rows:
        print("\nNo results to display.\n")
        return

    # Determine column widths dynamically
    config_width = max(18, max(len(row[0]) for row in all_rows)) + 1
    n_width = max(3, len(str(max(row[1] for row in all_rows)))) + 2
    cost_width = max(14, max(len(f"{row[2]:,.1f}") for row in all_rows)) + 2
    std_width = max(10, max(len(f"{row[3]:,.1f}") for row in all_rows)) + 2
    renege_width = 9
    esc_width = 8
    uplift_width = 10

    total_width = config_width + n_width + cost_width + std_width + renege_width + esc_width + uplift_width + 2

    # Print policy results table (before FIFO comparison)
    print("\n" + "=" * total_width)
    print(
        f"{'config':<{config_width}}{'n':>{n_width}}{'mean cost':>{cost_width}}"
        f"{'std':>{std_width}}{'renege%':>{renege_width}}{'esc%':>{esc_width}}{'uplift%':>{uplift_width}}"
    )
    print("-" * total_width)

    # Compute FIFO baseline and uplift for each config
    for config_name, n, mean_cost, std_cost, renege_pct, esc_pct, crashed in all_rows:
        if crashed:
            uplift_pct = CRASH_SENTINEL
            crash_marker = " [CRASH]"
        else:
            # Run FIFO on the same seeds to get fair comparison
            seeds = config_seeds_used.get(config_name, list(range(1, n + 1)))
            fifo_results = _run_fifo_baseline(config_name, seeds)
            fifo_mean = statistics.mean([r.total_cost_post_warmup for r in fifo_results])
            if fifo_mean > 0:
                uplift_pct = (fifo_mean - mean_cost) / fifo_mean * 100
            else:
                uplift_pct = 0.0
            crash_marker = ""

        print(
            f"{config_name:<{config_width}}{n:>{n_width}}{mean_cost:>{cost_width},.1f}"
            f"{std_cost:>{std_width},.1f}{renege_pct:>{renege_width}.1f}%{esc_pct:>{esc_width}.1f}%"
            f"{uplift_pct:>{uplift_width}.1f}%{crash_marker}"
        )

        if config_name not in ("sanity_check", "gentle_day"):
            config = get_config(config_name)
            if crashed:
                uplift_to_add = CRASH_SENTINEL
            else:
                uplift_to_add = uplift_pct

            if config.tier == "moderate":
                scored_moderate_uplift.append(uplift_to_add)
            elif config.tier == "adversarial":
                scored_adversarial_uplift.append(uplift_to_add)

    print("=" * total_width)

    if scored_moderate_uplift and scored_adversarial_uplift:
        mean_moderate = statistics.mean(scored_moderate_uplift)
        mean_adversarial = statistics.mean(scored_adversarial_uplift)
        overall_score = (1/3) * mean_moderate + (2/3) * mean_adversarial
        print(f"\nOVERALL SCORE: {overall_score:.1f}% (1/3 × moderate {mean_moderate:.1f}% + 2/3 × adversarial {mean_adversarial:.1f}%)")
    print()


def _run_fifo_baseline(config_name: str, seeds: List[int]) -> List:
    """Run FIFO baseline on specific seeds for fair uplift comparison."""
    fifo_results = []
    for seed in seeds:
        result = simulate_shift(config_name, seed, _load_policy_class("policies.fifo_baseline", "FIFOPolicy"))
        fifo_results.append(result)
    return fifo_results


def main(policy_name, run_specs, output_dir, n_workers, base_seed=None):
    # Resolve policy_name to (module, class)
    if policy_name not in AVAILABLE_POLICIES:
        raise ValueError(
            f"Unknown policy '{policy_name}'. Available policies: {', '.join(sorted(AVAILABLE_POLICIES.keys()))}"
        )
    policy_module, policy_class = AVAILABLE_POLICIES[policy_name]

    unknown = [r.config for r in run_specs if r.config not in available_configs()]
    if unknown:
        raise ValueError(f"Unknown config(s) {unknown}. Available: {available_configs()}")

    if output_dir is not None:
        os.makedirs(output_dir, exist_ok=True)

    seed_msg = f"seed={base_seed}" if base_seed is not None else "random seeds"
    print(f"Running policy '{policy_name}' ({policy_class} from {policy_module}) "
          f"across {len(run_specs)} config(s) using {n_workers} worker process(es) ({seed_msg})...")

    # Track which seeds were used for each config (needed for FIFO baseline comparison)
    config_seeds_used = {}
    for spec in run_specs:
        if base_seed is None:
            # Can't track seeds if they're random; FIFO runner will have to regenerate
            config_seeds_used[spec.config] = None
        else:
            config_seeds_used[spec.config] = [base_seed + i for i in range(spec.n_simulations)]

    results = run_all(run_specs, policy_module, policy_class, output_dir=output_dir, n_workers=n_workers, base_seed=base_seed)
    print_summary(results, config_seeds_used)

    if output_dir is not None:
        print(f"Event logs written to: {os.path.abspath(output_dir)}")


if __name__ == "__main__":
    # ============================================================
    # EDIT BELOW THIS LINE FOR TESTING / DEBUGGING
    # ============================================================

    # Which policy to run. Choose one name from AVAILABLE_POLICIES (defined above).
    # Examples: "my_policy", "fifo", "random"
    POLICY_NAME = "my_policy"

    # Which configs to run, and how many independent simulated
    # shifts ("replications") per config. See engine/configs.py for the
    # full list of available configs (organized into basic / moderate /
    # adversarial tiers), or just run this script once with an unknown
    # config name to see the full list in the error message.
    RUNS = [
        RunSpec(config="sanity_check", n_simulations=3),
        RunSpec(config="gentle_day", n_simulations=3),
        RunSpec(config="classic_baseline", n_simulations=3),
        RunSpec(config="surge_day", n_simulations=3),
        RunSpec(config="wide_doctor_spread", n_simulations=3),
        RunSpec(config="starvation_pressure", n_simulations=3),
        RunSpec(config="escalation_pressure", n_simulations=3),
        RunSpec(config="chronic_understaffing", n_simulations=3),
        RunSpec(config="predictable_surge_pressure", n_simulations=3),
        RunSpec(config="compound_pressure", n_simulations=3),
    ]

    # Set to a directory path (e.g. "logs") to write one CSV event log per
    # simulated shift there. Set to None to skip log export entirely.
    OUTPUT_DIR = "logs"

    # Number of parallel worker processes. Set to 1 to disable parallelism
    # (useful for debugging -- easier to read tracebacks from a single
    # process). Will be capped to the number of available CPUs.
    N_WORKERS = 1

    main(POLICY_NAME, RUNS, OUTPUT_DIR, N_WORKERS, base_seed=67)
