export function formatMoney(amount: number | null | undefined, currency: string): string {
  if (amount === null || amount === undefined || !Number.isFinite(amount)) return "Unavailable";
  try {
    return new Intl.NumberFormat("en-IN", { style: "currency", currency, maximumFractionDigits: 2 }).format(amount);
  } catch {
    return `${currency} ${amount.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
  }
}

export function formatPercent(value: number | null | undefined): string {
  return value === null || value === undefined ? "Unavailable" : `${(value * 100).toFixed(1)}%`;
}

export function formatDate(value: string | null | undefined, withTime = true): string {
  if (!value) return "Unavailable";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Unavailable";
  return new Intl.DateTimeFormat("en-IN", withTime
    ? { dateStyle: "medium", timeStyle: "short" }
    : { dateStyle: "medium" }).format(date);
}

export function deadlineLabel(value: string): { text: string; urgent: boolean; overdue: boolean } {
  const hours = (new Date(value).getTime() - Date.now()) / 3_600_000;
  if (hours < 0) return { text: `${Math.ceil(Math.abs(hours) / 24)}d overdue`, urgent: true, overdue: true };
  if (hours < 24) return { text: `${Math.max(1, Math.ceil(hours))}h remaining`, urgent: true, overdue: false };
  return { text: `${Math.ceil(hours / 24)}d remaining`, urgent: hours < 72, overdue: false };
}

export function recommendationTone(decision: string | null | undefined) {
  if (decision === "FIGHT") return "success" as const;
  if (decision === "ACCEPT") return "warning" as const;
  if (decision === "ESCALATE_DEGRADED") return "danger" as const;
  return "neutral" as const;
}
