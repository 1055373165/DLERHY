import { useEffect, useRef, useState } from "react";

import { CostEstimateNote } from "./CostEstimateNote";
import { enableNotifications, notificationsOn, notificationsSupported } from "../../lib/notifications";
import { usd } from "../../lib/overview";
import { fastReadingPreferred, setFastReadingPreferred } from "../../lib/preferences";
import s from "./StartDialog.module.css";

/** The one stop before translating: what it may cost, whether terms lock by themselves,
 * and whether to be told when it is done (the moment where asking for notifications makes sense). */
export function StartDialog({
  documentId,
  maxChargeUsd,
  pending,
  onCancel,
  onStart,
}: {
  documentId: string;
  maxChargeUsd: number | null;
  pending: boolean;
  onCancel: () => void;
  onStart: (options: { fastReading: boolean }) => void;
}) {
  const [fastReading, setFastReading] = useState(fastReadingPreferred);
  const [notify, setNotify] = useState(notificationsOn);
  const [notifyRefused, setNotifyRefused] = useState(false);
  const confirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    confirmRef.current?.focus();
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onCancel();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  return (
    <div className={s.backdrop} onClick={onCancel}>
      <div className={s.dialog} role="dialog" aria-modal="true" aria-label="开始翻译" onClick={(event) => event.stopPropagation()}>
        <h2 className={s.title}>开始翻译这本书</h2>
        <p className={s.cost}>
          {maxChargeUsd === null
            ? "服务商还没有设置单价，估算不出金额。翻译会按实际用量扣费。"
            : `预计最多花费 ${usd(maxChargeUsd)}（按服务商设置的单价估算，实际通常更少）。`}
        </p>
        <CostEstimateNote documentId={documentId} />

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
              <strong>完成时通知我</strong>：翻译完成、失败或需要你处理时，用浏览器通知提醒（现在会问一次权限）。
              {notifyRefused && <span className={s.refused}>浏览器没有允许通知，可以在浏览器的网站设置里打开。</span>}
            </span>
          </label>
        )}

        <div className={s.actions}>
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
            {pending ? "正在开始…" : "开始翻译"}
          </button>
        </div>
      </div>
    </div>
  );
}
