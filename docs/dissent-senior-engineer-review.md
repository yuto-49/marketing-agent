# Dissenting Design Review — "I would not build it this way"

A red-team of the closed-loop activation + simulation architecture, written in the voice
of a skeptical staff/principal engineer who has shipped (and buried) ML platforms. The
point is not to be contrarian for sport — it's to surface the load-bearing objections the
two prior docs are motivated to ignore. Read this *against* them.

---

## The one-paragraph version

You have designed a platform for a product that has not yet produced a single validated
result. Three MCP servers, Temporal, CTGAN, GraphRAG, PPI, a three-vendor connector
abstraction, versioned parameter posteriors — for zero paying customers and zero proof
the central claim (a sim cheaper than a real test) is even true. The architecture is the
residue of *imagining* having done the thing. It should be the residue of having actually
done it, by hand, several times. Delete 90% of this and go run the experiment manually.

---

## 1. You're abstracting before you've integrated once

The connector with LINE-direct / Lステップ / Salesforce adapters and capability
negotiation is a classic mistake: designing an abstraction against APIs you have never
called. The seams will be in the wrong places because real integrations surprise you —
LINE's narrowcast async progress model, audience-size minimums, the fact that "conversion"
isn't a thing the Messaging API even returns. **Build LINE-direct. Only LINE-direct.** The
abstraction is something you *extract* after the second integration hurts, not something
you design before the first one ships. Every hour spent on the interface is an hour spent
encoding guesses.

## 2. MCP is the wrong layer for a money-spending action

MCP is a protocol for exposing tools to *LLM agents*. You are using it as the integration
layer for sending paid marketing messages — i.e. putting an English-language, probabilistic,
model-mediated hop in the path of a financial transaction. Why? A send is a typed function
call: `send(audience, creative, idempotency_key)`. It wants a normal service, a queue, and
a cron, not a tool-calling protocol. The "agent-callable" framing is solving for a *demo*
("look, Claude sent the campaign"), not for production. Strip the agent out of the send
path entirely and you lose nothing operationally and gain auditability, determinism, and
the ability to hire an engineer who doesn't know what MCP is. Keep the LLM where it's
cheap and reversible (drafting, anomaly summaries), not where it spends yen.

## 3. The operational surface is a 5-person team's problem on a 0-revenue product

Count the pagers: Temporal cluster, 3 MCP services, an agent runtime, a CTGAN training
job, a GraphRAG index, Postgres, an object store, a KMS vault, an event log "graduating to
Kafka." Each is a thing that breaks at 2am. Who runs it? For a pilot, this should be **one
boring Python monolith, one Postgres, one job runner (or literally a cron), and the LLM
called as a function**. Temporal is a great answer to a durability problem you do not have
yet at one experiment per week. You can add it the day you have ten clients and a real
reliability incident — not before.

## 4. The simulation may be decoration that PPI quietly removes

Here's the uncomfortable one. The honest accuracy claim is PPI: sim over everything,
**bias-corrected by the real holdout**. But PPI's whole job is to *correct the sim with
real data*. The better your real anchor, the less the sim matters; the worse your real
anchor, the less you can trust the correction. There is a real regime — and it might be
*the* regime for mid-market — where the real holdout is doing all the work and the sim is
a confidence-inflating ornament. The killer question: **at what point does the sim change
a decision the client wouldn't have reached by just running the cheap LINE test you're
already running to validate it?** The closed loop *requires* real sends to close. So you
are already paying for the real experiment. Where is the cost asymmetry the entire
business rests on, once validation is mandatory?

## 5. The paired-cancellation trick proves the LLM is cosmetic

