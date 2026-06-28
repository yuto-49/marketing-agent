# MiroFish Simulation Structure — Build Spec

The data/ML layer that produces `τ_sim` (per-segment counterfactual lift) for the
closed-loop activation layer to validate. This spec pins down the I/O contracts, the
decision-model math, the LLM-modulation mechanics, the world builder, the runtime
interface, and the build order.

Core principle, made mechanical: **the data fixes the skeleton, the LLM adds the muscle —
and the muscle cancels out of the lift.** See §4: because both arms run on the same agent
with the same seed *and the same LLM-modulation draw*, the LLM's contribution largely
cancels in the paired difference, so it cannot manufacture `τ`. That's the rigorous
version of "never let the LLM invent the math."

---

## 0. What the simulation emits (the output contract)

Everything downstream consumes exactly this. Build to it first, fill it in second.

```jsonc
// sim_run output — one row per (variant, segment)
{
  "sim_run_id": "string",
  "client_id": "string",
  "param_version": "string",          // which segment_params version produced this
  "results": [
    {
      "segment_id": "S3_price_sensitive_suburban",
      "variant": "value_framing_LINE_-10pct",
      "tau_sim": 0.018,               // mean paired lift in p_buy vs control, this segment
      "tau_sim_ci95": [0.011, 0.026], // Monte Carlo CI over agents × seeds
      "n_agents": 4000,
      "p_buy_control": 0.041,
      "p_buy_treatment": 0.059,
      "band_share": 0.22              // fraction of |tau| attributable to LLM modulation (band sensitivity)
    }
  ],
  "predicted_winner": { "by_segment": { "S3": "value_framing_LINE_-10pct" } },
  "seed": 1234,
  "f_on_real_cohort": "ref://..."     // simulator scored on real targeted users' X (for PPI alignment)
}
```

Two fields people forget and you must not: **`band_share`** (how much of the predicted
lift came from the LLM vs the data — your honesty dial) and **`f_on_real_cohort`** (the
simulator scored on the *actual* targeted users' covariates, required for valid PPI in the
closed loop — `f` and `Y` must be on the same units).

---

## 1. Pipeline stages — I/O contracts

Each stage is a pure function `inputs -> artifact`, versioned, re-runnable, seeded. No
stage reads raw client formats — that's the ingest adapter's job (canonical schema first).

```
00_feature_validation   in: canonical transactions/campaigns/products
                        out: feature_signal.json  {feature: {carries_elasticity, carries_uplift, coverage}}
                        gate: a feature may set a parameter ONLY if it passes here (Layer-0 gate)

01_segment              in: canonical transactions (+CRM), feature_signal
                        out: segments.parquet  {customer_hash, segment_id}, segment_profiles.json {size, attr_dist}
                        method: map-to-CRM | RFM+kmeans | SAINT-embeddings+kmeans (tier-dependent)

02_fit_params           in: transactions, campaigns, products, segments
                        out: segment_params.json  (the SKELETON — see §3 for exact fields & units)
                        method: binary-GLM price coef + uplift/CATE meta-learner; hierarchical shrink for sparse segments

03_synthesize_pop       in: segment_profiles, segments
                        out: sampled_agents.parquet  {agent_id, segment_id, attr_vector}
                        method: CTGAN/VAE per-segment; preserves joint attr distribution; privacy buffer

04_persona_gen          in: sampled_agents
                        out: personas.parquet  {agent_id, persona_text, seed_memory}
                        method: LLM, attr_vector -> backstory (MUSCLE only; sets no numbers)

05_agent_config         in: sampled_agents, personas, segment_params
                        out: agent_config[]  (§5 schema — merge skeleton + muscle)

06_knowledge            in: products, reviews, campaigns, competitors
                        out: graphrag index, retrieval_namespace per segment
                        method: GraphRAG build; per-agent retrieval at inference
```

**Versioning contract:** every artifact carries `{param_version | pop_version | run_seed}`
and a provenance link to its inputs. The closed loop's recalibration writes a *new*
`segment_params.json` version; sim runs pin the version they used. Reproducibility is not
optional — PPI and band-sensitivity claims require it.

---

## 2. segment_params.json — the skeleton (exact fields & units)

