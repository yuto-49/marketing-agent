# MiroFish Closed-Loop Activation — Architecture

Sim → execute on LINE → observe real outcome → validate prediction → recalibrate.
MCP is the integration + action layer. The simulation and the live channel form one
feedback loop that updates the published accuracy metric.

This doc answers four design problems (MCP surface, the self-test loop, HITL/safety,
failure/rollback) and ships the four deliverables (component diagram + tool schema,
agent-loop pseudocode, tech stack, pilot MVP). It ends with three alternative
architectures, a recommendation, and the things in the premise that are wrong or
premature.

---

## 0. TL;DR / decisions

- **Don't make the agent hold the send button.** A deterministic orchestrator owns the
  loop and durable state; MCP servers are the trust boundary; the LLM agent is invoked
  for *judgment* (variant pick, anomaly read, recalibration sanity-check), not for
  control flow. (Architecture **C**, below.)
- **Three MCP servers split by blast radius**, not one: `activation` (side-effecting,
  gated), `telemetry` (read-only), `eval` (compute). The approval gate, spend caps, and
  consent join are enforced **inside the activation server**, not in the agent prompt.
- **For a validation product, don't run 70/30 toward the predicted winner.** That biases
  you toward confirming yourself and under-powers τ_real. Use a balanced split (or
  Thompson-with-an-exploration-floor) until the accuracy metric has earned trust.
