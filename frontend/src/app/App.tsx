import { QueryClientProvider } from "@tanstack/react-query";
import { Route, Routes } from "react-router-dom";

import { AppLayout } from "../components/AppLayout";
import { DeliverablesPage } from "../features/deliverables/DeliverablesPage";
import { LibraryPage } from "../features/library/LibraryPage";
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
            <Route path="/library" element={<LibraryPage />} />
          </Route>
        </Routes>
      </WorkspaceProvider>
    </QueryClientProvider>
  );
}