This is the only place numbers live. Units matter; get them coherent (see §3).

```jsonc
{
  "param_version": "2026-06-28.1",
  "segments": {
    "S3_price_sensitive_suburban": {
      "n_customers": 5120,                 // real support behind these fits — drives shrinkage weight
      "baseline_logit": -3.15,             // = logit(baseline_conversion); NOT a probability
      "price_coef_logit": -1.9,            // LOGIT-scale price sensitivity (see §3 unit fix), per unit Δlog_price
      "channel_offset_logit": { "LINE": 0.0, "Instagram": -0.8, "flyer": -0.35 },  // additive log-odds vs reference
      "creative_offset_logit": { "value_framing": 0.0, "aspirational": -0.6 },
      "uplift_prior": { "mean": 0.02, "var": 0.0009 },  // CATE prior for this segment, for shrinkage
      "shrinkage_weight": 0.7,             // 1 = fully client data, 0 = fully category/panel prior
      "fit_quality": { "price_r2": 0.31, "qini": 0.14, "n_campaigns": 22 }  // gates trust per-segment
    }
  }
}
```

---

## 3. The decision model — make the math coherent

The handoff's `p_buy = sigmoid(baseline + elasticity*Δlog_price + channel*creative*context)`
has a **unit bug**: a log-log *demand* elasticity (quantity vs price) is not a logit
coefficient on a *binary* purchase probability. Mixing them is incoherent. Fix:

> **Model the purchase decision as a single logit, and fit every coefficient on the logit
> scale from the binary outcome.** The "price elasticity" injected into the agent is a
> logit-scale price coefficient `price_coef_logit` fit from a binary GLM
> (`purchased ~ Δlog_price + controls`), **not** the log-log demand elasticity. Keep the
> log-log elasticity if you want it — but for *category-level volume* reporting, not as the
> per-agent sigmoid coefficient.

The decision rule for agent `i` in world `w` (a set of levers):

```
logit_anchor(i, w) =  baseline_logit[seg(i)]
                    +  price_coef_logit[seg(i)] * Δlog_price(w)
                    +  channel_offset_logit[seg(i)][channel(w)]
                    +  creative_offset_logit[seg(i)][creative(w)]
                    +  context_offset(i, w)          // from GraphRAG retrieval + event/signage injection

logit_final(i, w)  =  logit_anchor(i, w) + δ_llm(i, w)      // δ clipped to the band, §4
p_buy(i, w)        =  sigmoid( logit_final(i, w) )
```

Everything additive on the logit scale → composition is linear and the contribution of
each lever is auditable. `channel × creative` interactions, if the uplift model finds
them, enter as their own fitted `interaction_offset_logit` term — don't multiply
marginals.

Per-agent paired treatment effect:

```
τ_i(v) = p_buy(i, treatment=v) − p_buy(i, control)        // same i, same seed, same δ_llm draw
τ_sim(v | segment) = mean_i∈segment τ_i(v)
```

---

## 4. LLM modulation — the band, and why the muscle cancels

`δ_llm(i, w)` is the LLM's only numeric influence. It is **bounded in logit units** and
**drawn once per agent and reused across both arms**:

```
δ_raw(i)        = LLM_modulate(persona_i, retrieved_evidence, world_context)   // a scalar
δ_llm(i, w)     = clip(δ_raw(i), −band_logit[seg(i)], +band_logit[seg(i)])
```

Two load-bearing properties:

1. **Bounded.** `band_logit` is a logged, tunable hyperparameter per segment. Too wide →
   agents "buy anything exciting" (the failure the whole premise exists to avoid); too
   narrow → the LLM adds nothing. Report **band sensitivity** (`band_share` in §0): rerun
   with `band=0` and with the configured band; the difference in `τ` is the share of
   predicted lift the model — not the client's data — is responsible for. Clients see
   exactly how much of the number is theirs.

2. **Paired cancellation (the key design property, not in the handoff).** Because the
   *same* `δ_llm(i)` enters both `treatment` and `control`, it largely cancels in
   `τ_i = p_buy(treat) − p_buy(control)`. The residual is only the curvature of the
   sigmoid (the same logit shift maps to slightly different probability deltas at
   different baselines). So the LLM moves each agent's *absolute* realism freely but can
   only second-order-influence the *lift*. **The data-fixed intervention terms drive `τ`;
   the LLM cannot manufacture it.** This is the mechanism that makes "skeleton vs muscle"
   true rather than aspirational.

