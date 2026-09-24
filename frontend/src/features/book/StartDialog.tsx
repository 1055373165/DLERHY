import { useRef, useState } from "react";

import { CostEstimateNote } from "./CostEstimateNote";
import { enableNotifications, notificationsOn, notificationsSupported } from "../../lib/notifications";
import { usd } from "../../lib/overview";
import { fastReadingPreferred, setFastReadingPreferred } from "../../lib/preferences";
import { useFocusTrap } from "../../lib/useFocusTrap";
import s from "./StartDialog.module.css";

/** What the dialog starts: the free pilot, the whole book straight away, or the rest of the book after the pilot. */
export type StartMode = "pilot" | "book" | "rest";

const TITLES: Record<StartMode, { heading: string; name: string; confirm: string }> = {
  pilot: { heading: "免费试译前两章", name: "免费试译", confirm: "开始免费试译" },
  book: { heading: "开始翻译这本书", name: "开始翻译", confirm: "开始翻译" },
  rest: { heading: "翻译全书", name: "翻译全书", confirm: "翻译全书" },
};

/** The one stop before translating: what it may cost, whether terms lock by themselves,
 * and whether to be told when it is done (the moment where asking for notifications makes sense). */
export function StartDialog({
  documentId,
  maxChargeUsd,
  pending,
  onCancel,
  onStart,
  mode = "book",
  pilotChapters = [],
  onSkipPilot,
}: {
  documentId: string;
  maxChargeUsd: number | null;
  pending: boolean;
  onCancel: () => void;
  onStart: (options: { fastReading: boolean }) => void;
  mode?: StartMode;
  /** The pilot's chapter titles, to say what will be translated. */
  pilotChapters?: string[];
  /** Pilot mode: translate the whole book instead (paid). */
  onSkipPilot?: (options: { fastReading: boolean }) => void;
}) {
  const [fastReading, setFastReading] = useState(fastReadingPreferred);
  const [notify, setNotify] = useState(notificationsOn);
  const [notifyRefused, setNotifyRefused] = useState(false);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  // Escape, the Tab cycle and giving the focus back to the button that opened it all live in the hook.
  useFocusTrap(dialogRef, { onClose: onCancel, initialFocus: confirmRef });
  const titles = TITLES[mode];
  const chapters = pilotChapters.filter(Boolean).map((title) => `「${title}」`).join("和");

  return (
    <div className={s.backdrop} onClick={onCancel}>
      <div ref={dialogRef} className={s.dialog} role="dialog" aria-modal="true" aria-label={titles.name} onClick={(event) => event.stopPropagation()}>
        <h2 className={s.title}>{titles.heading}</h2>
        {mode === "pilot" ? (
          <>
            <p className={s.cost}>
              <strong className={s.free}>免费</strong>：{chapters ? `前两章${chapters}` : "前两章"}
              走完整流程——确定术语、翻译、润色、模型审校、自动复核，再生成中文版。费用由我们承担，不从你的余额里扣。
            </p>
            <p className={s.next}>
              试译好了，书页会打开试译结果：读一读这两章，看看还剩哪些问题，再决定要不要翻译全书。满意再付费，不满意不花钱。
            </p>
          </>
        ) : (
          <>
            <p className={s.cost}>
              {maxChargeUsd === null
                ? "服务商还没有设置单价，估算不出金额。翻译会按实际用量扣费。"
                : `预计最多花费 ${usd(maxChargeUsd)}（按服务商设置的单价估算，实际通常更少）。`}
              {mode === "rest" && "试译过的两章不再翻译、不再收费，术语表也沿用试译时定下的。"}
            </p>
            <CostEstimateNote documentId={documentId} remaining={mode === "rest"} />
          </>
        )}

        <label className={s.option}>
          <input type="checkbox" checked={fastReading} onChange={(event) => setFastReading(event.target.checked)} />
          <span>
            <strong>快速阅读模式</strong>：术语直接锁定，不停下来等你确认，一口气翻完。锁定的术语之后可以在「术语与决策」里改。
            {!fastReading && "关闭后，遇到需要锁定的术语会停下来等你批准。"}
          </span>
        </label>

        {notificationsSupported() && (
          <label className={s.option}>
            <input
              type="checkbox"
              checked={notify}
              onChange={async (event) => {
                if (!event.target.checked) {
                  setNotify(false);
                  return;
                }
                const granted = await enableNotifications();
                setNotify(granted);
                setNotifyRefused(!granted);
              }}
            />
            <span>
              <strong>完成时通知我</strong>：{mode === "pilot" ? "试译" : "翻译"}完成、失败或需要你处理时，用浏览器通知提醒（现在会问一次权限）。
              {notifyRefused && <span className={s.refused}>浏览器没有允许通知，可以在浏览器的网站设置里打开。</span>}
            </span>
          </label>
        )}

        <div className={s.actions}>
          {mode === "pilot" && onSkipPilot && (
            <button
              className={`btn btn-sm ${s.skip}`}
              disabled={pending}
              onClick={() => {
                setFastReadingPreferred(fastReading);
                onSkipPilot({ fastReading });
              }}
            >
              跳过试译，直接翻译全书{maxChargeUsd !== null ? `（约 ${usd(maxChargeUsd)}）` : ""}
            </button>
          )}
          <button className="btn" onClick={onCancel} disabled={pending}>
            取消
          </button>
          <button
            ref={confirmRef}
            className="btn btn-primary"
            disabled={pending}
            onClick={() => {
              setFastReadingPreferred(fastReading);
              onStart({ fastReading });
            }}
          >
            {pending ? "正在开始…" : titles.confirm}
          </button>
        </div>
      </div>
    </div>
  );
}
