#!/usr/bin/env bash
# ChargeGuard live demo - walks through all 3 decision paths against a
# running local instance. Run `poetry run python -m ml.train` and start
# the server first (see README "Demo" section).
export LC_ALL=C.UTF-8 2>/dev/null || export LC_ALL=en_US.UTF-8 2>/dev/null || true
set -euo pipefail

BASE_URL="${CHARGEGUARD_DEMO_URL:-http://127.0.0.1:8000}"
API_KEY="${API_KEY:?Set API_KEY env var to match the .env file used by the running server}"
HDR_AUTH=(-H "X-API-Key: ${API_KEY}")
HDR_JSON=(-H "Content-Type: application/json")

say() { printf "\n\033[1;36m%s\033[0m\n" "$1"; }
wait_for_decision() {
  local id="$1"
  for _ in $(seq 1 15); do
    result=$(curl -s "${HDR_AUTH[@]}" "${BASE_URL}/disputes/${id}")
    decision=$(echo "$result" | python3 -c "import json,sys; print(json.load(sys.stdin)['state'].get('decision') or '')" 2>/dev/null || echo "")
    if [ -n "$decision" ]; then echo "$result"; return 0; fi
    sleep 1
  done
  echo "$result"
}
show_result() {
  echo "$1" | python3 -c "
import json, sys
d = json.load(sys.stdin)['state']
print(f\"  decision:               {d.get('decision')}\")
print(f\"  win_probability:        {d.get('win_probability')}\")
print(f\"  expected_value:         {d.get('expected_value')}\")
print(f\"  third_party_fraud:      {d.get('third_party_fraud_indicators')}\")
print(f\"  identity_continuity:    {d.get('identity_continuity')}\")
print(f\"  contradiction_summary:  {d.get('contradiction_summary')}\")
print(f\"  final_outcome:          {d.get('final_outcome')}\")
print(f\"  rebuttal_pdf:           {d.get('rebuttal_document_path')}\")
"
}
future_deadline() { python3 -c "from datetime import datetime,timezone,timedelta; print((datetime.now(timezone.utc)+timedelta(days=$1)).isoformat())"; }

say "0. Health check"
curl -s "${BASE_URL}/health"; echo

say "1. Register demo merchant"
curl -s -X POST "${BASE_URL}/merchants" "${HDR_AUTH[@]}" "${HDR_JSON[@]}" \
  -d '{"merchant_id":"demo_merchant","name":"Demo Store","vertical":"ecommerce","payment_provider":"razorpay","shipping_provider":"shiprocket","average_order_value":1499,"chargeback_history_count":3}' \
  -o /dev/null -w "  status: %{http_code}\n"

say "2. STRONG CASE - delivered, signed, OTP verified, no fraud signals -> expect FIGHT"
curl -s -X POST "${BASE_URL}/webhook/chargeback" "${HDR_AUTH[@]}" "${HDR_JSON[@]}" \
  -d "{\"chargeback_id\":\"cb_demo_fight\",\"reason_code\":\"13.1\",\"card_network\":\"VISA\",\"dispute_amount\":1499,\"currency\":\"INR\",\"filing_deadline\":\"$(future_deadline 5)\",\"merchant_id\":\"demo_merchant\",\"order_id\":\"order_fight\",\"payment_id\":\"pay_fight\",\"tracking_id\":\"track_fight\"}" \
  -o /dev/null -w "  status: %{http_code}\n"
show_result "$(wait_for_decision cb_demo_fight)"

say "3. WEAK CASE - same strong evidence, tiny dispute amount -> expect ACCEPT (economics, not evidence, drives this)"
curl -s -X POST "${BASE_URL}/webhook/chargeback" "${HDR_AUTH[@]}" "${HDR_JSON[@]}" \
  -d "{\"chargeback_id\":\"cb_demo_accept\",\"reason_code\":\"13.1\",\"card_network\":\"VISA\",\"dispute_amount\":50,\"currency\":\"INR\",\"filing_deadline\":\"$(future_deadline 5)\",\"merchant_id\":\"demo_merchant\",\"order_id\":\"order_accept\",\"payment_id\":\"pay_accept\",\"tracking_id\":\"track_accept\"}" \
  -o /dev/null -w "  status: %{http_code}\n"
show_result "$(wait_for_decision cb_demo_accept)"

say "4. DEGRADED CASE - model artifact unavailable -> expect ESCALATE_DEGRADED, never a silent guess"
mv ml/artifacts/win_probability_model.pkl /tmp/model_backup.pkl 2>/dev/null || true
curl -s -X POST "${BASE_URL}/webhook/chargeback" "${HDR_AUTH[@]}" "${HDR_JSON[@]}" \
  -d "{\"chargeback_id\":\"cb_demo_escalate\",\"reason_code\":\"13.1\",\"card_network\":\"VISA\",\"dispute_amount\":1499,\"currency\":\"INR\",\"filing_deadline\":\"$(future_deadline 5)\",\"merchant_id\":\"demo_merchant\",\"order_id\":\"order_escalate\",\"payment_id\":\"pay_escalate\",\"tracking_id\":\"track_escalate\"}" \
  -o /dev/null -w "  status: %{http_code}\n"
show_result "$(wait_for_decision cb_demo_escalate)"
mv /tmp/model_backup.pkl ml/artifacts/win_probability_model.pkl 2>/dev/null || true

say "5. Live /stats across everything just processed"
curl -s "${BASE_URL}/stats" "${HDR_AUTH[@]}"; echo

say "Done. FIGHT case PDF ready at: output/rebuttals/cb_demo_fight_rebuttal.pdf"
