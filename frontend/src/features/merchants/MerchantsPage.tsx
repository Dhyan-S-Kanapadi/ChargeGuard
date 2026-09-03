import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Building2, Plus } from "lucide-react";
import { useState, type FormEvent } from "react";
import type { Merchant } from "../../api/schemas";
import { useConnection } from "../../app/ConnectionContext";
import { Badge, Button, EmptyState, ErrorState, Panel, Skeleton } from "../../components/ui";
import { formatMoney, formatPercent } from "../../utils/format";

type Platform = "shopify" | "woocommerce" | "custom" | "unknown";

export function MerchantsPage() {
  const { client } = useConnection();
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["merchants"], queryFn: ({ signal }) => client.merchants(signal) });
  const [open, setOpen] = useState(false);
  const [storeUrl, setStoreUrl] = useState("");
  const [platform, setPlatform] = useState<Platform>("unknown");
  const [platformOverridden, setPlatformOverridden] = useState(false);
  const [created, setCreated] = useState<Merchant | null>(null);
  const suggestion = useMutation({
    mutationFn: (url: string) => client.suggestPlatform(url),
    onSuccess: ({ suggestion: value }) => {
      if (!platformOverridden) setPlatform(value);
    },
  });
  const mutation = useMutation({
    mutationFn: (body: Record<string, unknown>) => client.createMerchant(body),
    onSuccess: async (merchant) => {
      setCreated(merchant);
      await queryClient.invalidateQueries({ queryKey: ["merchants"] });
    },
  });

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setCreated(null);
    const form = new FormData(event.currentTarget);
    mutation.mutate({
      merchant_id: form.get("merchant_id"), name: form.get("name"), vertical: form.get("vertical"),
      payment_provider: form.get("payment_provider") || null,
      razorpay_account_id: form.get("razorpay_account_id") || null,
      average_order_value: Number(form.get("average_order_value")), chargeback_history_count: 0,
      transaction_volume_30d_by_network: {}, store_url: storeUrl || null,
      storefront_platform: platform,
      shopify_admin_api_token: form.get("shopify_admin_api_token") || null,
      woocommerce_api_key: form.get("woocommerce_api_key") || null,
      woocommerce_api_secret: form.get("woocommerce_api_secret") || null,
    });
  };

  return <>
    <header className="page-heading"><div><p className="eyebrow">Workspace administration</p><h1>Merchants</h1><p>Provider mappings and observed dispute-ratio information.</p></div><Button onClick={() => { setOpen(!open); setCreated(null); }}><Plus />Add merchant</Button></header>
    {open ? <Panel title="Create merchant"><form className="form-grid" onSubmit={submit}>
      <label>Merchant ID<input name="merchant_id" required maxLength={100} /></label><label>Name<input name="name" required maxLength={200} /></label>
      <label>Vertical<select name="vertical"><option value="ecommerce">E-commerce</option><option value="food_delivery">Food delivery</option><option value="quick_commerce">Quick commerce</option></select></label>
      <label>Payment provider<select name="payment_provider"><option value="">None</option><option value="razorpay">Razorpay</option><option value="stripe">Stripe</option></select></label>
      <label>Razorpay account ID<input name="razorpay_account_id" maxLength={100} /></label><label>Average order value<input name="average_order_value" type="number" min="0" step="0.01" defaultValue="0" /></label>
      <label>Store URL<input name="store_url" type="url" maxLength={2048} value={storeUrl} onChange={(event) => { setStoreUrl(event.target.value); setPlatformOverridden(false); }} onBlur={() => { if (storeUrl) suggestion.mutate(storeUrl); }} /></label>
      <label>Storefront platform<select name="storefront_platform" value={platform} onChange={(event) => { setPlatform(event.target.value as Platform); setPlatformOverridden(true); }}><option value="unknown">Unknown</option><option value="shopify">Shopify</option><option value="woocommerce">WooCommerce</option><option value="custom">Custom build</option></select><small>{suggestion.isPending ? "Detecting platform…" : "Suggested from the store URL; you can override it."}</small></label>
      {platform === "shopify" ? <label>Shopify Admin API token<input name="shopify_admin_api_token" type="password" autoComplete="new-password" required /></label> : null}
      {platform === "woocommerce" ? <><label>WooCommerce API key<input name="woocommerce_api_key" type="password" autoComplete="new-password" required /></label><label>WooCommerce API secret<input name="woocommerce_api_secret" type="password" autoComplete="new-password" required /></label></> : null}
      <div className="button-row"><Button type="button" variant="ghost" onClick={() => setOpen(false)}>Cancel</Button><Button type="submit" loading={mutation.isPending}>Create merchant</Button></div>
      {created ? <p role="status">Credential verification: {created.platform_credential_verified ? "passed" : created.platform_credential_verification_reason || "not submitted"}.</p> : null}
      {mutation.error ? <ErrorState error={mutation.error} /> : null}{suggestion.error ? <ErrorState error={suggestion.error} /> : null}
    </form></Panel> : null}
    {query.isLoading ? <Skeleton lines={7} /> : query.error ? <ErrorState error={query.error} retry={() => void query.refetch()} /> : query.data?.length ? <div className="merchant-grid">{query.data.map((merchant) => <Panel key={merchant.merchant_id}>
      <div className="merchant-head"><span><Building2 /></span><div><h2>{merchant.name}</h2><small>{merchant.merchant_id}</small></div><Badge>{merchant.vertical.replaceAll("_", " ")}</Badge></div>
      <dl className="detail-grid"><div><dt>Payment provider</dt><dd>{merchant.payment_provider || "Not configured"}</dd></div><div><dt>Razorpay account</dt><dd>{merchant.razorpay_account_id || "Not mapped"}</dd></div><div><dt>Storefront</dt><dd>{merchant.storefront_platform}</dd></div><div><dt>Store credential</dt><dd>{merchant.platform_credential_verified ? "Verified" : "Not verified"}</dd></div><div><dt>Average order value</dt><dd>{formatMoney(merchant.average_order_value, "INR")}</dd></div><div><dt>Prior chargebacks</dt><dd>{merchant.chargeback_history_count}</dd></div></dl>
      {Object.entries(merchant.merchant_dispute_ratio).length ? <div className="ratio-list">{Object.entries(merchant.merchant_dispute_ratio).map(([network, ratio]) => <span key={network}><strong>{network}</strong><Badge tone={ratio.status === "OK" ? "success" : ratio.status === "WARNING" ? "warning" : "neutral"}>{ratio.status}</Badge><small>{formatPercent(ratio.current_ratio_pct == null ? null : ratio.current_ratio_pct / 100)}</small></span>)}</div> : <p className="muted">No transaction-volume ratios configured.</p>}
    </Panel>)}</div> : <EmptyState title="No merchants configured">Create a merchant to establish an operational workspace.</EmptyState>}
  </>;
}