The simulation spec is proud that `δ_llm` cancels in the paired difference, so the LLM
"can't manufacture the lift." Follow that to its conclusion: **if the LLM cancels out of
`τ`, the LLM does not affect the number the product sells.** It only moves the absolute
`p_buy` level, which you don't report. So either the muscle matters to the decision (and
can therefore bias it — your safety property is false) or it doesn't (and it's a UI
garnish you're spending LLM tokens and latency to render). You can't have "the LLM adds
something essential" and "the LLM cancels out of the answer" at once. The senior move:
**cut the LLM out of the decision path entirely.** Keep a regression + uplift model — no
LLM — for `τ`, and use the LLM only to *narrate* personas in the client-facing readout,
clearly labeled as color. Your `τ` gets cheaper, faster, deterministic, and more
defensible, and you stop paying for thousands of agent inferences per run to produce a
number they don't influence.

## 6. The skeleton barely exists for the customers you're targeting

"The data fixes the skeleton" is elegant until you read your own tier table. Mid-market
clients are exactly the ones without the price variation (never-discounted SKUs) or the
campaign-log volume (few campaigns per segment) to fit the parameters. So for most pilot
clients you're shrinking hard toward a category/panel prior — i.e. you're selling
*national-panel personas with a regression on top*, which is the incumbent product
(Dentsu People Model, Hakuhodo バーチャル生活者) you claimed to beat on own-data grounding.
The differentiation is strongest precisely where the data is rich, which is precisely where
the client could afford a real panel anyway. **Name the segment of clients who have enough
own-data to make this defensible AND not enough budget to just test for real. If that set
is small, the wedge is small.**

## 7. The published accuracy metric is a weapon you hand your skeptic

You correctly identified the マーケリサーチ owner as both champion and sharpest skeptic.
Then you propose to hand them a published number computed from small-sample, low-power real
experiments. A number you don't control, that swings, that they can falsify in one
unlucky engagement. Published metrics are a liability you take on *after* you have a
distribution of results wide enough to defend a range — not a launch feature. For the
pilot, the metric is a private internal gate, not a marketing asset. The first time the
number prints 0.4 on a Hit@1 because of a 200-conversion sample, the deal is dead and no
amount of "but the CI is wide" saves it.

## 8. The statistical power problem isn't a risk — it's probably fatal to the headline

The prior docs file "not enough conversion signal" as the riskiest *assumption*. A senior
eng calls it the *base case*. Tens of thousands of recipients, single-digit conversion, a
handful of variants → each experiment's `τ_real` CI is wider than the effect you're trying
to detect. You cannot distinguish your sim from a coin flip in one engagement. By the time
you've pooled enough experiments hierarchically to have a defensible number, a year has
passed and the pilot client has churned. **The "continuously self-validating accuracy
metric" may be statistically unreachable on mid-market data within the lifetime of a
pilot.** Everything downstream of that — the closed loop's whole reason to exist — inherits
the doubt.

## 9. Vendor and cost reality

You are one frontier-LLM price change, rate-limit policy, or ToS revision away from your
unit economics inverting — and you run *thousands* of agents per sim run, per refresh. For
a `τ` the LLM doesn't even move (see §5). Meanwhile Lステップ's API is two months old and on
your critical path. You have stacked three external dependencies (LLM vendor, LINE,
Lステップ) under a product whose margin you haven't measured.

---

## What he'd tell you to build instead

> "Don't build a platform. Build a deliverable, by hand, three times."

1. **One notebook, one client, one decision.** Canonical schema as a few pandas loaders.
   `02_fit_params` as a regression + a CausalML uplift model. No CTGAN — sample agents by
   bootstrapping the real rows (privacy via aggregation, not a GAN you have to validate).
   No GraphRAG. No LLM in the decision path.
2. **Predict the winner. Then run the real LINE A/B by hand** in the client's own console.
   One sender script, not an MCP server. Compare. Write the comparison in a doc.
