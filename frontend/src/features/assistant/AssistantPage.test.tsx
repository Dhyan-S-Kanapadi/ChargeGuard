import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { AssistantPage } from "./AssistantPage";

const client = vi.hoisted(() => ({ llmStatus: vi.fn(), askAssistant: vi.fn() }));
vi.mock("../../app/ConnectionContext", () => ({ useConnection: () => ({ client }) }));

beforeEach(() => {
  vi.resetAllMocks();
  client.llmStatus.mockResolvedValue({
    guard_ai: { mode: "live_configured", model: "test-model" },
    decision_review: { mode: "disabled", model: null },
  });
});

function showAssistant() {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <AssistantPage />
  </QueryClientProvider>);
}

it("shows configuration status and submits scoped chat questions", async () => {
  client.askAssistant.mockResolvedValue({ answer: "Synthetic case has elevated device risk.",
    based_on: { dispute_count: 1, stats_snapshot: true } });
  showAssistant();
  expect(await screen.findByText("Live LLM configured")).toBeInTheDocument();
  expect(screen.getByText("Disabled")).toBeInTheDocument();
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Optional chargeback context"), "disp_SIM_demo");
  await user.click(screen.getByRole("button", { name: "Explain the device-risk signals in the supplied cases." }));
  await user.click(screen.getByRole("button", { name: /Ask Guard AI/ }));
  expect(await screen.findByText("Synthetic case has elevated device risk.")).toBeInTheDocument();
  expect(client.askAssistant).toHaveBeenCalledWith(
    "Explain the device-risk signals in the supplied cases.", "disp_SIM_demo", expect.any(AbortSignal),
  );
});

it("shows an unavailable error and can retry without inventing an answer", async () => {
  client.askAssistant.mockRejectedValueOnce(new Error("Portfolio assistant is unavailable."))
    .mockResolvedValueOnce({ answer: "Supplied synthetic context only.",
      based_on: { dispute_count: 0, stats_snapshot: true } });
  showAssistant();
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Your question"), "Summarize");
  await user.click(screen.getByRole("button", { name: /Ask Guard AI/ }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Portfolio assistant is unavailable.");
  await user.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() => expect(screen.getByText("Supplied synthetic context only.")).toBeInTheDocument());
});
