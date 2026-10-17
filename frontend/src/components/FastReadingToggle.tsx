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
          ? "：出现次数少的术语也直接锁定。锁定的术语可以之后在书的「术语与决策」里改。"
          : "：已关闭。名称、缩写和常见术语自动锁定，出现次数少的术语记为首选译法（不强制），都不会停下来问你。"}
      </span>
    </label>
  );
}