3. **Do that for three clients.** Now you have three real `(τ_sim, τ_real)` pairs and an
   honest, private sense of whether the sim beats a naive baseline (e.g. "always pick the
   discount" or "pick last campaign's winner"). If it doesn't beat the naive baseline,
   *none of the architecture matters* and you've spent three notebooks finding out instead
   of three quarters.
4. **The architecture is whatever survived being done by hand three times.** The MCP
   servers, Temporal, the connector, the published metric — each earns its place by having
   been the manual step that hurt, repeatedly. Most of them won't make the cut, and the
   ones that do will have interfaces shaped by reality.

The fastest path to the platform is to not start with the platform.

---

## Where the dissent is wrong (steelman the original, so this is a review and not a rant)

To be fair to the design — the dissent over-rotates in three places:

- **Some abstractions are cheap insurance, not premature.** The *output contract*
  (`tau_sim` shape), version pinning, and the data-plane separation cost almost nothing up
  front and are genuinely painful to retrofit — especially the APPI/consent boundary,
  which is a legal requirement, not an optimization. Keep those even in the notebook.
- **The human-gate-at-the-server-boundary point stands regardless of architecture.** Even
  the hand-run version must not let an LLM trigger a send unsupervised. That's not
  platform gold-plating; it's the one safety property you keep at every scale.
- **"Cut the LLM from the decision path" is right for `τ` but the LLM is not worthless** —
  persona realism is part of how you *sell* to a 仮説検証-literate buyer who wants to read
  "customers like me said X." Keep it; just stop pretending it sets the number, and stop
  paying to run it at population scale when a sampled handful narrates fine.

## The synthesis (what I'd actually carry forward)

1. **Ship the notebook + one sender script for the first client. No platform.**
2. **Pull the LLM out of the `τ` computation;** keep it for labeled persona color on a
   sampled few.
3. **Keep three things from the platform design even in the notebook:** the `tau_sim`
   output contract, the APPI/consent + data-plane boundary, and the server-enforced
   "no unsupervised send" gate.
4. **Make the accuracy metric private** until you have a defensible distribution; never
   launch a published number off one low-power experiment.
5. **Before any of it, answer the §4 question in writing:** what decision does the sim
   change that a cheap direct LINE test wouldn't? If you can't answer it crisply, the
   architecture is premature no matter how good it is.

---

## Resolution — §4 answered, §8 narrowed (the pre-screener reframe)

The §4 killer question now has a crisp answer, and it changes the verdict on §8.

**§4 — what the sim does that the cheap test can't.** The cheap LINE test is neither cheap
nor unlimited: a finite list powers only a *handful* of arms, a real price change costs
margin/shelf/weeks (not "hours"), an unbuilt creative costs production, a micro-segment
can't be powered live, and every losing arm burns yen + block-risk goodwill. So the live
test can *confirm* 2–3 finalists; it cannot *explore* 40. **The sim is a combinatorial
pre-screener: rank a space too large/slow/irreversible to test live, then spend the scarce
real-test budget only on the top-k.** It also reaches counterfactuals with no cheap real
equivalent at all (an unset price, an unproduced creative). That is a real asymmetry, and
it's the justification §4 demanded.

**§8 — the power objection survives only in narrowed form.** The wide-CI critique is an
objection to validating *magnitude*. Pre-screening needs only *ranking* — "is the real
winner in the sim's top-k" — and **ordinal signal survives far smaller samples than
magnitude.** So the fatal-base-case framing of §8 over-reached: it applies to the
calibrated-`τ` product, not the rank-a-shortlist product. What remains is a *smaller,
testable* risk: that the sim's **rank ordering doesn't beat a naive baseline** ("pick last
campaign's winner"). That you can settle on a few backtested held-out campaigns before any
client promise — which is exactly the kind of cheap falsification this whole review asked
for.

**Net effect on the dissent.** Three of its strongest hits hold unchanged — abstract-late,
MCP-is-the-wrong-send-layer, cut-the-LLM-from-`τ`. But "the sim is decoration PPI removes"
(§4) and "power kills the headline" (§8) are answered by selling *ranking, not magnitude*:
the LLM-free regression+uplift model produces the rank, PPI/β move to an internal pricing
gate, and the published number becomes **rank-lift over the naive baseline**, not a
calibrated `τ`. The build-by-hand-three-times prescription is unchanged — but now its
success criterion is concrete: *does sim top-k recall beat the naive baseline on the held-out
campaigns?* If yes, the platform has earned its first organ. If no, stop.
