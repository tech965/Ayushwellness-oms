import { beforeEach, describe, expect, it, vi } from "vitest"
import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import { ChatPanel } from "@/components/ai-assistant/chat-panel"
import { useChatSuggestions, useSendChat } from "@/services/chat"
import type { ChatApiResponse } from "@/types/chat"

vi.mock("@/services/chat", () => ({
  useSendChat: vi.fn(),
  useChatSuggestions: vi.fn(),
}))

const mockedUseSendChat = vi.mocked(useSendChat)
const mockedUseSuggestions = vi.mocked(useChatSuggestions)

function apiResponse(overrides: Partial<ChatApiResponse> = {}): ChatApiResponse {
  return {
    answer: "Today's orders: 3. Revenue: ₹3,500.",
    ok: true,
    partial: false,
    tools_used: ["get_operations_summary"],
    sources: ["OMS database (synced from Shopify)"],
    data: {},
    error_code: null,
    conversation_id: "conv-1",
    model: "scripted",
    latency_ms: 812,
    timestamp: "2026-09-02T05:00:00Z",
    ...overrides,
  }
}

function setup(mutateAsync: ReturnType<typeof vi.fn>) {
  mockedUseSuggestions.mockReturnValue({ data: [] } as never)
  mockedUseSendChat.mockReturnValue({
    mutateAsync,
    isPending: false,
    reset: vi.fn(),
  } as never)
}

beforeEach(() => {
  window.localStorage.clear()
  vi.clearAllMocks()
})

describe("ChatPanel", () => {
  it("shows the empty state with fallback suggestions", () => {
    setup(vi.fn())
    renderWithProviders(<ChatPanel />)
    expect(screen.getByText("Ask about your live OMS data")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Today's revenue" })).toBeInTheDocument()
  })

  it("sends a question and renders the grounded answer with its data source", async () => {
    const mutateAsync = vi.fn().mockResolvedValue(apiResponse())
    setup(mutateAsync)
    renderWithProviders(<ChatPanel />)

    await userEvent.type(
      screen.getByLabelText("Message the OMS assistant"),
      "How many orders today?"
    )
    await userEvent.click(screen.getByRole("button", { name: "Send" }))

    expect(await screen.findByText("How many orders today?")).toBeInTheDocument()
    expect(await screen.findByText(/Today's orders: 3/)).toBeInTheDocument()
    expect(
      screen.getByText(/OMS database \(synced from Shopify\)/)
    ).toBeInTheDocument()

    expect(mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({ message: "How many orders today?", history: [] })
    )
  })

  it("marks a partial answer", async () => {
    const mutateAsync = vi
      .fn()
      .mockResolvedValue(apiResponse({ partial: true, answer: "Orders today: 3." }))
    setup(mutateAsync)
    renderWithProviders(<ChatPanel />)

    await userEvent.type(screen.getByLabelText("Message the OMS assistant"), "summary")
    await userEvent.click(screen.getByRole("button", { name: "Send" }))

    expect(await screen.findByText("partial data")).toBeInTheDocument()
  })

  it("shows a Retry action when the request fails", async () => {
    const mutateAsync = vi.fn().mockRejectedValue(new Error("network down"))
    setup(mutateAsync)
    renderWithProviders(<ChatPanel />)

    await userEvent.type(screen.getByLabelText("Message the OMS assistant"), "revenue today?")
    await userEvent.click(screen.getByRole("button", { name: "Send" }))

    expect(await screen.findByText("network down")).toBeInTheDocument()
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Retry/ })).toBeInTheDocument()
    )
  })

  it("clears the conversation", async () => {
    const mutateAsync = vi.fn().mockResolvedValue(apiResponse())
    setup(mutateAsync)
    renderWithProviders(<ChatPanel />)

    await userEvent.type(screen.getByLabelText("Message the OMS assistant"), "orders today?")
    await userEvent.click(screen.getByRole("button", { name: "Send" }))
    expect(await screen.findByText(/Today's orders: 3/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole("button", { name: /Clear/ }))
    expect(screen.getByText("Ask about your live OMS data")).toBeInTheDocument()
  })
})
