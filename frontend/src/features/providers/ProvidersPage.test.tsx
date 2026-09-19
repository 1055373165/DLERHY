import { QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";

import { queryClient } from "../../app/queryClient";
import { ProvidersPage } from "./ProvidersPage";

const ACTIVE = {
  id: "p1",
  name: "DeepSeek",
  provider_kind: "openai_compatible",
  model_name: "deepseek-v4-flash",
  base_url: "https://api.deepseek.com/v1",
  is_active: true,
  shared: true,
  api_key_preview: "sk-****-1234",
  max_output_tokens: 8192,
  input_cost_per_1m_tokens: 0.27,
  input_cache_hit_cost_per_1m_tokens: null,
  output_cost_per_1m_tokens: 1.1,
  request_overrides: { thinking: { type: "disabled" } },
  last_test_status: "untested",
};

function json(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), { status, headers: { "Content-Type": "application/json" } });
}

function renderPage() {
  queryClient.removeQueries({ queryKey: ["providers"] });
  return render(
    <QueryClientProvider client={queryClient}>
      <ProvidersPage />
    </QueryClientProvider>,
  );
}

describe("ProvidersPage", () => {
  it("shows prices and the thinking switch, and creates a provider with both", async () => {
    const posts: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === "POST") {
          posts.push(JSON.parse(String(init.body)));
          return json({ ...ACTIVE, id: "p2", name: "Kimi", is_active: false }, 201);
        }
        return json([ACTIVE]);
      }),
    );
    renderPage();
    expect(await screen.findByText("DeepSeek")).toBeInTheDocument();
    expect(screen.getByText(/输入 \$0.27 \/ 输出 \$1.1/)).toBeInTheDocument();
    expect(screen.getByText(/已关闭思考模式/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "添加服务商" }));
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "Kimi" } });
    fireEvent.change(screen.getByLabelText("模型"), { target: { value: "kimi-k2" } });
    fireEvent.change(screen.getByLabelText("API key"), { target: { value: "sk-new" } });
    fireEvent.change(screen.getByLabelText("输入"), { target: { value: "0.6" } });
    fireEvent.change(screen.getByLabelText("输出"), { target: { value: "2.5" } });
    fireEvent.click(screen.getByLabelText(/关闭思考模式/));
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("已保存「Kimi」。");
    expect(posts[0]).toMatchObject({
      name: "Kimi",
      model_name: "kimi-k2",
      api_key: "sk-new",
      input_cost_per_1m_tokens: 0.6,
      output_cost_per_1m_tokens: 2.5,
      input_cache_hit_cost_per_1m_tokens: null,
      request_overrides: { thinking: { type: "disabled" } },
      activate: false,
    });
  });

  it("rejects invalid advanced parameters before sending anything", async () => {
    const fetchMock = vi.fn(async () => json([]));
    vi.stubGlobal("fetch", fetchMock);
    renderPage();
    await screen.findByText(/还没有服务商/);
    fireEvent.click(screen.getByRole("button", { name: "添加服务商" }));
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "X" } });
    fireEvent.change(screen.getByLabelText("模型"), { target: { value: "m" } });
    fireEvent.change(screen.getByLabelText(/高级请求参数/), { target: { value: "{not json" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("高级请求参数不是合法的 JSON");
    await waitFor(() => expect(fetchMock.mock.calls.every(([, init]) => !init || (init as RequestInit).method !== "POST")).toBe(true));
  });
});
