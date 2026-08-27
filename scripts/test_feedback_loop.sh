#!/usr/bin/env bash
# Verifies the real-outcome feedback loop end to end against a running
# local server: fires FIGHT-path cases, records WIN/LOSS outcomes, and
# confirms retraining fires with the correct synthetic/real data split.
# Run with RETRAIN_RECORD_THRESHOLD set low (e.g. 3) for a fast pass:
#   export RETRAIN_RECORD_THRESHOLD=3   (set BEFORE starting the server)
export LC_ALL=C.UTF-8 2>/dev/null || export LC_ALL=en_US.UTF-8 2>/dev/null || true
set -euo pipefail
BASE_URL="${CHARGEGUARD_DEMO_URL:-http://127.0.0.1:8000}"
API_KEY="${API_KEY:?Set API_KEY to match the .env file used by the running server}"
H=(-H "X-API-Key: ${API_KEY}")
J=(-H "Content-Type: application/json")

say() { printf "\n== %s ==\n" "$1"; }
future_deadline() { python3 -c "from datetime import datetime,timezone,timedelta; print((datetime.now(timezone.utc)+timedelta(days=$1)).isoformat())"; }

say "Reset training data so we see the loop from a clean state"
rm -f ml/artifacts/outcomes.json ml/artifacts/training_metadata.json ml/artifacts/playbook_stats.json

say "Register merchant"
curl -s -X POST "${BASE_URL}/merchants" "${H[@]}" "${J[@]}" \
  -d '{"merchant_id":"fb_merchant","name":"Feedback Test Store","vertical":"ecommerce","payment_provider":"razorpay","shipping_provider":"shiprocket","average_order_value":1499,"chargeback_history_count":3}' \
  -o /dev/null -w "status: %{http_code}\n"

say "Fire 10 FIGHT-path cases, alternate WIN/LOSS outcomes, watch retrain trigger"
for i in $(seq 1 10); do
  cb_id="cb_fb_${i}"
  curl -s -X POST "${BASE_URL}/webhook/chargeback" "${H[@]}" "${J[@]}" \
    -d "{\"chargeback_id\":\"${cb_id}\",\"reason_code\":\"13.1\",\"card_network\":\"VISA\",\"dispute_amount\":1499,\"currency\":\"INR\",\"filing_deadline\":\"$(future_deadline 5)\",\"merchant_id\":\"fb_merchant\",\"order_id\":\"order_${i}\",\"payment_id\":\"pay_${i}\",\"tracking_id\":\"track_${i}\"}" \
    -o /dev/null -w ""

  for _ in $(seq 1 10); do
    result=$(curl -s "${H[@]}" "${BASE_URL}/disputes/${cb_id}")
    decision=$(echo "$result" | python3 -c "import json,sys; print(json.load(sys.stdin)['state'].get('decision') or '')" 2>/dev/null || echo "")
    [ -n "$decision" ] && break
    sleep 1
  done

  if [ "$i" -le 5 ]; then outcome="WIN"; else outcome="LOSS"; fi
  fb=$(curl -s -X POST "${BASE_URL}/disputes/${cb_id}/outcome" "${H[@]}" "${J[@]}" \
    -d "{\"outcome\":\"${outcome}\",\"reason\":\"test batch record ${i}\"}")
  echo "case ${i}: decision=${decision} outcome_recorded=${outcome} -> ${fb}"
done

say "Training data on disk after 10 real outcomes"
python3 -m json.tool ml/artifacts/training_metadata.json
echo "-- outcomes.json record count --"
python3 -c "import json; print(len(json.load(open('ml/artifacts/outcomes.json'))))"

say "Guard check: try recording an outcome on an ACCEPT case, expect 409"
curl -s -X POST "${BASE_URL}/webhook/chargeback" "${H[@]}" "${J[@]}" \
  -d "{\"chargeback_id\":\"cb_fb_accept_guard\",\"reason_code\":\"13.1\",\"card_network\":\"VISA\",\"dispute_amount\":50,\"currency\":\"INR\",\"filing_deadline\":\"$(future_deadline 5)\",\"merchant_id\":\"fb_merchant\",\"order_id\":\"order_guard\",\"payment_id\":\"pay_guard\",\"tracking_id\":\"track_guard\"}" \
  -o /dev/null -w ""
sleep 3
curl -s -X POST "${BASE_URL}/disputes/cb_fb_accept_guard/outcome" "${H[@]}" "${J[@]}" \
  -d '{"outcome":"WIN","reason":"should be rejected"}' -w "\nstatus: %{http_code}\n"
