import { describe, expect, it } from "vitest";

import type { DocumentRunSummary, RunPipelineDetail } from "./api";
import { awaitingApprovalStage, pipelineStageLabel, stageStatus } from "./workflow";

function runWith(pipeline: RunPipelineDetail): DocumentRunSummary {
  return {
    run_id: "run-1",
    document_id: "doc-1",
    run_type: "translate_full",
    status: "running",
    priority: 100,
    status_detail_json: { pipeline },
    created_at: "2026-09-19T12:00:00Z",
    updated_at: "2026-09-19T12:00:00Z",
  } as DocumentRunSummary;
}

describe("pipeline stage status", () => {
  it("reads the stage details the server writes under _cached_pipeline_stages", () => {
    const run = runWith({
      current_stage: "translate",
      _cached_pipeline_stages: { terminology: { status: "succeeded" }, translate: { status: "running" } },
    });
    expect(stageStatus(null, run, "terminology")).toBe("succeeded");
    expect(stageStatus(null, run, "translate")).toBe("running");
  });

  it("still reads runs written before the rename", () => {
    const run = runWith({ current_stage: "translate", stages: { translate: { status: "paused" } } });
    expect(stageStatus(null, run, "translate")).toBe("paused");
  });

  it("says a running agent stage is waiting for approval", () => {
    const run = runWith({
      current_stage: "terminology",
      _cached_pipeline_stages: {
        terminology: { status: "running", turn_status: "awaiting_approval", stop_reason: "approval:lock_term" },
      },
    });
    expect(stageStatus(null, run, "terminology")).toBe("awaiting_approval");
    expect(awaitingApprovalStage(null, run)?.label).toBe("术语与 BOOK.md");
    expect(pipelineStageLabel("terminology")).toBe("术语与 BOOK.md");
  });

  it("finds no approval stage while agents are simply running", () => {
    const run = runWith({ current_stage: "terminology", _cached_pipeline_stages: { terminology: { status: "running" } } });
    expect(awaitingApprovalStage(null, run)).toBeNull();
  });
});
