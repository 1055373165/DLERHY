import { useState } from "react";

import { disableNotifications, enableNotifications, notificationsOn, notificationsSupported } from "../lib/notifications";

/** 「完成时通知我」: asks the browser for permission only when clicked. */
export function NotifyMe({ label = "完成时通知我" }: { label?: string }) {
  const [on, setOn] = useState(notificationsOn);
  const [denied, setDenied] = useState(() => notificationsSupported() && window.Notification.permission === "denied");
  if (!notificationsSupported()) return null;
  if (denied) return <span className="note">浏览器不允许通知；可以在浏览器的网站设置里打开。</span>;
  if (on) {
    return (
      <span>
        翻译完成、失败或需要你处理时会通知你。{" "}
        <button
          className="btn btn-sm"
          onClick={() => {
            disableNotifications();
            setOn(false);
          }}
        >
          不再通知
        </button>
      </span>
    );
  }
  return (
    <button
      className="btn btn-sm"
      onClick={async () => {
        const granted = await enableNotifications();
        setOn(granted);
        setDenied(!granted && window.Notification.permission === "denied");
      }}
    >
      {label}
    </button>
  );
}
