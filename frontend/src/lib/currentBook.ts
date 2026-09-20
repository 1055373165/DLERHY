/** The last book the reader opened, so old addresses like /runs can still land on it. Per browser. */
export const STORAGE_KEY_DOCUMENT = "book-agent.current-document-id";

export function currentBookId(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY_DOCUMENT);
  } catch {
    return null;
  }
}

export function rememberBook(documentId: string): void {
  try {
    window.localStorage.setItem(STORAGE_KEY_DOCUMENT, documentId);
  } catch {
    /* only old addresses depend on it */
  }
}
