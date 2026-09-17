import { useState } from "react";

import { queryClient } from "../app/queryClient";
import { getStoredApiKey, setStoredApiKey } from "../lib/api";
import s from "./ApiKeySetting.module.css";

/** Sidebar control for the API key a protected deployment requires. */
export function ApiKeySetting() {
  const [stored, setStored] = useState(getStoredApiKey);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");

  const save = async () => {
    setStoredApiKey(draft);
    setStored(getStoredApiKey());
    setEditing(false);
    setDraft("");
    await queryClient.invalidateQueries();
  };

  if (!editing) {
    return (
      <button type="button" className={s.toggle} onClick={() => setEditing(true)}>
        {stored ? `API key ${stored.slice(0, 8)}…` : "设置 API key"}
      </button>
    );
  }
  return (
    <form
      className={s.form}
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <label className={s.label} htmlFor="api-key-input">API key</label>
      <input
        id="api-key-input"
        className={s.input}
        type="password"
        autoComplete="off"
        value={draft}
        placeholder={stored ? "留空并保存即清除" : "bak_…"}
        onChange={(event) => setDraft(event.target.value)}
      />
      <div className={s.buttons}>
        <button type="submit" className="btn btn-sm">保存</button>
        <button type="button" className="btn btn-sm" onClick={() => setEditing(false)}>取消</button>
      </div>
    </form>
  );
}
