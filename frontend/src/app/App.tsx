import { QueryClientProvider } from "@tanstack/react-query";
import { Route, Routes } from "react-router-dom";

import { AppLayout } from "../components/AppLayout";
import { ApprovalsPage } from "../features/approvals/ApprovalsPage";
import { IssuesPage } from "../features/issues/IssuesPage";
import { DeliverablesPage } from "../features/deliverables/DeliverablesPage";
import { LibraryPage } from "../features/library/LibraryPage";
import { ProvidersPage } from "../features/providers/ProvidersPage";
import { RunsPage } from "../features/runs/RunsPage";
import { WorkspacePage } from "../features/workspace/WorkspacePage";
import { WorkspaceProvider } from "./WorkspaceContext";
import { queryClient } from "./queryClient";

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <WorkspaceProvider>
        <Routes>
          <Route element={<AppLayout />}>
            <Route path="/" element={<WorkspacePage />} />
            <Route path="/runs" element={<RunsPage />} />
            <Route path="/deliverables" element={<DeliverablesPage />} />
            <Route path="/issues" element={<IssuesPage />} />
            <Route path="/approvals" element={<ApprovalsPage />} />
            <Route path="/library" element={<LibraryPage />} />
            <Route path="/providers" element={<ProvidersPage />} />
          </Route>
        </Routes>
      </WorkspaceProvider>
    </QueryClientProvider>
  );
}
