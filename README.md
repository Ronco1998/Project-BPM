# ED Doctor-Queue Challenge — Starter Kit

## What You're Doing

You're writing a policy that decides which waiting patient each free doctor should see next. Your policy is scored on **cost**: a weighted sum of patient wait times (weighted by urgency) plus penalties for patients who leave without being seen or whose condition worsens while waiting. Your **Overall Score** is the average improvement (uplift %) over a FIFO baseline across all evaluation scenarios.

See **PROPOSEL.md** for the full problem specification.

## Environment Setup

- **Python 3.10 or later** 

You may use any of the standart libraries you need in your policy.
If unsure please contact us.

## Directory Layout

```
.
├── README.md              (this file)
├── PROPOSEL.md            (full project specification)
├── run_simulation.py      (the script you run)
├── interface.py           (your policy's contract)
├── policies/
│   ├── policy_Jorden_Tom_Arik.py  (<- your policy goes here)
│   ├── fifo_baseline.py           (example baselines for comparison)
│   └── random_baseline.py
├── engine/                (simulator engine — read-only)
│   ├── simulator.py
│   └── configs.py
├── logs/                  (empty; you can export event logs here)
└── requirements.txt       (empty; included for compatibility)
```

## How to Run

Edit the settings block near the bottom of `run_simulation.py`:

```python
POLICY_NAME = "my_policy"          # "my_policy", "fifo", "random"

RUNS = [
    RunSpec(config="sanity_check", n_simulations=3),
    RunSpec(config="gentle_day", n_simulations=3),
    RunSpec(config="classic_baseline", n_simulations=3),
    RunSpec(config="surge_day", n_simulations=3),
    RunSpec(config="wide_doctor_spread", n_simulations=3),
    RunSpec(config="starvation_pressure", n_simulations=3),
    RunSpec(config="escalation_pressure", n_simulations=3),
]

OUTPUT_DIR = None       # None or "logs" to export detailed event CSVs
N_WORKERS = 4           # parallel workers; set to 1 for easier debugging
SEED = None             # None (random, usefull for evaluation) or integer (reproducible, usefull for debugging) 
```

Then run:

```bash
python run_simulation.py
```

You'll get output like:

```
config                  n       mean cost           std  renege%    esc%   uplift%
====================================================================================
classic_baseline       20     1,642,882      202,778      2.5%     2.2%       0.0%
surge_day              20    19,303,941    1,290,827     15.9%    15.5%       0.0%
wide_doctor_spread     20     3,406,888      416,219      4.9%     4.5%       0.0%
starvation_pressure    20    38,592,693    1,683,926     21.4%    13.3%       0.0%
escalation_pressure    20    96,767,415   13,432,657     32.3%    64.8%       0.0%

OVERALL SCORE: 0.0% (1/3 × moderate 0.0% + 2/3 × adversarial 0.0%)
```

### Output Columns

- **n**: replications (independent random seeds per run)
- **mean cost**: average total cost across replications
- **std**: standard deviation (measure of variability)
- **renege%**: fraction of patients who left without being seen
- **esc%**: fraction of high-acuity patients whose outcome worsened while waiting
- **uplift%**: your improvement vs. FIFO on the same random seeds (positive = better)

## Overall Score

Your **Overall Score** weights the harder tiers unequally:
$$\text{Overall Score} = \tfrac{1}{3}\,\bar{u}_{\text{moderate}} + \tfrac{2}{3}\,\bar{u}_{\text{adversarial}}$$

where $\bar{u}_{\text{moderate}}$ and $\bar{u}_{\text{adversarial}}$ are mean % improvement over FIFO, averaged separately within each tier. Crashing = -1000% on that config.

### Config Tiers

- **Basic** (`sanity_check`, `gentle_day`) — low stress; use to confirm your code works
- **Moderate** (`classic_baseline`, `surge_day`, `wide_doctor_spread`) — realistic ED day with one stress factor (higher demand or wider doctor-speed gap)
- **Adversarial** (`starvation_pressure`, `escalation_pressure`, `predictable_surge_pressure`) — stacked stress designed to surface trade-offs.


## Policy Rules

- Implement `choose_patient(state: HospitalState) -> int` — must return exactly one patient_id from `state.waiting_patients`
- Optional hooks: `on_consultation_complete()` and `on_patient_exit()` for tracking statistics
- Use any tools and libraries you find helpful to solve the problem
- **No side effects** — no file I/O, network access, or reaching outside the `state` object
- **Deterministic** — if you need randomness, seed it from `state`/`shift_config`, not external sources
- Any error crashes the run; no fallbacks

## Understanding the Simulated World

### Arrivals and Cycles

Arrivals follow deterministic, repeating patterns:

- **Hour-of-day cycle**: busier in the evening (~19:30 peak), quieter overnight (~3:00 trough)
- **Day-of-week cycle**: busier on weekends, quieter mid-week

These are fully predictable if you know `shift_elapsed_min`. To compute the current hour of day and day of week:

```python
hour_of_day = (state.shift_elapsed_min % 1440) / 60.0   # [0, 24)
day_of_week = (state.shift_elapsed_min % 10080) / 1440.0  # [0, 7)
```

Minute 0 = Monday 00:00 (midnight). A thoughtful policy can use this structure to anticipate busier periods.

### Doctor Speed Tiers

Each doctor has a `speed_tier` ("Fast", "Medium", or "Slow"). This affects how long consultation takes. See `state.available_doctor.speed_tier` when making assignment decisions.

### Patient Acuity

Each patient has a `pac_scale` (1, 2, or 3): low, medium, or high urgency. Higher acuity has higher cost weights in the objective.

## Debugging Tips

- **Single-process debugging**: Set `N_WORKERS = 1` for cleaner error tracebacks
- **Reproducible runs**: Set `SEED = 42` (or any integer) to get the same random variations across runs; useful for side-by-side comparisons
- **Start from a baseline**: Copy `policies/fifo_baseline.py` if you want a known-working policy to modify
- **Export logs**: Set `OUTPUT_DIR = "logs"` to get detailed CSV event logs (case_id, activity, timestamp, resource, pac_scale) for pm4py analysis or manual inspection