> If you instead let the LLM draw `δ` independently per arm, you reintroduce exactly the
> "LLM invents the lift" failure. Same-draw-across-arms is mandatory, not an optimization.

---

## 5. agent_config[] schema (refined from the handoff)

```jsonc
{
  "agent_id": "a_00417",
  "segment_id": "S3_price_sensitive_suburban",
  "persona": "<LLM backstory + seed memory>",     // muscle; sets no numbers
  "attr_vector": { "age": 38, "geo": "suburban", "freq_per_month": 2, "pref_channel": "LINE" },
  "reaction_params": {                             // skeleton; copied from segment_params (+ any agent-level draw)
    "baseline_logit": -3.15,
    "price_coef_logit": -1.9,
    "channel_offset_logit": { "LINE": 0.0, "Instagram": -0.8, "flyer": -0.35 },
    "creative_offset_logit": { "value_framing": 0.0, "aspirational": -0.6 }
  },
  "retrieval_namespace": "S3",
  "llm_modulation_band_logit": 0.4,                // IN LOGIT UNITS, per agent/segment
  "decision_rule_id": "logit_v1",                  // pin the rule version; math is code, not free text
  "seed": 1234                                     // per-agent seed for reproducible paired draws
}
```

Change from the handoff: `decision_rule` is an **id pinning a versioned code path**, not a
free-text formula (free text invites drift and isn't executable), and the band is in
**logit units**.

---

## 6. World builder — control vs treatment

```jsonc
// world_spec — two worlds, identical except the intervention, same agents, same seed
{
  "experiment_id": "string",
  "agents_ref": "ref://sampled_agents@pop_version",
  "seed": 1234,
  "control":   { "levers": { "price_delta_pct": 0,   "creative": "value_framing", "channel": "LINE" } },
  "treatments": [
    { "variant": "v1", "levers": { "price_delta_pct": -10, "creative": "value_framing", "channel": "LINE" } },
    { "variant": "v2", "levers": { "price_delta_pct": -10, "creative": "aspirational",  "channel": "Instagram" } }
  ],
  "context": { "event": "御中元", "season": "summer", "signage": [...] }  // ambient stimuli for GraphRAG/LLM
}
```

Each lever is an injectable that writes one field of `w`:
- `price.py` → `Δlog_price` (feeds `price_coef_logit`)
- `creative.py` → `creative` key (feeds `creative_offset_logit`)
- `channel.py` → `channel` key (feeds `channel_offset_logit`)
- `event.py` / `signage.py` → `context` (feeds `context_offset` via GraphRAG retrieval)

**Pairing & seeding contract (the part that makes the paired difference legitimate):**
- the same `agents_ref` runs every arm;
- `seed` propagates to every stochastic step — agent sampling order, GraphRAG retrieval
  tie-breaks, and the `δ_raw(i)` draw — so control and treatment differ **only** by the
  lever fields;
- `δ_raw(i)` is computed once per agent and cached, reused across all arms of the same
  `experiment_id`.

---

## 7. Runtime adapter interface (push to MiroFish)

```python
class RuntimeAdapter(Protocol):
    def push_agents(self, agent_config: list[AgentConfig]) -> AgentSetRef
    def push_world(self, world_spec: WorldSpec) -> WorldRef
    def run(self, agent_set: AgentSetRef, world: WorldRef, arm: str, seed: int) -> RunRef
    def collect(self, run: RunRef) -> list[AgentResponse]   # {agent_id, p_buy, logit_breakdown, retrieved_ids, delta_llm}

# AgentResponse MUST expose logit_breakdown (per-term contribution) and delta_llm,
# else band-sensitivity and per-lever attribution are impossible.
```

The adapter is the **only** seam to MiroFish; everything above it is engine-agnostic.
`collect` returning the per-term `logit_breakdown` is non-negotiable — it's what lets you
audit "how much of this lift came from price vs creative vs the LLM."

---

## 8. Aggregation → τ_sim

```
for each variant v, segment s:
    τ_sim(v|s)      = mean_{i in s} [ p_buy(i, v) − p_buy(i, control) ]
    τ_sim_ci95(v|s) = bootstrap over agents × seeds      // Monte Carlo, NOT a t-test on synthetic n
    band_share(v|s) = | τ_sim(v|s) − τ_sim_band0(v|s) | / |τ_sim(v|s)|
predicted_winner(s) = argmax_v τ_sim(v|s)
f_on_real_cohort    = run the same engine on the real targeted users' attr vectors (for PPI)
```

> **CI caveat:** the synthetic `n` is whatever you sampled, so a naive CI shrinks to zero
> by sampling more agents — meaningless. The honest CI comes from (a) bootstrap over the
> *fitted-parameter* uncertainty (propagate `segment_params` posterior) and (b) seed
> variation, **not** from synthetic population size. Report the parameter-uncertainty CI.

---

## 9. Validation & gates the sim must expose

- **Backtest** (`eval/backtest.py`): replay held-out real campaigns through the engine →
  Hit@1, sign-agreement, Spearman rank-corr, Qini. Decision-level, per §14.
- **Calibration** (`eval/`): regress `τ_real = α + β·τ_sim`; report `β, R²`. **Gate the
  pricing module on `β ∈ [0.8, 1.2]`** for that client — below it, the magnitude isn't
  trustworthy and you ship direction-only.
- **PPI** (`eval/ppi.py`): combine `f_on_real_cohort` over all + bias-correct on the small
  real anchor → `θ_PPI`, `EfficiencyGain`. The honest published-metric input.
- **Band sensitivity**: `band_share` per run, surfaced to the client.
- **Tail/non-response checks**: validate hardest on non-response base rates and tail
  segments — synthetic respondents regress to typical answers and understate variance, so
  the central case looks great and the tails fail. Track per-segment, not just pooled.

---

## 10. Build order (the sim's own MVP)

Mirrors handoff §15 but sequenced by *defensibility-first, no-LLM-first*:

1. **02_fit_params** — binary-GLM price coef + uplift/CATE. The defensible core, **no LLM
   yet**. Produces `segment_params.json`.
2. **eval/backtest.py + eval/ppi.py** — so the accuracy metric exists *before* any client
   promise. Validate step 1's params against held-out campaigns with τ only (no personas).
3. **01_segment + 03_synthesize_pop** — real segments + CTGAN population.
4. **world_builder + decision_rule (logit_v1) + runtime_adapter** — run paired worlds with
   `band=0` (skeleton only). At this point you can already produce `τ_sim` and validate it.
5. **04_persona_gen + 05_agent_config + 06_knowledge** — add the LLM muscle and GraphRAG.
   Turn the band up from 0; watch `band_share` and re-backtest. The LLM is the *last*
   thing added, and you can always fall back to `band=0` if it degrades calibration.

The deliberate property: **the simulation is useful and validatable at step 4, before any
LLM is involved.** The LLM is an enhancement layer you switch on only once the skeleton
backtests, and you can quantify exactly what it adds (`band_share`) and turn it off if it
hurts.

---

## 11. Risks specific to the sim structure

- **Unit incoherence** (log-log elasticity into a sigmoid) — fixed by §3; audit any
  parameter that claims to be an "elasticity" for which scale it's on.
- **LLM independent-draw leak** — if `δ_llm` is ever drawn per-arm instead of per-agent,
  the lift becomes LLM-manufactured. Enforce same-draw in the runtime adapter, test for it.
- **Fake precision from synthetic n** — §8 CI caveat; never let CI shrink by sampling more
  agents.
- **No-variation segments** — a never-discounted SKU has no estimable `price_coef_logit`;
  the Layer-0 gate (00) must mark it `unidentified` and the segment falls back to the
  category/panel prior with `shrinkage_weight→0`. Don't fit on noise; say the test isn't
  supported (the tier table is the honest answer).
- **Regression to typical / understated variance** — validate on tails and non-response
  base rates, not the central case; this is where synthetic methods flatter themselves.
- **Param/pop version drift** — every `τ_sim` must pin `param_version` + `pop_version` +
  `seed`, or the closed loop can't attribute a recalibration to the run that caused it.
```
