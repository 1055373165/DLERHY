import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";

import { queryClient } from "../app/queryClient";

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

afterEach(() => {
  queryClient.clear();
  window.localStorage.clear();
  vi.restoreAllMocks();
});
