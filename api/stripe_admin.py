"""Protected Stripe reconciliation and recovery over shared provider events."""
from datetime import datetime, timezone
from threading import Lock, Thread
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from api.auth import require_api_key
from api.store import store
from api.stripe_processor import process_stripe_provider_event
from integrations.payment_client_factory import PaymentClientFactory, PaymentConnectorError
from integrations.stripe import StripeRequestError
from integrations.credential_secrets import CredentialStoreError
from integrations.stripe_webhook import serialize_event

router=APIRouter(prefix="/internal/stripe", tags=["stripe-internal"], dependencies=[Depends(require_api_key)])
factory=PaymentClientFactory(store)
_startup_lock=Lock(); _startup_started=False

def recover(limit:int, schedule):
    events=store.list_recoverable_provider_events(provider="stripe", limit=min(100,max(1,limit))); scheduled=0
    for event in events:
        if store.requeue_provider_event(event["event_id"]): schedule(event["event_id"]); scheduled+=1
    return {"considered":len(events),"scheduled":scheduled}

def startup_recover(): recover(25, process_stripe_provider_event)

def schedule_startup_stripe_recovery():
    global _startup_started
    if __import__("os").getenv("STRIPE_RECOVER_PENDING_ON_STARTUP", "true").lower() not in {"1","true","yes","on"}: return False
    with _startup_lock:
        if _startup_started: return False
        _startup_started=True
    Thread(target=startup_recover, name="stripe-startup-recovery", daemon=True).start()
    return True

@router.post("/events/{event_id}/retry")
def retry(event_id:str, background_tasks:BackgroundTasks):
    event=store.get_provider_event(event_id)
    if not event or event.get("provider")!="stripe": raise HTTPException(404,"Stripe event not found.")
    if not store.requeue_provider_event(event_id, include_received=False): raise HTTPException(409,"Stripe event is not eligible for retry.")
    background_tasks.add_task(process_stripe_provider_event,event_id); return {"status":"queued","event_id":event_id}

@router.post("/process-pending")
def pending(background_tasks:BackgroundTasks, limit:int=Query(25,ge=1,le=100)):
    return recover(limit, lambda event_id: background_tasks.add_task(process_stripe_provider_event,event_id))

@router.post("/reconcile")
def reconcile(payload:dict, background_tasks:BackgroundTasks):
    merchant_id=payload.get("merchant_id"); merchant=store.get_merchant(merchant_id)
    if not merchant: raise HTTPException(404,"Merchant not found.")
    try: disputes=factory.for_merchant(merchant,"stripe").list_disputes(created_gte=payload.get("from_timestamp"),created_lte=payload.get("to_timestamp"),limit=min(100,max(1,int(payload.get("count",25)))))
    except (PaymentConnectorError, StripeRequestError, CredentialStoreError): raise HTTPException(503,"stripe_reconciliation_unavailable") from None
    connector_id=merchant.get("payment_connector_ids",{}).get("stripe"); connector=store.get_payment_connector(merchant_id,connector_id) if connector_id else None
    account=connector.get("provider_account_id") if connector else None; results=[]
    for dispute in disputes:
        event={"id":f"reconcile:{dispute.get('id')}:{dispute.get('status')}:{dispute.get('created')}","type":"charge.dispute.updated","account":account,"created":dispute.get("created"),"data":{"object":dispute}}
        safe=serialize_event(event); claimed=store.claim_provider_event({"event_id":event["id"],"provider":"stripe","event_type":event["type"],"provider_dispute_id":safe["data"]["object"].get("id"),"payment_id":safe["data"]["object"].get("payment_intent") or safe["data"]["object"].get("charge"),"account_id":account,"event_id_source":"reconciliation","provider_event_timestamp":datetime.fromtimestamp(int(event.get("created") or 0),timezone.utc),"event_data":safe,"processing_state":"received"})
        if not claimed: results.append({"status":"duplicate"}); continue
        store.queue_provider_event(event["id"]); background_tasks.add_task(process_stripe_provider_event,event["id"]); results.append({"status":"queued"})
    return {"count":len(results),"results":results}
