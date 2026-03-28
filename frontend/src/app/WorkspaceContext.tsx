import {
  createContext,
  startTransition,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  approveChapterMemoryProposal,
  rejectChapterMemoryProposal,
  SERVICE_LINKS,
  createRun,
  downloadChapterExport,
  downloadDocumentExport,
  getDocument,
  getDocumentChapterWorklistDetail,
  getDocumentExports,
  getHealth,
  getRun,
  getRunEvents,
  listDocumentHistory,
  retryRun,
  resumeRun,
  uploadDocument,
  type ChapterMemoryProposalDecisionResponse,
  type ChapterMemoryProposalDecisionPayload,
  type DocumentChapterWorklistDetail,
  type DocumentExportDashboard,
  type DocumentHistoryEntry,
  type DocumentRunSummary,
  type DocumentSummary,
  type HealthResponse,
  type RunAuditEvent,
} from "../lib/api";
import { getPrimaryRunAction, isRunActive } from "../lib/workflow";

interface WorkspaceContextValue {
  serviceLinks: typeof SERVICE_LINKS;
  selectedDocumentId: string | null;
  selectDocument: (documentId: string | null) => void;
  health: HealthResponse | null;
  healthLoading: boolean;
  currentDocument: DocumentSummary | null;
  currentDocumentLoading: boolean;
  currentDocumentError: string | null;
  currentRun: DocumentRunSummary | null;
  currentRunLoading: boolean;
  currentRunEvents: RunAuditEvent[];
  currentExports: DocumentExportDashboard | null;
  selectedReviewChapterId: string | null;
  selectReviewChapter: (chapterId: string | null) => void;
  currentChapterReviewDetail: DocumentChapterWorklistDetail | null;
  currentChapterReviewLoading: boolean;
  currentChapterReviewError: string | null;
  bootstrapHistory: DocumentHistoryEntry[];
  uploadPending: boolean;
  runActionPending: boolean;
  reviewDecisionPending: boolean;
  uploadFile: (file: File) => Promise<DocumentSummary>;
  runPrimaryAction: () => Promise<DocumentRunSummary>;
  refreshCurrentDocument: () => Promise<void>;
  approveMemoryProposal: (
    proposalId: string,
    payload: ChapterMemoryProposalDecisionPayload
  ) => Promise<ChapterMemoryProposalDecisionResponse>;
  rejectMemoryProposal: (
    proposalId: string,
    payload: ChapterMemoryProposalDecisionPayload
  ) => Promise<ChapterMemoryProposalDecisionResponse>;
  downloadAsset: (
    exportType: "merged_html" | "bilingual_html" | "review_package"
  ) => Promise<string>;
  downloadChapterAsset: (chapterId: string) => Promise<string>;
}

const STORAGE_KEY_DOCUMENT = "book-agent.current-document-id";

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

function readStoredDocumentId(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY_DOCUMENT);
  } catch {
    return null;
  }
}