- **The sim is a pre-screener; sell it on ranking, not magnitude.** Its job is to rank a
  candidate space too large/slow/risky to test live, so the scarce real-test budget hits
  only the top-k. The headline metric is **`Hit@k` / `RankCorr`** (is the real winner in
  the sim's shortlist?), *not* calibrated `τ`. Ordinal signal survives far smaller samples
  than magnitude — which is what makes this validatable on mid-market data at all. Demote
  calibration β to an internal gate; keep the published number a slow, shrunk aggregate.
- **Always ship a wildcard arm.** A pre-screener's silent failure is killing the true
  winner before it's tested — you only ever validate survivors. Send one sim-rejected
  candidate to a real arm every experiment; if it keeps winning, the sim is pruning winners.
- **Riskiest assumption (now narrower):** the power problem bites *magnitude* validation,
  not *ranking*. The real residual risk is that the sim's **rank ordering** isn't better
  than a naive baseline ("pick last campaign's winner") — if top-k recall doesn't beat
  that, the pre-screen adds nothing. Validate rank-lift over the naive baseline first. See §9.

---

## 1. Component diagram

```
                          ┌─────────────────────────────────────────────┐
                          │  ORCHESTRATOR  (Temporal durable workflow)   │
                          │  owns control flow, state machine, timers,   │
                          │  human-approval signals, retries, rollback   │
                          └───┬───────────────┬───────────────┬─────────-┘
            invokes for judgment │           │ calls (gated)  │ calls (read)
                          ┌──────▼──────┐     │                │
                          │ AGENT (LLM) │     │                │
                          │ Claude via  │     │                │
                          │ Agent SDK   │     │                │
                          │ advisory:   │     │                │
                          │ pick variant│     │                │
                          │ read anomaly│     │                │
                          └─────────────┘     │                │
                                              │                │
        ┌─────────────────────────────────────▼──┐   ┌─────────▼───────────────┐   ┌──────────────────┐
        │  activation-mcp   (WRITE, GATED)        │   │  telemetry-mcp (READ)   │   │  eval-mcp (COMPUTE)│
        │  create_audience / push_segment_message │   │  read_delivery_stats    │   │  decision_metrics  │
        │  schedule_campaign / set_tag            │   │  read_conversions       │   │  ppi_lift          │
        │  pause_campaign / rollback_campaign      │   │  read_audience / status │   │  recalibrate_params│
        │  ── enforces: approval token, spend cap, │   │  (no side effects)      │   │  refresh_accuracy  │
        │     freq cap, consent join, kill-switch  │   │                         │   │                    │
        └───────────────┬─────────────────────────┘   └──────────┬──────────────┘   └────────┬──────────┘
                        │  ChannelConnector (one interface, capability-negotiated)             │
        ┌───────────────▼──────────────────────────────────────────────────┐                 │
        │  Adapter: LINE-direct │ Adapter: Lステップ │ Adapter: Salesforce MC │                 │
        │  (Messaging+Insight)  │ (tags/scenarios)  │ (Journey/DataExt)      │                 │
        └───────────┬───────────┴────────┬──────────┴───────────┬───────────┘                 │
                    │                    │                      │                              │
              LINE Messaging       Lステップ API          Salesforce MC API                    │
              + Insight API        (opened 2026-04)                                            │
                                                                                              │
   ════════════════════════ DATA PLANE SEPARATION (no FKs across the line) ═══════════════════│═══════
                                                                                              │
   ┌── REAL plane (APPI-controlled, hashed IDs) ──┐      ┌── SYNTHETIC plane (LLM-free-roam) ─▼────┐
   │ consent_ledger  send_ledger  assignment      │      │ sampled_agents.parquet  agent_config[]  │
   │ outcome_events (delivery/click/conversion)   │      │ sim_runs  segment_params.json (versioned)│
   │ id_vault (hash→LINE userId, KMS, never to LLM)│      │ τ_sim per variant/segment               │
   └──────────────────────────────────────────────┘      └─────────────────────────────────────────┘
         only AGGREGATES cross: real → eval (segment-level τ_real, counts). Never individuals.
```

State homes (single source of truth each):
- **Temporal**: workflow/loop state, where in the lifecycle each experiment is.
- **Postgres**: `experiments`, `metrics`, `segment_params` (versioned), `consent_ledger`,
  `send_ledger`, `outcome_events`, `accuracy_metric` (rolling, per client/segment).
- **Object store (S3/GCS)**: parquet synthetic population + sim outputs.
- **KMS vault**: LINE channel tokens + hash→userId map. The agent never sees raw IDs.

---

## 2. MCP tool schema

Three servers. Trust boundary = process boundary. The agent can call `telemetry` and
`eval` freely; every `activation` call is gated server-side.

### activation-mcp (write, side-effecting, GATED)

```jsonc
// create_audience — build a targeting arm. Does the consent + frequency join here.
{
  "name": "create_audience",
  "input": {
    "experiment_id": "string",          // must be in 'approved' state, else REJECT
    "arm": "champion|challenger|control",
    "segment_id": "string",
    "assignment": { "method": "hash_bucket", "salt": "string", "range": [0, 70] },
    "max_size": "integer"               // enforced against per-experiment cap
  },
  "output": {
    "audience_ref": "string",           // provider audienceGroupId (opaque)
    "size_after_consent": "integer",
    "excluded": { "no_consent": 0, "freq_capped": 0, "no_id_match": 0 },
    "capability": { "per_user_conversion": "native|liff_fallback|none" }
  }
}

// push_segment_message — the actual send. Channel-agnostic; connector resolves provider.
{
  "name": "push_segment_message",
  "input": {
    "experiment_id": "string",
    "audience_ref": "string",
    "variant": { "creative_id": "string", "channel": "LINE", "offer": "string" },
    "idempotency_key": "string",        // == retryKey at LINE boundary; blocks double-send
    "send_window": { "not_before": "iso8601", "quiet_hours": "22:00-08:00 JST" }
  },
  "output": { "send_ref": "string", "status": "accepted|rejected", "reason": "string",
              "projected_cost_jpy": "integer", "projected_recipients": "integer" }
}

// schedule_campaign — same as push but deferred / recurring.
// set_tag — per-friend tag (Lステップ/SFMC only; LINE-direct returns capability_unsupported).
// pause_campaign — halt remaining async sends for an arm. Idempotent.
// rollback_campaign — pause challengers + route window to champion/BAU. Pre-authorized at approval.
```

Server-side preconditions every `push_*`/`schedule_*` checks (the agent **cannot**
bypass these — they live in the server, not the prompt):
1. `experiment.status == approved` and a valid, unexpired human approver token.
2. `projected_cost_jpy + spent_30d <= client.budget_cap`.
3. every recipient passes `consent_ledger` (marketing + channel opt-in) and `send_ledger`
   frequency cap.
4. global `freeze_all_sends` kill-switch is off.
Fail any → `rejected` with reason, no side effect, incident logged.

### telemetry-mcp (read-only, ungated)

```jsonc
{ "name": "read_delivery_stats",  // → LINE Insight / narrowcast progress / Lステップ
  "input": { "experiment_id": "string", "send_ref": "string" },
  "output": { "requested": 0, "delivered": 0, "failed": 0, "blocked_unsub": 0,
              "opens": 0, "clicks": 0, "status": "running|done|failed" } }

{ "name": "read_conversions",
  "input": { "experiment_id": "string", "window": { "from": "iso8601", "to": "iso8601" } },
  "output": { "by_arm": [ { "arm": "champion", "n": 0, "converted": 0,
                            "attribution": "native|liff|proxy_click" } ] } }

{ "name": "read_audience",  "input": {...}, "output": { "size": 0, "overlap_pct": 0.0 } }
{ "name": "get_campaign_status", "input": {...}, "output": { "phase": "...", "health": "..." } }
```

### eval-mcp (compute, read-only on internal data)

```jsonc
{ "name": "compute_decision_metrics",   // §14 decision-level — RANKING is the headline
  "input": { "experiment_id": "string", "k": 3 },
  "output": { "hit_at_k": 0, "recall_at_k": 0.0, "rank_corr": 0.0, "sign_acc": 0.0,
              "rank_lift_vs_baseline": 0.0,        // top-k recall minus naive baseline (the real go/no-go)
              "wildcard_beat_rate": 0.0,           // how often the sim-rejected wildcard won (anti-pruning)
              "calibration_beta": 0.0, "r2": 0.0, "mae": 0.0,   // magnitude = INTERNAL gate only
              "tau_real": [ { "variant": "v", "tau": 0.0, "ci95": [0,0] } ] } }

{ "name": "compute_ppi_lift",           // §14 PPI: sim over all + bias-correct on real subset
  "input": { "experiment_id": "string" },
  "output": { "theta_ppi": 0.0, "ci95": [0,0], "efficiency_gain": 0.0,
              "norm_acc": 0.0 } }     // Corr(sim,real)/Corr(real,real_retest)

{ "name": "recalibrate_params",         // Bayesian shrink old param → observed; PROPOSES a new version
  "input": { "experiment_id": "string", "segment_id": "string" },
  "output": { "param_version_proposed": "string", "delta": {...},
              "auto_apply_eligible": false } }   // false unless calibration gate + HITL pass

{ "name": "refresh_accuracy_metric",    // rolling, shrunk aggregate across experiments
  "input": { "client_id": "string", "segment_id": "string" },
  "output": { "metric": { "hit_at_k_ewma": 0.0, "rank_corr_ewma": 0.0,
                          "rank_lift_vs_baseline_ewma": 0.0,   // the published, defensible number
                          "efficiency_gain": 0.0, "ci95": [0,0], "n_experiments": 0 } } }
```

---

## 3. The connector abstraction (don't hard-wire a client's stack)

One `ChannelConnector` interface; per-client adapter chosen by config; **capability
negotiation** so the orchestrator degrades gracefully instead of failing.

```python
class ChannelConnector(Protocol):
    def capabilities(self) -> Caps           # {audiences, per_user_tags, native_conversion, narrowcast_ab}
    def create_audience(self, spec) -> Ref
    def send(self, audience_ref, variant, idem) -> SendRef
    def stats(self, send_ref) -> Stats
    def conversions(self, window) -> Conv
    def pause(self, send_ref) -> None
```

| Client tier | Path | Why | Conversion attribution |
|---|---|---|---|
| A — SMB, LINE OA only | **LINE-direct** (Messaging + Insight + Audience APIs) | cheapest, no extra vendor; narrowcast does the A/B split | weak → **LIFF redirect + webhook** fallback |
| B — mid-market (sweet spot) | **Lステップ** (API opened 2026-04) | per-friend tags, scenario flows, built-in conversion | native |
| C — enterprise | **Salesforce MC** (or Treasure Data CDP → LINE) | Journey Builder, identity resolution, Data Extensions | native + identity graph |

`capabilities()` returns flags; e.g. LINE-direct reports `per_user_tags: false,
native_conversion: false`. The orchestrator then auto-inserts the LIFF-redirect tracking
step and skips `set_tag`. The agent and the rest of the loop are written **once** against
the interface; the client's stack is config, not code.

> **Schedule risk to flag:** Lステップ's API is ~2 months old at pilot time. Keep
> LINE-direct as the fallback on the pilot critical path; don't bet the first deal on a
> brand-new API.

---

## 4. The self-test closed loop — data flow

```
(1) SIM (synthetic plane)           RANKED shortlist (top-k) per segment — pre-screen of a wide candidate space
        │ write proposal                experiments row: status=proposed
        ▼
(2) ALLOCATION PLAN                  champion=top-1 / challenger=top-2 / control=BAU / WILDCARD=sim-rejected
        │                               control = real holdout (τ_real); wildcard = catch silent pruning. BALANCED.
        ▼
(3) HUMAN APPROVAL GATE  ◄── Temporal signal ── dashboard. status=approved + budget cap + auto-rollback consent
        │
        ▼
(4) ACTIVATION (real plane, gated)   create_audience ×arms (hash-bucket, stable) → push_segment_message ×arms
        │                               consent+freq join happens here; raw IDs never leave the vault
        ▼
(5) OBSERVE                          telemetry-mcp polls delivery/clicks/conversions on Temporal timer
        │                               until window closes AND min-power reached (sequential, always-valid CI)
        ▼
(6) EVAL                             τ_real per arm → HEADLINE: Hit@k, RankCorr, rank-lift-vs-baseline, wildcard-beat-rate
        │                               INTERNAL gate: β, MAE (magnitude). PPI θ_PPI for the pricing depth story only.
        ▼
(7) RECALIBRATE (proposed)           Bayesian shrink segment_params toward observed → new version (provenance link)
        │                               auto-apply ONLY if calibration gate passes + HITL ok (early pilots: never)
        ▼
(8) REFRESH METRIC                   rolling shrunk aggregate per client/segment, with CI. NOT moved much by one run.
        │
        └──────────► feeds next SIM (closed)
```

**A constraint people miss:** PPI needs `f(X)` — the simulator's prediction — on the
*same covariates X as the real tested units*. So step (6) must run the simulator on the
actual targeted users' attribute vectors (or their nearest synthetic twins), not just on
the generic synthetic population. Without aligned `f` and `Y`, θ_PPI is not valid. Build
the "score-real-cohort-through-sim" path explicitly; it's easy to forget and it's the
hinge of the honest accuracy claim.

---

## 5. Agent loop — pseudocode

The **orchestrator** owns this (durable). The **agent** is called only at the `agent(...)`
points. MCP calls marked `[gated]` are refused server-side unless preconditions hold.

```python
@workflow
def experiment_loop(client_id, segment_id, sim_run):
    # 1. propose
    sim = load_sim(sim_run)                       # τ_sim(v), predicted winner — synthetic plane
    pick = agent.choose_variant(sim)              # LLM advisory: winner + rationale + confidence
    exp  = experiments.create(client_id, segment_id, sim, pick, status="proposed")

    # 2. allocate — BALANCED for validation, not 70/30 (see §8)
    plan = allocate(exp, scheme="balanced_with_control",
                    arms={"champion": sim.shortlist[0],      # sim's top-k drives the arms
                          "challenger": sim.shortlist[1],
                          "control": "BAU",
                          "wildcard": pick_wildcard(sim)})   # a sim-REJECTED candidate, to catch pruning
    if lift_ci_includes_zero(sim, pick.winner):   # don't message if you can't predict a positive effect
        return abort(exp, "predicted lift CI includes 0 — not worth a real send")

    # 3. human gate (Temporal blocks here for a signal)
    approval = await human_approval(exp, plan, budget_cap, allow_auto_rollback=True)
    if not approval.granted: return abort(exp, "not approved")
    exp.approve(approval.token)

    # 4. activate — every call gated server-side
    refs = {}
    for arm in plan.arms:
        aud = activation.create_audience(exp.id, arm, segment_id, plan.assignment[arm])   # [gated]
        if arm != "control":
            refs[arm] = activation.push_segment_message(exp.id, aud.ref, plan.variant[arm],
                                                        idempotency_key=key(exp,arm))      # [gated]

    # 5. observe with guardrails (sequential monitoring — auto-rollback fires here)
    while not window_closed(exp) or not min_power(exp):
        await timer(hours=6)
        stats = {a: telemetry.read_delivery_stats(exp.id, r) for a,r in refs.items()}
        if send_health_bad(stats):        rollback(exp, "send failure/rate-limit");  raise Halt
        if harm_threshold_crossed(stats): rollback(exp, "challenger harm (block/unsub)"); raise Halt
        live = telemetry.read_conversions(exp.id, window_so_far(exp))
        if divergence_gate(sim, live):    # |τ_sim−τ_real| or sign flip, significant
            note = agent.interpret_divergence(sim, live)     # LLM advisory: noise vs model-wrong
            if note.model_wrong: rollback(exp, "sim/real divergence"); freeze_recalibration(segment_id); raise Halt

    # 6. eval
    m   = eval.compute_decision_metrics(exp.id, k=3)         # headline: Hit@k, rankcorr, rank-lift, wildcard-beat; β/MAE internal
    ppi = eval.compute_ppi_lift(exp.id)                      # θ_PPI, EfficiencyGain, NormAcc

    # 7. recalibrate — PROPOSED, not auto-applied in early pilots
    prop = eval.recalibrate_params(exp.id, segment_id)
    if prop.auto_apply_eligible and calibration_gate(m):     # β∈[0.8,1.2] & history strong
        params.apply(prop.param_version_proposed)
    else:
        await human_review(prop)                             # human decides whether to bless the new params

    # 8. refresh the published metric — slow, shrunk, CI-backed
    eval.refresh_accuracy_metric(client_id, segment_id)
    return finalize(exp, m, ppi)
```

---

## 6. Human-in-the-loop & safety gates

- **Approval gate sits at the MCP server boundary, not in the prompt.** `activation-mcp`
  refuses any `push_*`/`schedule_*` whose `experiment_id` isn't `approved` with a valid
  human token. You cannot trust the LLM's restraint to not spend money — the server is
  the enforcer. Approval is a signed record: who, when, budget cap, max audience, and
  pre-consent to the auto-rollback triggers (so rollback can fire at 2am without a human).
- **Spend / over-messaging caps** (LINE bills per message; ~70% of users have blocked an
  OA for irrelevance — over-sending is an existential brand risk, not a cost line):
  - hard per-experiment **and** per-client rolling-30d budget cap, checked server-side
    against `projected_cost_jpy` *before* send.
  - **frequency cap** per friend per week via `send_ledger`; capped users are excluded
    from the audience at build time.
  - **relevance gate**: refuse to send to a segment whose predicted lift CI includes
    zero (above) — don't pay to message where you can't even predict benefit.
  - quiet hours / send windows.
- **APPI consent**: `consent_ledger` keyed by hashed ID, with marketing + per-channel
  opt-in. `create_audience` inner-joins against it; no consent → excluded. IDs are hashed
  at ingest; the hash→LINE-userId map lives in a KMS vault that only the connector
  resolves at the API boundary. **The agent never sees a raw user ID.**
- **Synthetic vs real data-plane separation**: two physically separate stores, no foreign
  keys across them. The synthetic plane (agents, params, sim) is where the LLM roams
  freely. The real plane (consent, targeting lists, outcomes, IDs) is access-controlled;
  the agent touches it only through gated MCP tools and never reads raw rows. The **only**
  bridge is aggregate statistics flowing real→eval (segment-level τ_real, counts).
  Individual real records never enter the synthetic plane — this is both the APPI
  guarantee and what stops the LLM from ever conditioning on a real person.

---

## 7. Failure & rollback

| Failure | Detection | Response |
|---|---|---|
| Send fails / partial | poll narrowcast progress / Insight; `failed/requested` > θ | halt remaining sends for arm, mark degraded, incident; idempotency/`retryKey` prevents double-send on retry |
| Rate limit (429) | connector sees 429 | token-bucket backoff in connector (below MCP); if sustained, **pause** experiment (don't drop messages), alert |
| Challenger harms users | sequential test on block/unsub/conversion crosses harm threshold w/ significance | **auto-pause challenger**, reallocate to champion/BAU, incident. Always-valid CI to avoid peeking bias |
| Sim/real diverge past gate | `divergence_gate` during observe | LLM reads *noise vs model-wrong*; if model-wrong → rollback to BAU **and freeze auto-recalibration for that segment** (don't let one bad run poison params), require human review |
| Catastrophe | anything | global `freeze_all_sends` kill-switch checked on every activation call |

- **Auto-rollback** = pre-authorized at approval time. `rollback_campaign` pauses
  challengers and routes the rest of the window to champion/BAU. It's a gated, logged MCP
  action that fires without a human because the human pre-consented to its triggers.
- **Alerting**: incidents → ops Slack/LINE with severity; recalibration freezes and
  rollbacks are P1; budget/cap rejections are P2; degraded-capability fallbacks are P3.

---

## 8. Three alternative architectures + recommendation

**A — Fully-agentic, MCP-driven execution.** The LLM agent holds control flow and calls
write tools to send, monitor, recalibrate autonomously.
*Pro:* max automation, lowest latency-to-act, "magical" demo.
*Con:* large blast radius on a money-spending, brand-risky action; LLM nondeterminism on
sends; hard to audit/replay; regulatory exposure. **Wrong for a pilot — unforced error.**

**B — Thin "MCP reads, human sends."** Agent simulates, drafts the experiment + audience,
computes metrics; a human executes the actual send in the LINE/Lステップ console. MCP only
reads stats back; recalibration still automated.
*Pro:* safest, fastest to ship, builds client trust, least engineering.
*Con:* doesn't actually *close* the loop on execution (the headline) — but keeps ~90% of
the value (the validation/recalibration loop).

**C — Scheduled/durable orchestrator, agent-assisted.** A workflow engine (Temporal) owns
the loop, state, timers, retries, and the human-approval signal. MCP tools are called by
workflow steps. The agent is invoked only for judgment (variant pick, anomaly read,
recalibration sanity-check) — fenced out of control flow. Sends still pass the human gate.
*Pro:* durable, replayable, idempotent, auditable; nondeterminism boxed into advisory
roles; production-grade.
*Con:* less "magical"; more upfront engineering than B.

**Recommendation: C as the backbone, B's posture for the first pilot, evolve toward
autonomy.** Run the durable orchestrator from day one (it's the thing you can't bolt on
later), but keep a mandatory human gate on every send for the first N experiments — i.e.
behave like B inside C's skeleton. Relax the gate later to "auto-send within pre-approved
caps for segments where calibration β∈[0.8,1.2] and Hit@1 history is strong." This gives
you the closed loop without betting the client relationship on an LLM autonomously
spending money on day one. Not A (blast radius). Not pure B (it never closes the loop —
which is the whole pitch).

---

## 9. What in the premise is wrong or premature

> **Reframe that resolves half of this section: the sim is a pre-screener, judged on
> ranking.** The original premise reads as "predict the magnitude of lift, then validate
> the magnitude." If instead the sim's job is to *rank* a large candidate space so the
> live test only confirms the top-k, the bar drops from calibrated `τ` to "real winner in
> the top-k" — an ordinal claim that survives the small samples mid-market gives you.
> Magnitude becomes an internal gate (does pricing get to claim depth?), not the headline.
> This directly weakens the power objection below (it bit magnitude, not ranking) and the
> published-metric danger (you publish `Hit@k`/`RankCorr`, not a swinging `τ`). The points
> that remain — allocation, recalibration, the holdout, the wildcard — still stand.

1. **"Continuously updates the published accuracy metric" off live sends — statistically
   dangerous.** Mid-market experiments are low-powered (tens of thousands of recipients,
   single-digit % conversion, few variants). A *published* number that moves on every
   run will swing and chase noise. Make it a **slow, shrunk aggregate** (EWMA / hierarchical
   pool) updated on a cadence with proper CIs; never let one experiment move it much.
2. **"Most traffic to the predicted winner (70/30)" undermines validation.** For a product
   sold on its accuracy metric, a winner-heavy split biases toward self-confirmation and
   under-powers τ_real. Use **balanced arms** (or Thompson sampling with an
   exploration floor). There's a real earn-vs-validate tension — resolve it toward
   *validate* while the metric is young, *earn* once it's trusted.
3. **Auto-recalibrating params from live outcomes with no human early on → feedback
   instability.** Sim drives sends, sends drive params, params drive sim. Worse: your own
   sends change behavior (fatigue), so τ_real is contaminated by the act of testing. Gate
   recalibration behind significance + HITL until trust exists; freeze it on a diverging run.
4. **The real holdout control is non-negotiable — don't let the client talk you out of
   it.** It costs sends you could've monetized and is politically awkward, but without it
   there is no τ_real and the entire loop is theater. Make it small but real.
5. **Lステップ API is ~2 months old at pilot.** Don't put a brand-new API on the critical
   path; ship the pilot on LINE-direct (or whichever stack the client already runs) and
   treat Lステップ as an adapter you harden after the first deal.

**Riskiest assumption (restated for the pre-screener framing):** *not* that the sim
predicts magnitude accurately — that bar is dropped. The risk that remains is that the
sim's **rank ordering doesn't beat a naive baseline** ("always discount", "pick last
campaign's winner"). If the sim's top-k recall is no better than that baseline, the
pre-screen adds nothing and the whole loop is ceremony around a coin flip. This is more
testable than the old magnitude claim: ranking holds up at small samples, so you can settle
it on a few held-out campaigns. *Mitigations:* validate **rank-lift over the naive
baseline** first (the real go/no-go); use the **wildcard arm** to catch the sim silently
pruning winners; keep PPI/hierarchical pooling for the magnitude story you tell *internally*
about pricing, not the headline. If even the ranking fails to beat the baseline, you don't
have a product — and you'll have found out cheaply, on backtest, before any client promise.

---

## 10. Tech stack

- **MCP framework:** Python official `mcp` SDK (FastMCP). Python because the eval/ML stack
  (CausalML, EconML, statsmodels, scikit-uplift, PPI) is Python — keep eval-mcp in-process
  with the models. Three servers deployed as separate services by trust boundary.
- **Orchestrator / agent runtime:** **Temporal** for durable workflows (retries, timers
  for the observe window, human-task signal for the approval gate, replayable audit log).
  The **agent runs server-side** (Claude via the Agent SDK), invoked by workflow steps —
  it does **not** live in the user's Claude Code session and does **not** hold control flow.
- **Persistence:** Postgres (experiments, metrics, versioned `segment_params`,
  `consent_ledger`, `send_ledger`, `outcome_events`, rolling `accuracy_metric`); object
  store (S3/GCS) for parquet synthetic population + sim outputs; KMS-backed vault for LINE
  tokens + hash→userId map. Outcome events as an append-only table (graduate to Kafka only
  if volume demands).
- **Observability:** OpenTelemetry traces across MCP calls; metrics dashboard; incident
  alerting to Slack/LINE. Every gated rejection and rollback is a first-class event.

---

## 11. Minimum viable loop for one pilot client

Architecture B executed once, instrumented to grow into C.

- **One client, one segment, one campaign decision, two variants** + a small **real
  holdout**. Use **LINE-direct** (avoid the new-API dependency) unless the client already
  lives in Lステップ.
- **Loop:** sim → human reviews & approves in a minimal dashboard → orchestrator sends two
  narrowcast audiences + holds out the control → telemetry polls Insight API for 7 days →
  eval computes Hit@1 / sign-agreement / τ_real with CI + **one** PPI estimate → writes a
  validation report. Param recalibration is **proposed to a human, not auto-applied.** The
  "published metric" for the pilot is this single experiment's result with an honest CI,
  framed as *the first calibration point*, not a hero number.
- **Explicitly NOT in the MVP:** auto-recalibration, bandit allocation, multi-segment,
  the Salesforce path, autonomous sends, auto-rollback (a manual pause button is enough
  for one supervised experiment). Human gate on **every** send.
- **Why this is the right MVP:** it proves the one thing that's actually hard and actually
  sells — *can the sim's prediction survive contact with a real LINE audience, measured
  honestly?* — while spending the least money and taking the least brand risk. Everything
  else (autonomy, bandits, recalibration) is an optimization you earn the right to add
  once the accuracy metric has a track record.
```
