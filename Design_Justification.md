# Design Justification — Deterministic Dynamic Priority Index

## 1. The Objective We Optimized

The grader scores a policy by the **cost uplift against a FIFO baseline**, averaged
across configurations and combined as:

$$\text{Overall} = \tfrac{1}{3}\,\overline{\text{uplift}}_{\text{moderate}} + \tfrac{2}{3}\,\overline{\text{uplift}}_{\text{adversarial}}$$

where per-shift cost is

$$\text{Cost} = \sum_i w(\text{pac}_i)\cdot W_i \;+\; \text{penalty}_{\text{renege}}\cdot(\text{reneges}) \;+\; \text{penalty}_{\text{escalation}}\cdot(\text{escalations}).$$

Because adversarial configs carry **twice** the weight, our design is deliberately
biased toward surviving stacked-stress environments (extreme renege penalties,
escalation storms, chronic understaffing) rather than merely polishing the easy day.

## 2. The Policy: A Marginal-Cost Priority Index

Every time a doctor becomes free, we score each waiting patient by the **marginal
cost of making them wait one more increment**, $C_i$, and serve the argmax. This is
a greedy, myopic realization of the $c\mu$ family, but with the "cost rate" made
*acuity- and time-aware* instead of constant.

**PAC 1 / PAC 2 — linear renege risk.**

$$C_i = w(\text{pac}) + \alpha_{\text{pac}} \cdot \text{penalty}_{\text{renege}} \cdot W_i$$

Reneging is a memoryless (exponential) hazard: the probability a low-acuity patient
walks out grows *roughly linearly* in the wait for the ranges we see. So the expected
marginal renege cost is linear in $W_i$, scaled by the published renege penalty and a
learned sensitivity $\alpha$. PAC 1 and PAC 2 differ only in their base weight and
their $\alpha$, letting the tuner decide how hard to defend each tier.

**PAC 3 — exponential escalation hazard with a safety window.**

$$C_i = w(3) + \text{penalty}_{\text{escalation}} \cdot \exp\!\big(\beta\,(W_i - \tau_{\text{dynamic}})\big)$$

Clinical deterioration is not linear. Below a **safe window** $\tau$ a high-acuity
patient is stable; past it, mortality/escalation risk climbs sharply. The exponential
term encodes exactly that "cliff": it is near-flat while $W_i < \tau$ and then rises
steeply, forcing PAC-3 patients to the front *before* they tip over rather than after.

