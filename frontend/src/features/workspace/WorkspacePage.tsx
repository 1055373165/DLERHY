import { useEffect, useState } from "react";

import { useWorkspace } from "../../app/WorkspaceContext";
import { StatusBadge } from "../../components/StatusBadge";
import { Surface } from "../../components/Surface";
import type { ChapterWorklistTimelineEntry, ExecuteActionResponse } from "../../lib/api";
import {
  documentBadge,
  formatDate,
  formatNumber,
  getPrimaryRunAction,
  nextMilestoneText,
  preferredTitle,
  shorten,
  sourceLabel,
  statusLabel,
} from "../../lib/workflow";
import styles from "./WorkspacePage.module.css";

type MessageTone = "success" | "error";
type WorkbenchMode = "focused" | "flow";
type QueueOutcomeFilter = "all" | "release-ready" | "observe";
type TimelineFocusTarget =
  | {
      eventId: string;
      section: "actions";
      actionId?: string | null;
      label: string;
      helper: string;
    }
  | {
      eventId: string;
      section: "assignment";
      label: string;
      helper: string;
    }
  | {
      eventId: string;
      section: "proposal";
      proposalId?: string | null;
      label: string;
      helper: string;
    };
type OperatorConvergenceSnapshot = {
  pendingProposalCount: number;
  activeSnapshotVersion: number | null;
  ownerName: string;
  actionStatus: string;
};
type RecentOperatorChange = {
  chapterId: string;
  kind: "proposal" | "assignment" | "action";
  title: string;
  body: string;
  highlights: string[];
  before: OperatorConvergenceSnapshot | null;
  eventToken: string;
};
type PendingChapterFocus = {
  chapterId: string;
  section: TimelineFocusTarget["section"];
  label: string;
  helper: string;
};
type RecommendedNextStep = {
  title: string;
  body: string;
  actionKind: "proposal" | "assignment" | "action" | null;
  actionLabel?: string;
};
type NextQueueRecommendation = {
  title: string;
  body: string;
  actionLabel: string;
  focus: PendingChapterFocus;
};
type SessionTrailEntry = {
  chapterId: string;
  chapterLabel: string;
  changeTitle: string;
  summary: string;
  kind: RecentOperatorChange["kind"];
  chainLabel: string;
  revisitHint: string;
};
type SessionDigest = {
  processedCount: number;
  latestChapterLabel: string;
  latestChainLabel: string;
  kindSummary: string[];
  continuityHint: string;
};
type FlowHandoff = {
  targetChapterId: string;
  sourceChapterLabel: string;
  targetChapterLabel: string;
  reasonTitle: string;
  reasonBody: string;
  completedStepCount: number;
  lastProgressToken: string | null;
};
type FlowHandoffStep = {
  orderLabel: string;
  title: string;
  value: string;
  helper: string;
  section: TimelineFocusTarget["section"];
  actionLabel: string;
};
type FocusedPriorityItem = {
  rankLabel: string;
  label: string;
  value: string;
  hint: string;
  section: TimelineFocusTarget["section"];
  actionLabel: string;
};
type QueueLensPriority = {
  title: string;
  value: string;
  helper: string;
  section: TimelineFocusTarget["section"];
  actionLabel: string;
};
type ReleaseGateSummary = {
  statusLabel: string;
  helper: string;
  checks: Array<{
    label: string;
    value: string;
    passed: boolean;
  }>;
};
type ReleaseLaneFallback = {
  chapterId: string;
  chapterLabel: string;
  helper: string;
  chips: string[];
  focus: PendingChapterFocus;
};
type ReleaseLaneDecision = {
  statusLabel: string;
  helper: string;
  actionLabel: string;
};
type ReleaseLaneBatchSummary = {
  statusLabel: string;
  helper: string;
};
type ReleaseLaneResultFeedback = {
  statusLabel: string;
  helper: string;
};
type ReleaseLaneContinuationFeedback = {
  statusLabel: string;
  helper: string;
};
type ReleaseLaneExitStrategy = {
  statusLabel: string;
  helper: string;
  actionLabel: string;
  actionKind: "observe-current" | "observe-fallback" | "next-release" | "reset";
};
type ReleaseLaneCompletionState = {
  statusLabel: string;
  helper: string;
  queueHint: string;
};
type ReleaseLaneBatchPhase = {
  statusLabel: string;
  helper: string;
  queueHint: string;
};
type ReleaseLaneBatchDigest = {
  statusLabel: string;
  helper: string;
  queueHint: string;
};
type ReleaseLanePressure = {
  statusLabel: string;
  helper: string;
  chips: string[];
};
type ReleaseLaneConfidence = {
  statusLabel: string;
  helper: string;
  chips: string[];
};
type ReleaseLaneHealthSummary = {
  statusLabel: string;
  helper: string;
  chips: string[];
};
type ReleaseLaneDrift = {
  statusLabel: string;
  helper: string;
  chips: string[];
};
type ReleaseLanePressureAction = {
  statusLabel: string;
  helper: string;
  actionLabel: string;
  actionKind: "continue-release" | "switch-observe" | "reset";
};
type ReleaseLaneRoutingCue = {
  statusLabel: string;
  helper: string;
  actionLabel: string;
  source: "pressure" | "exit";
  chips: string[];
};
type ReleaseLaneEntryCue = {
  statusLabel: string;
  helper: string;
  chips: string[];
};
type QueueLensPreset = {
  key: string;
  label: string;
  helper: string;
  assignment: "all" | "assigned" | "unassigned";
  ownerName: string;
  outcome: QueueOutcomeFilter;
  count: number;
};

const STORAGE_KEY_WORKBENCH_MODE = "book-agent.workbench-mode";

function readInitialWorkbenchMode(): WorkbenchMode {
  if (typeof window === "undefined") {
    return "flow";
  }
  const persisted = window.localStorage.getItem(STORAGE_KEY_WORKBENCH_MODE);
  return persisted === "focused" || persisted === "flow" ? persisted : "flow";
}

