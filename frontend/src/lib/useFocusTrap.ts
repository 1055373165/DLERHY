import { useEffect, type RefObject } from "react";

const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled]):not([type=hidden])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "summary",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

/** Keep the keyboard inside an open dialog.
 *
 * Focus moves to the dialog when it opens, Tab and Shift+Tab cycle within it, Escape closes it,
 * and whatever had the focus before gets it back on close — so a keyboard reader is never
 * dropped onto the page behind the dialog.
 */
export function useFocusTrap(
  ref: RefObject<HTMLElement | null>,
  { onClose, initialFocus }: { onClose: () => void; initialFocus?: RefObject<HTMLElement | null> }
): void {
  useEffect(() => {
    const container = ref.current;
    if (!container) return;
    const restoreTo = document.activeElement as HTMLElement | null;

    const focusable = (): HTMLElement[] =>
      Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (node) => node.offsetParent !== null || node === document.activeElement
      );

    (initialFocus?.current ?? focusable()[0] ?? container).focus();

    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
        return;
      }
      if (event.key !== "Tab") return;
      const stops = focusable();
      if (stops.length === 0) {
        event.preventDefault();
        return;
      }
      const first = stops[0];
      const last = stops[stops.length - 1];
      const active = document.activeElement;
      // Wrap at both ends, and pull the focus back if it ever left the dialog.
      if (!container.contains(active)) {
        event.preventDefault();
        first.focus();
      } else if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      restoreTo?.focus?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onClose]);
}