**Concrete $\tau$.** We anchor $\tau_{\text{base}} \approx 37.5$ min. This maps to the
escalation "knee" we identified in Part 1 — the point where the PAC-3 outcome curve
stops being flat and the marginal risk of an additional minute of waiting jumps. Setting
$\tau$ near that knee means the exponential stays quiet for genuinely stable patients
(so we don't starve PAC 1/2 needlessly) but detonates just as a PAC-3 case approaches
the danger zone.

## 3. Surge Anticipation (Using Time-of-Day That Actually Changes Decisions)

The arrival process is deterministic in expectation: a nightly peak near **19:30**, an
overnight trough, and heavier **weekends**. We compute a surge signal in $[0,1]$:

$$\text{surge} = \tfrac{1}{2}\big(1 + \cos((\text{hour}-19.5)\cdot \pi/12)\big),$$

raised to a higher floor on weekends ($\text{day} \ge 5$). We then **shrink the safe
window when a surge is imminent**:

$$\tau_{\text{dynamic}} = \tau_{\text{base}} - \text{surge}\cdot\tau_{\text{penalty}}.$$

The insight: if a flood of new patients is about to arrive, a PAC-3 case that looks
"still safe" now will be much harder to reach in ten minutes. Contracting $\tau$ pulls
those patients forward *pre-emptively*. Crucially, this is a **per-candidate,
decision-changing** use of time — it re-ranks PAC 3 relative to PAC 1/2 — unlike a
uniform clock multiplier (see §5).

## 4. The Central Trade-off: How α and β Reconcile the Adversarial Extremes

The two adversarial worlds pull in opposite directions, and the tuned parameters are
precisely the knobs that balance them:

- **`starvation_pressure`** — `penalty_renege = 5000`, nearly flat acuity weights
  ($w_1{=}1, w_2{=}w_3{=}1.2$), high renege hazards. Here the enemy is patients walking
  out. Value is created by **churning the renege-prone PAC 1/2 queue fast**, which
  demands a *large* $\alpha$ so linear renege cost quickly overtakes a PAC-3's baseline.
- **`escalation_pressure`** — standard weights but a high escalation hazard and a
  PAC-3-heavy mix. Here the enemy is deterioration. Value is created by **guarding
  PAC 3**, which demands a *responsive* $\beta$/$\tau$ so the exponential fires early.

A single global rule cannot serve both, so the tuner splits the difference through the
**relative magnitudes**:

- $\beta \approx 0.0102$ (near the floor) keeps the PAC-3 exponential *gentle* — it
  ramps rather than spikes — so PAC 3 is protected around $\tau$ **without** completely
  freezing out PAC 1/2. If $\beta$ were large, `starvation_pressure` would collapse as
  reneging patients were ignored.
- $\alpha_1 \approx 5.4\!\times\!10^{-3}$ vs. $\alpha_2 \approx 1.6\!\times\!10^{-4}$:
  the tuner defends **PAC 1 far more aggressively than PAC 2**. That is a subtle but
  rational outcome — PAC 2 already carries a higher base weight $w_2 > w_1$, so it earns
  priority "for free," whereas PAC 1's only claim to a doctor is its accumulating renege
  risk. Weighting $\alpha_1 \gg \alpha_2$ prevents the lowest-acuity, highest-flight-risk
  patients from being perpetually starved under heavy renege penalties.

In effect $\beta$ and $\tau$ own the *escalation* axis, $\alpha$ owns the *renege* axis,
and their tuned ratio is the mathematical compromise between the two adversarial regimes.
The final search (40 trials × 8 configs × 3 seeds) reached an **Overall Score of 28.78%**
(moderate 48.71%, adversarial 18.82%) — the gap between the two confirming that the
adversarial tier is where the real cost lives, exactly as its $\tfrac{2}{3}$ weight intends.

## 5. The $c\mu$ Efficiency-Divisor Realization

We initially theorized a full $c\mu$-based scheduling rule that divided each cost rate by
the doctor's expected service time $\mathbb{E}[S_{\text{tier}}]$ and multiplied by an
end-of-shift urgency factor $\big(1+\gamma\max(0,1-T_{\text{rem}}/120)\big)$.

Structural analysis of the simulator's **pull-based** event loop killed both terms. The
engine offers exactly **one** free doctor per decision tick and asks only *"which waiting
patient for this doctor?"*. Within that single tick the doctor is fixed and the clock is
fixed, so both $\mathbb{E}[S_{\text{tier}}]$ and the $\gamma$ time-factor are **identical
positive scalars across the entire candidate array**. Multiplying every $C_i$ by the same
constant cannot move the argmax:

$$\arg\max_i \big(C_i \cdot \text{scale}\big) = \arg\max_i C_i, \quad \text{scale} > 0 \text{ constant per tick}.$$

So we dropped them from the hot loop and rank on the dynamic marginal cost $C_i$ alone —
leaner, faster, and provably decision-equivalent. (`gamma` is retained only for
tuner-output compatibility; it is inert, and doubly so because the 91-day shift means
$T_{\text{rem}} < 120$ almost never occurs.) We still maintain the per-tier service-time
EMA online (initialized to Fast 15 / Medium 20 / Slow 30, updated as
$0.8\cdot\text{old} + 0.2\cdot\text{actual}$), because it is the natural hook for the *one*
way these variables **could** legitimately re-enter the decision — see below.

**Is the remark only about tuning? No — it is structural.** No value of $\gamma$ can
rescue a uniform per-tick scalar; the invariance is a property of the decision geometry,
not of the parameter search. Doctor speed and time-of-day only influence the decision when
they enter the score **non-uniformly across candidates**:

- **Time-of-day** *already* does so — via $\tau_{\text{dynamic}}$, which re-ranks PAC 3
  against PAC 1/2 as a surge approaches (§3). What is inert is only the *uniform* clock
  multiplier $\gamma$, not our use of the clock.
- **Doctor speed tier** *could* be made decision-relevant two ways we consciously
  declined. (i) A **true $c\mu$ divisor** uses the expected service time of *that job*,
  $\mathbb{E}[S \mid \text{pac}, \text{tier}]$; since PAC-3 consults run far longer than
  PAC-1 (≈58 vs ≈34 min), this varies per candidate and *would* change the ranking —
  slightly deprioritizing expensive PAC-3 in favor of throughput. (ii) An
  **interaction / matching** term (e.g., steer Fast doctors toward PAC-3 to clear
  escalation risk quickest) makes the score depend on tier *differently per patient*.

We rejected both because: (a) the fixed pull order and the inability to *skip* a doctor
cap the gain from matching; (b) a throughput-first divisor works against the very acuity
protection the penalty structure rewards; and (c) estimating
$\mathbb{E}[S\mid\text{pac},\text{tier}]$ online adds variance for a benefit the tuner
could not distinguish from noise. The marginal-cost index already encodes the economics
that matter, so we let $C_i$ stand alone.