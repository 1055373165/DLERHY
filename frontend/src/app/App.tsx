import { QueryClientProvider } from "@tanstack/react-query";
import { Navigate, Route, Routes, useParams } from "react-router-dom";

import { AppLayout } from "../components/AppLayout";
import { BookIndex, BookPage } from "../features/book/BookPage";
import { LogTab } from "../features/book/LogTab";
import { ProgressTab } from "../features/book/ProgressTab";
import { TermsTab } from "../features/book/TermsTab";
import { HomePage } from "../features/home/HomePage";
import { InboxPage } from "../features/inbox/InboxPage";
import { IssuesPage } from "../features/issues/IssuesPage";
import { LibraryPage } from "../features/library/LibraryPage";
import { ReaderPage } from "../features/library/ReaderPage";
import { SettingsPage } from "../features/settings/SettingsPage";
import { WorkspacePage } from "../features/workspace/WorkspacePage";
import { STORAGE_KEY_DOCUMENT, WorkspaceProvider } from "./WorkspaceContext";
import { queryClient } from "./queryClient";

function currentBookId(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY_DOCUMENT);
  } catch {
    return null;
  }
}

/** Old per-concept pages now live on the book's page; without a current book, go home. Replace, so Back does not bounce. */
function ToCurrentBook({ tab }: { tab: string }) {
  const id = currentBookId();
  return <Navigate to={id ? `/books/${id}/${tab}` : "/"} replace />;
}

function OldReaderLink() {
  const { documentId = "" } = useParams();
  return <Navigate to={`/books/${documentId}/read`} replace />;
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <WorkspaceProvider>
        <Routes>
          <Route element={<AppLayout />}>
            <Route path="/" element={<HomePage />} />
            <Route path="/books/:documentId" element={<BookPage />}>
              <Route index element={<BookIndex />} />
              <Route path="progress" element={<ProgressTab />} />
              <Route path="read" element={<ReaderPage />} />
              <Route path="terms" element={<TermsTab />} />
              <Route path="issues" element={<IssuesPage />} />
              <Route path="log" element={<LogTab />} />
              <Route path="chapters" element={<WorkspacePage />} />
            </Route>
            <Route path="/inbox" element={<InboxPage />} />
            <Route path="/library" element={<LibraryPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            {/* Pages from before the book-centred layout. */}
            <Route path="/library/:documentId/read" element={<OldReaderLink />} />
            <Route path="/runs" element={<ToCurrentBook tab="progress" />} />
            <Route path="/deliverables" element={<ToCurrentBook tab="read" />} />
            <Route path="/issues" element={<ToCurrentBook tab="issues" />} />
            <Route path="/approvals" element={<Navigate to="/inbox" replace />} />
            <Route path="/providers" element={<Navigate to="/settings" replace />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </WorkspaceProvider>
    </QueryClientProvider>
  );
}