function writeStoredDocumentId(documentId: string | null): void {
  try {
    if (documentId) {
      window.localStorage.setItem(STORAGE_KEY_DOCUMENT, documentId);
      return;
    }
    window.localStorage.removeItem(STORAGE_KEY_DOCUMENT);
  } catch {
    // ignore storage failures
  }
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [selectedDocumentId, setSelectedDocumentId] = useState<string | null>(() =>
    typeof window === "undefined" ? null : readStoredDocumentId()
  );
  const [selectedReviewChapterId, setSelectedReviewChapterId] = useState<string | null>(null);

  const healthQuery = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
    refetchInterval: 30_000,
  });

  const bootstrapHistoryQuery = useQuery({
    queryKey: ["document-history", "bootstrap"],
    queryFn: () => listDocumentHistory({ limit: 12, offset: 0 }),
    staleTime: 30_000,
  });

  const currentDocumentQuery = useQuery({
    queryKey: ["document", selectedDocumentId],
    enabled: Boolean(selectedDocumentId),
    queryFn: () => getDocument(selectedDocumentId as string),
    refetchInterval: (query) =>
      isRunActive((query.state.data as DocumentSummary | undefined)?.latest_run_status) ? 2500 : false,
    retry: false,
  });

  const currentRunId = currentDocumentQuery.data?.latest_run_id ?? null;
  const currentRunQuery = useQuery({
    queryKey: ["run", currentRunId],
    enabled: Boolean(currentRunId),
    queryFn: () => getRun(currentRunId as string),
    refetchInterval: isRunActive(currentDocumentQuery.data?.latest_run_status) ? 2500 : false,
  });

  const currentRunEventsQuery = useQuery({
    queryKey: ["run-events", currentRunId],
    enabled: Boolean(currentRunId),
    queryFn: () => getRunEvents(currentRunId as string),
    refetchInterval: isRunActive(currentRunQuery.data?.status) ? 2500 : false,
  });

  const currentExportsQuery = useQuery({
    queryKey: ["document-exports", selectedDocumentId],
    enabled: Boolean(selectedDocumentId),
    queryFn: () => getDocumentExports(selectedDocumentId as string),
    refetchInterval: isRunActive(currentRunQuery.data?.status || currentDocumentQuery.data?.latest_run_status) ? 2500 : false,
  });

  const currentChapterReviewQuery = useQuery({
    queryKey: ["chapter-worklist-detail", selectedDocumentId, selectedReviewChapterId],
    enabled: Boolean(selectedDocumentId && selectedReviewChapterId),
    queryFn: () =>
      getDocumentChapterWorklistDetail(
        selectedDocumentId as string,
        selectedReviewChapterId as string
      ),
    refetchInterval: isRunActive(currentRunQuery.data?.status || currentDocumentQuery.data?.latest_run_status) ? 2500 : false,
    retry: false,
  });

  useEffect(() => {
    writeStoredDocumentId(selectedDocumentId);
  }, [selectedDocumentId]);

  useEffect(() => {
    if (selectedDocumentId || !bootstrapHistoryQuery.data?.entries.length) {
      return;
    }
    const activeEntry = bootstrapHistoryQuery.data.entries.find((entry) =>
      isRunActive(entry.latest_run_status)
    );
    if (activeEntry) {
      startTransition(() => {
        setSelectedDocumentId(activeEntry.document_id);
      });
    }
  }, [bootstrapHistoryQuery.data, selectedDocumentId]);

  useEffect(() => {
    const chapters = currentDocumentQuery.data?.chapters ?? [];
    if (!chapters.length) {
      if (selectedReviewChapterId) {
        startTransition(() => {
          setSelectedReviewChapterId(null);
        });
      }
      return;
    }
    const stillValid = selectedReviewChapterId
      ? chapters.some((chapter) => chapter.chapter_id === selectedReviewChapterId)
      : false;
    if (stillValid) {
      return;
    }
    const preferredChapter =
      chapters.find((chapter) => chapter.open_issue_count > 0)?.chapter_id ?? chapters[0]?.chapter_id ?? null;
    startTransition(() => {
      setSelectedReviewChapterId(preferredChapter);
    });
  }, [currentDocumentQuery.data?.chapters, selectedReviewChapterId]);

  useEffect(() => {
    const message = currentDocumentQuery.error instanceof Error ? currentDocumentQuery.error.message : "";
    if (message.includes("Document not found")) {
      startTransition(() => {
        setSelectedDocumentId(null);
      });
    }
  }, [currentDocumentQuery.error]);

  function selectDocument(documentId: string | null): void {
    startTransition(() => {
      setSelectedDocumentId(documentId);
    });
  }

  function selectReviewChapter(chapterId: string | null): void {
    startTransition(() => {
      setSelectedReviewChapterId(chapterId);
    });
  }

  async function invalidateWorkspaceQueries(): Promise<void> {
    await queryClient.invalidateQueries({
      predicate(query) {
        const rootKey = String(query.queryKey[0] ?? "");
        return ["document", "run", "run-events", "document-exports", "document-history"].includes(
          rootKey
        );
      },
    });
  }

  async function refreshCurrentDocument(): Promise<void> {
    if (!selectedDocumentId) {
      return;
    }
    await invalidateWorkspaceQueries();
    await queryClient.refetchQueries({
      queryKey: ["document", selectedDocumentId],
      exact: true,
    });
  }

  const uploadMutation = useMutation({
    mutationFn: uploadDocument,
    onSuccess: async (document) => {
      queryClient.setQueryData(["document", document.document_id], document);
      selectDocument(document.document_id);
      await invalidateWorkspaceQueries();
    },
  });

  const runActionMutation = useMutation({
    mutationFn: async () => {
      const currentDocument = currentDocumentQuery.data;
      const currentRun = currentRunQuery.data;
      const action = getPrimaryRunAction(currentDocument, currentRun);
      if (!currentDocument || action.disabled) {
        throw new Error("请先上传并解析书稿。");
      }
      if (action.mode === "create") {
        const created = await createRun(currentDocument.document_id);
        return resumeRun(created.run_id, {
          actor_id: "react-ui",
          note: "start from standalone React workspace",
        });
      }
      if (action.mode === "resume" && action.runId) {
        return resumeRun(action.runId, {
          actor_id: "react-ui",
          note: "resume from standalone React workspace",
        });
      }
      if (action.mode === "retry" && action.runId) {
        return retryRun(action.runId, {
          actor_id: "react-ui",
          note: "retry from standalone React workspace",
        });
      }
      if (action.mode === "recover" && currentDocument.document_id) {
        const refreshedDocument = await getDocument(currentDocument.document_id);
        const refreshedRunId = refreshedDocument.latest_run_id;
        if (!refreshedRunId) {
          throw new Error("这条 run 还没有收敛成可恢复状态，请稍后再试。");
        }
        const refreshedRun = await getRun(refreshedRunId);
        const refreshedAction = getPrimaryRunAction(refreshedDocument, refreshedRun);
        if (refreshedAction.mode === "resume" && refreshedAction.runId) {
          return resumeRun(refreshedAction.runId, {
            actor_id: "react-ui",
            note: "resume after refresh from standalone React workspace",
          });
        }
        if (refreshedAction.mode === "retry" && refreshedAction.runId) {
          return retryRun(refreshedAction.runId, {
            actor_id: "react-ui",
            note: "retry after refresh from standalone React workspace",
          });
        }
        throw new Error("阶段失败已识别，但当前 run 仍未转为可恢复状态。");
      }
      throw new Error("当前没有可执行的运行操作。");
    },
    onSuccess: async () => {
      await invalidateWorkspaceQueries();
    },
  });

  const reviewDecisionMutation = useMutation({
    mutationFn: async ({
      proposalId,
      decision,
      payload,
    }: {
      proposalId: string;
      decision: "approved" | "rejected";
      payload: ChapterMemoryProposalDecisionPayload;
    }) => {
      if (!selectedDocumentId || !selectedReviewChapterId) {
        throw new Error("当前没有可操作的章节。");
      }
      return decision === "approved"
        ? approveChapterMemoryProposal(selectedDocumentId, selectedReviewChapterId, proposalId, payload)
        : rejectChapterMemoryProposal(selectedDocumentId, selectedReviewChapterId, proposalId, payload);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["chapter-worklist-detail", selectedDocumentId, selectedReviewChapterId],
      });
      await queryClient.invalidateQueries({
        queryKey: ["document", selectedDocumentId],
      });
    },
  });

  async function downloadAsset(
    exportType: "merged_html" | "bilingual_html" | "review_package"
  ): Promise<string> {
    if (!selectedDocumentId) {
      throw new Error("请先载入当前书籍。");
    }
    return downloadDocumentExport(selectedDocumentId, exportType);
  }

  async function downloadChapterAsset(chapterId: string): Promise<string> {
    if (!selectedDocumentId) {
      throw new Error("请先载入当前书籍。");
    }
    return downloadChapterExport(selectedDocumentId, chapterId);
  }

  const value: WorkspaceContextValue = {
    serviceLinks: SERVICE_LINKS,
    selectedDocumentId,
    selectDocument,
    health: healthQuery.data ?? null,
    healthLoading: healthQuery.isLoading,
    currentDocument: currentDocumentQuery.data ?? null,
    currentDocumentLoading: currentDocumentQuery.isLoading,
    currentDocumentError:
      currentDocumentQuery.error instanceof Error ? currentDocumentQuery.error.message : null,
    currentRun: currentRunQuery.data ?? null,
    currentRunLoading: currentRunQuery.isLoading,
    currentRunEvents: currentRunEventsQuery.data?.entries ?? [],
    currentExports: currentExportsQuery.data ?? null,
    selectedReviewChapterId,
    selectReviewChapter,
    currentChapterReviewDetail: currentChapterReviewQuery.data ?? null,
    currentChapterReviewLoading: currentChapterReviewQuery.isLoading,
    currentChapterReviewError:
      currentChapterReviewQuery.error instanceof Error ? currentChapterReviewQuery.error.message : null,
    bootstrapHistory: bootstrapHistoryQuery.data?.entries ?? [],
    uploadPending: uploadMutation.isPending,
    runActionPending: runActionMutation.isPending,
    reviewDecisionPending: reviewDecisionMutation.isPending,
    uploadFile: uploadMutation.mutateAsync,
    runPrimaryAction: runActionMutation.mutateAsync,
    refreshCurrentDocument,
    approveMemoryProposal: (proposalId, payload) =>
      reviewDecisionMutation.mutateAsync({ proposalId, decision: "approved", payload }),
    rejectMemoryProposal: (proposalId, payload) =>
      reviewDecisionMutation.mutateAsync({ proposalId, decision: "rejected", payload }),
    downloadAsset,
    downloadChapterAsset,
  };

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace(): WorkspaceContextValue {
  const value = useContext(WorkspaceContext);
  if (!value) {
    throw new Error("useWorkspace must be used within WorkspaceProvider");
  }
  return value;
}
