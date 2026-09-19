import { startTransition, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  approveChapterMemoryProposal,
  downloadChapterExport,
  executeIssueAction,
  getDocumentChapterWorklist,
  getDocumentChapterWorklistDetail,
  rejectChapterMemoryProposal,
  type ChapterListItem,
  type ChapterMemoryProposalDecisionPayload,
  type DocumentChapterWorklistFilters,
} from "../../lib/api";

type QueuePriority = "all" | "immediate" | "high" | "medium";
type Assignment = "all" | "assigned" | "unassigned";

const DEFAULT_FILTERS = { queuePriority: "all" as QueuePriority, assignment: "all" as Assignment };

/** The chapter review tab's own state: the book's chapter queue, the chosen chapter and its review actions.
 * Lives with the tab, so nothing polls for it on other pages. */
export function useChapterReview(documentId: string, chapters: ChapterListItem[], { active }: { active: boolean }) {
  const queryClient = useQueryClient();
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [selectedChapterId, setSelectedChapterId] = useState<string | null>(null);
  const poll = active ? 15_000 : false;

  const apiFilters: DocumentChapterWorklistFilters = {
    limit: 50,
    offset: 0,
    queuePriority: filters.queuePriority === "all" ? undefined : filters.queuePriority,
    assigned: filters.assignment === "all" ? undefined : filters.assignment === "assigned",
  };
  const worklist = useQuery({
    queryKey: ["chapter-worklist", documentId, filters.queuePriority, filters.assignment],
    queryFn: () => getDocumentChapterWorklist(documentId, apiFilters),
    refetchInterval: poll,
    retry: false,
  });
  const detail = useQuery({
    queryKey: ["chapter-worklist-detail", documentId, selectedChapterId],
    enabled: Boolean(selectedChapterId),
    queryFn: () => getDocumentChapterWorklistDetail(documentId, selectedChapterId as string),
    refetchInterval: poll,
    retry: false,
  });

  // Keep a chapter selected: the first in the queue, else the book's first chapter.
  useEffect(() => {
    const queue = worklist.data?.entries ?? [];
    const known = new Set([...queue.map((entry) => entry.chapter_id), ...chapters.map((chapter) => chapter.chapter_id)]);
    if (selectedChapterId && known.has(selectedChapterId)) return;
    const preferred = queue[0]?.chapter_id ?? chapters[0]?.chapter_id ?? null;
    if (preferred !== selectedChapterId) startTransition(() => setSelectedChapterId(preferred));
  }, [worklist.data?.entries, chapters, selectedChapterId]);

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["chapter-worklist", documentId] });
    await queryClient.invalidateQueries({ queryKey: ["chapter-worklist-detail", documentId] });
    await queryClient.invalidateQueries({ queryKey: ["overview", documentId] });
  };

  const decision = useMutation({
    mutationFn: ({ proposalId, approved, payload }: { proposalId: string; approved: boolean; payload: ChapterMemoryProposalDecisionPayload }) => {
      if (!selectedChapterId) throw new Error("当前没有可操作的章节。");
      return approved
        ? approveChapterMemoryProposal(documentId, selectedChapterId, proposalId, payload)
        : rejectChapterMemoryProposal(documentId, selectedChapterId, proposalId, payload);
    },
    onSuccess: refresh,
  });
  const execution = useMutation({
    mutationFn: ({ actionId, runFollowup }: { actionId: string; runFollowup: boolean }) => executeIssueAction(actionId, { runFollowup }),
    onSuccess: refresh,
  });

  return {
    chapterWorklist: worklist.data ?? null,
    chapterWorklistLoading: worklist.isLoading,
    chapterWorklistFilters: filters,
    setChapterQueuePriorityFilter: (value: QueuePriority) => startTransition(() => setFilters((current) => ({ ...current, queuePriority: value }))),
    setChapterAssignmentFilter: (value: Assignment) => startTransition(() => setFilters((current) => ({ ...current, assignment: value }))),
    clearChapterWorklistFilters: () => startTransition(() => setFilters(DEFAULT_FILTERS)),
    selectedReviewChapterId: selectedChapterId,
    selectReviewChapter: (chapterId: string | null) => startTransition(() => setSelectedChapterId(chapterId)),
    currentChapterReviewDetail: detail.data ?? null,
    currentChapterReviewLoading: detail.isLoading,
    reviewDecisionPending: decision.isPending,
    actionExecutionPending: execution.isPending,
    approveMemoryProposal: (proposalId: string, payload: ChapterMemoryProposalDecisionPayload) =>
      decision.mutateAsync({ proposalId, approved: true, payload }),
    rejectMemoryProposal: (proposalId: string, payload: ChapterMemoryProposalDecisionPayload) =>
      decision.mutateAsync({ proposalId, approved: false, payload }),
    executeChapterAction: (actionId: string, runFollowup = true) => execution.mutateAsync({ actionId, runFollowup }),
    downloadChapterAsset: (chapterId: string) => downloadChapterExport(documentId, chapterId),
  };
}
