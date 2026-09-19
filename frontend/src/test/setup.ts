import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterEach, vi } from "vitest";

import { queryClient } from "../app/queryClient";

// The dev machine is often heavily loaded: findBy* waits up to 10s (it returns as soon as the element appears).
configure({ asyncUtilTimeout: 10000 });

if (!("createObjectURL" in URL)) {
  Object.defineProperty(URL, "createObjectURL", {
    value: vi.fn(() => "blob:mock-url"),
    configurable: true,
  });
}

if (!("revokeObjectURL" in URL)) {
  Object.defineProperty(URL, "revokeObjectURL", {
    value: vi.fn(),
    configurable: true,
  });
}

Object.defineProperty(window.HTMLElement.prototype, "scrollIntoView", {
  value: vi.fn(),
  configurable: true,
});

afterEach(async () => {
  // Unmount first, then drop queries still in flight: otherwise a mounted page refetches, or an
  // answer still on its way lands in the next test's cache.
  cleanup();
  await queryClient.cancelQueries();
  queryClient.clear();
  window.localStorage.clear();
  vi.restoreAllMocks();
});