export function WorkspacePage() {
  const {
    currentDocument,
    currentRun,
    currentExports,
    chapterWorklist,
    chapterWorklistLoading,
    chapterWorklistError,
    currentChapterReviewDetail,
    currentChapterReviewError,
    currentChapterReviewLoading,
    currentDocumentError,
    selectedReviewChapterId,
    selectReviewChapter,
    chapterWorklistFilters,
    setChapterQueuePriorityFilter,
    setChapterAssignmentFilter,
    setChapterAssignedOwnerFilter,
    clearChapterWorklistFilters,
    uploadPending,
    runActionPending,
    reviewDecisionPending,
    assignmentPending,
    actionExecutionPending,
    approveMemoryProposal,
    rejectMemoryProposal,
    assignChapterOwner,
    clearChapterAssignment,
    executeChapterAction,
    uploadFile,
    runPrimaryAction,
    refreshCurrentDocument,
  } = useWorkspace();
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploadMessage, setUploadMessage] = useState<{ tone: MessageTone; text: string } | null>(null);
  const [actionMessage, setActionMessage] = useState<{ tone: MessageTone; text: string } | null>(null);
  const [reviewMessage, setReviewMessage] = useState<{ tone: MessageTone; text: string } | null>(null);
  const [lastActionExecution, setLastActionExecution] = useState<{
    result: ExecuteActionResponse;
    createdAt: string;
  } | null>(null);
  const [timelineFocus, setTimelineFocus] = useState<TimelineFocusTarget | null>(null);
  const [pendingChapterFocus, setPendingChapterFocus] = useState<PendingChapterFocus | null>(null);
  const [recentOperatorChange, setRecentOperatorChange] = useState<RecentOperatorChange | null>(null);
  const [sessionTrail, setSessionTrail] = useState<SessionTrailEntry[]>([]);
  const [flowHandoff, setFlowHandoff] = useState<FlowHandoff | null>(null);
  const [workbenchMode, setWorkbenchMode] = useState<WorkbenchMode>(() => readInitialWorkbenchMode());
  const [queueOutcomeFilter, setQueueOutcomeFilter] = useState<QueueOutcomeFilter>("all");
  const [releaseSignalExpanded, setReleaseSignalExpanded] = useState(false);
  const [reviewerName, setReviewerName] = useState("reviewer-ui");
  const [reviewerNote, setReviewerNote] = useState("");
  const [assignmentOwner, setAssignmentOwner] = useState("");

  const action = getPrimaryRunAction(currentDocument, currentRun);
  const badge = documentBadge(currentDocument, currentRun);
  const queueEntries = chapterWorklist?.entries ?? [];
  const visibleQueueEntries = queueEntries.filter((entry) => queueOutcomeMatchesFilter(entry, queueOutcomeFilter));
  const ownerWorkloads = chapterWorklist?.owner_workload_summary ?? [];
  const hasActiveQueueFilters =
    chapterWorklistFilters.queuePriority !== "all" ||
    chapterWorklistFilters.assignment !== "all" ||
    Boolean(chapterWorklistFilters.assignedOwnerName);
  const activeQueueFilters = buildActiveQueueFilters(chapterWorklistFilters);
  const selectedOwnerWorkload =
    ownerWorkloads.find((owner) => owner.owner_name === chapterWorklistFilters.assignedOwnerName) ?? null;
  const queueReleaseReadyCount = queueEntries.filter((entry) => isQueueEntryReleaseReady(entry)).length;
  const queueObserveCount = Math.max(queueEntries.length - queueReleaseReadyCount, 0);
  const visibleReleaseReadyCount = visibleQueueEntries.filter((entry) => isQueueEntryReleaseReady(entry)).length;
  const visibleObserveCount = Math.max(visibleQueueEntries.length - visibleReleaseReadyCount, 0);
  const sharedQueueEntries = queueEntries.filter((entry) => !entry.is_assigned);
  const sharedReleaseReadyCount = sharedQueueEntries.filter((entry) => isQueueEntryReleaseReady(entry)).length;
  const sharedObserveCount = Math.max(sharedQueueEntries.length - sharedReleaseReadyCount, 0);
  const selectedOwnerQueueEntries = selectedOwnerWorkload
    ? queueEntries.filter((entry) => entry.assigned_owner_name === selectedOwnerWorkload.owner_name)
    : [];
  const selectedOwnerReleaseReadyCount = selectedOwnerQueueEntries.filter((entry) =>
    isQueueEntryReleaseReady(entry)
  ).length;
  const selectedOwnerObserveCount = Math.max(
    selectedOwnerQueueEntries.length - selectedOwnerReleaseReadyCount,
    0
  );
  const operatorLenses = buildQueueLensPresets({
    sharedReleaseReadyCount,
    sharedObserveCount,
    selectedOwnerName: selectedOwnerWorkload?.owner_name ?? null,
    selectedOwnerReleaseReadyCount,
    selectedOwnerObserveCount,
  });
  const activeQueueLens =
    operatorLenses.find((lens) =>
      queueLensIsActive(
        lens,
        chapterWorklistFilters.assignment,
        chapterWorklistFilters.assignedOwnerName,
        queueOutcomeFilter
      )
    ) ?? null;
  const selectedQueueEntry =
    visibleQueueEntries.find((entry) => entry.chapter_id === selectedReviewChapterId) ?? null;
  const selectedQueueIndex = selectedQueueEntry
    ? visibleQueueEntries.findIndex((entry) => entry.chapter_id === selectedQueueEntry.chapter_id)
    : -1;
  const nextQueueEntry =
    selectedQueueIndex >= 0 && selectedQueueIndex < visibleQueueEntries.length - 1
      ? visibleQueueEntries[selectedQueueIndex + 1]
      : null;
  const nextQueueRecommendation = nextQueueEntry ? buildNextQueueRecommendation(nextQueueEntry) : null;
  const isFlowMode = workbenchMode === "flow";
  const timelineGroups = groupTimelineEntries(currentChapterReviewDetail?.timeline ?? []);
  const selectedChapterRecentChange =
    recentOperatorChange?.chapterId === selectedReviewChapterId ? recentOperatorChange : null;
  const sessionDigest = sessionTrail.length ? buildSessionDigest(sessionTrail) : null;
  const selectedChapterCurrentSnapshot = buildOperatorSnapshot(
    selectedQueueEntry,
    currentChapterReviewDetail
  );
  const selectedChapterConvergenceItems =
    selectedChapterRecentChange && selectedChapterCurrentSnapshot
      ? buildConvergenceItems(selectedChapterRecentChange.before, selectedChapterCurrentSnapshot)
      : [];
  const selectedChapterImpactedTimelineEventId =
    selectedChapterRecentChange && currentChapterReviewDetail
      ? currentChapterReviewDetail.timeline.find((entry) =>
          timelineEntryMatchesRecentChange(entry, selectedChapterRecentChange)
        )?.event_id ?? null
      : null;
  const selectedChapterNextStep = selectedChapterRecentChange
    ? buildRecentChangeNextStep(
        selectedChapterRecentChange,
        selectedChapterCurrentSnapshot,
        currentChapterReviewDetail
      )
    : null;
  const selectedQueueOutcome = selectedChapterRecentChange
    ? buildQueueOutcomeSummary(selectedChapterRecentChange, currentChapterReviewDetail)
    : null;
  const activeFlowHandoff =
    isFlowMode && flowHandoff?.targetChapterId === selectedReviewChapterId ? flowHandoff : null;
  const activeFlowHandoffSteps =
    activeFlowHandoff && currentChapterReviewDetail
      ? buildFlowHandoffSteps(currentChapterReviewDetail, selectedQueueEntry)
      : [];
  const activeFlowCompletedStepCount = activeFlowHandoff
    ? Math.min(activeFlowHandoff.completedStepCount, activeFlowHandoffSteps.length)
    : 0;
  const activeFlowCompletedSteps = activeFlowHandoffSteps.slice(0, activeFlowCompletedStepCount);
  const activeFlowCurrentStep = activeFlowHandoffSteps[activeFlowCompletedStepCount] ?? null;
  const activeFlowQueuedSteps = activeFlowCurrentStep
    ? activeFlowHandoffSteps.slice(activeFlowCompletedStepCount + 1)
    : [];
  const activeFlowExitReady = Boolean(activeFlowHandoff && !activeFlowCurrentStep && activeFlowCompletedSteps.length);
  const focusedPriorityItems =
    !isFlowMode && currentChapterReviewDetail
      ? buildFocusedPriorityItems(currentChapterReviewDetail, selectedQueueEntry)
      : [];
  const activeQueueLensPriority =
    isFlowMode && activeQueueLens && currentChapterReviewDetail
      ? buildQueueLensPriority(activeQueueLens, currentChapterReviewDetail, selectedQueueEntry)
      : null;
  const activeReleaseGate =
    isFlowMode &&
    activeQueueLens?.outcome === "release-ready" &&
    currentChapterReviewDetail
      ? buildReleaseGateSummary(currentChapterReviewDetail)
      : null;
  const activeReleaseGateFailures = activeReleaseGate?.checks.filter((check) => !check.passed) ?? [];
  const activeReleaseLaneObserveEntries =
    isFlowMode && activeQueueLens?.outcome === "release-ready"
      ? activeQueueLens.ownerName
        ? selectedOwnerQueueEntries.filter((entry) => !isQueueEntryReleaseReady(entry))
        : activeQueueLens.assignment === "unassigned"
          ? sharedQueueEntries.filter((entry) => !isQueueEntryReleaseReady(entry))
          : queueEntries.filter((entry) => !isQueueEntryReleaseReady(entry))
      : [];
  const activeReleaseLaneObserveCount = activeReleaseLaneObserveEntries.length;
  const releaseLaneObserveFallback =
    isFlowMode && activeQueueLens?.outcome === "release-ready" && activeReleaseLaneObserveCount
      ? buildReleaseLaneFallback(activeReleaseLaneObserveEntries)
      : null;
  const activeReleaseLaneDecision =
    isFlowMode && activeQueueLens?.outcome === "release-ready" && activeReleaseGate
      ? buildReleaseLaneDecision({
          hasGateFailures: activeReleaseGateFailures.length > 0,
          nextQueueEntry,
          observeFallback: releaseLaneObserveFallback,
        })
      : null;
  const activeReleaseLaneBatchSummary =
    isFlowMode && activeQueueLens?.outcome === "release-ready"
      ? buildReleaseLaneBatchSummary({
          visibleCount: visibleQueueEntries.length,
          selectedIndex: selectedQueueIndex,
          observeCount: queueObserveCount,
        })
      : null;
  const activeReleaseLaneResultFeedback =
    isFlowMode &&
    activeQueueLens?.outcome === "release-ready" &&
    selectedChapterRecentChange &&
    selectedQueueOutcome
      ? buildReleaseLaneResultFeedback({
          statusLabel: selectedQueueOutcome.statusLabel,
          chainLabel: selectedQueueOutcome.chainLabel,
          nextQueueEntry,
          observeFallback: releaseLaneObserveFallback,
        })
      : null;
  const activeReleaseLaneContinuationFeedback =
    isFlowMode &&
    activeQueueLens?.outcome === "release-ready" &&
    selectedChapterRecentChange &&
    selectedQueueOutcome
      ? buildReleaseLaneContinuationFeedback({
          statusLabel: selectedQueueOutcome.statusLabel,
          visibleCount: visibleQueueEntries.length,
          selectedIndex: selectedQueueIndex,
          observeCount: queueObserveCount,
          nextQueueEntry,
          observeFallback: releaseLaneObserveFallback,
        })
      : null;
  const activeReleaseLaneExitStrategy =
    isFlowMode &&
    activeQueueLens?.outcome === "release-ready" &&
    selectedChapterRecentChange &&
    selectedQueueOutcome
      ? buildReleaseLaneExitStrategy({
          statusLabel: selectedQueueOutcome.statusLabel,
          nextQueueEntry,
          observeFallback: releaseLaneObserveFallback,
        })
      : null;
  const activeReleaseLaneCompletionState =
    isFlowMode &&
    activeQueueLens?.outcome === "release-ready" &&
    selectedChapterRecentChange &&
    selectedQueueOutcome
      ? buildReleaseLaneCompletionState({
          statusLabel: selectedQueueOutcome.statusLabel,
          nextQueueEntry,
          observeFallback: releaseLaneObserveFallback,
        })
      : null;
  const activeReleaseLaneBatchPhase =
    isFlowMode &&
    activeQueueLens?.outcome === "release-ready" &&
    selectedChapterRecentChange &&
    selectedQueueOutcome
      ? buildReleaseLaneBatchPhase({
          statusLabel: selectedQueueOutcome.statusLabel,
          nextQueueEntry,
          observeFallback: releaseLaneObserveFallback,
        })
      : null;
  const activeReleaseLaneBatchDigest =
    isFlowMode &&
    activeQueueLens?.outcome === "release-ready" &&
    selectedChapterRecentChange &&
    selectedQueueOutcome
      ? buildReleaseLaneBatchDigest({
          statusLabel: selectedQueueOutcome.statusLabel,
          visibleCount: visibleQueueEntries.length,
          selectedIndex: selectedQueueIndex,
          observeCount: queueObserveCount,
        })
      : null;
  const activeReleaseLanePressure =
    isFlowMode && activeQueueLens?.outcome === "release-ready"
      ? buildReleaseLanePressure({
          visibleCount: visibleQueueEntries.length,
          observeCount: activeReleaseLaneObserveCount,
        })
      : null;
  const activeReleaseLaneConfidence =
    isFlowMode && activeQueueLens?.outcome === "release-ready"
      ? buildReleaseLaneConfidence({
          hasGateFailures: activeReleaseGateFailures.length > 0,
          visibleCount: visibleQueueEntries.length,
          observeCount: activeReleaseLaneObserveCount,
        })
      : null;
  const activeReleaseLaneDrift =
    isFlowMode && activeQueueLens?.outcome === "release-ready"
      ? buildReleaseLaneDrift({
          hasGateFailures: activeReleaseGateFailures.length > 0,
          visibleCount: visibleQueueEntries.length,
          observeCount: activeReleaseLaneObserveCount,
        })
      : null;
  const activeReleaseLanePressureAction =
    isFlowMode && activeQueueLens?.outcome === "release-ready"
      ? buildReleaseLanePressureAction({
          visibleCount: visibleQueueEntries.length,
          observeCount: activeReleaseLaneObserveCount,
        })
      : null;
  const activeReleaseLaneHealthSummary =
    isFlowMode &&
    activeQueueLens?.outcome === "release-ready" &&
    activeReleaseLanePressureAction &&
    activeReleaseLaneConfidence &&
    activeReleaseLaneDrift
      ? buildReleaseLaneHealthSummary({
          pressureActionKind: activeReleaseLanePressureAction.actionKind,
          confidenceStatus: activeReleaseLaneConfidence.statusLabel,
          driftStatus: activeReleaseLaneDrift.statusLabel,
        })
      : null;
  const activeReleaseLaneRoutingCue =
    isFlowMode &&
    activeQueueLens?.outcome === "release-ready" &&
    activeReleaseLaneHealthSummary &&
    (activeReleaseLaneExitStrategy || activeReleaseLanePressureAction)
      ? buildReleaseLaneRoutingCue({
          healthSummary: activeReleaseLaneHealthSummary,
          pressureAction: activeReleaseLanePressureAction,
          exitStrategy: activeReleaseLaneExitStrategy,
        })
      : null;
  const activeReleaseLaneEntryCue =
    isFlowMode &&
    activeQueueLens?.outcome === "release-ready" &&
    activeReleaseLaneRoutingCue &&
    activeReleaseLaneHealthSummary
      ? buildReleaseLaneEntryCue({
          routingCue: activeReleaseLaneRoutingCue,
          healthSummary: activeReleaseLaneHealthSummary,
          chapterLabel: selectedQueueEntry
            ? `第 ${selectedQueueEntry.ordinal} 章 · ${selectedQueueEntry.title_src || `Chapter ${selectedQueueEntry.ordinal}`}`
            : null,
        })
      : null;
  const releaseLaneHealthIsDecisive = Boolean(
    activeReleaseLaneHealthSummary && activeReleaseLaneHealthSummary.statusLabel !== "稳态推进"
  );
  const allowReleaseSignalToggle = Boolean(activeReleaseLaneHealthSummary && !selectedChapterRecentChange);
  const showCondensedReleaseSignals =
    Boolean(activeReleaseLaneHealthSummary) &&
    releaseLaneHealthIsDecisive &&
    !selectedChapterRecentChange &&
    !releaseSignalExpanded;
  const activeReleaseLaneSignalSnapshot =
    showCondensedReleaseSignals && activeReleaseLaneHealthSummary && activeReleaseLaneRoutingCue
      ? {
          statusLabel: "支持信号已收拢",
          helper: `当前路线建议已经足够明确：${activeReleaseLaneRoutingCue.statusLabel}。如需复核，再展开把握度 / 漂移 / 压力细节。`,
          chips: [
            `路线 · ${activeReleaseLaneRoutingCue.statusLabel}`,
            ...activeReleaseLaneHealthSummary.chips,
          ],
        }
      : null;
  const showReleaseLaneSessionDigest =
    isFlowMode &&
    activeQueueLens?.outcome === "release-ready" &&
    Boolean(activeReleaseLaneBatchDigest || activeReleaseLaneBatchPhase);
  const releaseLaneFallback =
    isFlowMode && activeQueueLens?.outcome === "release-ready" && !visibleQueueEntries.length
      ? buildReleaseLaneFallback(queueEntries)
      : null;
  const focusedPrimaryItem = !isFlowMode && focusedPriorityItems.length ? focusedPriorityItems[0] : null;
  const focusedSecondaryItems = focusedPrimaryItem ? focusedPriorityItems.slice(1) : [];
  const focusedActionEntry =
    timelineFocus?.section === "actions"
      ? currentChapterReviewDetail?.recent_actions.find(
          (entry) => entry.action_id === timelineFocus.actionId
        ) ?? null
      : null;
  const focusedProposal =
    timelineFocus?.section === "proposal"
      ? currentChapterReviewDetail?.memory_proposals.pending_proposals.find(
          (proposal) => proposal.proposal_id === timelineFocus.proposalId
        ) ?? null
      : null;
  const focusedAssignment = timelineFocus?.section === "assignment" ? currentChapterReviewDetail?.assignment : null;

  useEffect(() => {
    setAssignmentOwner(currentChapterReviewDetail?.assignment?.owner_name ?? "");
  }, [currentChapterReviewDetail?.assignment?.owner_name, selectedReviewChapterId]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    window.localStorage.setItem(STORAGE_KEY_WORKBENCH_MODE, workbenchMode);
  }, [workbenchMode]);

  useEffect(() => {
    setLastActionExecution(null);
    setTimelineFocus(null);
  }, [selectedReviewChapterId]);

  useEffect(() => {
    if (workbenchMode !== "flow") {
      setFlowHandoff(null);
    }
  }, [workbenchMode]);

  useEffect(() => {
    if (!allowReleaseSignalToggle) {
      setReleaseSignalExpanded(false);
      return;
    }
    setReleaseSignalExpanded(false);
  }, [allowReleaseSignalToggle, activeQueueLens?.key, activeReleaseLaneHealthSummary?.statusLabel, selectedReviewChapterId]);

  useEffect(() => {
    if (workbenchMode !== "flow") {
      return;
    }
    if (!visibleQueueEntries.length) {
      return;
    }
    const stillVisible = selectedReviewChapterId
      ? visibleQueueEntries.some((entry) => entry.chapter_id === selectedReviewChapterId)
      : false;
    if (stillVisible) {
      return;
    }
    selectReviewChapter(visibleQueueEntries[0]?.chapter_id ?? null);
  }, [workbenchMode, visibleQueueEntries, selectedReviewChapterId, selectReviewChapter]);

  useEffect(() => {
    if (!recentOperatorChange || !flowHandoff || recentOperatorChange.chapterId !== flowHandoff.targetChapterId) {
      return;
    }
    setFlowHandoff((current) => {
      if (!current || current.targetChapterId !== recentOperatorChange.chapterId) {
        return current;
      }
      if (current.lastProgressToken === recentOperatorChange.eventToken) {
        return current;
      }
      return {
        ...current,
        completedStepCount: current.completedStepCount + 1,
        lastProgressToken: recentOperatorChange.eventToken,
      };
    });
  }, [recentOperatorChange, flowHandoff]);

  useEffect(() => {
    if (!pendingChapterFocus || pendingChapterFocus.chapterId !== selectedReviewChapterId || !currentChapterReviewDetail) {
      return;
    }
    setTimelineFocus(buildPendingChapterFocusTarget(pendingChapterFocus, currentChapterReviewDetail));
    setPendingChapterFocus(null);
  }, [pendingChapterFocus, selectedReviewChapterId, currentChapterReviewDetail]);

  async function handleUpload() {
    if (!selectedFile) {
      setUploadMessage({ tone: "error", text: "请先选择一本 EPUB 或 PDF 书稿。" });
      return;
    }
    try {
      const document = await uploadFile(selectedFile);
      setActionMessage(null);
      setUploadMessage({
        tone: "success",
        text: `已完成《${preferredTitle(document)}》的解析。现在可以启动整书转换。`,
      });
    } catch (error) {
      setActionMessage(null);
      setUploadMessage({
        tone: "error",
        text: error instanceof Error ? error.message : "上传失败，请稍后重试。",
      });
    }
  }

  async function handlePrimaryAction() {
    try {
      const run = await runPrimaryAction();
      setUploadMessage(null);
      setActionMessage({
        tone: "success",
        text: `后台整书转换已推进，当前 run：${run.run_id.slice(0, 6)}。页面会自动更新。`,
      });
    } catch (error) {
      setUploadMessage(null);
      setActionMessage({
        tone: "error",
        text: error instanceof Error ? error.message : "运行操作失败，请稍后再试。",
      });
    }
  }

  async function handleProposalDecision(proposalId: string, decision: "approved" | "rejected") {
    if (!selectedReviewChapterId) {
      setReviewMessage({ tone: "error", text: "请先选择一个章节。" });
      return;
    }
    const beforeSnapshot = buildOperatorSnapshot(selectedQueueEntry, currentChapterReviewDetail);
    try {
      const payload = {
        actor_name: reviewerName.trim() || undefined,
        note: reviewerNote.trim() || undefined,
      };
      const result =
        decision === "approved"
          ? await approveMemoryProposal(proposalId, payload)
          : await rejectMemoryProposal(proposalId, payload);
      setReviewMessage({
        tone: "success",
        text:
          decision === "approved"
            ? `已批准 ${shorten(result.proposal.proposal_id, 5)}，snapshot v${result.committed_snapshot_version ?? "—"} 已生效。`
            : `已驳回 ${shorten(result.proposal.proposal_id, 5)}，等待新的 rerun proposal。`,
      });
      setRecentOperatorChange({
        chapterId: selectedReviewChapterId,
        kind: "proposal",
        title: decision === "approved" ? "Memory proposal 已批准" : "Memory proposal 已驳回",
        body:
          decision === "approved"
            ? "这次 override 已回写到 chapter memory 治理链，后续 review 会继续围绕新的 snapshot 收敛。"
            : "旧 proposal 已退出待审批面板，章节会等待新的 rerun proposal 再进入审批。",
        highlights:
          decision === "approved"
            ? [
                `Proposal ${shorten(result.proposal.proposal_id, 5)}`,
                `Snapshot v${result.committed_snapshot_version ?? "—"}`,
              ]
            : [`Proposal ${shorten(result.proposal.proposal_id, 5)}`, "等待新 proposal"],
        before: beforeSnapshot,
        eventToken: new Date().toISOString(),
      });
      setReviewerNote("");
    } catch (error) {
      setReviewMessage({
        tone: "error",
        text: error instanceof Error ? error.message : "审批操作失败，请稍后重试。",
      });
    }
  }

  async function handleAssignment(mode: "assign" | "clear") {
    if (!selectedReviewChapterId) {
      setReviewMessage({ tone: "error", text: "请先选择一个章节。" });
      return;
    }
    if (mode === "assign" && !assignmentOwner.trim()) {
      setReviewMessage({ tone: "error", text: "请先填写要分派的处理人。" });
      return;
    }
    const beforeSnapshot = buildOperatorSnapshot(selectedQueueEntry, currentChapterReviewDetail);
    try {
      if (mode === "assign") {
        const assignment = await assignChapterOwner(selectedReviewChapterId, {
          owner_name: assignmentOwner.trim(),
          assigned_by: reviewerName.trim() || "reviewer-ui",
          note: reviewerNote.trim() || undefined,
        });
        setReviewMessage({
          tone: "success",
          text: `章节已分派给 ${assignment.owner_name}。后续 review、action 和 memory proposal 会继续收敛到同一条时间线。`,
        });
        setRecentOperatorChange({
          chapterId: selectedReviewChapterId,
          kind: "assignment",
          title: "章节 assignment 已更新",
          body: "队列所有权已经切到新的 operator，后续 follow-up 和 override 会沿着这条 ownership 链继续推进。",
          highlights: [assignment.owner_name, `By ${assignment.assigned_by}`],
          before: beforeSnapshot,
          eventToken: new Date().toISOString(),
        });
      } else {
        await clearChapterAssignment(selectedReviewChapterId, {
          cleared_by: reviewerName.trim() || "reviewer-ui",
          note: reviewerNote.trim() || undefined,
        });
        setReviewMessage({
          tone: "success",
          text: "章节已回收到共享队列。其他 operator 现在可以继续接手处理。",
        });
        setRecentOperatorChange({
          chapterId: selectedReviewChapterId,
          kind: "assignment",
          title: "章节已回收至共享队列",
          body: "这章不再绑定单一 owner，当前队列里的其他 operator 都可以继续接手。",
          highlights: ["共享队列", `By ${reviewerName.trim() || "reviewer-ui"}`],
          before: beforeSnapshot,
          eventToken: new Date().toISOString(),
        });
        setAssignmentOwner("");
      }
      setReviewerNote("");
    } catch (error) {
      setReviewMessage({
        tone: "error",
        text: error instanceof Error ? error.message : "章节分派失败，请稍后重试。",
      });
    }
  }

  async function handleExecuteAction(actionId: string) {
    if (!selectedReviewChapterId) {
      setReviewMessage({ tone: "error", text: "请先选择一个章节。" });
      return;
    }
    const beforeSnapshot = buildOperatorSnapshot(selectedQueueEntry, currentChapterReviewDetail);
    try {
      const result = await executeChapterAction(actionId, true);
      setLastActionExecution({
        result,
        createdAt: new Date().toISOString(),
      });
      setReviewMessage({
        tone: "success",
        text: [
          `已执行 ${shorten(result.action_id, 5)}。`,
          result.followup_executed ? "follow-up rerun 已触发。" : "当前没有新的 follow-up rerun。",
          result.issue_resolved === true ? "相关 issue 已收敛。" : null,
          result.recheck_issue_count != null ? `复检 issue 数 ${result.recheck_issue_count}。` : null,
        ]
          .filter(Boolean)
          .join(" "),
      });
      setRecentOperatorChange({
        chapterId: selectedReviewChapterId,
        kind: "action",
        title: "Follow-up action 已执行",
        body: result.followup_executed
          ? "这次操作已经触发新的 rerun/replay，队列会沿着新的 scope 继续收敛。"
          : "这次操作没有触发新的 rerun，但 issue/action 状态已经完成一次显式推进。",
        highlights: [
          `Action ${shorten(result.action_id, 5)}`,
          result.followup_executed ? "已触发 rerun" : "未触发 rerun",
          result.issue_resolved ? "issue 已收敛" : `复检 ${formatNumber(result.recheck_issue_count ?? 0)}`,
        ],
        before: beforeSnapshot,
        eventToken: new Date().toISOString(),
      });
    } catch (error) {
      setReviewMessage({
        tone: "error",
        text: error instanceof Error ? error.message : "执行 action 失败，请稍后重试。",
      });
    }
  }

  function handleRecommendedNextStep() {
    if (!selectedChapterNextStep) {
      return;
    }
    if (selectedChapterNextStep.actionKind === "proposal") {
      const proposal = currentChapterReviewDetail?.memory_proposals.pending_proposals[0];
      if (!proposal) {
        return;
      }
      setTimelineFocus({
        eventId: `next-step-proposal-${proposal.proposal_id}`,
        section: "proposal",
        proposalId: proposal.proposal_id,
        label: `Memory Override · ${shorten(proposal.proposal_id, 5)}`,
        helper: "已把焦点切到下一条待审批 proposal，可以直接批准或驳回。",
      });
      return;
    }
    if (selectedChapterNextStep.actionKind === "assignment") {
      setTimelineFocus({
        eventId: "next-step-assignment",
        section: "assignment",
        label: currentChapterReviewDetail?.assignment?.owner_name
          ? `Assignment · ${currentChapterReviewDetail.assignment.owner_name}`
          : "Assignment · 共享队列",
        helper: currentChapterReviewDetail?.assignment?.owner_name
          ? "已把焦点切到 assignment 控制区，可以继续交接、回收或补充备注。"
          : "已把焦点切到 assignment 控制区，可以重新分派这章的 owner。",
      });
      return;
    }
    if (selectedChapterNextStep.actionKind === "action") {
      const actionEntry = currentChapterReviewDetail?.recent_actions[0];
      if (!actionEntry) {
        return;
      }
      setTimelineFocus({
        eventId: `next-step-action-${actionEntry.action_id}`,
        section: "actions",
        actionId: actionEntry.action_id,
        label: `Follow-up Action · ${actionEntry.action_type || actionEntry.issue_type || shorten(actionEntry.action_id, 5)}`,
        helper: "已把焦点切到 follow-up action，可以直接核对 rerun 结果或继续执行下一步。",
      });
    }
  }

  function handleAdvanceToNextChapter() {
    if (!nextQueueEntry || !nextQueueRecommendation) {
      return;
    }
    const sourceChapterLabel = selectedQueueEntry
      ? `第 ${selectedQueueEntry.ordinal} 章 · ${selectedQueueEntry.title_src || `Chapter ${selectedQueueEntry.ordinal}`}`
      : "当前章节";
    const targetChapterLabel = `第 ${nextQueueEntry.ordinal} 章 · ${nextQueueEntry.title_src || `Chapter ${nextQueueEntry.ordinal}`}`;
    if (selectedQueueEntry && selectedChapterRecentChange) {
      const summary =
        selectedChapterConvergenceItems[0]?.value ??
        selectedChapterRecentChange.highlights[0] ??
        selectedChapterRecentChange.title;
      setSessionTrail((current) => {
        const nextEntry = {
          chapterId: selectedQueueEntry.chapter_id,
          chapterLabel: sourceChapterLabel,
          changeTitle: selectedChapterRecentChange.title,
          summary,
          kind: selectedChapterRecentChange.kind,
          chainLabel: sessionTrailChainLabel(selectedChapterRecentChange.kind),
          revisitHint: sessionTrailRevisitHint(selectedChapterRecentChange.kind),
        } satisfies SessionTrailEntry;
        return [nextEntry, ...current.filter((entry) => entry.chapterId !== nextEntry.chapterId)].slice(0, 3);
      });
    }
    setFlowHandoff({
      targetChapterId: nextQueueEntry.chapter_id,
      sourceChapterLabel,
      targetChapterLabel,
      reasonTitle: nextQueueRecommendation.title,
      reasonBody: nextQueueRecommendation.body,
      completedStepCount: 0,
      lastProgressToken: null,
    });
    setReviewMessage({
      tone: "success",
      text: `已切到第 ${nextQueueEntry.ordinal} 章，优先处理 ${nextQueueRecommendation.title.replace("下一章先看", "").replace("下一章先清", "").trim()}。`,
    });
    setTimelineFocus(null);
    setPendingChapterFocus(nextQueueRecommendation.focus);
    selectReviewChapter(nextQueueEntry.chapter_id);
  }

  function handleFocusCurrentChapterPriority(item: FocusedPriorityItem) {
    if (!currentChapterReviewDetail) {
      return;
    }
    if (item.section === "proposal") {
      const proposal = currentChapterReviewDetail.memory_proposals.pending_proposals[0];
      if (!proposal) {
        return;
      }
      setTimelineFocus({
        eventId: `focused-priority-proposal-${proposal.proposal_id}`,
        section: "proposal",
        proposalId: proposal.proposal_id,
        label: `Memory Override · ${shorten(proposal.proposal_id, 5)}`,
        helper: "已把焦点切到当前章节的待审批 proposal，可以直接做 approve / reject。",
      });
      return;
    }
    if (item.section === "assignment") {
      setTimelineFocus({
        eventId: `focused-priority-assignment-${selectedReviewChapterId ?? "chapter"}`,
        section: "assignment",
        label: currentChapterReviewDetail.assignment?.owner_name
          ? `Assignment · ${currentChapterReviewDetail.assignment.owner_name}`
          : "Assignment · 共享队列",
        helper: currentChapterReviewDetail.assignment?.owner_name
          ? "已把焦点切到当前章节的 assignment 控制区，可以继续交接、回收或补充备注。"
          : "已把焦点切到当前章节的 assignment 控制区，可以为这章重新指定 owner。",
      });
      return;
    }
    const actionEntry = currentChapterReviewDetail.recent_actions[0];
    if (!actionEntry) {
      return;
    }
    setTimelineFocus({
      eventId: `focused-priority-action-${actionEntry.action_id}`,
      section: "actions",
      actionId: actionEntry.action_id,
      label: `Follow-up Action · ${actionEntry.action_type || actionEntry.issue_type || shorten(actionEntry.action_id, 5)}`,
      helper: "已把焦点切到当前章节的 follow-up action，可以直接执行或核对 rerun / recheck 收敛情况。",
    });
  }

  function handleFocusQueueLensPriority(priority: QueueLensPriority) {
    if (!currentChapterReviewDetail) {
      return;
    }
    if (priority.section === "proposal") {
      const proposal = currentChapterReviewDetail.memory_proposals.pending_proposals[0];
      if (!proposal) {
        return;
      }
      setTimelineFocus({
        eventId: `queue-lens-priority-proposal-${proposal.proposal_id}`,
        section: "proposal",
        proposalId: proposal.proposal_id,
        label: `Memory Override · ${shorten(proposal.proposal_id, 5)}`,
        helper: priority.helper,
      });
      return;
    }
    if (priority.section === "assignment") {
      setTimelineFocus({
        eventId: `queue-lens-priority-assignment-${selectedReviewChapterId ?? "chapter"}`,
        section: "assignment",
        label: currentChapterReviewDetail.assignment?.owner_name
          ? `Assignment · ${currentChapterReviewDetail.assignment.owner_name}`
          : "Assignment · 共享队列",
        helper: priority.helper,
      });
      return;
    }
    const actionEntry = currentChapterReviewDetail.recent_actions[0];
    if (!actionEntry) {
      return;
    }
    setTimelineFocus({
      eventId: `queue-lens-priority-action-${actionEntry.action_id}`,
      section: "actions",
      actionId: actionEntry.action_id,
      label: `Follow-up Action · ${actionEntry.action_type || actionEntry.issue_type || shorten(actionEntry.action_id, 5)}`,
      helper: priority.helper,
    });
  }

  function handleFocusFlowHandoffStep(step: FlowHandoffStep) {
    if (!currentChapterReviewDetail) {
      return;
    }
    if (step.section === "proposal") {
      const proposal = currentChapterReviewDetail.memory_proposals.pending_proposals[0];
      if (!proposal) {
        return;
      }
      setTimelineFocus({
        eventId: `flow-handoff-proposal-${proposal.proposal_id}`,
        section: "proposal",
        proposalId: proposal.proposal_id,
        label: `Memory Override · ${shorten(proposal.proposal_id, 5)}`,
        helper: step.helper,
      });
      return;
    }
    if (step.section === "assignment") {
      setTimelineFocus({
        eventId: `flow-handoff-assignment-${selectedReviewChapterId ?? "chapter"}`,
        section: "assignment",
        label: currentChapterReviewDetail.assignment?.owner_name
          ? `Assignment · ${currentChapterReviewDetail.assignment.owner_name}`
          : "Assignment · 共享队列",
        helper: step.helper,
      });
      return;
    }
    const actionEntry = currentChapterReviewDetail.recent_actions[0];
    if (!actionEntry) {
      return;
    }
    setTimelineFocus({
      eventId: `flow-handoff-action-${actionEntry.action_id}`,
      section: "actions",
      actionId: actionEntry.action_id,
      label: `Follow-up Action · ${actionEntry.action_type || actionEntry.issue_type || shorten(actionEntry.action_id, 5)}`,
      helper: step.helper,
    });
  }

  function handleFlowExit(action: "review-current" | "queue-overview") {
    if (action === "review-current" && selectedChapterNextStep?.actionKind) {
      handleRecommendedNextStep();
    } else {
      setTimelineFocus(null);
    }
    setFlowHandoff(null);
    setReviewMessage({
      tone: "success",
      text:
        action === "review-current"
          ? "已结束当前接力，继续停在本章做最终复核。"
          : "已结束当前接力，可以回到左侧队列继续挑选下一章或调整筛选范围。",
    });
  }

  function handleQueueLens(lens: QueueLensPreset) {
    setQueueOutcomeFilter(lens.outcome);
    if (lens.ownerName) {
      setChapterAssignmentFilter("all");
      setChapterAssignedOwnerFilter(lens.ownerName);
      return;
    }
    setChapterAssignedOwnerFilter("");
    setChapterAssignmentFilter(lens.assignment);
  }

  function handleResetQueueLens() {
    setQueueOutcomeFilter("all");
    setChapterAssignedOwnerFilter("");
    setChapterAssignmentFilter("all");
  }

  function handleFocusReleaseLaneFallback() {
    if (!releaseLaneFallback) {
      return;
    }
    setReviewMessage({
      tone: "success",
      text: `已切到 ${releaseLaneFallback.chapterLabel}，先把当前最接近放行的一步收口。`,
    });
    setTimelineFocus(null);
    setPendingChapterFocus(releaseLaneFallback.focus);
    selectReviewChapter(releaseLaneFallback.chapterId);
  }

  function handleInspectReleaseObserveLane() {
    if (!releaseLaneObserveFallback) {
      return;
    }
    setQueueOutcomeFilter("observe");
    setReviewMessage({
      tone: "success",
      text: `已切到 ${releaseLaneObserveFallback.chapterLabel}，先把这条放行候选 lane 里最后仍需观察的一步收口。`,
    });
    setTimelineFocus(null);
    setPendingChapterFocus(releaseLaneObserveFallback.focus);
    selectReviewChapter(releaseLaneObserveFallback.chapterId);
  }

  function handleReleaseLaneExitStrategy() {
    if (!activeReleaseLaneExitStrategy) {
      return;
    }
    if (activeReleaseLaneExitStrategy.actionKind === "next-release") {
      handleAdvanceToNextChapter();
      return;
    }
    if (activeReleaseLaneExitStrategy.actionKind === "observe-fallback") {
      handleInspectReleaseObserveLane();
      return;
    }
    if (activeReleaseLaneExitStrategy.actionKind === "observe-current") {
      setQueueOutcomeFilter("observe");
      const observePriority = currentChapterReviewDetail
        ? buildFocusedPriorityItems(currentChapterReviewDetail, selectedQueueEntry)[0] ?? null
        : null;
      if (observePriority) {
        handleFocusCurrentChapterPriority(observePriority);
      } else {
        setTimelineFocus(null);
      }
      setReviewMessage({
        tone: "success",
        text: "当前章已退回继续观察 lane，先留在本章把 blocker / proposal / open issues 收口。",
      });
      return;
    }
    handleResetQueueLens();
    setReviewMessage({
      tone: "success",
      text: "当前 release-ready lane 已收口，可以回到整条队列继续扫描其他章节。",
    });
  }

  function handleReleaseLanePressureAction() {
    if (!activeReleaseLanePressureAction) {
      return;
    }
    if (activeReleaseLanePressureAction.actionKind === "switch-observe") {
      handleInspectReleaseObserveLane();
      return;
    }
    if (activeReleaseLanePressureAction.actionKind === "reset") {
      handleResetQueueLens();
      setReviewMessage({
        tone: "success",
        text: "当前 release-ready scope 已经没有可继续推进的章节，可以回到整条队列重新扫描。",
      });
      return;
    }

    const releaseTarget = selectedQueueEntry ?? visibleQueueEntries[0] ?? null;
    if (releaseTarget && releaseTarget.chapter_id !== selectedReviewChapterId) {
      selectReviewChapter(releaseTarget.chapter_id);
    }
    if (selectedQueueEntry && activeQueueLensPriority) {
      handleFocusQueueLensPriority(activeQueueLensPriority);
    } else {
      setTimelineFocus(null);
    }
    setReviewMessage({
      tone: "success",
      text: releaseTarget
        ? `继续沿第 ${releaseTarget.ordinal} 章的 release-ready lane 推进，先把当前可直接放行的判断收口。`
        : "当前 scope 仍有放行余量，继续沿 release-ready lane 推进。",
    });
  }

  function handleReleaseLaneRoutingCue() {
    if (!activeReleaseLaneRoutingCue) {
      return;
    }
    if (activeReleaseLaneRoutingCue.source === "exit") {
      handleReleaseLaneExitStrategy();
      return;
    }
    handleReleaseLanePressureAction();
  }

  function handleToggleReleaseSignals() {
    setReleaseSignalExpanded((current) => !current);
  }

  return (
    <div className={styles.grid}>
      <div className={styles.summaryStack}>
        <Surface
          eyebrow="Current Book"
          title="当前书籍"
          description="当前书稿、主操作和下一步都集中在这里，不再把判断拆散到多个区域。"
          aside={currentDocument ? <StatusBadge tone={badge.tone} label={badge.label} /> : null}
        >
          {currentDocument ? (
            <>
              <div className={styles.hero}>
                <div className={styles.documentMeta}>
                  <h3 className={styles.documentTitle}>{preferredTitle(currentDocument)}</h3>
                  <div className={styles.metaStrip}>
                    <span>{currentDocument.author || "作者待识别"}</span>
                    <span>{sourceLabel(currentDocument.source_type)}</span>
                    <span>文档状态 {statusLabel(currentDocument.status)}</span>
                  </div>
                  <p className={styles.documentCopy}>
                    最近运行 {statusLabel(currentDocument.latest_run_status)} · 更新时间{" "}
                    {formatDate(currentDocument.latest_run_updated_at || currentRun?.updated_at)}
                  </p>
                </div>
                <div className={styles.heroAside}>
                  <div className={styles.nextStep}>
                    <div className={styles.nextStepLabel}>Next</div>
                    <p className={styles.nextStepText}>
                      {nextMilestoneText(currentDocument, currentRun, currentExports)}
                    </p>
                  </div>
                  <div className={styles.buttonRow}>
                    <button
                      className={styles.button}
                      type="button"
                      onClick={handlePrimaryAction}
                      disabled={action.disabled || runActionPending}
                    >
                      {runActionPending ? "处理中…" : action.label}
                    </button>
                    <button
                      className={styles.ghostButton}
                      type="button"
                      onClick={() => void refreshCurrentDocument()}
                    >
                      刷新状态
                    </button>
                  </div>
                  {actionMessage ? (
                    <div
                      className={`${styles.message} ${
                        actionMessage.tone === "success" ? styles.messageSuccess : styles.messageError
                      }`}
                    >
                      {actionMessage.text}
                    </div>
                  ) : null}
                </div>
              </div>
              <div className={styles.statGrid}>
                <div className={styles.statCard}>
                  <div className={styles.statLabel}>章节</div>
                  <div className={styles.statValue}>{formatNumber(currentDocument.chapter_count)}</div>
                </div>
                <div className={styles.statCard}>
                  <div className={styles.statLabel}>Packet</div>
                  <div className={styles.statValue}>{formatNumber(currentDocument.packet_count)}</div>
                </div>
                <div className={styles.statCard}>
                  <div className={styles.statLabel}>Open Issues</div>
                  <div className={styles.statValue}>{formatNumber(currentDocument.open_issue_count)}</div>
                </div>
                <div className={styles.statCard}>
                  <div className={styles.statLabel}>最近交付</div>
                  <div className={styles.statValue}>
                    {currentDocument.merged_export_ready ? "已就绪" : "未生成"}
                  </div>
                </div>
              </div>
            </>
          ) : (
            <div className={styles.emptyState}>
              <strong>当前还没有载入书籍。</strong>
              <span>先上传一本英文书。解析完成后，这里只会展示对你下一步有帮助的信息。</span>
            </div>
          )}
          {currentDocumentError ? (
            <div className={`${styles.message} ${styles.messageError}`}>{currentDocumentError}</div>
          ) : null}
        </Surface>
      </div>

      <div className={styles.uploadStack}>
        <Surface
          eyebrow="Ingest"
          title="上传入口"
          description="上传 EPUB 或 PDF 后，这本书会立即成为当前工作对象。"
        >
          <label className={styles.fileField}>
            <span className={styles.fileLabel}>选择源文件</span>
            <input
              aria-label="选择书稿文件"
              className={styles.fileInput}
              type="file"
              accept=".epub,.pdf,application/epub+zip,application/pdf"
              onChange={(event) => setSelectedFile(event.target.files?.[0] ?? null)}
            />
            <span className={styles.fileNote}>
              {selectedFile
                ? `已选择 ${selectedFile.name} · ${formatNumber(Math.round(selectedFile.size / 1024))} KB`
                : "只保留一个上传入口。解析成功后，工作台会自动切到这本书。"}
            </span>
          </label>
          <div className={styles.buttonRow}>
            <button className={styles.button} type="button" onClick={handleUpload} disabled={uploadPending}>
              {uploadPending ? "上传中…" : "上传并解析"}
            </button>
          </div>
          {uploadMessage ? (
            <div
              className={`${styles.message} ${
                uploadMessage.tone === "success" ? styles.messageSuccess : styles.messageError
              }`}
            >
              {uploadMessage.text}
            </div>
          ) : null}
        </Surface>
      </div>

      <div className={styles.reviewStack}>
        <Surface
          eyebrow="Chapter Workbench"
          title="章节工作台"
          description="把章节队列、处理人分派、proposal override 和 review/action timeline 收进同一个操作面。"
          aside={
            chapterWorklist
              ? `${chapterWorklist.entry_count} 章在队列中 · ${chapterWorklist.immediate_attention_count} 章需立即处理`
              : null
          }
        >
          {!currentDocument ? (
            <div className={styles.emptyState}>
              <strong>先载入一本书，再进入章节工作台。</strong>
              <span>这里会显示当前待处理章节、负责人、proposal 和 follow-up timeline。</span>
            </div>
          ) : (
            <div className={styles.workbenchShell}>
              <aside className={styles.queueRail}>
                <div className={styles.queueRailHeader}>
                  <div>
                    <div className={styles.fileLabel}>Queue</div>
                    <h3 className={styles.reviewSectionTitle}>待处理章节</h3>
                  </div>
                  <p className={styles.reviewSectionCopy}>
                    先按 immediate / SLA / owner-ready 看清队列，再切换到具体章节。
                  </p>
                </div>

                <div className={styles.queueSummaryGrid}>
                  <div className={styles.queueSummaryCard}>
                    <span className={styles.reviewMetricLabel}>Queued</span>
                    <strong className={styles.reviewMetricValue}>
                      {formatNumber(chapterWorklist?.entry_count)}
                    </strong>
                  </div>
                  <div className={styles.queueSummaryCard}>
                    <span className={styles.reviewMetricLabel}>Immediate</span>
                    <strong className={styles.reviewMetricValue}>
                      {formatNumber(chapterWorklist?.immediate_attention_count)}
                    </strong>
                  </div>
                  <div className={styles.queueSummaryCard}>
                    <span className={styles.reviewMetricLabel}>Assigned</span>
                    <strong className={styles.reviewMetricValue}>
                      {formatNumber(chapterWorklist?.assigned_count)}
                    </strong>
                  </div>
                </div>

                <div className={styles.queueFilterPanel}>
                  <label className={styles.inlineField}>
                    <span className={styles.inlineFieldLabel}>优先级</span>
                    <select
                      aria-label="队列优先级筛选"
                      className={styles.reviewSelect}
                      value={chapterWorklistFilters.queuePriority}
                      onChange={(event) =>
                        setChapterQueuePriorityFilter(
                          event.target.value as "all" | "immediate" | "high" | "medium"
                        )
                      }
                    >
                      <option value="all">全部优先级</option>
                      <option value="immediate">Immediate</option>
                      <option value="high">High</option>
                      <option value="medium">Medium</option>
                    </select>
                  </label>
                  <label className={styles.inlineField}>
                    <span className={styles.inlineFieldLabel}>分派状态</span>
                    <select
                      aria-label="章节分派筛选"
                      className={styles.reviewSelect}
                      value={chapterWorklistFilters.assignment}
                      onChange={(event) => {
                        const nextValue = event.target.value as "all" | "assigned" | "unassigned";
                        if (nextValue === "unassigned" && chapterWorklistFilters.assignedOwnerName) {
                          setChapterAssignedOwnerFilter("");
                        }
                        setChapterAssignmentFilter(nextValue);
                      }}
                    >
                      <option value="all">全部章节</option>
                      <option value="assigned">仅已分派</option>
                      <option value="unassigned">仅共享队列</option>
                    </select>
                  </label>
                  <label className={styles.inlineField}>
                    <span className={styles.inlineFieldLabel}>Owner 视角</span>
                    <select
                      aria-label="owner 视角筛选"
                      className={styles.reviewSelect}
                      value={chapterWorklistFilters.assignedOwnerName}
                      onChange={(event) => {
                        const nextOwner = event.target.value;
                        if (nextOwner) {
                          setChapterAssignmentFilter("all");
                        }
                        setChapterAssignedOwnerFilter(nextOwner);
                      }}
                    >
                      <option value="">全部 owner</option>
                      {ownerWorkloads.map((owner) => (
                        <option key={owner.owner_name} value={owner.owner_name}>
                          {owner.owner_name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <button
                    className={styles.ghostButton}
                    type="button"
                    disabled={!hasActiveQueueFilters}
                    onClick={clearChapterWorklistFilters}
                  >
                    清除筛选
                  </button>
                </div>

                {ownerWorkloads.length ? (
                  <div className={styles.ownerRail}>
                    {ownerWorkloads.map((owner) => {
                      const active = chapterWorklistFilters.assignedOwnerName === owner.owner_name;
                      return (
                        <button
                          key={owner.owner_name}
                          type="button"
                          className={`${styles.ownerCard} ${active ? styles.ownerCardActive : ""}`}
                          onClick={() =>
                            setChapterAssignedOwnerFilter(active ? "" : owner.owner_name)
                          }
                        >
                          <span className={styles.ownerName}>{owner.owner_name}</span>
                          <span className={styles.ownerMeta}>
                            {formatNumber(owner.assigned_chapter_count)} 章 · Immediate{" "}
                            {formatNumber(owner.immediate_count)}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                ) : null}

                {isFlowMode ? (
                  <section className={styles.queueInspector}>
                    <div className={styles.reviewSectionHeader}>
                      <div>
                        <div className={styles.fileLabel}>Active Scope</div>
                        <h4 className={styles.reviewSectionTitle}>当前筛选范围</h4>
                      </div>
                      <p className={styles.reviewSectionCopy}>
                        先确认你现在看到的是整条队列，还是某个 owner / assignment 子集。
                      </p>
                    </div>
                    <div className={styles.queueInspectorGrid}>
                      <div className={styles.queueInspectorCard}>
                        <span className={styles.reviewMetricLabel}>Visible</span>
                        <strong>
                          {formatNumber(visibleQueueEntries.length)} /{" "}
                          {formatNumber(chapterWorklist?.worklist_count)}
                        </strong>
                        <p className={styles.timelineDetail}>chapters in current scope</p>
                      </div>
                      <div className={styles.queueInspectorCard}>
                        <span className={styles.reviewMetricLabel}>Filters</span>
                        <strong>{hasActiveQueueFilters ? "已启用" : "未启用"}</strong>
                        <p className={styles.timelineDetail}>
                          {hasActiveQueueFilters
                            ? "当前队列已收窄到更明确的操作范围。"
                            : "当前展示整条 reviewer/operator 队列。"}
                        </p>
                      </div>
                      <div className={styles.queueInspectorCard}>
                        <span className={styles.reviewMetricLabel}>Queue 判断</span>
                        <strong>
                          放行 {formatNumber(visibleReleaseReadyCount)} · 观察 {formatNumber(visibleObserveCount)}
                        </strong>
                        <p className={styles.timelineDetail}>
                          {queueOutcomeFilter === "all"
                            ? hasActiveQueueFilters
                              ? "这是当前筛选范围下的判断，不是整条队列的全局统计。"
                              : "先看当前范围里有多少章适合放行，再决定是否继续扫描整条队列。"
                            : "当前是 queue judgment 视角，detail 会跟着只显示这类章节。"}
                        </p>
                      </div>
                    </div>
                    <div className={styles.modeSwitchControls} role="tablist" aria-label="queue judgment filter">
                      <button
                        type="button"
                        role="tab"
                        aria-selected={queueOutcomeFilter === "all"}
                        className={`${styles.modeSwitchButton} ${
                          queueOutcomeFilter === "all" ? styles.modeSwitchButtonActive : ""
                        }`}
                        onClick={() => setQueueOutcomeFilter("all")}
                      >
                        全部章节
                      </button>
                      <button
                        type="button"
                        role="tab"
                        aria-selected={queueOutcomeFilter === "release-ready"}
                        className={`${styles.modeSwitchButton} ${
                          queueOutcomeFilter === "release-ready" ? styles.modeSwitchButtonActive : ""
                        }`}
                        onClick={() => setQueueOutcomeFilter("release-ready")}
                      >
                        只看放行候选
                      </button>
                      <button
                        type="button"
                        role="tab"
                        aria-selected={queueOutcomeFilter === "observe"}
                        className={`${styles.modeSwitchButton} ${
                          queueOutcomeFilter === "observe" ? styles.modeSwitchButtonActive : ""
                        }`}
                        onClick={() => setQueueOutcomeFilter("observe")}
                      >
                        只看继续观察
                      </button>
                    </div>
                    {activeQueueFilters.length ? (
                      <div className={styles.filterChipRow}>
                        {activeQueueFilters.map((label) => (
                          <span key={label} className={styles.filterChip}>
                            {label}
                          </span>
                        ))}
                      </div>
                    ) : (
                      <p className={styles.timelineDetail}>当前未启用过滤，适合做整条队列扫描。</p>
                    )}
                    {activeQueueLens?.outcome === "release-ready" &&
                    (activeReleaseLanePressure ||
                      activeReleaseLaneBatchDigest ||
                      activeReleaseLaneExitStrategy ||
                      activeReleaseLaneBatchPhase) ? (
                      <div className={styles.nextStepCard}>
                        <span className={styles.deltaLabel}>放行链总览</span>
                        <strong className={styles.deltaValue}>
                          {activeReleaseLaneBatchDigest?.statusLabel ??
                            activeReleaseLaneBatchPhase?.statusLabel ??
                            activeReleaseLanePressure?.statusLabel}
                        </strong>
                        <p className={styles.timelineDetail}>
                          {activeReleaseLaneExitStrategy
                            ? `当前建议：${activeReleaseLaneExitStrategy.statusLabel}。${activeReleaseLaneExitStrategy.helper}`
                            : activeReleaseLaneBatchDigest?.helper ??
                              activeReleaseLaneBatchPhase?.helper ??
                              activeReleaseLanePressure?.helper}
                        </p>
                        {activeReleaseLaneRoutingCue ? (
                          <div className={styles.deltaCard}>
                            <span className={styles.deltaLabel}>当前路线建议</span>
                            <strong className={styles.deltaValue}>{activeReleaseLaneRoutingCue.statusLabel}</strong>
                            <p className={styles.timelineDetail}>{activeReleaseLaneRoutingCue.helper}</p>
                            <div className={styles.filterChipRow}>
                              {activeReleaseLaneRoutingCue.chips.map((chip) => (
                                <span key={chip} className={styles.filterChip}>
                                  {chip}
                                </span>
                              ))}
                            </div>
                            <div className={styles.nextStepActions}>
                              <button className={styles.button} type="button" onClick={handleReleaseLaneRoutingCue}>
                                {activeReleaseLaneRoutingCue.actionLabel}
                              </button>
                              {allowReleaseSignalToggle ? (
                                <button
                                  className={styles.ghostButton}
                                  type="button"
                                  onClick={handleToggleReleaseSignals}
                                >
                                  {showCondensedReleaseSignals ? "展开支持信号" : "收起支持信号"}
                                </button>
                              ) : null}
                            </div>
                          </div>
                        ) : null}
                        {activeReleaseLaneSignalSnapshot ? (
                          <div className={styles.deltaCard}>
                            <span className={styles.deltaLabel}>支持信号已收拢</span>
                            <strong className={styles.deltaValue}>{activeReleaseLaneSignalSnapshot.statusLabel}</strong>
                            <p className={styles.timelineDetail}>{activeReleaseLaneSignalSnapshot.helper}</p>
                            <div className={styles.filterChipRow}>
                              {activeReleaseLaneSignalSnapshot.chips.map((chip) => (
                                <span key={chip} className={styles.filterChip}>
                                  {chip}
                                </span>
                              ))}
                            </div>
                          </div>
                        ) : null}
                        <div className={styles.filterChipRow}>
                          {activeReleaseLaneBatchDigest ? (
                            <span className={styles.filterChip}>
                              批处理摘要 · {activeReleaseLaneBatchDigest.queueHint}
                            </span>
                          ) : null}
                          {activeReleaseLaneBatchPhase ? (
                            <span className={styles.filterChip}>
                              当前阶段 · {activeReleaseLaneBatchPhase.queueHint}
                            </span>
                          ) : null}
                        </div>
                        {activeReleaseLanePressure && !showCondensedReleaseSignals ? (
                          <div className={styles.deltaCard}>
                            <span className={styles.deltaLabel}>Lane 压力</span>
                            <strong className={styles.deltaValue}>{activeReleaseLanePressure.statusLabel}</strong>
                            <p className={styles.timelineDetail}>{activeReleaseLanePressure.helper}</p>
                            <div className={styles.filterChipRow}>
                              {activeReleaseLanePressure.chips.map((chip) => (
                                <span key={chip} className={styles.filterChip}>
                                  {chip}
                                </span>
                              ))}
                            </div>
                          </div>
                        ) : null}
                        {activeReleaseLaneHealthSummary && !showCondensedReleaseSignals ? (
                          <div className={styles.deltaCard}>
                            <span className={styles.deltaLabel}>Lane Health</span>
                            <strong className={styles.deltaValue}>
                              {activeReleaseLaneHealthSummary.statusLabel}
                            </strong>
                            <p className={styles.timelineDetail}>{activeReleaseLaneHealthSummary.helper}</p>
                            <div className={styles.filterChipRow}>
                              {activeReleaseLaneHealthSummary.chips.map((chip) => (
                                <span key={chip} className={styles.filterChip}>
                                  {chip}
                                </span>
                              ))}
                            </div>
                          </div>
                        ) : null}
                        {activeReleaseLaneConfidence && !showCondensedReleaseSignals ? (
                          <div className={styles.deltaCard}>
                            <span className={styles.deltaLabel}>Lane 把握度</span>
                            <strong className={styles.deltaValue}>{activeReleaseLaneConfidence.statusLabel}</strong>
                            <p className={styles.timelineDetail}>{activeReleaseLaneConfidence.helper}</p>
                            <div className={styles.filterChipRow}>
                              {activeReleaseLaneConfidence.chips.map((chip) => (
                                <span key={chip} className={styles.filterChip}>
                                  {chip}
                                </span>
                              ))}
                            </div>
                          </div>
                        ) : null}
                        {activeReleaseLaneDrift && !showCondensedReleaseSignals ? (
                          <div className={styles.deltaCard}>
                            <span className={styles.deltaLabel}>Lane 漂移</span>
                            <strong className={styles.deltaValue}>{activeReleaseLaneDrift.statusLabel}</strong>
                            <p className={styles.timelineDetail}>{activeReleaseLaneDrift.helper}</p>
                            <div className={styles.filterChipRow}>
                              {activeReleaseLaneDrift.chips.map((chip) => (
                                <span key={chip} className={styles.filterChip}>
                                  {chip}
                                </span>
                              ))}
                            </div>
                          </div>
                        ) : null}
                        {activeReleaseLanePressureAction && !showCondensedReleaseSignals ? (
                          <div className={styles.deltaCard}>
                            <span className={styles.deltaLabel}>压力建议</span>
                            <strong className={styles.deltaValue}>
                              {activeReleaseLanePressureAction.statusLabel}
                            </strong>
                            <p className={styles.timelineDetail}>{activeReleaseLanePressureAction.helper}</p>
                          </div>
                        ) : null}
                      </div>
                    ) : null}
                    {selectedOwnerWorkload ? (
                      <div className={styles.ownerSnapshotGrid}>
                        <div className={styles.ownerSnapshotCard}>
                          <span className={styles.reviewMetricLabel}>Owner</span>
                          <strong>{selectedOwnerWorkload.owner_name}</strong>
                          <p className={styles.timelineDetail}>
                            {formatNumber(selectedOwnerWorkload.assigned_chapter_count)} 章在此 owner 下
                          </p>
                        </div>
                        <div className={styles.ownerSnapshotCard}>
                          <span className={styles.reviewMetricLabel}>Immediate</span>
                          <strong>{formatNumber(selectedOwnerWorkload.immediate_count)}</strong>
                          <p className={styles.timelineDetail}>
                            blocker {formatNumber(selectedOwnerWorkload.total_active_blocking_issue_count)}
                          </p>
                        </div>
                      </div>
                    ) : null}
                    <div className={styles.queueInspector}>
                      <div className={styles.reviewSectionHeader}>
                        <div>
                          <div className={styles.fileLabel}>Operator Lens</div>
                          <h4 className={styles.reviewSectionTitle}>一键切到可处理子队列</h4>
                        </div>
                        <p className={styles.reviewSectionCopy}>
                          把 judgment、owner 和 assignment 收成预设视角，少做手动组合筛选。
                        </p>
                      </div>
                      <div className={styles.modeSwitchControls}>
                        {operatorLenses.map((lens) => {
                          const active = activeQueueLens?.key === lens.key;
                          return (
                            <button
                              key={lens.key}
                              type="button"
                              className={`${styles.modeSwitchButton} ${
                                active ? styles.modeSwitchButtonActive : ""
                              }`}
                              onClick={() => handleQueueLens(lens)}
                            >
                              {lens.label} · {formatNumber(lens.count)}
                            </button>
                          );
                        })}
                      </div>
                      <p className={styles.timelineDetail}>
                        {selectedOwnerWorkload
                          ? `当前已选 owner ${selectedOwnerWorkload.owner_name}，可以直接切到这位 operator 名下的放行候选或继续观察章节。`
                          : "先从共享队列切观察/放行候选，再决定是否收窄到具体 owner。"}
                      </p>
                      {activeReleaseLaneEntryCue ? (
                        <div className={styles.nextStepCard}>
                          <span className={styles.deltaLabel}>入口判断</span>
                          <strong className={styles.deltaValue}>{activeReleaseLaneEntryCue.statusLabel}</strong>
                          <p className={styles.timelineDetail}>{activeReleaseLaneEntryCue.helper}</p>
                          <div className={styles.filterChipRow}>
                            {activeReleaseLaneEntryCue.chips.map((chip) => (
                              <span key={chip} className={styles.filterChip}>
                                {chip}
                              </span>
                            ))}
                          </div>
                          <div className={styles.nextStepActions}>
                            <button className={styles.button} type="button" onClick={handleReleaseLaneRoutingCue}>
                              按入口判断处理
                            </button>
                          </div>
                        </div>
                      ) : null}
                      {activeQueueLens ? (
                        <div className={styles.nextStepCard}>
                          <span className={styles.deltaLabel}>当前 operator lane</span>
                          <strong className={styles.deltaValue}>{activeQueueLens.label}</strong>
                          <p className={styles.timelineDetail}>{activeQueueLens.helper}</p>
                          {activeQueueLens.outcome === "release-ready" &&
                          (activeReleaseLaneBatchDigest || activeReleaseLaneBatchPhase) ? (
                            <div className={styles.filterChipRow}>
                              <span className={styles.filterChip}>Lane 摘要</span>
                              {activeReleaseLaneBatchDigest ? (
                                <span className={styles.filterChip}>
                                  批处理摘要 · {activeReleaseLaneBatchDigest.queueHint}
                                </span>
                              ) : null}
                              {activeReleaseLaneBatchPhase ? (
                                <span className={styles.filterChip}>
                                  当前阶段 · {activeReleaseLaneBatchPhase.queueHint}
                                </span>
                              ) : null}
                            </div>
                          ) : null}
                          <div className={styles.deltaGrid}>
                            <div className={styles.deltaCard}>
                              <span className={styles.deltaLabel}>当前位置</span>
                              <strong className={styles.deltaValue}>
                                {visibleQueueEntries.length
                                  ? `${formatNumber(Math.max(selectedQueueIndex + 1, 1))} / ${formatNumber(
                                      visibleQueueEntries.length
                                    )}`
                                  : "0 / 0"}
                              </strong>
                              <p className={styles.timelineDetail}>
                                {selectedQueueEntry
                                  ? `当前聚焦第 ${selectedQueueEntry.ordinal} 章 · ${selectedQueueEntry.title_src || `Chapter ${selectedQueueEntry.ordinal}`}`
                                  : "当前 lane 下没有可聚焦章节。"}
                              </p>
                            </div>
                            <div className={styles.deltaCard}>
                              <span className={styles.deltaLabel}>下一步</span>
                              <strong className={styles.deltaValue}>
                                {nextQueueEntry
                                  ? `第 ${nextQueueEntry.ordinal} 章`
                                  : visibleQueueEntries.length
                                    ? "已到 lane 末尾"
                                    : "等待切回其他视角"}
                              </strong>
                              <p className={styles.timelineDetail}>
                                {nextQueueEntry
                                  ? `下一章是 ${nextQueueEntry.title_src || `Chapter ${nextQueueEntry.ordinal}`}`
                                  : visibleQueueEntries.length
                                    ? "这条子队列已经扫到末尾，适合停在当前章继续处理或切回整条队列。"
                                    : "当前 lane 没有匹配章节，可以切回全部章节或调整 owner / assignment。"}
                              </p>
                            </div>
                          </div>
                          {activeQueueLensPriority ? (
                            <div className={styles.deltaCard}>
                              <span className={styles.deltaLabel}>当前 lane 先处理</span>
                              <strong className={styles.deltaValue}>{activeQueueLensPriority.title}</strong>
                              <p className={styles.timelineDetail}>
                                {activeQueueLensPriority.value} · {activeQueueLensPriority.helper}
                              </p>
                            </div>
                          ) : null}
                          {activeReleaseLaneRoutingCue ? (
                            <div className={styles.deltaCard}>
                              <span className={styles.deltaLabel}>Operator 路线建议</span>
                              <strong className={styles.deltaValue}>{activeReleaseLaneRoutingCue.statusLabel}</strong>
                              <p className={styles.timelineDetail}>{activeReleaseLaneRoutingCue.helper}</p>
                            </div>
                          ) : null}
                          {activeReleaseLaneSignalSnapshot ? (
                            <div className={styles.deltaCard}>
                              <span className={styles.deltaLabel}>Operator 支持信号</span>
                              <strong className={styles.deltaValue}>{activeReleaseLaneSignalSnapshot.statusLabel}</strong>
                              <p className={styles.timelineDetail}>{activeReleaseLaneSignalSnapshot.helper}</p>
                            </div>
                          ) : null}
                          {activeQueueLens.outcome === "release-ready" ? (
                            <div className={styles.deltaCard}>
                              <span className={styles.deltaLabel}>放行候选结构</span>
                              <strong className={styles.deltaValue}>
                                可直接放行 {formatNumber(queueReleaseReadyCount)} · 最后观察 {formatNumber(queueObserveCount)}
                              </strong>
                              <p className={styles.timelineDetail}>
                                {queueObserveCount
                                  ? "这条 scope 里既有可直接放行章节，也还有仍需最后观察的章节；放行 lane 不该和最后观察 lane 脱节。"
                                  : "当前 scope 下已没有最后观察章节，可以沿着这条 lane 连续完成最终复核。"}
                              </p>
                            </div>
                          ) : null}
                          {activeReleaseLaneDecision ? (
                            <div className={styles.deltaCard}>
                              <span className={styles.deltaLabel}>连续放行决策</span>
                              <strong className={styles.deltaValue}>{activeReleaseLaneDecision.statusLabel}</strong>
                              <p className={styles.timelineDetail}>{activeReleaseLaneDecision.helper}</p>
                            </div>
                          ) : null}
                          {activeReleaseLaneBatchSummary ? (
                            <div className={styles.deltaCard}>
                              <span className={styles.deltaLabel}>批量放行反馈</span>
                              <strong className={styles.deltaValue}>{activeReleaseLaneBatchSummary.statusLabel}</strong>
                              <p className={styles.timelineDetail}>{activeReleaseLaneBatchSummary.helper}</p>
                            </div>
                          ) : null}
                          {activeReleaseLaneHealthSummary && !showCondensedReleaseSignals ? (
                            <div className={styles.deltaCard}>
                              <span className={styles.deltaLabel}>Operator Lane Health</span>
                              <strong className={styles.deltaValue}>
                                {activeReleaseLaneHealthSummary.statusLabel}
                              </strong>
                              <p className={styles.timelineDetail}>{activeReleaseLaneHealthSummary.helper}</p>
                            </div>
                          ) : null}
                          {activeReleaseLaneConfidence && !showCondensedReleaseSignals ? (
                            <div className={styles.deltaCard}>
                              <span className={styles.deltaLabel}>Operator 放行把握度</span>
                              <strong className={styles.deltaValue}>{activeReleaseLaneConfidence.statusLabel}</strong>
                              <p className={styles.timelineDetail}>{activeReleaseLaneConfidence.helper}</p>
                            </div>
                          ) : null}
                          {activeReleaseLaneDrift && !showCondensedReleaseSignals ? (
                            <div className={styles.deltaCard}>
                              <span className={styles.deltaLabel}>Operator 漂移趋势</span>
                              <strong className={styles.deltaValue}>{activeReleaseLaneDrift.statusLabel}</strong>
                              <p className={styles.timelineDetail}>{activeReleaseLaneDrift.helper}</p>
                            </div>
                          ) : null}
                          {activeReleaseLanePressureAction && !showCondensedReleaseSignals ? (
                            <div className={styles.deltaCard}>
                              <span className={styles.deltaLabel}>Operator 压力建议</span>
                              <strong className={styles.deltaValue}>
                                {activeReleaseLanePressureAction.statusLabel}
                              </strong>
                              <p className={styles.timelineDetail}>{activeReleaseLanePressureAction.helper}</p>
                            </div>
                          ) : null}
                          {activeReleaseLaneResultFeedback ? (
                            <div className={styles.deltaCard}>
                              <span className={styles.deltaLabel}>连续放行结果反馈</span>
                              <strong className={styles.deltaValue}>{activeReleaseLaneResultFeedback.statusLabel}</strong>
                              <p className={styles.timelineDetail}>{activeReleaseLaneResultFeedback.helper}</p>
                            </div>
                          ) : null}
                          {activeReleaseLaneContinuationFeedback ? (
                            <div className={styles.deltaCard}>
                              <span className={styles.deltaLabel}>放行 lane 收口反馈</span>
                              <strong className={styles.deltaValue}>
                                {activeReleaseLaneContinuationFeedback.statusLabel}
                              </strong>
                              <p className={styles.timelineDetail}>{activeReleaseLaneContinuationFeedback.helper}</p>
                            </div>
                          ) : null}
                          {activeReleaseLaneExitStrategy ? (
                            <div className={styles.deltaCard}>
                              <span className={styles.deltaLabel}>放行 lane 退出策略</span>
                              <strong className={styles.deltaValue}>{activeReleaseLaneExitStrategy.statusLabel}</strong>
                              <p className={styles.timelineDetail}>{activeReleaseLaneExitStrategy.helper}</p>
                              <div className={styles.nextStepActions}>
                                <button className={styles.button} type="button" onClick={handleReleaseLaneExitStrategy}>
                                  {activeReleaseLaneExitStrategy.actionLabel}
                                </button>
                              </div>
                            </div>
                          ) : null}
                          {activeReleaseLaneCompletionState ? (
                            <div className={styles.deltaCard}>
                              <span className={styles.deltaLabel}>放行链完成态</span>
                              <strong className={styles.deltaValue}>{activeReleaseLaneCompletionState.statusLabel}</strong>
                              <p className={styles.timelineDetail}>{activeReleaseLaneCompletionState.helper}</p>
                            </div>
                          ) : null}
                          {activeReleaseLaneBatchPhase ? (
                            <div className={styles.deltaCard}>
                              <span className={styles.deltaLabel}>放行批处理阶段</span>
                              <strong className={styles.deltaValue}>{activeReleaseLaneBatchPhase.statusLabel}</strong>
                              <p className={styles.timelineDetail}>{activeReleaseLaneBatchPhase.helper}</p>
                            </div>
                          ) : null}
                          {activeReleaseLaneBatchDigest ? (
                            <div className={styles.deltaCard}>
                              <span className={styles.deltaLabel}>放行批处理摘要</span>
                              <strong className={styles.deltaValue}>{activeReleaseLaneBatchDigest.statusLabel}</strong>
                              <p className={styles.timelineDetail}>{activeReleaseLaneBatchDigest.helper}</p>
                            </div>
                          ) : null}
                          {activeReleaseGate ? (
                            <div className={styles.deltaCard}>
                              <span className={styles.deltaLabel}>放行门</span>
                              <strong className={styles.deltaValue}>{activeReleaseGate.statusLabel}</strong>
                              <p className={styles.timelineDetail}>{activeReleaseGate.helper}</p>
                              <div className={styles.filterChipRow}>
                                {activeReleaseGate.checks.map((check) => (
                                  <span key={check.label} className={styles.filterChip}>
                                    {check.label} · {check.value}
                                  </span>
                                ))}
                              </div>
                            </div>
                          ) : null}
                          {activeReleaseGate ? (
                            <div className={styles.deltaCard}>
                              <span className={styles.deltaLabel}>连续放行判断</span>
                              <strong className={styles.deltaValue}>
                                {activeReleaseGateFailures.length
                                  ? `还差 ${formatNumber(activeReleaseGateFailures.length)} 道 gate`
                                  : "当前章已满足放行门"}
                              </strong>
                              <p className={styles.timelineDetail}>
                                {activeReleaseGateFailures.length
                                  ? `先收口 ${activeReleaseGateFailures.map((check) => check.label).join(" / ")}，再继续推进这一条放行候选 lane。`
                                  : nextQueueEntry
                                    ? "当前章已经进入可放行态，完成最后复核后可以直接继续下一条放行候选。"
                                    : queueObserveCount
                                      ? "当前可直接放行章节已经收口，完成最后复核后可以切到仍需最后观察的章节继续推进。"
                                      : "当前章已经进入可放行态，完成最后复核后可以回到队列继续扫描其他章节。"}
                              </p>
                            </div>
                          ) : null}
                          {releaseLaneFallback ? (
                            <div className={styles.deltaCard}>
                              <span className={styles.deltaLabel}>最接近放行</span>
                              <strong className={styles.deltaValue}>{releaseLaneFallback.chapterLabel}</strong>
                              <p className={styles.timelineDetail}>{releaseLaneFallback.helper}</p>
                              <div className={styles.filterChipRow}>
                                {releaseLaneFallback.chips.map((chip) => (
                                  <span key={chip} className={styles.filterChip}>
                                    {chip}
                                  </span>
                                ))}
                              </div>
                            </div>
                          ) : null}
                          <div className={styles.nextStepActions}>
                            {activeQueueLensPriority ? (
                              <button
                                className={styles.button}
                                type="button"
                                onClick={() => handleFocusQueueLensPriority(activeQueueLensPriority)}
                              >
                                {activeQueueLensPriority.actionLabel}
                              </button>
                            ) : null}
                            {activeQueueLens.outcome === "release-ready" && releaseLaneObserveFallback ? (
                              <button
                                className={styles.ghostButton}
                                type="button"
                                onClick={handleInspectReleaseObserveLane}
                              >
                                {activeReleaseLaneDecision?.actionLabel === "放行后看最后观察"
                                  ? "放行后看最后观察"
                                  : "查看仍需最后观察"}
                              </button>
                            ) : null}
                            {nextQueueEntry ? (
                              <button className={styles.ghostButton} type="button" onClick={handleAdvanceToNextChapter}>
                                {activeQueueLens.outcome === "release-ready"
                                  ? "切到下一条放行候选"
                                  : "切到下一条继续观察"}
                              </button>
                            ) : null}
                            <button className={styles.ghostButton} type="button" onClick={handleResetQueueLens}>
                              切回全部章节
                            </button>
                          </div>
                        </div>
                      ) : null}
                    </div>
                  </section>
                ) : (
                  <section className={styles.queueInspector}>
                    <div className={styles.reviewSectionHeader}>
                      <div>
                        <div className={styles.fileLabel}>Focused Review</div>
                        <h4 className={styles.reviewSectionTitle}>当前章节摘要</h4>
                      </div>
                      <p className={styles.reviewSectionCopy}>
                        单章精查模式下，先守住当前章节的 blocker、proposal 和 owner 语义，不再强调整条队列范围。
                      </p>
                    </div>
                    <div className={styles.queueInspectorGrid}>
                      <div className={styles.queueInspectorCard}>
                        <span className={styles.reviewMetricLabel}>Chapter</span>
                        <strong>
                          第 {selectedQueueEntry?.ordinal ?? currentChapterReviewDetail?.ordinal ?? "—"} 章
                        </strong>
                        <p className={styles.timelineDetail}>
                          {selectedQueueEntry?.title_src ||
                            currentChapterReviewDetail?.title_src ||
                            "当前章节"}
                        </p>
                      </div>
                      <div className={styles.queueInspectorCard}>
                        <span className={styles.reviewMetricLabel}>Driver</span>
                        <strong>{selectedQueueEntry?.queue_driver ?? "当前章节待确认"}</strong>
                        <p className={styles.timelineDetail}>
                          {selectedQueueEntry?.owner_ready_reason || "优先围绕当前章节的 blocker 和 proposal 做决策。"}
                        </p>
                      </div>
                    </div>
                    <p className={styles.timelineDetail}>
                      如果要恢复整条队列扫描，可切回 `连续处理` 模式。
                    </p>
                  </section>
                )}

                {chapterWorklistError ? (
                  <div className={`${styles.message} ${styles.messageError}`}>{chapterWorklistError}</div>
                ) : null}

                {chapterWorklistLoading ? (
                  <div className={styles.reviewEmpty}>正在加载章节队列…</div>
                ) : visibleQueueEntries.length ? (
                  <div className={styles.queueList}>
                    {visibleQueueEntries.map((entry) => {
                      const active = entry.chapter_id === selectedReviewChapterId;
                      const entryOutcome = buildQueueEntryOutcome(entry);
                      return (
                        <button
                          key={entry.chapter_id}
                          type="button"
                          className={`${styles.queueCard} ${active ? styles.queueCardActive : ""}`}
                          onClick={() => {
                            setReviewMessage(null);
                            selectReviewChapter(entry.chapter_id);
                          }}
                        >
                          <div className={styles.queueCardTop}>
                            <div className={styles.queueRankRow}>
                              <span className={styles.queueRank}>#{entry.queue_rank}</span>
                              {recentOperatorChange?.chapterId === entry.chapter_id ? (
                                <>
                                  <span className={styles.changeBadge}>最新操作</span>
                                  <span className={styles.changeKindBadge}>
                                    {recentChangeKindLabel(recentOperatorChange.kind)}
                                  </span>
                                </>
                              ) : null}
                            </div>
                            <span className={styles.queuePriority}>{queuePriorityLabel(entry.queue_priority)}</span>
                          </div>
                          <h4 className={styles.queueTitle}>
                            第 {entry.ordinal} 章 · {entry.title_src || `Chapter ${entry.ordinal}`}
                          </h4>
                          <p className={styles.queueMeta}>
                            {entry.queue_driver} · SLA {slaStatusLabel(entry.sla_status)} ·{" "}
                            {entry.assigned_owner_name ? `Owner ${entry.assigned_owner_name}` : "未分派"}
                          </p>
                          <div className={styles.queueStatRow}>
                            <span>Blockers {formatNumber(entry.active_blocking_issue_count)}</span>
                            <span>Pending {formatNumber(entry.memory_proposals.pending_proposal_count)}</span>
                          </div>
                          <p className={styles.queueOutcomeMeta}>
                            {entryOutcome.statusLabel} · {entryOutcome.reasonLabel}
                          </p>
                          {recentOperatorChange?.chapterId === entry.chapter_id &&
                          selectedReviewChapterId === entry.chapter_id ? (
                            <>
                              {selectedChapterConvergenceItems.length ? (
                                <p className={styles.queueDeltaHint}>
                                  {selectedChapterConvergenceItems
                                    .slice(0, 2)
                                    .map((item) => item.value)
                                    .join(" · ")}
                                </p>
                              ) : null}
                              {selectedQueueOutcome ? (
                                <p className={styles.queueOutcomeHint}>
                                  {selectedQueueOutcome.chainLabel} · {selectedQueueOutcome.statusLabel}
                                </p>
                              ) : null}
                              {activeQueueLens?.outcome === "release-ready" && activeReleaseLaneExitStrategy ? (
                                <p className={styles.queueOutcomeHint}>
                                  放行链反馈 · {activeReleaseLaneExitStrategy.statusLabel}
                                </p>
                              ) : null}
                              {activeQueueLens?.outcome === "release-ready" && activeReleaseLaneCompletionState ? (
                                <p className={styles.queueOutcomeHint}>
                                  放行链完成态 · {activeReleaseLaneCompletionState.queueHint}
                                </p>
                              ) : null}
                              {activeQueueLens?.outcome === "release-ready" && activeReleaseLaneBatchPhase ? (
                                <p className={styles.queueOutcomeHint}>
                                  批处理阶段 · {activeReleaseLaneBatchPhase.queueHint}
                                </p>
                              ) : null}
                              {activeQueueLens?.outcome === "release-ready" && activeReleaseLaneBatchDigest ? (
                                <p className={styles.queueOutcomeHint}>
                                  批处理摘要 · {activeReleaseLaneBatchDigest.queueHint}
                                </p>
                              ) : null}
                            </>
                          ) : null}
                        </button>
                      );
                    })}
                  </div>
                ) : (
                  <div className={styles.reviewEmpty}>
                    {activeQueueLens?.outcome === "release-ready" ? (
                      <>
                        <p className={styles.timelineDetail}>
                          当前放行候选 lane 里还没有章节。更常见的下一步是切回 `继续观察`，把 blocker、proposal 或最后一次 action 先收口。
                        </p>
                        {releaseLaneFallback ? (
                          <div className={styles.nextStepCard}>
                            <span className={styles.deltaLabel}>最接近放行</span>
                            <strong className={styles.deltaValue}>{releaseLaneFallback.chapterLabel}</strong>
                            <p className={styles.timelineDetail}>{releaseLaneFallback.helper}</p>
                            <div className={styles.filterChipRow}>
                              {releaseLaneFallback.chips.map((chip) => (
                                <span key={chip} className={styles.filterChip}>
                                  {chip}
                                </span>
                              ))}
                            </div>
                            <div className={styles.nextStepActions}>
                              <button
                                className={styles.button}
                                type="button"
                                onClick={handleFocusReleaseLaneFallback}
                              >
                                查看最接近放行章节
                              </button>
                              <button
                                className={styles.ghostButton}
                                type="button"
                                onClick={() => setQueueOutcomeFilter("observe")}
                              >
                                切到继续观察
                              </button>
                              <button className={styles.ghostButton} type="button" onClick={handleResetQueueLens}>
                                切回全部章节
                              </button>
                            </div>
                          </div>
                        ) : (
                          <div className={styles.nextStepActions}>
                            <button
                              className={styles.button}
                              type="button"
                              onClick={() => setQueueOutcomeFilter("observe")}
                            >
                              切到继续观察
                            </button>
                            <button className={styles.ghostButton} type="button" onClick={handleResetQueueLens}>
                              切回全部章节
                            </button>
                          </div>
                        )}
                      </>
                    ) : queueOutcomeFilter !== "all"
                      ? "当前判断视角下没有匹配章节。可以切回全部章节，或继续调整 owner / assignment 过滤。"
                      : hasActiveQueueFilters
                        ? "当前过滤条件下没有匹配章节。可以放宽优先级、owner 或分派条件。"
                      : "当前书籍还没有进入 reviewer/operator 队列的章节。"}
                  </div>
                )}
              </aside>

              <div className={styles.workbenchMain}>
                <div className={styles.reviewHeader}>
                  <label className={styles.reviewSelectField}>
                    <span className={styles.fileLabel}>当前章节</span>
                    <select
                      className={styles.reviewSelect}
                      value={selectedReviewChapterId ?? ""}
                      onChange={(event) => {
                        setReviewMessage(null);
                        selectReviewChapter(event.target.value || null);
                      }}
                    >
                      {(currentDocument.chapters || []).map((chapter) => (
                        <option key={chapter.chapter_id} value={chapter.chapter_id}>
                          第 {chapter.ordinal} 章 · {chapter.title_src || `Chapter ${chapter.ordinal}`}
                        </option>
                      ))}
                    </select>
                  </label>
                  <div className={styles.reviewMetricGrid}>
                    <div className={styles.reviewMetric}>
                      <span className={styles.reviewMetricLabel}>Pending</span>
                      <strong className={styles.reviewMetricValue}>
                        {formatNumber(currentChapterReviewDetail?.memory_proposals.pending_proposal_count)}
                      </strong>
                    </div>
                    <div className={styles.reviewMetric}>
                      <span className={styles.reviewMetricLabel}>Blockers</span>
                      <strong className={styles.reviewMetricValue}>
                        {formatNumber(currentChapterReviewDetail?.current_active_blocking_issue_count)}
                      </strong>
                    </div>
                    <div className={styles.reviewMetric}>
                      <span className={styles.reviewMetricLabel}>Assignment</span>
                      <strong className={styles.reviewMetricValue}>
                        {currentChapterReviewDetail?.assignment?.owner_name ?? "共享队列"}
                      </strong>
                    </div>
                    <div className={styles.reviewMetric}>
                      <span className={styles.reviewMetricLabel}>Timeline</span>
                      <strong className={styles.reviewMetricValue}>
                        {formatNumber(currentChapterReviewDetail?.timeline.length)}
                      </strong>
                    </div>
                  </div>
                </div>

                <div className={styles.modeSwitch}>
                  <div className={styles.reviewSectionHeader}>
                    <div>
                      <div className={styles.fileLabel}>Workbench Mode</div>
                      <h4 className={styles.reviewSectionTitle}>
                        {isFlowMode ? "连续处理" : "单章精查"}
                      </h4>
                    </div>
                    <p className={styles.reviewSectionCopy}>
                      {isFlowMode
                        ? "适合连续处理章节队列，保留 session digest、session trail 和 next-in-queue 推荐。"
                        : "适合专注当前章节，隐藏连续处理提示，只保留当前章节的决策与收敛信息。"}
                    </p>
                  </div>
                  <div className={styles.modeSwitchControls} role="tablist" aria-label="工作模式">
                    <button
                      type="button"
                      role="tab"
                      aria-selected={workbenchMode === "focused"}
                      className={`${styles.modeSwitchButton} ${
                        workbenchMode === "focused" ? styles.modeSwitchButtonActive : ""
                      }`}
                      onClick={() => setWorkbenchMode("focused")}
                    >
                      单章精查
                    </button>
                    <button
                      type="button"
                      role="tab"
                      aria-selected={workbenchMode === "flow"}
                      className={`${styles.modeSwitchButton} ${
                        workbenchMode === "flow" ? styles.modeSwitchButtonActive : ""
                      }`}
                      onClick={() => setWorkbenchMode("flow")}
                    >
                      连续处理
                    </button>
                  </div>
                </div>

                {isFlowMode && selectedQueueEntry ? (
                  <div className={styles.sessionDigest}>
                    <div className={styles.reviewSectionHeader}>
                      <div>
                        <div className={styles.fileLabel}>Flow Momentum</div>
                        <h4 className={styles.reviewSectionTitle}>连续处理节奏</h4>
                      </div>
                      <p className={styles.reviewSectionCopy}>
                        连续处理模式下，先确认自己在队列中的位置、还剩多少章、下一章重点是什么，再决定是否直接往前推进。
                      </p>
                    </div>
                    <div className={styles.sessionDigestGrid}>
                      <div className={styles.sessionDigestCard}>
                        <span className={styles.deltaLabel}>当前队列位置</span>
                        <strong className={styles.deltaValue}>
                          第 {formatNumber(selectedQueueIndex + 1)} / {formatNumber(queueEntries.length)} 章
                        </strong>
                        <p className={styles.timelineDetail}>
                          {selectedQueueEntry.title_src || `Chapter ${selectedQueueEntry.ordinal}`}
                        </p>
                      </div>
                      <div className={styles.sessionDigestCard}>
                        <span className={styles.deltaLabel}>剩余章节</span>
                        <strong className={styles.deltaValue}>
                          {formatNumber(Math.max(queueEntries.length - selectedQueueIndex - 1, 0))} 章
                        </strong>
                        <p className={styles.timelineDetail}>
                          {nextQueueEntry
                            ? `下一章是第 ${nextQueueEntry.ordinal} 章 · ${nextQueueEntry.title_src || `Chapter ${nextQueueEntry.ordinal}`}`
                            : "当前已经在本筛选范围的最后一章。"}
                        </p>
                      </div>
                      <div className={styles.sessionDigestCard}>
                        <span className={styles.deltaLabel}>下一章重点</span>
                        <strong className={styles.sessionTrailChain}>
                          {nextQueueRecommendation?.title || "当前已到队列末尾"}
                        </strong>
                        <p className={styles.timelineDetail}>
                          {nextQueueRecommendation?.body || "可以停在当前章节完成最终复核，或调整筛选条件后继续扫描队列。"}
                        </p>
                      </div>
                    </div>
                    {nextQueueEntry && nextQueueRecommendation ? (
                      <div className={styles.nextStepActions}>
                        <button className={styles.button} type="button" onClick={handleAdvanceToNextChapter}>
                          {nextQueueRecommendation.actionLabel}
                        </button>
                      </div>
                    ) : null}
                  </div>
                ) : null}

                {activeFlowHandoff ? (
                  <div className={styles.flowHandoffCard}>
                    <div className={styles.reviewSectionHeader}>
                      <div>
                        <div className={styles.fileLabel}>Flow Handoff</div>
                        <h4 className={styles.reviewSectionTitle}>连续处理接力</h4>
                      </div>
                      <button
                        className={styles.ghostButton}
                        type="button"
                        onClick={() => setFlowHandoff(null)}
                      >
                        隐藏接力提示
                      </button>
                    </div>
                    <p className={styles.timelineDetail}>
                      已从 {activeFlowHandoff.sourceChapterLabel} 切到 {activeFlowHandoff.targetChapterLabel}。
                    </p>
                    <div className={styles.deltaGrid}>
                      <div className={styles.deltaCard}>
                        <span className={styles.deltaLabel}>切换原因</span>
                        <strong className={styles.deltaValue}>{activeFlowHandoff.reasonTitle}</strong>
                        <p className={styles.timelineDetail}>{activeFlowHandoff.reasonBody}</p>
                      </div>
                      <div className={styles.deltaCard}>
                        <span className={styles.deltaLabel}>当前聚焦</span>
                        <strong className={styles.deltaValue}>
                          {timelineFocus?.label || "正在定位下一章关键面"}
                        </strong>
                        <p className={styles.timelineDetail}>
                          {timelineFocus?.helper || "章节 detail 已切换完成，关键 surface 正在收口到当前工作面。"}
                        </p>
                      </div>
                    </div>
                    {activeFlowCurrentStep ? (
                      <div className={styles.handoffStepLane}>
                        <div className={styles.reviewSectionHeader}>
                          <div>
                            <div className={styles.fileLabel}>Handoff Progress</div>
                            <h4 className={styles.reviewSectionTitle}>接力顺序</h4>
                          </div>
                          <p className={styles.reviewSectionCopy}>
                            切到下一章后，系统会把已完成的步骤压缩掉，只把当前下一步顶上来。
                          </p>
                        </div>
                        <div className={styles.deltaGrid}>
                          <div className={styles.deltaCard}>
                            <span className={styles.deltaLabel}>接力进度</span>
                            <strong className={styles.deltaValue}>
                              {formatNumber(activeFlowCompletedStepCount)} / {formatNumber(activeFlowHandoffSteps.length)} 已收口
                            </strong>
                            <p className={styles.timelineDetail}>
                              {activeFlowCompletedSteps.length
                                ? `已完成 ${activeFlowCompletedSteps.map((step) => step.title).join(" / ")}。`
                                : "当前还在这章的第一步。"}
                            </p>
                          </div>
                          <div className={styles.deltaCard}>
                            <span className={styles.deltaLabel}>当前下一步</span>
                            <strong className={styles.deltaValue}>{activeFlowCurrentStep.title}</strong>
                            <p className={styles.timelineDetail}>{activeFlowCurrentStep.helper}</p>
                          </div>
                        </div>
                        <button
                          type="button"
                          className={styles.handoffStepCard}
                          onClick={() => handleFocusFlowHandoffStep(activeFlowCurrentStep)}
                        >
                          <div className={styles.queueRankRow}>
                            <span className={styles.changeBadge}>{activeFlowCurrentStep.orderLabel}</span>
                            <span className={styles.changeKindBadge}>{activeFlowCurrentStep.title}</span>
                          </div>
                          <strong>{activeFlowCurrentStep.value}</strong>
                          <p className={styles.timelineDetail}>{activeFlowCurrentStep.helper}</p>
                          <span className={styles.handoffStepAction}>{activeFlowCurrentStep.actionLabel}</span>
                        </button>
                        {activeFlowQueuedSteps.length ? (
                          <div className={styles.handoffQueuedSteps}>
                            {activeFlowQueuedSteps.map((step) => (
                              <div key={step.title} className={styles.handoffQueuedStep}>
                                <span className={styles.changeBadge}>{step.orderLabel}</span>
                                <span className={styles.timelineDetail}>
                                  接下来：{step.title} · {step.value}
                                </span>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <div className={styles.handoffQueuedSteps}>
                            <div className={styles.handoffQueuedStep}>
                              <span className={styles.changeBadge}>✓</span>
                              <span className={styles.timelineDetail}>当前这章的接力步骤已经全部排完，收口后可以继续回到队列。</span>
                            </div>
                          </div>
                        )}
                      </div>
                    ) : null}
                    {activeFlowExitReady ? (
                      <div className={styles.nextStepCard}>
                        <span className={styles.deltaLabel}>Flow Exit Strategy</span>
                        <strong className={styles.deltaValue}>当前这章的接力步骤已经收口</strong>
                        <p className={styles.timelineDetail}>
                          {nextQueueEntry
                            ? "你现在可以停在当前章做最终复核，或者直接继续下一章。"
                            : "当前已经到这段队列的末尾，适合停在本章做最终复核，或回到左侧队列重新分诊。"}
                        </p>
                        <div className={styles.nextStepActions}>
                          <button
                            className={styles.button}
                            type="button"
                            onClick={() => handleFlowExit("review-current")}
                          >
                            停在当前章复核
                          </button>
                          {nextQueueEntry && nextQueueRecommendation ? (
                            <button
                              className={styles.ghostButton}
                              type="button"
                              onClick={handleAdvanceToNextChapter}
                            >
                              {nextQueueRecommendation.actionLabel}
                            </button>
                          ) : (
                            <button
                              className={styles.ghostButton}
                              type="button"
                              onClick={() => handleFlowExit("queue-overview")}
                            >
                              回到队列视角
                            </button>
                          )}
                        </div>
                      </div>
                    ) : null}
                  </div>
                ) : null}

                {!isFlowMode && focusedPrimaryItem ? (
                  <section className={styles.infoPanel}>
                    <div className={styles.reviewSectionHeader}>
                      <div>
                        <div className={styles.fileLabel}>Focused Review</div>
                        <h3 className={styles.reviewSectionTitle}>当前章节优先面</h3>
                      </div>
                      <p className={styles.reviewSectionCopy}>
                        单章精查模式下，先把这章最值得处理的 blocker、proposal、follow-up 收到一个面里。
                      </p>
                    </div>
                    <div className={styles.focusedDecisionStrip}>
                      <div className={styles.focusedDecisionContent}>
                        <div className={styles.queueRankRow}>
                          <span className={styles.changeBadge}>{focusedPrimaryItem.rankLabel}</span>
                          <span className={styles.changeKindBadge}>{focusedPrimaryItem.label}</span>
                        </div>
                        <strong className={styles.deltaValue}>{focusedPrimaryItem.value}</strong>
                        <p className={styles.timelineDetail}>{focusedPrimaryItem.hint}</p>
                      </div>
                      <div className={styles.focusedDecisionActions}>
                        <div className={styles.deltaGrid}>
                          <div className={styles.deltaCard}>
                            <span className={styles.deltaLabel}>当前先处理</span>
                            <strong className={styles.deltaValue}>{focusedPrimaryItem.label}</strong>
                            <p className={styles.timelineDetail}>先把这条链路收口，再看 proposal、owner 或 follow-up 余项。</p>
                          </div>
                          <div className={styles.deltaCard}>
                            <span className={styles.deltaLabel}>后续待看</span>
                            <strong className={styles.deltaValue}>
                              {formatNumber(focusedSecondaryItems.length)} 个次级面
                            </strong>
                            <p className={styles.timelineDetail}>
                              {focusedSecondaryItems.length
                                ? `接下来优先看 ${focusedSecondaryItems.map((item) => item.label).join(" / ")}。`
                                : "当前章节已经没有更多次级面待处理。"}
                            </p>
                          </div>
                        </div>
                        <div className={styles.nextStepActions}>
                          <button
                            className={styles.button}
                            type="button"
                            onClick={() => handleFocusCurrentChapterPriority(focusedPrimaryItem)}
                          >
                            {focusedPrimaryItem.actionLabel}
                          </button>
                        </div>
                      </div>
                    </div>
                    {focusedSecondaryItems.length ? (
                      <div className={styles.focusedPriorityList}>
                        {focusedSecondaryItems.map((item) => (
                        <button
                          key={item.label}
                          type="button"
                          className={styles.focusedPriorityCard}
                          onClick={() => handleFocusCurrentChapterPriority(item)}
                        >
                          <div className={styles.queueRankRow}>
                            <span className={styles.changeBadge}>{item.rankLabel}</span>
                            <span className={styles.changeKindBadge}>{item.label}</span>
                          </div>
                          <strong>{item.value}</strong>
                          <p className={styles.timelineDetail}>{item.hint}</p>
                          <span className={styles.focusedPriorityAction}>{item.actionLabel}</span>
                        </button>
                        ))}
                      </div>
                    ) : null}
                  </section>
                ) : null}

                {isFlowMode && (sessionDigest || showReleaseLaneSessionDigest) ? (
                  <div className={styles.sessionDigest}>
                    <div className={styles.reviewSectionHeader}>
                      <div>
                        <div className={styles.fileLabel}>Session Digest</div>
                        <h4 className={styles.reviewSectionTitle}>连续处理摘要</h4>
                      </div>
                      <p className={styles.reviewSectionCopy}>
                        先看这一轮已经推进了多少章、主要沿着哪条链在推进，再决定是否继续批处理。
                      </p>
                    </div>
                    <div className={styles.sessionDigestGrid}>
                      {sessionDigest ? (
                        <>
                          <div className={styles.sessionDigestCard}>
                            <span className={styles.deltaLabel}>已处理章节</span>
                            <strong className={styles.deltaValue}>{formatNumber(sessionDigest.processedCount)} 章</strong>
                            <p className={styles.timelineDetail}>{sessionDigest.kindSummary.join(" · ")}</p>
                          </div>
                          <div className={styles.sessionDigestCard}>
                            <span className={styles.deltaLabel}>最近主链</span>
                            <strong className={styles.sessionTrailChain}>{sessionDigest.latestChainLabel}</strong>
                            <p className={styles.timelineDetail}>{sessionDigest.latestChapterLabel}</p>
                          </div>
                          <div className={styles.sessionDigestCard}>
                            <span className={styles.deltaLabel}>连续处理提示</span>
                            <strong className={styles.deltaValue}>继续沿最近链路推进</strong>
                            <p className={styles.timelineDetail}>{sessionDigest.continuityHint}</p>
                          </div>
                        </>
                      ) : null}
                      {showReleaseLaneSessionDigest ? (
                        <div className={styles.sessionDigestCard}>
                          <span className={styles.deltaLabel}>Release-ready 批处理</span>
                          <strong className={styles.deltaValue}>
                            {activeReleaseLaneBatchDigest?.statusLabel ?? activeReleaseLaneBatchPhase?.statusLabel}
                          </strong>
                          <p className={styles.timelineDetail}>
                            {activeReleaseLaneBatchDigest?.helper ?? activeReleaseLaneBatchPhase?.helper}
                          </p>
                          {activeReleaseLaneBatchPhase ? (
                            <p className={styles.queueDeltaHint}>当前阶段 · {activeReleaseLaneBatchPhase.queueHint}</p>
                          ) : null}
                        </div>
                      ) : null}
                      {showReleaseLaneSessionDigest && activeReleaseLaneRoutingCue ? (
                        <div className={styles.sessionDigestCard}>
                          <span className={styles.deltaLabel}>Release-ready 路线建议</span>
                          <strong className={styles.deltaValue}>{activeReleaseLaneRoutingCue.statusLabel}</strong>
                          <p className={styles.timelineDetail}>{activeReleaseLaneRoutingCue.helper}</p>
                        </div>
                      ) : null}
                      {showReleaseLaneSessionDigest && activeReleaseLaneSignalSnapshot ? (
                        <div className={styles.sessionDigestCard}>
                          <span className={styles.deltaLabel}>Release-ready 支持信号</span>
                          <strong className={styles.deltaValue}>{activeReleaseLaneSignalSnapshot.statusLabel}</strong>
                          <p className={styles.timelineDetail}>{activeReleaseLaneSignalSnapshot.helper}</p>
                        </div>
                      ) : null}
                      {showReleaseLaneSessionDigest &&
                      activeReleaseLaneHealthSummary &&
                      !showCondensedReleaseSignals ? (
                        <div className={styles.sessionDigestCard}>
                          <span className={styles.deltaLabel}>Release-ready Lane Health</span>
                          <strong className={styles.deltaValue}>{activeReleaseLaneHealthSummary.statusLabel}</strong>
                          <p className={styles.timelineDetail}>{activeReleaseLaneHealthSummary.helper}</p>
                        </div>
                      ) : null}
                      {showReleaseLaneSessionDigest &&
                      activeReleaseLanePressureAction &&
                      !showCondensedReleaseSignals ? (
                        <div className={styles.sessionDigestCard}>
                          <span className={styles.deltaLabel}>Release-ready 去留判断</span>
                          <strong className={styles.deltaValue}>{activeReleaseLanePressureAction.statusLabel}</strong>
                          <p className={styles.timelineDetail}>{activeReleaseLanePressureAction.helper}</p>
                        </div>
                      ) : null}
                      {showReleaseLaneSessionDigest &&
                      activeReleaseLaneConfidence &&
                      !showCondensedReleaseSignals ? (
                        <div className={styles.sessionDigestCard}>
                          <span className={styles.deltaLabel}>Release-ready 把握度</span>
                          <strong className={styles.deltaValue}>{activeReleaseLaneConfidence.statusLabel}</strong>
                          <p className={styles.timelineDetail}>{activeReleaseLaneConfidence.helper}</p>
                        </div>
                      ) : null}
                      {showReleaseLaneSessionDigest &&
                      activeReleaseLaneDrift &&
                      !showCondensedReleaseSignals ? (
                        <div className={styles.sessionDigestCard}>
                          <span className={styles.deltaLabel}>Release-ready 漂移趋势</span>
                          <strong className={styles.deltaValue}>{activeReleaseLaneDrift.statusLabel}</strong>
                          <p className={styles.timelineDetail}>{activeReleaseLaneDrift.helper}</p>
                        </div>
                      ) : null}
                      {showReleaseLaneSessionDigest && activeReleaseLaneExitStrategy ? (
                        <div className={styles.sessionDigestCard}>
                          <span className={styles.deltaLabel}>本轮建议</span>
                          <strong className={styles.deltaValue}>{activeReleaseLaneExitStrategy.statusLabel}</strong>
                          <p className={styles.timelineDetail}>{activeReleaseLaneExitStrategy.helper}</p>
                          <div className={styles.nextStepActions}>
                            <button className={styles.button} type="button" onClick={handleReleaseLaneExitStrategy}>
                              {activeReleaseLaneExitStrategy.actionLabel}
                            </button>
                          </div>
                        </div>
                      ) : null}
                    </div>
                  </div>
                ) : null}

                {isFlowMode && sessionTrail.length ? (
                  <div className={styles.sessionTrail}>
                    <div className={styles.reviewSectionHeader}>
                      <div>
                        <div className={styles.fileLabel}>Session Trail</div>
                        <h4 className={styles.reviewSectionTitle}>刚处理过的章节</h4>
                      </div>
                      <p className={styles.reviewSectionCopy}>
                        连续处理多章时，可以随时跳回刚处理过的章节，不用再手动翻队列。
                      </p>
                    </div>
                    <div className={styles.sessionTrailList}>
                      {sessionTrail.map((entry) => (
                        <button
                          key={entry.chapterId}
                          type="button"
                          aria-label={`回到 ${entry.chapterLabel}`}
                          className={`${styles.sessionTrailCard} ${
                            entry.chapterId === selectedReviewChapterId ? styles.sessionTrailCardActive : ""
                          }`}
                          onClick={() => {
                            setReviewMessage({
                              tone: "success",
                              text: `已返回 ${entry.chapterLabel}，继续查看刚才的 ${entry.changeTitle}。`,
                            });
                            selectReviewChapter(entry.chapterId);
                          }}
                        >
                          <div className={styles.queueRankRow}>
                            <span className={styles.changeBadge}>刚处理</span>
                            <span className={styles.changeKindBadge}>{recentChangeKindLabel(entry.kind)}</span>
                          </div>
                          <strong className={styles.deltaValue}>{entry.chapterLabel}</strong>
                          <div className={styles.sessionTrailMeta}>
                            <span className={styles.deltaLabel}>处理链</span>
                            <strong className={styles.sessionTrailChain}>{entry.chainLabel}</strong>
                          </div>
                          <p className={styles.timelineDetail}>{entry.changeTitle}</p>
                          <p className={styles.queueDeltaHint}>{entry.summary}</p>
                          <p className={styles.sessionTrailHint}>{entry.revisitHint}</p>
                        </button>
                      ))}
                    </div>
                  </div>
                ) : null}

                <div
                  className={`${styles.assignmentBar} ${
                    timelineFocus?.section === "assignment" ? styles.focusSection : ""
                  }`}
                >
                  <label className={styles.inlineField}>
                    <span className={styles.inlineFieldLabel}>操作人</span>
                    <input
                      aria-label="操作人"
                      className={styles.inlineInput}
                      type="text"
                      value={reviewerName}
                      onChange={(event) => setReviewerName(event.target.value)}
                      placeholder="reviewer-ui"
                    />
                  </label>
                  <label className={styles.inlineField}>
                    <span className={styles.inlineFieldLabel}>指派给</span>
                    <input
                      aria-label="指派给"
                      className={styles.inlineInput}
                      type="text"
                      value={assignmentOwner}
                      onChange={(event) => setAssignmentOwner(event.target.value)}
                      placeholder="operator-name"
                    />
                  </label>
                  <label className={`${styles.inlineField} ${styles.inlineFieldWide}`}>
                    <span className={styles.inlineFieldLabel}>备注</span>
                    <input
                      aria-label="备注"
                      className={styles.inlineInput}
                      type="text"
                      value={reviewerNote}
                      onChange={(event) => setReviewerNote(event.target.value)}
                      placeholder="记录这次审批、assignment 或 override 的理由"
                    />
                  </label>
                  <div className={styles.assignmentActions}>
                    <button
                      className={styles.button}
                      type="button"
                      disabled={assignmentPending}
                      onClick={() => void handleAssignment("assign")}
                    >
                      {assignmentPending ? "处理中…" : "指派章节"}
                    </button>
                    <button
                      className={styles.ghostButton}
                      type="button"
                      disabled={assignmentPending || !currentChapterReviewDetail?.assignment}
                      onClick={() => void handleAssignment("clear")}
                    >
                      归还共享队列
                    </button>
                  </div>
                </div>

                {timelineFocus ? (
                  <div className={styles.focusBanner}>
                    <div>
                      <div className={styles.fileLabel}>Current Focus</div>
                      <strong className={styles.focusTitle}>{timelineFocus.label}</strong>
                      <p className={styles.timelineDetail}>{timelineFocus.helper}</p>
                    </div>
                    <div className={styles.focusActions}>
                      {timelineFocus.section === "actions" && focusedActionEntry ? (
                        <button
                          className={styles.button}
                          type="button"
                          disabled={actionExecutionPending || focusedActionEntry.status === "completed"}
                          onClick={() => void handleExecuteAction(focusedActionEntry.action_id)}
                        >
                          {actionExecutionPending ? "执行中…" : "执行当前 follow-up"}
                        </button>
                      ) : null}
                      {timelineFocus.section === "proposal" && focusedProposal ? (
                        <>
                          <button
                            className={styles.button}
                            type="button"
                            disabled={reviewDecisionPending}
                            onClick={() => void handleProposalDecision(focusedProposal.proposal_id, "approved")}
                          >
                            {reviewDecisionPending ? "处理中…" : "批准 focused proposal"}
                          </button>
                          <button
                            className={styles.ghostButton}
                            type="button"
                            disabled={reviewDecisionPending}
                            onClick={() => void handleProposalDecision(focusedProposal.proposal_id, "rejected")}
                          >
                            驳回 focused proposal
                          </button>
                        </>
                      ) : null}
                      {timelineFocus.section === "assignment" && focusedAssignment ? (
                        <button
                          className={styles.ghostButton}
                          type="button"
                          disabled={assignmentPending}
                          onClick={() => void handleAssignment("clear")}
                        >
                          {assignmentPending ? "处理中…" : "回收当前 assignment"}
                        </button>
                      ) : null}
                      <button
                        className={styles.ghostButton}
                        type="button"
                        onClick={() => setTimelineFocus(null)}
                      >
                        清除聚焦
                      </button>
                    </div>
                  </div>
                ) : null}

                {selectedChapterRecentChange ? (
                  <div className={styles.changeCard}>
                    <div className={styles.reviewSectionHeader}>
                      <div>
                        <div className={styles.fileLabel}>Latest Change</div>
                        <h4 className={styles.reviewSectionTitle}>{selectedChapterRecentChange.title}</h4>
                      </div>
                      <button
                        className={styles.ghostButton}
                        type="button"
                        onClick={() => setRecentOperatorChange(null)}
                      >
                        清除回写
                      </button>
                    </div>
                    <p className={styles.timelineDetail}>{selectedChapterRecentChange.body}</p>
                    <div className={styles.filterChipRow}>
                      {selectedChapterRecentChange.highlights.map((item) => (
                        <span key={item} className={styles.filterChip}>
                          {item}
                        </span>
                      ))}
                    </div>
                    {selectedChapterConvergenceItems.length ? (
                      <div className={styles.deltaGrid}>
                        {selectedChapterConvergenceItems.map((item) => (
                          <div key={item.label} className={styles.deltaCard}>
                            <span className={styles.deltaLabel}>{item.label}</span>
                            <strong className={styles.deltaValue}>{item.value}</strong>
                          </div>
                        ))}
                      </div>
                    ) : null}
                    {selectedChapterNextStep ? (
                      <div className={styles.nextStepCard}>
                        <span className={styles.deltaLabel}>Recommended Next Step</span>
                        <strong className={styles.deltaValue}>{selectedChapterNextStep.title}</strong>
                        <p className={styles.timelineDetail}>{selectedChapterNextStep.body}</p>
                        {selectedChapterNextStep.actionLabel ? (
                          <div className={styles.nextStepActions}>
                            <button
                              className={styles.button}
                              type="button"
                              onClick={handleRecommendedNextStep}
                            >
                              {selectedChapterNextStep.actionLabel}
                            </button>
                          </div>
                        ) : null}
                        {isFlowMode && nextQueueEntry && nextQueueRecommendation ? (
                          <div className={styles.nextQueueCard}>
                            <div>
                              <span className={styles.deltaLabel}>Next in Queue</span>
                              <strong className={styles.deltaValue}>
                                第 {nextQueueEntry.ordinal} 章 · {nextQueueEntry.title_src || `Chapter ${nextQueueEntry.ordinal}`}
                              </strong>
                              <p className={styles.timelineDetail}>{nextQueueRecommendation.title}</p>
                              <p className={styles.timelineDetail}>{nextQueueRecommendation.body}</p>
                            </div>
                            <button
                              className={styles.ghostButton}
                              type="button"
                              onClick={handleAdvanceToNextChapter}
                            >
                              {nextQueueRecommendation.actionLabel}
                            </button>
                          </div>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                ) : null}

                {reviewMessage ? (
                  <div
                    className={`${styles.message} ${
                      reviewMessage.tone === "success" ? styles.messageSuccess : styles.messageError
                    }`}
                  >
                    {reviewMessage.text}
                  </div>
                ) : null}
                {currentChapterReviewError ? (
                  <div className={`${styles.message} ${styles.messageError}`}>{currentChapterReviewError}</div>
                ) : null}

                {currentChapterReviewLoading ? (
                  <div className={styles.emptyState}>正在加载章节工作台数据…</div>
                ) : currentChapterReviewDetail ? (
                  <div className={styles.reviewLayout}>
                    <div className={styles.reviewColumn}>
                      <section className={styles.infoPanel}>
                        <div className={styles.reviewSectionHeader}>
                          <div>
                            <h3 className={styles.reviewSectionTitle}>Queue Context</h3>
                            <p className={styles.reviewSectionCopy}>
                              用当前 queue driver、SLA 和 assignment 状态快速判断这章应该先做什么。
                            </p>
                          </div>
                        </div>
                        <div className={styles.contextGrid}>
                          <div className={styles.contextCard}>
                            <span className={styles.reviewMetricLabel}>Driver</span>
                            <strong>{selectedQueueEntry?.queue_driver ?? "—"}</strong>
                            <p className={styles.timelineDetail}>
                              {selectedQueueEntry?.regression_hint || "当前没有明确的回归提示。"}
                            </p>
                          </div>
                          <div className={styles.contextCard}>
                            <span className={styles.reviewMetricLabel}>SLA</span>
                            <strong>{slaStatusLabel(selectedQueueEntry?.sla_status)}</strong>
                            <p className={styles.timelineDetail}>
                              {selectedQueueEntry?.owner_ready_reason || "尚未生成 owner-ready 说明。"}
                            </p>
                          </div>
                          <div className={styles.contextCard}>
                            <span className={styles.reviewMetricLabel}>Assignment</span>
                            <strong>
                              {currentChapterReviewDetail.assignment?.owner_name ?? "共享队列"}
                            </strong>
                            <p className={styles.timelineDetail}>
                              {currentChapterReviewDetail.assignment
                                ? `Assigned by ${currentChapterReviewDetail.assignment.assigned_by} · ${formatDate(
                                    currentChapterReviewDetail.assignment.assigned_at
                                  )}`
                                : "这章当前没有 owner。"}
                            </p>
                          </div>
                        </div>
                      </section>

                      <section className={styles.infoPanel}>
                        <div className={styles.reviewSectionHeader}>
                          <div>
                            <h3 className={styles.reviewSectionTitle}>待审批 Proposal</h3>
                            <p className={styles.reviewSectionCopy}>
                              当前章节共有 {formatNumber(currentChapterReviewDetail.memory_proposals.proposal_count)} 条 proposal，
                              其中待决策 {formatNumber(currentChapterReviewDetail.memory_proposals.pending_proposal_count)} 条。
                            </p>
                          </div>
                        </div>
                        {currentChapterReviewDetail.memory_proposals.pending_proposals.length ? (
                          <div className={styles.proposalList}>
                            {currentChapterReviewDetail.memory_proposals.pending_proposals.map((proposal) => (
                              <article
                                key={proposal.proposal_id}
                                className={`${styles.proposalCard} ${
                                  timelineFocus?.section === "proposal" &&
                                  timelineFocus.proposalId === proposal.proposal_id
                                    ? styles.focusCard
                                    : ""
                                }`}
                              >
                                <div className={styles.proposalTop}>
                                  <div>
                                    <div className={styles.proposalEyebrow}>
                                      Packet {shorten(proposal.packet_id, 5)}
                                    </div>
                                    <h4 className={styles.proposalTitle}>
                                      Proposal {shorten(proposal.proposal_id, 6)}
                                    </h4>
                                  </div>
                                  <span className={styles.proposalStatus}>{proposal.status}</span>
                                </div>
                                <p className={styles.proposalMeta}>
                                  Translation run {shorten(proposal.translation_run_id, 6)} · base snapshot v
                                  {proposal.base_snapshot_version ?? "—"} · 提交于{" "}
                                  {formatDate(proposal.updated_at)}
                                </p>
                                <div className={styles.proposalActions}>
                                  <button
                                    className={styles.button}
                                    type="button"
                                    disabled={reviewDecisionPending}
                                    onClick={() => void handleProposalDecision(proposal.proposal_id, "approved")}
                                  >
                                    {reviewDecisionPending ? "处理中…" : "批准写入"}
                                  </button>
                                  <button
                                    className={styles.ghostButton}
                                    type="button"
                                    disabled={reviewDecisionPending}
                                    onClick={() => void handleProposalDecision(proposal.proposal_id, "rejected")}
                                  >
                                    驳回并等待新 proposal
                                  </button>
                                </div>
                              </article>
                            ))}
                          </div>
                        ) : (
                          <div className={styles.reviewEmpty}>
                            当前章节没有待审批 proposal。review pass 后提交的 snapshot 和被驳回的 proposal 都会继续留在时间线里。
                          </div>
                        )}
                      </section>
                    </div>

                    <div className={styles.reviewColumn}>
                      <section className={styles.infoPanel}>
                        <div className={styles.reviewSectionHeader}>
                          <div>
                            <h3 className={styles.reviewSectionTitle}>Issue / Action Summary</h3>
                            <p className={styles.reviewSectionCopy}>
                              先确认 blocker 和 follow-up action，再决定这次是 approve、reject 还是换 owner。
                            </p>
                          </div>
                        </div>
                        <div className={styles.signalGrid}>
                          <div
                            className={`${styles.signalCard} ${
                              timelineFocus?.section === "actions" ? styles.focusSection : ""
                            }`}
                          >
                            <div className={styles.signalHeader}>
                              <span className={styles.reviewMetricLabel}>Recent Issues</span>
                              <strong>{formatNumber(currentChapterReviewDetail.recent_issues.length)}</strong>
                            </div>
                            {currentChapterReviewDetail.recent_issues.length ? (
                              <div className={styles.signalList}>
                                {currentChapterReviewDetail.recent_issues.slice(0, 3).map((issue) => (
                                  <article key={issue.issue_id} className={styles.signalItem}>
                                    <h4 className={styles.signalTitle}>{issue.issue_type}</h4>
                                    <p className={styles.timelineDetail}>
                                      {issue.root_cause_layer} · {issue.severity} ·{" "}
                                      {issue.blocking ? "blocking" : issue.status}
                                    </p>
                                  </article>
                                ))}
                              </div>
                            ) : (
                              <div className={styles.reviewEmpty}>当前没有 recent issues。</div>
                            )}
                          </div>

                          <div className={styles.signalCard}>
                            <div className={styles.signalHeader}>
                              <span className={styles.reviewMetricLabel}>Recent Actions</span>
                              <strong>{formatNumber(currentChapterReviewDetail.recent_actions.length)}</strong>
                            </div>
                            {currentChapterReviewDetail.recent_actions.length ? (
                              <div className={styles.signalList}>
                                {currentChapterReviewDetail.recent_actions.slice(0, 3).map((entry) => (
                                  <article
                                    key={entry.action_id}
                                    className={`${styles.signalItem} ${
                                      timelineFocus?.section === "actions" &&
                                      timelineFocus.actionId === entry.action_id
                                        ? styles.focusCard
                                        : ""
                                    }`}
                                  >
                                    <h4 className={styles.signalTitle}>{entry.action_type}</h4>
                                    <p className={styles.timelineDetail}>
                                      {entry.issue_type} · {entry.scope_type}{" "}
                                      {entry.scope_id ? shorten(entry.scope_id, 5) : ""}
                                    </p>
                                    <p className={styles.timelineDetail}>状态 {entry.status}</p>
                                    <div className={styles.signalActions}>
                                      <button
                                        className={styles.ghostButton}
                                        type="button"
                                        disabled={actionExecutionPending || entry.status === "completed"}
                                        onClick={() => void handleExecuteAction(entry.action_id)}
                                      >
                                        {actionExecutionPending ? "执行中…" : "执行 follow-up"}
                                      </button>
                                    </div>
                                  </article>
                                ))}
                              </div>
                            ) : (
                              <div className={styles.reviewEmpty}>当前没有 recent actions。</div>
                            )}
                            {lastActionExecution ? (
                              <article className={styles.executionCard}>
                                <div className={styles.signalHeader}>
                                  <span className={styles.reviewMetricLabel}>最近执行结果</span>
                                  <strong>{formatDate(lastActionExecution.createdAt)}</strong>
                                </div>
                                <div className={styles.executionGrid}>
                                  <div className={styles.executionMetric}>
                                    <span className={styles.reviewMetricLabel}>Action</span>
                                    <strong>{shorten(lastActionExecution.result.action_id, 5)}</strong>
                                    <p className={styles.timelineDetail}>
                                      状态 {lastActionExecution.result.status}
                                    </p>
                                  </div>
                                  <div className={styles.executionMetric}>
                                    <span className={styles.reviewMetricLabel}>Follow-up</span>
                                    <strong>
                                      {lastActionExecution.result.followup_executed
                                        ? "已触发 rerun"
                                        : "未触发 rerun"}
                                    </strong>
                                    <p className={styles.timelineDetail}>
                                      {lastActionExecution.result.rerun_scope_type
                                        ? `${lastActionExecution.result.rerun_scope_type} · ${formatNumber(
                                            lastActionExecution.result.rerun_scope_ids.length
                                          )} scopes`
                                        : "当前没有新的 replay scope。"}
                                    </p>
                                  </div>
                                  <div className={styles.executionMetric}>
                                    <span className={styles.reviewMetricLabel}>Issue</span>
                                    <strong>
                                      {lastActionExecution.result.issue_resolved
                                        ? "已收敛"
                                        : "待复检确认"}
                                    </strong>
                                    <p className={styles.timelineDetail}>
                                      复检 issue 数 {formatNumber(lastActionExecution.result.recheck_issue_count ?? 0)}
                                    </p>
                                  </div>
                                </div>
                              </article>
                            ) : null}
                          </div>
                        </div>
                      </section>

                      <section className={styles.infoPanel}>
                        <div className={styles.reviewSectionHeader}>
                          <div>
                            <h3 className={styles.reviewSectionTitle}>Review / Action Timeline</h3>
                            <p className={styles.reviewSectionCopy}>
                              同时查看 action、assignment 和 memory override 的最近动作，避免 reviewer 在多个列表之间来回跳。
                            </p>
                          </div>
                        </div>
                        {timelineGroups.length ? (
                          <div className={styles.timelineGroupList}>
                            {timelineGroups.map((group) => (
                              <section key={group.key} className={styles.timelineGroup}>
                                <div className={styles.timelineGroupHeader}>
                                  <div>
                                    <div className={styles.fileLabel}>Timeline Group</div>
                                    <h4 className={styles.timelineGroupTitle}>{group.title}</h4>
                                  </div>
                                  <span className={styles.timelineGroupCount}>
                                    {formatNumber(group.entries.length)}
                                  </span>
                                </div>
                                <div className={styles.timelineList}>
                                  {group.entries.map((entry) => {
                                    const impacted = selectedChapterImpactedTimelineEventId === entry.event_id;
                                    return (
                                      <button
                                        key={`${entry.source_kind}-${entry.event_id}-${entry.created_at}`}
                                        className={`${styles.timelineCard} ${styles.timelineEventButton} ${
                                          timelineFocus?.eventId === entry.event_id ? styles.timelineCardActive : ""
                                        } ${impacted ? styles.timelineCardRecentChange : ""}`}
                                        type="button"
                                        aria-label={`聚焦 ${timelineHeadline(entry)}`}
                                        aria-pressed={timelineFocus?.eventId === entry.event_id}
                                        onClick={() => setTimelineFocus(buildTimelineFocus(entry))}
                                      >
                                        <div className={styles.timelineTop}>
                                          <span className={styles.timelineTag}>{timelineLabel(entry)}</span>
                                          <div className={styles.timelineTopMeta}>
                                            {impacted ? (
                                              <span className={styles.timelineImpactBadge}>已影响当前状态</span>
                                            ) : null}
                                            <span className={styles.timelineDate}>{formatDate(entry.created_at)}</span>
                                          </div>
                                        </div>
                                        <h4 className={styles.timelineTitle}>{timelineHeadline(entry)}</h4>
                                        <p className={styles.timelineDetail}>{timelineDetail(entry)}</p>
                                        {entry.note ? <p className={styles.timelineNote}>备注：{entry.note}</p> : null}
                                      </button>
                                    );
                                  })}
                                </div>
                              </section>
                            ))}
                          </div>
                        ) : (
                          <div className={styles.reviewEmpty}>当前章节还没有可展示的时间线事件。</div>
                        )}
                      </section>
                    </div>
                  </div>
                ) : (
                  <div className={styles.emptyState}>当前章节尚未生成可复核的上下文。</div>
                )}
              </div>
            </div>
          )}
        </Surface>
      </div>
    </div>
  );
}

function queuePriorityLabel(priority?: string | null) {
  if (!priority) {
    return "未知优先级";
  }
  if (priority === "immediate") {
    return "Immediate";
  }
  if (priority === "high") {
    return "High";
  }
  if (priority === "medium") {
    return "Medium";
  }
  return priority;
}

function slaStatusLabel(status?: string | null) {
  if (!status) {
    return "未知";
  }
  if (status === "breached") {
    return "已超时";
  }
  if (status === "due_soon") {
    return "临近 SLA";
  }
  if (status === "on_track") {
    return "正常";
  }
  return status;
}

function timelineLabel(entry: {
  source_kind: string;
  event_kind: string;
  action_type?: string | null;
  decision?: string | null;
}) {
  if (entry.source_kind === "memory_proposal") {
    return entry.decision === "approved" ? "Memory Approved" : "Memory Rejected";
  }
  if (entry.source_kind === "assignment") {
    return entry.event_kind === "cleared" ? "Assignment Cleared" : "Assignment Set";
  }
  return entry.action_type ? `Action · ${entry.action_type}` : "Issue Action";
}

function timelineHeadline(entry: {
  source_kind: string;
  issue_type?: string | null;
  owner_name?: string | null;
  actor_name?: string | null;
  proposal_id?: string | null;
}) {
  if (entry.source_kind === "memory_proposal") {
    return `${entry.actor_name || "system"} 对 proposal ${shorten(entry.proposal_id, 5)} 做出决策`;
  }
  if (entry.source_kind === "assignment") {
    return entry.owner_name ? `章节已分派给 ${entry.owner_name}` : "章节重新回到共享队列";
  }
  return entry.issue_type ? `${entry.issue_type} 触发 follow-up 动作` : "Issue action 已更新";
}

function timelineDetail(entry: {
  source_kind: string;
  decision?: string | null;
  issue_type?: string | null;
  action_type?: string | null;
  scope_type?: string | null;
  scope_id?: string | null;
  actor_name?: string | null;
}) {
  if (entry.source_kind === "memory_proposal") {
    return `${entry.actor_name || "system"} 将 memory proposal 标记为 ${
      entry.decision === "approved" ? "approved" : "rejected"
    }。`;
  }
  if (entry.source_kind === "assignment") {
    return entry.actor_name ? `${entry.actor_name} 更新了章节处理人。` : "章节所有权发生变化。";
  }
  return `${entry.issue_type || "Issue"} -> ${entry.action_type || "action"} · ${
    entry.scope_type || "scope"
  } ${entry.scope_id ? shorten(entry.scope_id, 5) : ""}`.trim();
}

function groupTimelineEntries(entries: ChapterWorklistTimelineEntry[]) {
  const grouped = new Map<string, ChapterWorklistTimelineEntry[]>();
  for (const entry of entries) {
    const key = timelineGroupKey(entry.source_kind);
    const current = grouped.get(key) ?? [];
    current.push(entry);
    grouped.set(key, current);
  }
  return Array.from(grouped.entries()).map(([key, groupEntries]) => ({
    key,
    title: timelineGroupTitle(key),
    entries: groupEntries.sort((left, right) => right.created_at.localeCompare(left.created_at)),
  }));
}

function timelineGroupKey(sourceKind: string) {
  if (sourceKind === "action") {
    return "action";
  }
  if (sourceKind === "assignment") {
    return "assignment";
  }
  if (sourceKind === "memory_proposal") {
    return "memory_proposal";
  }
  return "other";
}

function timelineGroupTitle(groupKey: string) {
  if (groupKey === "action") {
    return "Follow-up Actions";
  }
  if (groupKey === "assignment") {
    return "Assignments";
  }
  if (groupKey === "memory_proposal") {
    return "Memory Overrides";
  }
  return "Other Events";
}

function buildActiveQueueFilters(filters: {
  queuePriority: "all" | "immediate" | "high" | "medium";
  assignment: "all" | "assigned" | "unassigned";
  assignedOwnerName: string;
}) {
  const labels: string[] = [];
  if (filters.queuePriority !== "all") {
    labels.push(`Priority · ${queuePriorityLabel(filters.queuePriority)}`);
  }
  if (filters.assignment === "assigned") {
    labels.push("Assignment · 已分派");
  } else if (filters.assignment === "unassigned") {
    labels.push("Assignment · 共享队列");
  }
  if (filters.assignedOwnerName) {
    labels.push(`Owner · ${filters.assignedOwnerName}`);
  }
  return labels;
}

function buildTimelineFocus(entry: ChapterWorklistTimelineEntry): TimelineFocusTarget {
  if (entry.source_kind === "assignment") {
    return {
      eventId: entry.event_id,
      section: "assignment",
      label: entry.owner_name ? `Assignment · ${entry.owner_name}` : "Assignment · 共享队列",
      helper: "已把焦点切到 assignment 控制区，可以继续指派、回收或补充备注。",
    };
  }
  if (entry.source_kind === "memory_proposal") {
    return {
      eventId: entry.event_id,
      section: "proposal",
      proposalId: entry.proposal_id,
      label: `Memory Override · ${shorten(entry.proposal_id, 5)}`,
      helper: "已把焦点切到 proposal 区。若这条 proposal 仍待审批，会直接高亮对应卡片。",
    };
  }
  return {
    eventId: entry.event_id,
    section: "actions",
    actionId: entry.action_id ?? entry.event_id,
    label: `Follow-up Action · ${entry.action_type || entry.issue_type || shorten(entry.event_id, 5)}`,
    helper: "已把焦点切到 Recent Actions，可以直接执行 follow-up 或核对最近一次执行结果。",
  };
}

function buildOperatorSnapshot(
  queueEntry:
    | {
        memory_proposals?: { pending_proposal_count?: number; active_snapshot_version?: number | null };
        assigned_owner_name?: string | null;
      }
    | null,
  detail:
    | {
        memory_proposals: { pending_proposal_count: number; active_snapshot_version?: number | null };
        assignment?: { owner_name?: string | null } | null;
        recent_actions: Array<{ status: string }>;
      }
    | null
) {
  if (!queueEntry && !detail) {
    return null;
  }
  return {
    pendingProposalCount:
      detail?.memory_proposals.pending_proposal_count ??
      queueEntry?.memory_proposals?.pending_proposal_count ??
      0,
    activeSnapshotVersion:
      detail?.memory_proposals.active_snapshot_version ??
      queueEntry?.memory_proposals?.active_snapshot_version ??
      null,
    ownerName: detail?.assignment?.owner_name ?? queueEntry?.assigned_owner_name ?? "共享队列",
    actionStatus: detail?.recent_actions[0]?.status ?? "unknown",
  } satisfies OperatorConvergenceSnapshot;
}

function buildConvergenceItems(
  before: OperatorConvergenceSnapshot | null,
  after: OperatorConvergenceSnapshot | null
) {
  if (!before || !after) {
    return [];
  }
  const items: Array<{ label: string; value: string }> = [];
  if (before.pendingProposalCount !== after.pendingProposalCount) {
    items.push({
      label: "Pending",
      value: `Pending ${before.pendingProposalCount} -> ${after.pendingProposalCount}`,
    });
  }
  if (before.activeSnapshotVersion !== after.activeSnapshotVersion) {
    items.push({
      label: "Snapshot",
      value: `Snapshot v${before.activeSnapshotVersion ?? "—"} -> v${after.activeSnapshotVersion ?? "—"}`,
    });
  }
  if (before.ownerName !== after.ownerName) {
    items.push({
      label: "Owner",
      value: `Owner ${before.ownerName} -> ${after.ownerName}`,
    });
  }
  if (before.actionStatus !== after.actionStatus) {
    items.push({
      label: "Action",
      value: `Action ${before.actionStatus} -> ${after.actionStatus}`,
    });
  }
  return items;
}

function buildQueueOutcomeSummary(
  change: RecentOperatorChange,
  detail:
    | {
        current_open_issue_count: number;
        current_active_blocking_issue_count: number;
        memory_proposals: { pending_proposal_count: number };
        recent_actions: Array<{ status: string }>;
      }
    | null
) {
  if (!detail) {
    return null;
  }
  const releaseReady =
    detail.current_open_issue_count === 0 &&
    detail.current_active_blocking_issue_count === 0 &&
    detail.memory_proposals.pending_proposal_count === 0 &&
    (detail.recent_actions[0]?.status ?? "unknown") === "completed";
  return {
    chainLabel: sessionTrailChainLabel(change.kind),
    statusLabel: releaseReady ? "适合放行" : "继续观察",
  };
}

function isQueueEntryReleaseReady(entry: {
  active_blocking_issue_count: number;
  open_issue_count: number;
  memory_proposals: { pending_proposal_count: number };
}) {
  return (
    entry.active_blocking_issue_count === 0 &&
    entry.open_issue_count === 0 &&
    entry.memory_proposals.pending_proposal_count === 0
  );
}

function queueOutcomeMatchesFilter(
  entry: {
    active_blocking_issue_count: number;
    open_issue_count: number;
    memory_proposals: { pending_proposal_count: number };
  },
  filter: QueueOutcomeFilter
) {
  if (filter === "all") {
    return true;
  }
  const releaseReady = isQueueEntryReleaseReady(entry);
  return filter === "release-ready" ? releaseReady : !releaseReady;
}

function buildQueueLensPresets(input: {
  sharedReleaseReadyCount: number;
  sharedObserveCount: number;
  selectedOwnerName: string | null;
  selectedOwnerReleaseReadyCount: number;
  selectedOwnerObserveCount: number;
}) {
  const presets: QueueLensPreset[] = [
    {
      key: "shared-observe",
      label: "共享队列 · 继续观察",
      helper: "只看共享队列里仍需继续观察的章节。",
      assignment: "unassigned",
      ownerName: "",
      outcome: "observe",
      count: input.sharedObserveCount,
    },
    {
      key: "shared-release-ready",
      label: "共享队列 · 放行候选",
      helper: "只看共享队列里已经接近可放行的章节。",
      assignment: "unassigned",
      ownerName: "",
      outcome: "release-ready",
      count: input.sharedReleaseReadyCount,
    },
  ];
  if (input.selectedOwnerName) {
    presets.push(
      {
        key: "owner-observe",
        label: `${input.selectedOwnerName} · 继续观察`,
        helper: "只看当前 owner 名下仍需继续观察的章节。",
        assignment: "all",
        ownerName: input.selectedOwnerName,
        outcome: "observe",
        count: input.selectedOwnerObserveCount,
      },
      {
        key: "owner-release-ready",
        label: `${input.selectedOwnerName} · 放行候选`,
        helper: "只看当前 owner 名下已经接近可放行的章节。",
        assignment: "all",
        ownerName: input.selectedOwnerName,
        outcome: "release-ready",
        count: input.selectedOwnerReleaseReadyCount,
      }
    );
  }
  return presets;
}

function queueLensIsActive(
  lens: QueueLensPreset,
  assignmentFilter: "all" | "assigned" | "unassigned",
  assignedOwnerName: string,
  outcomeFilter: QueueOutcomeFilter
) {
  return (
    lens.assignment === assignmentFilter &&
    lens.ownerName === assignedOwnerName &&
    lens.outcome === outcomeFilter
  );
}

function buildQueueEntryOutcome(entry: {
  active_blocking_issue_count: number;
  open_issue_count: number;
  memory_proposals: { pending_proposal_count: number };
}) {
  if (entry.active_blocking_issue_count > 0) {
    return {
      statusLabel: "继续观察",
      reasonLabel: "存在 blocker",
    };
  }
  if (entry.memory_proposals.pending_proposal_count > 0) {
    return {
      statusLabel: "继续观察",
      reasonLabel: "待审批 proposal",
    };
  }
  if (entry.open_issue_count > 0) {
    return {
      statusLabel: "继续观察",
      reasonLabel: "仍有 open issues",
    };
  }
  return {
    statusLabel: "适合放行",
    reasonLabel: "当前未见 blocker / proposal / open issue",
  };
}

function buildQueueLensPriority(
  lens: QueueLensPreset,
  detail: {
    current_active_blocking_issue_count: number;
    memory_proposals: { pending_proposal_count: number };
    assignment?: { owner_name?: string | null } | null;
    recent_actions: Array<{ status: string; action_type?: string | null }>;
  },
  queueEntry:
    | {
        queue_driver?: string | null;
        open_issue_count: number;
      }
    | null
): QueueLensPriority {
  if (lens.outcome === "observe") {
    const firstPriority = buildFocusedPriorityItems(detail, queueEntry)[0];
    return {
      title: firstPriority.label,
      value: firstPriority.value,
      helper: firstPriority.hint,
      section: firstPriority.section,
      actionLabel: firstPriority.actionLabel,
    };
  }
  const latestAction = detail.recent_actions[0];
  if (latestAction) {
    return {
      title: "最终复核",
      value: `${latestAction.action_type || "当前 action"} · ${statusLabel(latestAction.status)}`,
      helper:
        latestAction.status === "completed"
          ? "这条 lane 里的章节已经接近可放行，先核对最新 follow-up 和 timeline 是否都进入稳定完成态。"
          : "虽然当前章节已进入放行候选，但最近 action 还没完成，先确认结果是否真正落盘。",
      section: "actions",
      actionLabel: "查看最终复核",
    };
  }
  if (detail.memory_proposals.pending_proposal_count > 0) {
    return {
      title: "最终确认 proposal",
      value: `${detail.memory_proposals.pending_proposal_count} 条待审批`,
      helper: "这条 lane 已接近可放行，但仍有 proposal 挂着，先把 memory override 收口。",
      section: "proposal",
      actionLabel: "查看当前 proposal",
    };
  }
  return {
    title: "最终确认 owner",
    value: detail.assignment?.owner_name ? `Owner ${detail.assignment.owner_name}` : "共享队列",
    helper: "这条 lane 没有 blocker / proposal 压力，最后确认 owner handoff 和当前章节语义是否一致。",
    section: "assignment",
    actionLabel: "查看当前 owner",
  };
}

function buildReleaseGateSummary(detail: {
  current_open_issue_count: number;
  current_active_blocking_issue_count: number;
  memory_proposals: { pending_proposal_count: number };
  recent_actions: Array<{ status: string }>;
}): ReleaseGateSummary {
  const latestActionCompleted = (detail.recent_actions[0]?.status ?? "unknown") === "completed";
  const checks = [
    {
      label: "Blocker",
      value: detail.current_active_blocking_issue_count === 0 ? "0" : String(detail.current_active_blocking_issue_count),
      passed: detail.current_active_blocking_issue_count === 0,
    },
    {
      label: "Open issues",
      value: detail.current_open_issue_count === 0 ? "0" : String(detail.current_open_issue_count),
      passed: detail.current_open_issue_count === 0,
    },
    {
      label: "Pending proposal",
      value:
        detail.memory_proposals.pending_proposal_count === 0
          ? "0"
          : String(detail.memory_proposals.pending_proposal_count),
      passed: detail.memory_proposals.pending_proposal_count === 0,
    },
    {
      label: "Latest action",
      value: latestActionCompleted ? "completed" : "waiting",
      passed: latestActionCompleted,
    },
  ];
  const releaseReady = checks.every((check) => check.passed);
  return {
    statusLabel: releaseReady ? "适合放行" : "最后观察",
    helper: releaseReady
      ? "当前章节已经满足放行门，可以把注意力放到最终确认和下一章接力。"
      : "这条 lane 里的章节虽然接近可放行，但还需要最后一轮观察，先核对未收口的 gate。",
    checks,
  };
}

function buildReleaseLaneDecision(input: {
  hasGateFailures: boolean;
  nextQueueEntry: {
    ordinal: number;
    title_src?: string | null;
  } | null;
  observeFallback: ReleaseLaneFallback | null;
}): ReleaseLaneDecision {
  if (input.hasGateFailures) {
    return {
      statusLabel: "还差最后观察",
      helper: "当前章还没真正进入可放行态，先留在本章做最终复核，把最后一条 gate 收口。",
      actionLabel: "查看最终复核",
    };
  }
  if (input.nextQueueEntry) {
    return {
      statusLabel: "现在可放行",
      helper: `当前章已经满足放行门；完成最终复核后，下一步直接继续第 ${input.nextQueueEntry.ordinal} 章 · ${
        input.nextQueueEntry.title_src || `Chapter ${input.nextQueueEntry.ordinal}`
      }。`,
      actionLabel: "切到下一条放行候选",
    };
  }
  if (input.observeFallback) {
    return {
      statusLabel: "现在可放行",
      helper: `当前章已经满足放行门；这条 lane 收口后，下一步切到 ${input.observeFallback.chapterLabel} 做最后观察。`,
      actionLabel: "放行后看最后观察",
    };
  }
  return {
    statusLabel: "现在可放行",
    helper: "当前 scope 下已经没有更多放行候选或最后观察章节，完成最终复核后可以切回整条队列继续扫描。",
    actionLabel: "切回全部章节",
  };
}

function buildReleaseLaneBatchSummary(input: {
  visibleCount: number;
  selectedIndex: number;
  observeCount: number;
}): ReleaseLaneBatchSummary {
  const currentIndex = input.selectedIndex >= 0 ? input.selectedIndex : 0;
  const remainingReleaseReady = input.visibleCount
    ? Math.max(input.visibleCount - currentIndex, 0)
    : 0;
  if (!input.visibleCount) {
    return {
      statusLabel: `本轮可放行 0 章 · 最后观察 ${input.observeCount} 章`,
      helper:
        input.observeCount > 0
          ? `当前这条放行 lane 已经空了，下一步回到最后观察 lane 继续处理 ${formatNumber(input.observeCount)} 章。`
          : "当前 scope 下没有可直接放行章节，也没有最后观察章节，可以回到整条队列重新扫描。",
    };
  }
  return {
    statusLabel: `本轮还可连续推进 ${remainingReleaseReady} 章 · 之后观察 ${input.observeCount} 章`,
    helper:
      input.observeCount > 0
        ? `当前这条放行 lane 还剩 ${formatNumber(remainingReleaseReady)} 章可连续复核；收口后，再回到 ${formatNumber(
            input.observeCount
          )} 章最后观察。`
        : `当前这条放行 lane 还剩 ${formatNumber(remainingReleaseReady)} 章可连续复核；收口后可直接回到整条队列继续扫描。`,
  };
}

function buildReleaseLaneResultFeedback(input: {
  statusLabel: string;
  chainLabel: string;
  nextQueueEntry: {
    ordinal: number;
    title_src?: string | null;
  } | null;
  observeFallback: ReleaseLaneFallback | null;
}): ReleaseLaneResultFeedback {
  if (input.statusLabel !== "适合放行") {
    return {
      statusLabel: "这次操作后退回继续观察",
      helper: `${input.chainLabel} 已经把当前章从放行候选拉回继续观察，下一步先留在本章把 blocker / proposal / open issues 收口。`,
    };
  }
  if (input.nextQueueEntry) {
    return {
      statusLabel: "这次操作后继续下一条放行候选",
      helper: `${input.chainLabel} 已经保持当前章的放行态；下一步直接切到第 ${input.nextQueueEntry.ordinal} 章 · ${
        input.nextQueueEntry.title_src || `Chapter ${input.nextQueueEntry.ordinal}`
      } 继续这条放行 lane。`,
    };
  }
  if (input.observeFallback) {
    return {
      statusLabel: "这次操作后切回最后观察",
      helper: `${input.chainLabel} 已经保持当前章的放行态；这条放行 lane 收口后，下一步切到 ${input.observeFallback.chapterLabel} 继续最后观察。`,
    };
  }
  return {
    statusLabel: "这次操作后本轮放行已收口",
    helper: `${input.chainLabel} 已经保持当前章的放行态；当前 scope 下没有更多放行候选或最后观察章节，可以回到整条队列继续扫描。`,
  };
}

function buildReleaseLaneContinuationFeedback(input: {
  statusLabel: string;
  visibleCount: number;
  selectedIndex: number;
  observeCount: number;
  nextQueueEntry: {
    ordinal: number;
    title_src?: string | null;
  } | null;
  observeFallback: ReleaseLaneFallback | null;
}): ReleaseLaneContinuationFeedback {
  if (input.statusLabel !== "适合放行") {
    const remainingReleaseReady = Math.max(input.visibleCount - 1, 0);
    const observeAfter = input.observeCount + 1;
    return {
      statusLabel: `还剩 ${formatNumber(remainingReleaseReady)} 章可直接放行 · 之后观察 ${formatNumber(observeAfter)} 章`,
      helper:
        remainingReleaseReady > 0
          ? `当前章退回继续观察后，这条放行 lane 里还剩 ${formatNumber(
              remainingReleaseReady
            )} 章可直接推进；观察 lane 扩大到 ${formatNumber(observeAfter)} 章。`
          : `当前章退回继续观察后，这条放行 lane 已收口；下一步先回到 ${formatNumber(
              observeAfter
            )} 章最后观察。`,
    };
  }

  const currentIndex = input.selectedIndex >= 0 ? input.selectedIndex : 0;
  const remainingReleaseReady = Math.max(input.visibleCount - currentIndex - 1, 0);
  return {
    statusLabel: `还剩 ${formatNumber(remainingReleaseReady)} 章可直接放行 · 之后观察 ${formatNumber(input.observeCount)} 章`,
    helper: input.nextQueueEntry
      ? `当前章已经完成这轮放行动作；继续第 ${input.nextQueueEntry.ordinal} 章 · ${
          input.nextQueueEntry.title_src || `Chapter ${input.nextQueueEntry.ordinal}`
        } 后，这条 lane 还剩 ${formatNumber(remainingReleaseReady)} 章可直接推进。`
      : input.observeFallback
        ? `当前章已经完成这轮放行动作；这条放行 lane 已收口，下一步切到 ${input.observeFallback.chapterLabel}，继续 ${formatNumber(
            input.observeCount
          )} 章最后观察。`
        : "当前章已经完成这轮放行动作；这条放行 lane 和最后观察 lane 都已收口，可以回到整条队列继续扫描。",
  };
}

function buildReleaseLaneExitStrategy(input: {
  statusLabel: string;
  nextQueueEntry: {
    ordinal: number;
    title_src?: string | null;
  } | null;
  observeFallback: ReleaseLaneFallback | null;
}): ReleaseLaneExitStrategy {
  if (input.statusLabel !== "适合放行") {
    return {
      statusLabel: "当前章退回继续观察",
      helper: "这次 operator 动作把当前章拉回观察 lane，下一步先留在本章把最后一个 blocker / proposal / open issue 收口。",
      actionLabel: "切到继续观察 lane",
      actionKind: "observe-current",
    };
  }
  if (input.nextQueueEntry) {
    return {
      statusLabel: "继续下一条放行候选",
      helper: `当前章已经完成放行动作，下一步直接接力第 ${input.nextQueueEntry.ordinal} 章 · ${
        input.nextQueueEntry.title_src || `Chapter ${input.nextQueueEntry.ordinal}`
      }。`,
      actionLabel: "继续下一条放行候选",
      actionKind: "next-release",
    };
  }
  if (input.observeFallback) {
    return {
      statusLabel: "切到最后观察 lane",
      helper: `当前 release-ready lane 已收口，下一步切到 ${input.observeFallback.chapterLabel} 继续最后观察。`,
      actionLabel: "切到最后观察 lane",
      actionKind: "observe-fallback",
    };
  }
  return {
    statusLabel: "回到整条队列",
    helper: "当前 release-ready lane 和最后观察 lane 都已收口，下一步回到整条队列继续扫描。",
    actionLabel: "回到整条队列",
    actionKind: "reset",
  };
}

function buildReleaseLaneCompletionState(input: {
  statusLabel: string;
  nextQueueEntry: {
    ordinal: number;
    title_src?: string | null;
  } | null;
  observeFallback: ReleaseLaneFallback | null;
}): ReleaseLaneCompletionState {
  if (input.statusLabel !== "适合放行") {
    return {
      statusLabel: "这条放行链已退回观察",
      helper: "当前章这次操作后不再处于 release-ready 态，这条放行链会先暂停，优先回到继续观察 lane 收口未完成的 gate。",
      queueHint: "退回继续观察",
    };
  }
  if (input.nextQueueEntry) {
    return {
      statusLabel: "这条放行链仍在推进中",
      helper: `当前章已经完成这轮放行动作，接下来由第 ${input.nextQueueEntry.ordinal} 章 · ${
        input.nextQueueEntry.title_src || `Chapter ${input.nextQueueEntry.ordinal}`
      } 继续这条 release-ready lane。`,
      queueHint: "继续推进中",
    };
  }
  if (input.observeFallback) {
    return {
      statusLabel: "这条放行链本轮已收口",
      helper: `当前章已经完成这轮放行动作，release-ready lane 暂时收口；下一步切到 ${input.observeFallback.chapterLabel} 做最后观察。`,
      queueHint: "本轮已收口",
    };
  }
  return {
    statusLabel: "当前 scope 已整体收口",
    helper: "当前 release-ready lane 和最后观察 lane 都没有待处理章节，可以回到整条队列重新扫描新的候选。",
    queueHint: "整体已收口",
  };
}

function buildReleaseLaneBatchPhase(input: {
  statusLabel: string;
  nextQueueEntry: {
    ordinal: number;
    title_src?: string | null;
  } | null;
  observeFallback: ReleaseLaneFallback | null;
}): ReleaseLaneBatchPhase {
  if (input.statusLabel !== "适合放行") {
    return {
      statusLabel: "已退回继续观察修正",
      helper: "这次 operator 动作把当前章拉回观察 lane，这一轮批处理会先暂停连续放行，回到 blocker / proposal / open issue 的修正链。",
      queueHint: "已退回继续观察",
    };
  }
  if (input.nextQueueEntry) {
    return {
      statusLabel: "连续放行中",
      helper: `当前章已经完成放行动作，当前批处理会继续推进到第 ${input.nextQueueEntry.ordinal} 章 · ${
        input.nextQueueEntry.title_src || `Chapter ${input.nextQueueEntry.ordinal}`
      }。`,
      queueHint: "连续放行中",
    };
  }
  if (input.observeFallback) {
    return {
      statusLabel: "已转入最后观察收尾",
      helper: `当前 release-ready lane 已经没有下一条可直接放行章节，当前批处理转入 ${input.observeFallback.chapterLabel} 的最后观察收尾。`,
      queueHint: "已转入最后观察收尾",
    };
  }
  return {
    statusLabel: "本轮批处理已收口",
    helper: "当前 release-ready lane 和最后观察 lane 都已收口，这一轮批处理已经完成。",
    queueHint: "本轮批处理已收口",
  };
}

function buildReleaseLaneBatchDigest(input: {
  statusLabel: string;
  visibleCount: number;
  selectedIndex: number;
  observeCount: number;
}): ReleaseLaneBatchDigest {
  if (input.statusLabel !== "适合放行") {
    const observeAfter = input.observeCount + 1;
    return {
      statusLabel: `已完成放行 0 / ${formatNumber(input.visibleCount)} 章 · 待观察 ${formatNumber(observeAfter)} 章`,
      helper: `当前章退回继续观察后，这一轮批处理暂时没有新增放行收口；下一步先回到 ${formatNumber(
        observeAfter
      )} 章观察链继续修正。`,
      queueHint: `放行 0 / ${formatNumber(input.visibleCount)} · 观察 ${formatNumber(observeAfter)}`,
    };
  }
  const currentIndex = input.selectedIndex >= 0 ? input.selectedIndex : 0;
  const completedReleaseCount = Math.min(currentIndex + 1, input.visibleCount);
  const remainingReleaseCount = Math.max(input.visibleCount - completedReleaseCount, 0);
  return {
    statusLabel: `已完成放行 ${formatNumber(completedReleaseCount)} / ${formatNumber(input.visibleCount)} 章 · 待观察 ${formatNumber(input.observeCount)} 章`,
    helper:
      remainingReleaseCount > 0
        ? `这一轮批处理已经完成 ${formatNumber(completedReleaseCount)} 章放行，后面还剩 ${formatNumber(
            remainingReleaseCount
          )} 章 release-ready、${formatNumber(input.observeCount)} 章最后观察。`
        : `这一轮批处理已经完成当前 release-ready lane 的 ${formatNumber(
            completedReleaseCount
          )} 章放行；下一步回到 ${formatNumber(input.observeCount)} 章最后观察收尾。`,
    queueHint: `放行 ${formatNumber(completedReleaseCount)} / ${formatNumber(input.visibleCount)} · 观察 ${formatNumber(
      input.observeCount
    )}`,
  };
}

function buildReleaseLanePressure(input: {
  visibleCount: number;
  observeCount: number;
}): ReleaseLanePressure {
  const chips = [`可直放 ${formatNumber(input.visibleCount)}`, `待观察 ${formatNumber(input.observeCount)}`];
  if (input.visibleCount <= 0) {
    return {
      statusLabel: "release-ready runway 已空",
      helper:
        input.observeCount > 0
          ? `当前 scope 已没有可直接放行章节，先回 ${formatNumber(input.observeCount)} 章观察链修正，再等待新的 release-ready 候选出现。`
          : "当前 scope 下已没有 release-ready 或观察 backlog，可以回到整条队列重新扫描。",
      chips,
    };
  }
  if (input.visibleCount === 1 && input.observeCount > 0) {
    return {
      statusLabel: "放行余量见底",
      helper: `当前 scope 只剩最后 ${formatNumber(
        input.visibleCount
      )} 章 release-ready；做完当前后更适合切回 ${formatNumber(input.observeCount)} 章最后观察。`,
      chips,
    };
  }
  if (input.observeCount === 0) {
    return {
      statusLabel: input.visibleCount === 1 ? "最后一章可放行" : "继续冲放行",
      helper:
        input.visibleCount === 1
          ? "当前 scope 只剩最后 1 章 release-ready，完成后这条 lane 就会整体收口。"
          : `当前 scope 还有 ${formatNumber(input.visibleCount)} 章 release-ready，观察 backlog 已清空，适合连续推进放行。`,
      chips,
    };
  }
  return {
    statusLabel: "还有放行余量",
    helper: `当前 scope 还剩 ${formatNumber(input.visibleCount)} 章 release-ready，观察 backlog ${formatNumber(
      input.observeCount
    )} 章；可以继续推进放行，但要准备在余量见底后切回观察 lane。`,
    chips,
  };
}

function buildReleaseLaneConfidence(input: {
  hasGateFailures: boolean;
  visibleCount: number;
  observeCount: number;
}): ReleaseLaneConfidence {
  const chips = [
    `可直放 ${formatNumber(input.visibleCount)}`,
    `观察 backlog ${formatNumber(input.observeCount)}`,
    input.hasGateFailures ? "当前 gate 未全绿" : "当前 gate 已全绿",
  ];
  if (input.hasGateFailures) {
    return {
      statusLabel: "放行把握度不足",
      helper: "当前章节自身的放行门还没完全通过，这条 lane 还不能被视为稳定可冲的 release-ready 批处理。",
      chips,
    };
  }
  if (input.visibleCount <= 0) {
    return {
      statusLabel: "放行把握度已归零",
      helper: "当前 scope 已没有可直接放行章节，这条 lane 现在更像一次收口后的观察回扫，而不是连续放行链。",
      chips,
    };
  }
  if (input.visibleCount === 1 && input.observeCount > 0) {
    return {
      statusLabel: "放行把握度临界",
      helper: "当前 scope 只剩最后 1 章可直放，观察 backlog 仍在；这更像最后一条放行候选，而不是稳定的连续放行 lane。",
      chips,
    };
  }
  if (input.observeCount === 0) {
    return {
      statusLabel: input.visibleCount === 1 ? "放行把握度高" : "放行把握度很高",
      helper:
        input.visibleCount === 1
          ? "当前只剩最后 1 章且观察 backlog 已清空，这条 lane 可以很干净地完成收口。"
          : `当前还有 ${formatNumber(input.visibleCount)} 章可直放，观察 backlog 已清空，适合把这条 lane 当成稳定的连续放行链。`,
      chips,
    };
  }
  return {
    statusLabel: "放行把握度稳定",
    helper: `当前 scope 仍有 ${formatNumber(input.visibleCount)} 章可直放，同时观察 backlog 还有 ${formatNumber(
      input.observeCount
    )} 章；这条 lane 还值得继续推进，但已经需要开始关注收尾切换。`,
    chips,
  };
}

function buildReleaseLaneHealthSummary(input: {
  pressureActionKind: ReleaseLanePressureAction["actionKind"];
  confidenceStatus: string;
  driftStatus: string;
}): ReleaseLaneHealthSummary {
  const chips = [
    `去留 · ${input.pressureActionKind}`,
    `把握度 · ${input.confidenceStatus}`,
    `漂移 · ${input.driftStatus}`,
  ];
  if (input.pressureActionKind === "reset" || input.driftStatus === "已回退到观察链") {
    return {
      statusLabel: "需要切回观察修正",
      helper: "当前这条 lane 已经不再是稳定的 release-ready 批处理，应该优先回到观察链或整条队列重新分诊。",
      chips,
    };
  }
  if (
    input.pressureActionKind === "switch-observe" ||
    input.confidenceStatus === "放行把握度临界" ||
    input.driftStatus === "正在逼近切换点"
  ) {
    return {
      statusLabel: "临界收尾",
      helper: "当前这条 lane 仍有 release-ready 价值，但已经进入收尾窗口，适合在最后一条放行候选与观察 backlog 之间快速切换。",
      chips,
    };
  }
  if (
    input.confidenceStatus === "放行把握度很高" ||
    input.confidenceStatus === "放行把握度高" ||
    input.driftStatus === "持续变稳"
  ) {
    return {
      statusLabel: "健康可冲",
      helper: "当前这条 lane 具备稳定推进 release-ready 的条件，reviewer 可以把它当成高质量的连续放行工作面。",
      chips,
    };
  }
  return {
    statusLabel: "稳态推进",
    helper: "当前这条 lane 仍值得继续推进，但已经需要留意下一步是否转入最后观察收尾。",
    chips,
  };
}

function buildReleaseLaneRoutingCue(input: {
  healthSummary: ReleaseLaneHealthSummary;
  pressureAction: ReleaseLanePressureAction | null;
  exitStrategy: ReleaseLaneExitStrategy | null;
}): ReleaseLaneRoutingCue {
  if (input.exitStrategy) {
    return {
      statusLabel: input.exitStrategy.statusLabel,
      helper: `Lane Health 显示 ${input.healthSummary.statusLabel}。${input.exitStrategy.helper}`,
      actionLabel: input.exitStrategy.actionLabel,
      source: "exit",
      chips: [
        `Lane Health · ${input.healthSummary.statusLabel}`,
        `退出策略 · ${input.exitStrategy.statusLabel}`,
      ],
    };
  }
  if (input.pressureAction) {
    return {
      statusLabel: input.pressureAction.statusLabel,
      helper: `Lane Health 显示 ${input.healthSummary.statusLabel}。${input.pressureAction.helper}`,
      actionLabel: input.pressureAction.actionLabel,
      source: "pressure",
      chips: [
        `Lane Health · ${input.healthSummary.statusLabel}`,
        `压力建议 · ${input.pressureAction.statusLabel}`,
      ],
    };
  }
  return {
    statusLabel: input.healthSummary.statusLabel,
    helper: input.healthSummary.helper,
    actionLabel: "保持当前视角",
    source: "pressure",
    chips: [`Lane Health · ${input.healthSummary.statusLabel}`],
  };
}

function buildReleaseLaneEntryCue(input: {
  routingCue: ReleaseLaneRoutingCue;
  healthSummary: ReleaseLaneHealthSummary;
  chapterLabel: string | null;
}): ReleaseLaneEntryCue {
  return {
    statusLabel: input.routingCue.statusLabel,
    helper: input.chapterLabel
      ? `进入当前 release-ready 子队列后，先按这条判断决定是否继续停留在 ${input.chapterLabel}，不用先扫中段 supporting cards。`
      : "进入当前 release-ready 子队列后，先按这条判断决定是否继续停留在这条 lane，不用先扫中段 supporting cards。",
    chips: [
      `Lane Health · ${input.healthSummary.statusLabel}`,
      `入口动作 · ${input.routingCue.statusLabel}`,
    ],
  };
}

function buildReleaseLaneDrift(input: {
  hasGateFailures: boolean;
  visibleCount: number;
  observeCount: number;
}): ReleaseLaneDrift {
  const chips = [
    `可直放 ${formatNumber(input.visibleCount)}`,
    `观察 backlog ${formatNumber(input.observeCount)}`,
    input.hasGateFailures ? "gate 波动" : "gate 稳定",
  ];
  if (input.hasGateFailures) {
    return {
      statusLabel: "已回退到观察链",
      helper: "当前章节的放行门出现波动，这条 lane 的趋势已经从 release-ready 转回继续观察修正。",
      chips,
    };
  }
  if (input.visibleCount <= 0) {
    return {
      statusLabel: "已进入收尾回扫",
      helper: "当前 scope 已没有可直放章节，这条 lane 的趋势已经明确转入最后观察或回队列收尾。",
      chips,
    };
  }
  if (input.visibleCount === 1 && input.observeCount > 0) {
    return {
      statusLabel: "正在逼近切换点",
      helper: "当前只剩最后一条 release-ready 候选，观察 backlog 仍在；这条 lane 已经明显向收尾切换漂移。",
      chips,
    };
  }
  if (input.observeCount === 0) {
    return {
      statusLabel: input.visibleCount > 1 ? "持续变稳" : "平稳收口",
      helper:
        input.visibleCount > 1
          ? "当前可直放章节仍然充足，观察 backlog 已清空，这条 lane 正在向稳定的连续放行状态变稳。"
          : "当前只剩最后一条 release-ready 且观察 backlog 已清空，这条 lane 正在平稳收口。",
      chips,
    };
  }
  return {
    statusLabel: "稳定推进中",
    helper: "当前仍有可直放章节，同时观察 backlog 还在；这条 lane 还在稳定推进，但已经开始向最终收尾缓慢漂移。",
    chips,
  };
}

function buildReleaseLanePressureAction(input: {
  visibleCount: number;
  observeCount: number;
}): ReleaseLanePressureAction {
  if (input.visibleCount <= 0) {
    if (input.observeCount > 0) {
      return {
        statusLabel: "先切回最后观察",
        helper: `当前 scope 已没有可继续推进的 release-ready 章节，先回 ${formatNumber(
          input.observeCount
        )} 章观察 backlog 收口更划算。`,
        actionLabel: "切到最后观察 backlog",
        actionKind: "switch-observe",
      };
    }
    return {
      statusLabel: "回到整条队列",
      helper: "当前 scope 下 release-ready 和观察 backlog 都已经清空，适合回到整条队列重新扫描。",
      actionLabel: "回到整条队列",
      actionKind: "reset",
    };
  }
  if (input.visibleCount === 1 && input.observeCount > 0) {
    return {
      statusLabel: "观察 backlog 优先",
      helper: `当前 scope 只剩最后 1 章 release-ready，而观察 backlog 还有 ${formatNumber(
        input.observeCount
      )} 章；这时更适合先切回最后观察 lane。`,
      actionLabel: "切到最后观察 backlog",
      actionKind: "switch-observe",
    };
  }
  return {
    statusLabel: "继续推进 release-ready",
    helper:
      input.observeCount > 0
        ? `当前 scope 仍有 ${formatNumber(input.visibleCount)} 章可直接放行，同时观察 backlog 还有 ${formatNumber(
            input.observeCount
          )} 章；先继续冲 release-ready 更合算。`
        : `当前 scope 还有 ${formatNumber(input.visibleCount)} 章可直接放行，观察 backlog 已清空，适合继续连续推进。`,
    actionLabel: "按 release-ready 继续推进",
    actionKind: "continue-release",
  };
}

function buildReleaseLaneFallback(
  entries: Array<{
    chapter_id: string;
    ordinal: number;
    title_src?: string | null;
    queue_rank: number;
    active_blocking_issue_count: number;
    open_issue_count: number;
    memory_proposals: { pending_proposal_count: number };
  }>
): ReleaseLaneFallback | null {
  if (!entries.length) {
    return null;
  }
  const candidate = [...entries].sort((left, right) => {
    const leftScore = buildReleaseLaneGapScore(left);
    const rightScore = buildReleaseLaneGapScore(right);
    if (leftScore !== rightScore) {
      return leftScore - rightScore;
    }
    return left.queue_rank - right.queue_rank;
  })[0];
  const chapterLabel = `第 ${candidate.ordinal} 章 · ${candidate.title_src || `Chapter ${candidate.ordinal}`}`;
  if (candidate.active_blocking_issue_count > 0) {
    return {
      chapterId: candidate.chapter_id,
      chapterLabel,
      helper: `这章离放行最近，但还有 ${formatNumber(candidate.active_blocking_issue_count)} 个 blocker 没收口，先看 follow-up / issue。`,
      chips: [
        `Blocker · ${formatNumber(candidate.active_blocking_issue_count)}`,
        `Open issues · ${formatNumber(candidate.open_issue_count)}`,
      ],
      focus: {
        chapterId: candidate.chapter_id,
        section: "actions",
        label: "最后观察 · blocker",
        helper: "已把焦点切到最接近放行的章节，先把 blocker / follow-up 收口。",
      },
    };
  }
  if (candidate.memory_proposals.pending_proposal_count > 0) {
    return {
      chapterId: candidate.chapter_id,
      chapterLabel,
      helper: `这章离放行最近，但还有 ${formatNumber(candidate.memory_proposals.pending_proposal_count)} 条 proposal 待审批，先收口 memory override。`,
      chips: [
        `Pending proposal · ${formatNumber(candidate.memory_proposals.pending_proposal_count)}`,
        `Open issues · ${formatNumber(candidate.open_issue_count)}`,
      ],
      focus: {
        chapterId: candidate.chapter_id,
        section: "proposal",
        label: "最后观察 · proposal",
        helper: "已把焦点切到最接近放行的章节，先做最后一条 proposal 决策。",
      },
    };
  }
  return {
    chapterId: candidate.chapter_id,
    chapterLabel,
    helper: `这章离放行最近，但还有 ${formatNumber(candidate.open_issue_count)} 个 open issue 待最终观察，先核对最近 action 和 recheck。`,
    chips: [`Open issues · ${formatNumber(candidate.open_issue_count)}`],
    focus: {
      chapterId: candidate.chapter_id,
      section: "actions",
      label: "最后观察 · open issues",
      helper: "已把焦点切到最接近放行的章节，先核对最后观察和 rerun / recheck 是否都落盘。",
    },
  };
}

function buildReleaseLaneGapScore(entry: {
  active_blocking_issue_count: number;
  open_issue_count: number;
  memory_proposals: { pending_proposal_count: number };
}) {
  return (
    entry.active_blocking_issue_count * 100 +
    entry.memory_proposals.pending_proposal_count * 10 +
    entry.open_issue_count
  );
}

function recentChangeKindLabel(kind: RecentOperatorChange["kind"]) {
  if (kind === "proposal") {
    return "Proposal 回写";
  }
  if (kind === "assignment") {
    return "Assignment 回写";
  }
  return "Action 回写";
}

function sessionTrailChainLabel(kind: RecentOperatorChange["kind"]) {
  if (kind === "proposal") {
    return "Proposal -> Snapshot -> Blocker";
  }
  if (kind === "assignment") {
    return "Assignment -> Owner Handoff";
  }
  return "Action -> Rerun -> Recheck";
}

function sessionTrailRevisitHint(kind: RecentOperatorChange["kind"]) {
  if (kind === "proposal") {
    return "回跳后先看 proposal 和 blocker 是否继续收敛。";
  }
  if (kind === "assignment") {
    return "回跳后先看 owner 和 assignment 变化是否已经生效。";
  }
  return "回跳后先看 action 结果和 rerun/recheck 是否已经落盘。";
}

function buildSessionDigest(entries: SessionTrailEntry[]): SessionDigest {
  const latest = entries[0];
  const proposalCount = entries.filter((entry) => entry.kind === "proposal").length;
  const assignmentCount = entries.filter((entry) => entry.kind === "assignment").length;
  const actionCount = entries.filter((entry) => entry.kind === "action").length;
  return {
    processedCount: entries.length,
    latestChapterLabel: latest.chapterLabel,
    latestChainLabel: latest.chainLabel,
    kindSummary: [
      actionCount ? `Action ${actionCount}` : null,
      proposalCount ? `Proposal ${proposalCount}` : null,
      assignmentCount ? `Assignment ${assignmentCount}` : null,
    ].filter(Boolean) as string[],
    continuityHint: latest.revisitHint,
  };
}

function timelineEntryMatchesRecentChange(
  entry: ChapterWorklistTimelineEntry,
  change: RecentOperatorChange
) {
  if (change.kind === "proposal") {
    return entry.source_kind === "memory_proposal";
  }
  if (change.kind === "assignment") {
    return entry.source_kind === "assignment";
  }
  return entry.source_kind === "action";
}

function buildRecentChangeNextStep(
  change: RecentOperatorChange,
  snapshot: OperatorConvergenceSnapshot | null,
  detail:
    | {
        current_active_blocking_issue_count: number;
        memory_proposals: { pending_proposal_count: number };
        assignment?: { owner_name?: string | null } | null;
      }
    | null
) {
  if (!snapshot || !detail) {
    return null;
  }
  if (change.kind === "proposal") {
    if (snapshot.pendingProposalCount > 0) {
      return {
        title: "继续清理剩余 proposal",
        body: `当前还有 ${snapshot.pendingProposalCount} 条待审批 proposal，优先继续处理 memory override，避免章节停在半收敛状态。`,
        actionKind: "proposal",
        actionLabel: "聚焦下一条 proposal",
      };
    }
    if (detail.current_active_blocking_issue_count > 0) {
      return {
        title: "转入 blocker / follow-up 处理",
        body: `proposal 已收敛，但当前仍有 ${detail.current_active_blocking_issue_count} 个 blocker，下一步应切回 issue/action 面继续推进。`,
        actionKind: "action",
        actionLabel: "切到 follow-up",
      };
    }
    return {
      title: "切回章节队列确认是否可放行",
      body: "当前没有待审批 proposal，也没有明显 blocker，可以回到章节队列判断这章是否已经达到下一阶段门槛。",
      actionKind: null,
    };
  }
  if (change.kind === "assignment") {
    return detail.assignment?.owner_name
      ? {
          title: "由新 owner 接手 follow-up",
          body: `章节现在已经绑定到 ${detail.assignment.owner_name}，下一步应由当前 owner 继续执行 blocker 处理或 proposal 审批。`,
          actionKind: "assignment",
          actionLabel: "查看当前 owner",
        }
      : {
          title: "回到共享队列重新分诊",
          body: "章节已回收到共享队列，下一步应由值班 operator 重新确认优先级、owner 和 follow-up 动作。",
          actionKind: "assignment",
          actionLabel: "查看 assignment",
        };
  }
  return snapshot.actionStatus === "completed"
    ? {
        title: "复核 rerun / recheck 结果",
        body: "follow-up action 已执行完成，下一步应检查 rerun 后的 issue、timeline 和 queue 状态是否真正收敛。",
        actionKind: "action",
        actionLabel: "查看 rerun 结果",
      }
    : {
        title: "继续盯 action 执行结果",
        body: "当前 action 还没有进入完成态，下一步应继续观察 recheck / rerun 是否落盘，再决定是否扩大处理范围。",
        actionKind: "action",
        actionLabel: "切到 follow-up",
      };
}

function buildNextQueueRecommendation(entry: {
  chapter_id: string;
  ordinal: number;
  title_src?: string | null;
  active_blocking_issue_count: number;
  open_issue_count: number;
  dominant_issue_type?: string | null;
  memory_proposals: { pending_proposal_count: number };
  assigned_owner_name?: string | null;
  queue_driver?: string | null;
}) {
  const chapterLabel = `第 ${entry.ordinal} 章 · ${entry.title_src || `Chapter ${entry.ordinal}`}`;
  if (entry.active_blocking_issue_count > 0) {
    return {
      title: "下一章先看 blocker / follow-up",
      body: `${chapterLabel} 当前有 ${entry.active_blocking_issue_count} 个 blocker，优先核对 follow-up action 和 issue family。`,
      actionLabel: "切到下一章 blocker",
      focus: {
        chapterId: entry.chapter_id,
        section: "actions",
        label: `Follow-up Action · ${entry.dominant_issue_type || chapterLabel}`,
        helper: "已把焦点切到下一章的 follow-up action，优先处理最紧的 blocker。",
      },
    } satisfies NextQueueRecommendation;
  }
  if (entry.memory_proposals.pending_proposal_count > 0) {
    return {
      title: "下一章先清 pending proposal",
      body: `${chapterLabel} 还有 ${entry.memory_proposals.pending_proposal_count} 条待审批 proposal，优先把 memory override 收敛。`,
      actionLabel: "切到下一章 proposal",
      focus: {
        chapterId: entry.chapter_id,
        section: "proposal",
        label: `Memory Override · ${chapterLabel}`,
        helper: "已把焦点切到下一章的 proposal 区，可以继续做 approve / reject。",
      },
    } satisfies NextQueueRecommendation;
  }
  if (entry.open_issue_count > 0) {
    return {
      title: "下一章先看 follow-up action",
      body: `${chapterLabel} 仍有 ${entry.open_issue_count} 个 open issues，优先检查 action 和 rerun 收敛情况。`,
      actionLabel: "切到下一章重点",
      focus: {
        chapterId: entry.chapter_id,
        section: "actions",
        label: `Follow-up Action · ${chapterLabel}`,
        helper: "已把焦点切到下一章的 follow-up action，可以直接核对当前最关键的问题链。",
      },
    } satisfies NextQueueRecommendation;
  }
  return {
    title: "下一章先确认 owner / queue 状态",
    body: `${chapterLabel} 当前由 ${entry.assigned_owner_name || "共享队列"} 承接，可以先确认 owner handoff 是否还需要调整。`,
    actionLabel: "切到下一章 owner",
    focus: {
      chapterId: entry.chapter_id,
      section: "assignment",
      label: `Assignment · ${entry.assigned_owner_name || "共享队列"}`,
      helper: "已把焦点切到下一章的 assignment 控制区，可以继续分派或回收。",
    },
  } satisfies NextQueueRecommendation;
}

function buildFlowHandoffSteps(
  detail: {
    current_active_blocking_issue_count: number;
    memory_proposals: { pending_proposal_count: number; active_snapshot_version?: number | null };
    assignment?: { owner_name?: string | null } | null;
    recent_actions: Array<{ status: string; action_type?: string | null }>;
  },
  queueEntry:
    | {
        queue_driver?: string | null;
        open_issue_count?: number;
      }
    | null
): FlowHandoffStep[] {
  const latestAction = detail.recent_actions[0];
  const steps: Omit<FlowHandoffStep, "orderLabel">[] = [];
  const pushStep = (step: Omit<FlowHandoffStep, "orderLabel">) => {
    if (steps.some((current) => current.section === step.section)) {
      return;
    }
    steps.push(step);
  };

  if (detail.current_active_blocking_issue_count > 0) {
    pushStep({
      title: "先稳 blocker",
      value: `${detail.current_active_blocking_issue_count} 个 blocker`,
      helper: `先沿着 ${queueEntry?.queue_driver || "当前阻断"} 这条链路收口，别在切章后立刻分散到次级信号。`,
      section: "actions",
      actionLabel: "先看 blocker / follow-up",
    });
  } else if (queueEntry?.open_issue_count && queueEntry.open_issue_count > 0 && latestAction) {
    pushStep({
      title: "先看 recent action",
      value: `${latestAction.action_type || "当前 action"} · ${statusLabel(latestAction.status)}`,
      helper: "这章虽然没有 active blocker，但仍有 open issues，先核对最近 follow-up 有没有把问题真正往下推。",
      section: "actions",
      actionLabel: "先看当前 action",
    });
  } else if (detail.memory_proposals.pending_proposal_count > 0) {
    pushStep({
      title: "先清 proposal",
      value: `${detail.memory_proposals.pending_proposal_count} 条待审批`,
      helper: "这章没有 active blocker，先把 proposal 做掉，避免切入下一轮 review 时还留着半收敛 memory。",
      section: "proposal",
      actionLabel: "先看 pending proposal",
    });
  } else if (latestAction) {
    pushStep({
      title: "先看 recent action",
      value: `${latestAction.action_type || "当前 action"} · ${statusLabel(latestAction.status)}`,
      helper: "先从这章最近一次 follow-up 入手，确认它有没有把问题继续往下推进。",
      section: "actions",
      actionLabel: "先看当前 action",
    });
  }

  if (detail.memory_proposals.pending_proposal_count > 0) {
    pushStep({
      title: "再看 proposal",
      value: `Snapshot v${detail.memory_proposals.active_snapshot_version ?? "—"}`,
      helper: "确认 memory override 是该批准、驳回，还是应该让它继续留在待审面。",
      section: "proposal",
      actionLabel: "查看 proposal 面",
    });
  }

  if (latestAction) {
    pushStep({
      title: "再核 recent action",
      value: `${latestAction.action_type || "当前 action"} · ${statusLabel(latestAction.status)}`,
      helper: "就算切章已经完成，还是要确认最新 follow-up 有没有真的把问题往下推。",
      section: "actions",
      actionLabel: "查看 recent action",
    });
  }

  pushStep({
    title: "最后确认 owner",
    value: detail.assignment?.owner_name ? `Owner ${detail.assignment.owner_name}` : "共享队列",
    helper: detail.assignment?.owner_name
      ? "最后再确认 owner handoff 还是否合理，避免因为切章顺手把本章 owner 语义弄乱。"
      : "如果前两步已经收敛，就确认这章是否还需要继续留在共享队列。",
    section: "assignment",
    actionLabel: "查看 assignment",
  });

  return steps.map((step, index) => ({
    ...step,
    orderLabel: String(index + 1),
  }));
}

function buildFocusedPriorityItems(
  detail: {
    current_active_blocking_issue_count: number;
    memory_proposals: { pending_proposal_count: number; active_snapshot_version?: number | null };
    assignment?: { owner_name?: string | null } | null;
    recent_actions: Array<{ status: string; action_type?: string | null }>;
  },
  queueEntry:
    | {
        queue_driver?: string | null;
        open_issue_count: number;
      }
    | null
): FocusedPriorityItem[] {
  const latestAction = detail.recent_actions[0];
  return [
    detail.current_active_blocking_issue_count > 0
      ? {
          rankLabel: "先处理",
          label: "首要阻断",
          value: `${detail.current_active_blocking_issue_count} 个 blocker`,
          hint: `优先从 Issue / Action Summary 和 timeline 中定位当前阻断链，当前 queue driver 为 ${queueEntry?.queue_driver || "章节阻断"}。`,
          section: "actions",
          actionLabel: "查看 blocker / follow-up",
        }
      : {
          rankLabel: "先处理",
          label: "首要阻断",
          value: "当前无 blocker",
          hint: "这章暂时没有 active blocking issue，可以把注意力转到 proposal 或 follow-up 收敛上。",
          section: "proposal",
          actionLabel: "查看当前 proposal",
        },
    detail.memory_proposals.pending_proposal_count > 0
      ? {
          rankLabel: "随后",
          label: "待决 Proposal",
          value: `${detail.memory_proposals.pending_proposal_count} 条待审批`,
          hint: `当前 snapshot v${detail.memory_proposals.active_snapshot_version ?? "—"}，优先决定 proposal 是否进入正式 chapter memory。`,
          section: "proposal",
          actionLabel: "查看 pending proposal",
        }
      : {
          rankLabel: "随后",
          label: "待决 Proposal",
          value: "proposal 已收敛",
          hint: "当前没有待审批 proposal，可以把精力放回 blocker、action 或 owner handoff。",
          section: "actions",
          actionLabel: "查看当前 action",
        },
    latestAction
      ? {
          rankLabel: "最后复核",
          label: "Follow-up",
          value: `${latestAction.action_type || "当前 action"} · ${statusLabel(latestAction.status)}`,
          hint:
            latestAction.status === "completed"
              ? "最新 follow-up 已完成，下一步应优先核对 rerun / recheck 结果是否真正收敛。"
              : "最新 follow-up 还没进入完成态，继续盯 action 结果比扫描整条队列更重要。",
          section: "actions",
          actionLabel: "查看当前 follow-up",
        }
      : {
          rankLabel: "最后复核",
          label: "Follow-up",
          value: detail.assignment?.owner_name ? `Owner ${detail.assignment.owner_name}` : "共享队列",
          hint: "当前章节还没有 recent action，可先确认 owner handoff 或直接处理 proposal / blocker。",
          section: "assignment",
          actionLabel: "查看当前 owner",
        },
  ];
}

function buildPendingChapterFocusTarget(
  pending: PendingChapterFocus,
  detail: {
    recent_actions: Array<{ action_id: string; action_type?: string | null; issue_type?: string | null }>;
    memory_proposals: { pending_proposals: Array<{ proposal_id: string }> };
    assignment?: { owner_name?: string | null } | null;
  }
): TimelineFocusTarget {
  if (pending.section === "actions") {
    const actionEntry = detail.recent_actions[0];
    return {
      eventId: `next-chapter-focus-${pending.chapterId}-actions`,
      section: "actions",
      actionId: actionEntry?.action_id ?? null,
      label: `Follow-up Action · ${actionEntry?.action_type || actionEntry?.issue_type || pending.label}`,
      helper: pending.helper,
    };
  }
  if (pending.section === "proposal") {
    const proposal = detail.memory_proposals.pending_proposals[0];
    return {
      eventId: `next-chapter-focus-${pending.chapterId}-proposal`,
      section: "proposal",
      proposalId: proposal?.proposal_id ?? null,
      label: `Memory Override · ${shorten(proposal?.proposal_id ?? pending.chapterId, 5)}`,
      helper: pending.helper,
    };
  }
  return {
    eventId: `next-chapter-focus-${pending.chapterId}-assignment`,
    section: "assignment",
    label: `Assignment · ${detail.assignment?.owner_name || "共享队列"}`,
    helper: pending.helper,
  };
}
