import type { DisputeDetail } from "../../api/schemas";

export function sumByCurrency(disputes: DisputeDetail[], value: (dispute: DisputeDetail) => number | null | undefined) {
  return disputes.reduce<Record<string, number>>((totals, dispute) => {
    const amount = value(dispute);
    if (amount === null || amount === undefined || !Number.isFinite(amount)) return totals;
    const currency = dispute.state.currency;
    totals[currency] = (totals[currency] ?? 0) + amount;
    return totals;
  }, {});
}

export function rankActionRadar(disputes: DisputeDetail[]) {
  return [...disputes]
    .filter((dispute) => !["WIN", "LOSS", "ACCEPTED_NO_CONTEST"].includes(dispute.state.final_outcome ?? ""))
    .sort((left, right) => {
      const risk = (item: DisputeDetail) => item.status === "failed" ? 0 : item.state.evidence_collection_degraded ? 1 : 2;
      return risk(left) - risk(right)
        || new Date(left.state.filing_deadline).getTime() - new Date(right.state.filing_deadline).getTime()
        || (right.state.expected_value ?? Number.NEGATIVE_INFINITY) - (left.state.expected_value ?? Number.NEGATIVE_INFINITY);
    });
}
