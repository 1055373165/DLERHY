import { useState } from "react";

import { fastReadingPreferred, setFastReadingPreferred } from "../lib/preferences";
import s from "./FastReadingToggle.module.css";

/** Next to "start": whether the run may lock terms without asking. Applies to runs started afterwards. */
export function FastReadingToggle() {
  const [on, setOn] = useState(fastReadingPreferred);
  return (
    <label className={s.toggle}>
      <input
        type="checkbox"
        checked={on}
        onChange={(event) => {
          setOn(event.target.checked);
          setFastReadingPreferred(event.target.checked);
        }}
      />
      <span className={s.text}>
        <strong>快速阅读模式</strong>
        {on
          ? "：术语直接锁定，不再停下来等你确认，翻译一口气跑完。锁定的术语可以之后在书的「术语与决策」里改。"
          : "：已关闭。遇到需要锁定的术语会停下来，等你在「需要你处理」里确认后才继续翻译。"}
      </span>
    </label>
  );
}
