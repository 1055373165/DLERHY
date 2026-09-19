import { QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";

import { queryClient } from "../../app/queryClient";
import { BudgetMeter } from "../../components/BudgetMeter";
import type { Issue } from "../../lib/api";
import { StructureEditForm } from "./StructureEditForm";

const DOCUMENT_ID = "11111111-1111-4111-8111-111111111111";

const ISSUE = {
  id: "i1",
  document_id: DOCUMENT_ID,
  issue_type: "STRUCTURE_SUGGESTION",
  root_cause_layer: "structure",
  severity: "medium",
  blocking: false,
  detector: "model",
  status: "open",
  version: 1,
  reopen_count: 0,
  evidence_json: { kind: "bad_split", block_ids: ["b1", "b2"], suggestion: "one paragraph cut at the page break", page_number: 4 },
} as unknown as Issue;

function json(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), { status, headers: { "Content-Type": "application/json" } });
}

function wrap(node: React.ReactNode) {
  return render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>);
}

describe("StructureEditForm", () => {
  it("prefills a merge from a bad_split suggestion and posts it", async () => {
    const bodies: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        expect(String(input)).toContain(`/v1/documents/${DOCUMENT_ID}/structure-edits`);
        bodies.push(JSON.parse(String(init?.body)));
        return json({ edit_id: "e1", kind: "merge_blocks", status: "applied", actor_id: "api:local", parse_revision_version: 3, retranslate_packet_count: 1 }, 201);
      }),
    );
    wrap(<StructureEditForm documentId={DOCUMENT_ID} issue={ISSUE} />);
    expect(screen.getByLabelText("结构编辑")).toHaveValue("merge_blocks");
    expect(screen.getByLabelText("前一块")).toHaveValue("b1");
    expect(screen.getByLabelText("后一块")).toHaveValue("b2");
    fireEvent.click(screen.getByRole("button", { name: "应用编辑" }));
    await screen.findByText(/解析版本 v3.*1 个 packet 需要重译/);
    expect(bodies[0]).toEqual({ kind: "merge_blocks", reason: "one paragraph cut at the page break", first_block_id: "b1", second_block_id: "b2" });
  });

  it("needs split text before a split can be applied and shows server rejections", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json({ detail: "second_part_starts_with occurs more than once; quote a longer piece" }, 409)),
    );
    wrap(<StructureEditForm documentId={DOCUMENT_ID} issue={{ ...ISSUE, evidence_json: { kind: "bad_merge", block_ids: ["b9"] } } as Issue} />);
    const apply = screen.getByRole("button", { name: "应用编辑" });
    fireEvent.change(screen.getByLabelText("理由"), { target: { value: "two paragraphs" } });
    expect(apply).toBeDisabled();
    fireEvent.change(screen.getByLabelText("第二块开头的原文"), { target: { value: "Tools" } });
    expect(apply).toBeEnabled();
    fireEvent.click(apply);
    await screen.findByText(/occurs more than once/);
  });
});

describe("BudgetMeter", () => {
  it("shows spend against the cap and warns when it is used up", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        json({ org_id: "o", org_name: "acme", monthly_budget_usd: 5, spent_usd: 6, remaining_usd: 0, exhausted: true, period_start: "2026-09-01T00:00:00+00:00" }),
      ),
    );
    queryClient.removeQueries({ queryKey: ["org-budget"] });
    wrap(<BudgetMeter />);
    expect(await screen.findByText("$6.00 / $5.00")).toBeInTheDocument();
    expect(screen.getByRole("meter", { name: "本月预算用量" })).toHaveAttribute("aria-valuenow", "100");
    expect(screen.getByText(/预算已用完/)).toBeInTheDocument();
  });

  it("stays hidden when the server does not answer", async () => {
    const fetchMock = vi.fn(async () => json({ detail: "Not Found" }, 404));
    vi.stubGlobal("fetch", fetchMock);
    queryClient.removeQueries({ queryKey: ["org-budget"] });
    const { container } = wrap(<BudgetMeter />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
