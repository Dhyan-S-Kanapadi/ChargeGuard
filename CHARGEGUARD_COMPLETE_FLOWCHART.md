# ChargeGuard complete connected control flow

This is the Mermaid version of the implementation-aligned ChargeGuard control flow. Dashed arrows represent supporting data, recovery, or operator paths rather than the primary synchronous path.

```mermaid
flowchart TD
  %% ---------------------------------------------------------------------------
  %% Merchant setup and order-correlation foundation
  %% ---------------------------------------------------------------------------
  subgraph SETUP["1. Merchant setup and order-correlation foundation"]
    direction TB
    operator["Operator connects dashboard<br/>Enter API origin and API key<br/>Same-origin guard · 12-second timeout · Zod response validation"]
    api_auth{"X-API-Key valid?<br/>Fail closed on missing or mismatched key"}
    auth_reject(["401 Unauthorized<br/>No mutation or provider call"])
    platform_suggest["Platform suggestion<br/>Validate public HTTP(S) target and every redirect<br/>Shopify / WooCommerce / custom / unknown<br/>Suggestion never writes authoritative platform"]
    merchant_upsert["Create or update merchant<br/>Validate merchant and provider configuration<br/>Verify submitted storefront credential synchronously<br/>Persist safe verification result · never return secrets"]

    payment_connectors["Payment connector lifecycle<br/>Verify Razorpay or Stripe credential live<br/>Encrypt secret before activation<br/>Merchant ownership and verified status checked before decrypt<br/>Rotation retires and deletes predecessor secret"]
    seon_connector["SEON connector lifecycle<br/>Encrypt as verification_pending<br/>First real check activates candidate<br/>401/403 invalidates · timeout/5xx remains transient"]
    ingest_choice{"How is order history supplied?"}
    generic_ingest["Generic order ingestion<br/>POST /orders/ingest<br/>API-key auth · payload validation · merchant existence<br/>Upsert order, customer, device, address and identifiers<br/>Identifier collision returns 409"]
    shopify_sync["Shopify historical sync<br/>Require verified Shopify credential and HTTPS<br/>Same-host Link pagination · bounded 429 backoff<br/>Normalize valid orders · skip/count bad records safely"]
    order_store[("Merchant-scoped Order store<br/>Key: merchant_id + order_id<br/>Exact provider-payment/order/commerce identifiers<br/>CE3 same-merchant and normalized-email date query")]

    operator --> api_auth
    api_auth -- YES --> platform_suggest
    api_auth -- NO --> auth_reject
    platform_suggest --> merchant_upsert
    merchant_upsert -->|Configure payment| payment_connectors
    merchant_upsert -->|Configure device risk| seon_connector
    merchant_upsert -->|Supply order history| ingest_choice
    ingest_choice -->|CUSTOM| generic_ingest
    ingest_choice -->|SHOPIFY| shopify_sync
    generic_ingest --> order_store
    shopify_sync --> order_store
  end

  %% ---------------------------------------------------------------------------
  %% Dispute ingress and provider lifecycle
  %% ---------------------------------------------------------------------------
  subgraph INGRESS["2. Dispute ingress and provider-event control flow"]
    direction TB

    subgraph INTERNAL["Authenticated normalized ingress"]
      direction TB
      internal_event["POST /webhook/chargeback<br/>Trusted normalized chargeback"]
      internal_validate["Authenticate, rate-limit and validate<br/>Pydantic payload · merchant exists<br/>Deterministic deadline · sanitized initial state"]
      internal_claim{"New chargeback ID?<br/>Atomic dispute claim"}
      internal_duplicate(["409 duplicate<br/>Existing dispute preserved"])
      internal_schedule["Persist received · return 202<br/>Schedule graph by chargeback ID<br/>processing → completed or failed"]

      internal_event --> internal_validate --> internal_claim
      internal_claim -- NO --> internal_duplicate
      internal_claim -- YES --> internal_schedule
    end

    subgraph RAZORPAY["Signed Razorpay ingress"]
      direction TB
      razorpay_event["POST /webhook/razorpay<br/>Created · action-required · under-review · won · lost · closed"]
      body_signature["Raw-body security checks<br/>Enabled and size limits<br/>Require X-Razorpay-Signature<br/>HMAC-SHA256 exact raw bytes<br/>Constant-time comparison before JSON trust"]
      signature_ok{"Signature valid?"}
      signature_reject(["401 / 400 / 413<br/>Reject without trusting payload"])
      parse_claim["Parse, minimize and claim event<br/>Strict envelope validation<br/>Provider event ID or SHA-256 fallback<br/>Persist processing-required minimized data and payload hash"]
      event_new{"Event claim is new?"}
      event_duplicate(["200 duplicate<br/>No duplicate scheduling"])
      event_supported{"Supported event type?"}
      event_ignored(["Persist ignored<br/>Authentic unknown event remains auditable"])
      event_queue["Atomic queue transition · return 202<br/>Background task receives event ID only"]
      processing_claim{"Processing claim acquired?<br/>Queued or recoverable states only"}
      processor_skip(["Skipped or missing<br/>Concurrent/finished work is not repeated"])
      merchant_map{"Exact account_id maps to merchant?<br/>Verified Razorpay ownership only"}
      event_unresolved(["Persist unresolved<br/>Retry after connector mapping"])
      normalize["Enrich and normalize provider dispute<br/>Fetch missing payment facts through verified connector<br/>Normalize money, rail, network, IDs, lifecycle and deadline<br/>Keep provider and network reason separate<br/>Never guess a reason or network"]
      automation_eligible{"Safe to automate created event?<br/>CARD · supported network and reason<br/>playbook/template · future deadline · usable enrichment"}
      provider_manual["Manual-review state<br/>ESCALATE_DEGRADED · PENDING<br/>Store explicit blocker<br/>Never invent missing create state"]
      provider_upsert["Idempotent dispute lifecycle upsert<br/>Schedule new eligible create<br/>Update action-required / under-review<br/>WON/LOST request outcome gate<br/>Ordering guards reject stale regression"]

      razorpay_event --> body_signature --> signature_ok
      signature_ok -- NO --> signature_reject
      signature_ok -- YES --> parse_claim --> event_new
      event_new -- NO --> event_duplicate
      event_new -- YES --> event_supported
      event_supported -- NO --> event_ignored
      event_supported -- YES --> event_queue --> processing_claim
      processing_claim -- NO --> processor_skip
      processing_claim -- YES --> merchant_map
      merchant_map -- NO --> event_unresolved
      merchant_map -- YES --> normalize --> automation_eligible
      automation_eligible -- NO --> provider_manual
      automation_eligible -- "YES / lifecycle" --> provider_upsert
    end
  end

  %% ---------------------------------------------------------------------------
  %% LangGraph execution
  %% ---------------------------------------------------------------------------
  subgraph GRAPH["3. Complete LangGraph execution"]
    direction TB
    g0["Shared ChargebackState enters graph<br/>Inputs · sanitized merchant · evidence · degradation<br/>intelligence · response · outcome"]
    g1["G1 · orchestrator<br/>Compute UTC deadline priority<br/>Build auditable evidence plan<br/>Enable food agents for food/quick-commerce or 13.1/13.3/4853"]
    priority{"Priority is overdue?<br/>Only overdue takes expedited partial-evidence path"}

    subgraph FULL["Full evidence path"]
      direction TB
      g3["G3 · transaction_evidence<br/>Razorpay/Stripe stub or verified merchant connector<br/>Normalize money, auth, customer/device and explicit commerce refs<br/>Failure → neutral evidence plus degradation"]
      g4["G4 · order_correlation<br/>Exact merchant-scoped identifier priority only<br/>Never match by amount, email or date proximity<br/>Success copies tracking/fulfillment and marks order disputed"]
      g5["G5 · shipping_evidence<br/>Require correlated tracking ID<br/>Shiprocket primary or Delhivery primary with safe fallback<br/>Normalize status, delivery time/GPS, signature, photo and events<br/>No LLM; failure → UNKNOWN plus degradation"]
      g6["G6 · device_evidence<br/>SEON through merchant-owned encrypted connector<br/>Normalize fraud score, fingerprint, geo/login and VPN<br/>Failure → neutral device evidence plus degradation"]
      g7["G7 · comms_evidence<br/>Exact order reference and customer email<br/>Gmail and Freshdesk fail independently<br/>Normalize post-delivery contact and prior complaint"]
      g8["G8 · consortium_evidence<br/>Ethoca and Verifi queried independently<br/>Normalize completeness, matches and cross-merchant history<br/>Failure → neutral incomplete lookup"]
      full_route{"Food evidence required?"}

      g3 --> g4 --> g5 --> g6 --> g7 --> g8 --> full_route
    end

    subgraph EXPEDITED["Overdue expedited path"]
      direction TB
      g3e["G3-E · expedited_transaction_evidence<br/>Same transaction agent and safeguards"]
      g4e["G4-E · expedited_order_correlation<br/>Same exact identifier rules"]
      g5e["G5-E · expedited_shipping_evidence<br/>Same deterministic shipping agent<br/>Skip device, comms, consortium and food nodes"]
      exp_ce3{"Visa + reason 10.4?"}

      g3e --> g4e --> g5e --> exp_ce3
    end

    subgraph FOOD["Optional food and quick-commerce evidence"]
      direction TB
      g10["G10 · delivery_photo_evidence<br/>Use shipping photo or fetch it from food adapter<br/>Optional Claude Vision returns delivered/address/confidence<br/>LLM is evidence interpretation only; failure stays neutral"]
      g11["G11 · order_timeline_evidence<br/>Deterministic food-platform lookup<br/>Normalize placed/accepted/picked/delivered/rating<br/>Failure → empty timeline and continue"]
      g10 --> g11
    end

    g12["G12 · purchase_history_evidence · Visa CE3.0<br/>Same merchant/email · 365-to-120-day prior window<br/>Exclude current, disputed and fraud-flagged orders<br/>Each candidate needs at least 2 exact matches including IP<br/>At least 2 candidates qualify the case<br/>No history → insufficient_history without raising"]
    g13["G13 · deterministic scoring authority<br/>Qualified Visa 10.4 CE3 → fixed 0.95 override<br/>Else versioned logistic model with 20 normalized features<br/>EV = probability × amount − response cost<br/>Any degradation/model failure → ESCALATE_DEGRADED<br/>Else EV above threshold → FIGHT; otherwise ACCEPT"]
    g14["G14 · decision_review · optional advisory LLM<br/>Privacy-bounded normalized facts only<br/>Strict JSON · temperature 0 · bounded call<br/>Snapshot/finally restore every authoritative field<br/>Recommendation cannot change decision, EV, filing or outcome"]
    g15{"Authoritative deterministic decision?"}

    g16["G16 · rebuttal_builder<br/>Exact network playbook and reason template<br/>Structured packet and evidence highlights<br/>Qualified CE3 uses transaction table<br/>Optional non-CE3 narrative with deterministic fallback<br/>Write PDF and JSON fact sidecar"]
    g17{"G17 · quality_check<br/>Maximum 3 checks<br/>Valid PDF/sidecar · page cap · matching case facts<br/>Required evidence · highlights · prohibited-language checks"}
    g18["G18 · filing<br/>Require approved quality and existing PDF<br/>Record local filed_* confirmation and filed_at<br/>No provider/network submission exists<br/>Outcome remains PENDING"]
    g19["G19 · accept_and_log<br/>ACCEPT · accepted_no_filing<br/>ACCEPTED_NO_CONTEST · never LOSS"]
    g20["G20 · human_escalation<br/>human_review_required · PENDING<br/>Optional bounded summary only for degraded cases"]
    learning_route{"Real WIN/LOSS and filed invariant already true?"}
    graph_end(["END<br/>Completed dispute persisted"])

    g0 --> g1 --> priority
    priority -- "NO · FULL" --> g3
    priority -- "YES · EXPEDITED" --> g3e
    full_route -- "YES · FOOD" --> g10
    full_route -- "NO + VISA 10.4" --> g12
    full_route -- "NO + STANDARD" --> g13
    g11 -- "VISA 10.4" --> g12
    g11 -- STANDARD --> g13
    exp_ce3 -- YES --> g12
    exp_ce3 -- NO --> g13
    g12 --> g13 --> g14 --> g15
    g15 -- FIGHT --> g16 --> g17
    g17 -- APPROVED --> g18
    g17 -- "AUTO-FIXABLE · attempts under 3" --> g16
    g17 -- "NON-FIXABLE / EXHAUSTED" --> g20
    g15 -- ACCEPT --> g19
    g15 -- ESCALATE_DEGRADED --> g20
    g18 --> learning_route
    g19 --> learning_route
    g20 --> learning_route
    learning_route -- "NO / PENDING" --> graph_end
  end

  %% ---------------------------------------------------------------------------
  %% Adjudication, learning and operations
  %% ---------------------------------------------------------------------------
  subgraph OPERATIONS["4. Adjudication, learning, recovery and operator flows"]
    direction TB
    outcome_sources["Outcome sources<br/>Ordered provider WON/LOST<br/>or authenticated POST /disputes/id/outcome<br/>CLOSED never invents an outcome"]
    outcome_gate{"Eligible adjudicated outcome?<br/>FIGHT · quality approved · filed_at · filed_* confirmation<br/>Same outcome idempotent · conflicting terminal rejected"}
    outcome_reject(["Reject or no-op<br/>Unfiled/conflicting state cannot enter learning"])
    g22["G22 · learning<br/>One idempotent real WIN/LOSS feature record per filed case<br/>Update playbook statistics<br/>Threshold-based locked retraining<br/>Synthetic seed rows decay as real outcomes grow<br/>Atomic model and metadata writes"]
    artifacts[("Filed-only ML artifacts<br/>outcomes · playbook stats · model · training metadata<br/>Ignored by Git")]

    recovery["Razorpay recovery and retry<br/>Protected metadata-only event list<br/>Bounded recoverable batch<br/>Atomic requeue and processing claim"]
    reconcile["Razorpay reconciliation<br/>Verified merchant connector and bounded window<br/>Deterministic reconciliation event IDs<br/>Same sanitized lifecycle processor<br/>Continue safely across individual failures"]
    simulator["Development Razorpay simulator<br/>API key + feature flag · disabled in production<br/>Validate legal lifecycle transition<br/>Sign Razorpay-shaped body<br/>Loopback /webhook/razorpay only · no provider action"]

    classification["Human-in-the-loop reason classification<br/>Only isolated unmapped Razorpay CARD reason<br/>Bounded LLM suggestion against verified candidates<br/>Authenticated operator must approve exact stored suggestion<br/>Atomic claim prevents stale or duplicate resume"]
    read_api["Dispute reads and redaction<br/>Default removes raw evidence and sensitive customer data<br/>LLM review safe-key allowlist<br/>include_raw also requires X-Internal-Token"]
    stats_assistant["Stats and portfolio assistant<br/>Win rate uses filed WIN/LOSS only<br/>Assistant receives redacted bounded context<br/>Read-only and rate-limited"]

    app_store[("Synchronized application store<br/>Single-process RLock · deep-copy reads<br/>Optional atomic JSON persistence<br/>Merchant/order/dispute/event state guards")]
    secret_store[("Encrypted credential store<br/>Fernet-authenticated payment and SEON secrets<br/>Atomic writes · fail closed<br/>Secrets never enter state, logs or API")]
    outputs["Outputs and invariants<br/>FIGHT is not WIN · ACCEPT is not LOSS<br/>Degradation requires human review<br/>LLMs never control security, money, routing, filing or outcome<br/>Filing remains a local confirmation stub"]
    final_end(["ChargeGuard lifecycle remains auditable<br/>Accepted events, decisions, degradation, artifacts, retries and eligible outcomes have stored paths"])

    outcome_sources --> outcome_gate
    outcome_gate -- YES --> g22 --> artifacts
    outcome_gate -- NO --> outcome_reject
    read_api -. Redacted context .-> stats_assistant
    artifacts --> final_end
    stats_assistant --> final_end
    outputs --> final_end
  end

  %% Primary convergence and post-graph lifecycle.
  internal_schedule -->|Background graph| g0
  provider_upsert -->|Eligible CREATE| g0
  provider_upsert -. WON / LOST .-> outcome_sources
  learning_route -- YES --> g22

  %% Recovery, simulation and human review paths.
  recovery -. Requeue .-> processing_claim
  reconcile -. Sanitized claimed event .-> normalize
  simulator -. Signed loopback POST .-> razorpay_event
  provider_manual -. Isolated unknown reason .-> classification
  classification -. Approved exact classification .-> g0
  graph_end -. Stored completed state .-> read_api

  %% Supporting storage and connector relationships.
  order_store -. Exact correlation .-> g4
  order_store -. CE3 matching pool .-> g12
  payment_connectors -. Verified Razorpay enrichment .-> normalize
  seon_connector -. First live SEON request .-> g6
  merchant_upsert -. Merchant state .-> app_store
  order_store -. Order state .-> app_store
  parse_claim -. Provider event state .-> app_store
  g0 -. Dispute state .-> app_store
  payment_connectors -. Encrypted payment secret .-> secret_store
  seon_connector -. Encrypted SEON secret .-> secret_store
  g16 -. PDF and fact sidecar .-> outputs
  app_store -. Auditable state .-> outputs

  %% Visual semantics.
  classDef process fill:#102838,stroke:#4db8ff,color:#eef6fb,stroke-width:2px;
  classDef decision fill:#2c281b,stroke:#ffca58,color:#eef6fb,stroke-width:3px;
  classDef security fill:#211820,stroke:#ff7180,color:#eef6fb,stroke-width:2px;
  classDef data fill:#0d292b,stroke:#3bd6d0,color:#eef6fb,stroke-width:2px;
  classDef ai fill:#211b32,stroke:#b79cff,color:#eef6fb,stroke-width:2px;
  classDef warning fill:#2b1d18,stroke:#ff945f,color:#eef6fb,stroke-width:2px;
  classDef success fill:#10291f,stroke:#43d68f,color:#eef6fb,stroke-width:2px;

  class operator,merchant_upsert,internal_event,body_signature,razorpay_event,simulator,read_api security;
  class api_auth,ingest_choice,internal_claim,signature_ok,event_new,event_supported,processing_claim,merchant_map,automation_eligible,priority,full_route,exp_ce3,g12,g13,g15,g17,learning_route,outcome_gate decision;
  class order_store,generic_ingest,shopify_sync,parse_claim,event_queue,provider_upsert,g0,g22,artifacts,app_store,secret_store data;
  class g10,g14,classification,stats_assistant ai;
  class auth_reject,internal_duplicate,signature_reject,event_ignored,processor_skip,event_unresolved,provider_manual,g3e,g4e,g5e,g20,outcome_reject,recovery warning;
  class internal_schedule,event_duplicate,g18,g19,graph_end,final_end success;
  class platform_suggest,payment_connectors,seon_connector,internal_validate,normalize,g1,g3,g4,g5,g6,g7,g8,g11,g16,reconcile,outputs process;
```

## Legend

- Blue: deterministic process or graph node
- Yellow diamond: deterministic decision or route
- Red: authentication, signature, credential, or sensitive-data boundary
- Cyan: persistence or stored data
- Purple: optional AI/LLM assistance
- Orange: degraded, rejected, recoverable, or human-review path
- Green: accepted terminal or successful action

## Current deployment boundary

This diagram describes the implemented local/staging simulation. Filing is a local confirmation stub, the application store is designed for a controlled single-process deployment, and real provider connectivity still requires provider test-mode certification.
