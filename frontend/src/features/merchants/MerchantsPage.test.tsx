import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Merchant, PaymentConnector } from "../../api/schemas";
import { PaymentConnection } from "./MerchantsPage";

const mockClient = vi.hoisted(() => ({
  paymentConnectors: vi.fn(),
  connectRazorpay: vi.fn(),
  connectStripe: vi.fn(),
  verifyPaymentConnector: vi.fn(),
  disconnectPaymentConnector: vi.fn(),
}));

vi.mock("../../app/ConnectionContext", () => ({
  useConnection: () => ({ client: mockClient }),
}));

const merchant = {
  merchant_id: "merchant-ui",
  name: "UI Merchant",
  vertical: "ecommerce",
  payment_provider: null,
  payment_connector_id: null,
  payment_connector_ids: {},
  freshdesk_domain: "",
  average_order_value: 100,
  chargeback_history_count: 0,
  transaction_volume_30d_by_network: {},
  merchant_dispute_ratio: {},
  storefront_platform: "unknown",
  platform_credential_verified: false,
} as Merchant;

const connector = {
  connector_id: "paycon-ui",
  merchant_id: "merchant-ui",
  provider: "razorpay",
  provider_account_id: "acc_UI123",
  status: "verified",
  credential_hint: "ending in 1234",
  verified_at: "2026-09-04T12:00:00Z",
  created_at: "2026-09-04T12:00:00Z",
  updated_at: "2026-09-04T12:00:00Z",
  last_error_code: null,
} as PaymentConnector;

function renderConnection(connectors: PaymentConnector[] = []) {
  mockClient.paymentConnectors.mockResolvedValue(connectors);
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(<QueryClientProvider client={queryClient}><PaymentConnection merchant={merchant} /></QueryClientProvider>);
  return queryClient;
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
});

describe("merchant payment connection", () => {
  it("renders only safe connector status and metadata", async () => {
    renderConnection([connector]);

    expect(await screen.findByText("ending in 1234")).toBeInTheDocument();
    expect(screen.getByText("acc_UI123")).toBeInTheDocument();
    expect(screen.getByText("verified")).toBeInTheDocument();
    expect(screen.queryByLabelText("Razorpay Key Secret")).not.toBeInTheDocument();
  });

  it("submits Razorpay credentials, clears them, and never caches or stores them", async () => {
    mockClient.connectRazorpay.mockResolvedValue(connector);
    const queryClient = renderConnection();
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Razorpay Key ID"), "rzp_test_UI1234");
    await user.type(screen.getByLabelText("Razorpay Key Secret"), "ui-super-secret");
    await user.type(screen.getByLabelText("Razorpay Account ID"), "acc_UI123");
    await user.click(screen.getByRole("button", { name: "Connect and verify" }));

    await waitFor(() => expect(mockClient.connectRazorpay).toHaveBeenCalledWith(
      "merchant-ui", "rzp_test_UI1234", "ui-super-secret", "acc_UI123",
    ));
    expect(screen.queryByDisplayValue("ui-super-secret")).not.toBeInTheDocument();
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((item) => item.state.data))).not.toContain("ui-super-secret");
    expect(JSON.stringify(localStorage)).not.toContain("ui-super-secret");
    expect(JSON.stringify(sessionStorage)).not.toContain("ui-super-secret");
  });

  it("submits Stripe credentials through the Stripe-only request", async () => {
    mockClient.connectStripe.mockResolvedValue({ ...connector, provider: "stripe", provider_account_id: "acct_UI" });
    renderConnection();
    const user = userEvent.setup();
    await user.selectOptions(await screen.findByLabelText("Provider"), "stripe");
    await user.type(screen.getByLabelText("Stripe Secret API Key"), "sk_test_StripeUI1");
    await user.click(screen.getByRole("button", { name: "Connect and verify" }));

    await waitFor(() => expect(mockClient.connectStripe).toHaveBeenCalledWith("merchant-ui", "sk_test_StripeUI1"));
    expect(mockClient.connectRazorpay).not.toHaveBeenCalled();
    expect(screen.queryByDisplayValue("sk_test_StripeUI1")).not.toBeInTheDocument();
  });

  it("shows safe verification failures", async () => {
    mockClient.verifyPaymentConnector.mockRejectedValue(new Error("provider_unavailable"));
    renderConnection([connector]);
    await userEvent.click(await screen.findByRole("button", { name: "Test connection" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("provider_unavailable");
  });

  it("shows a failed connection without displaying the submitted secret", async () => {
    mockClient.connectRazorpay.mockResolvedValue({ ...connector, status: "invalid", last_error_code: "provider_authentication_failed" });
    renderConnection();
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Razorpay Key ID"), "rzp_test_UI1234");
    await user.type(screen.getByLabelText("Razorpay Key Secret"), "rejected-secret");
    await user.type(screen.getByLabelText("Razorpay Account ID"), "acc_UI123");
    await user.click(screen.getByRole("button", { name: "Connect and verify" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("provider_authentication_failed");
    expect(screen.queryByText("rejected-secret")).not.toBeInTheDocument();
  });

  it("requires confirmation before disconnecting", async () => {
    mockClient.disconnectPaymentConnector.mockResolvedValue({ ...connector, status: "disconnected" });
    renderConnection([connector]);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Disconnect" }));
    expect(mockClient.disconnectPaymentConnector).not.toHaveBeenCalled();
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Disconnect" }));
    await waitFor(() => expect(mockClient.disconnectPaymentConnector).toHaveBeenCalledWith("merchant-ui", "paycon-ui"));
  });
});
