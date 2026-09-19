import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { queryClient } from "../../app/queryClient";
import { type Issue, type StructureEditRequest, createStructureEdit } from "../../lib/api";
import s from "./StructureEditForm.module.css";

type EditKind = StructureEditRequest["kind"];

const KIND_TEXT: Record<EditKind, string> = {
  relabel_block: "改块类型",
  split_block: "拆分块",
  merge_blocks: "合并到前一块",
  link_caption: "关联图注",
};

// The Structure Agent's problem kinds and the edit that fixes each.
const KIND_BY_PROBLEM: Record<string, EditKind> = {
  wrong_block_type: "relabel_block",
  missed_heading: "relabel_block",
  false_heading: "relabel_block",
  bad_merge: "split_block",
  bad_split: "merge_blocks",
  caption_link: "link_caption",
};

const BLOCK_TYPES = ["paragraph", "heading", "quote", "footnote", "caption", "code", "list_item", "equation"];

type Props = { documentId: string; issue: Issue };

/** Applies a structure edit for a STRUCTURE_SUGGESTION issue; the fork keeps translations of unchanged sentences. */
export function StructureEditForm({ documentId, issue }: Props) {
  const evidence = (issue.evidence_json ?? {}) as Record<string, unknown>;
  const problem = String(evidence.kind ?? "");
  const blockIds = Array.isArray(evidence.block_ids) ? evidence.block_ids.map(String) : [];
  const [kind, setKind] = useState<EditKind>(KIND_BY_PROBLEM[problem] ?? "relabel_block");
  const [first, setFirst] = useState(blockIds[0] ?? "");
  const [second, setSecond] = useState(blockIds[1] ?? "");
  const [blockType, setBlockType] = useState(problem === "false_heading" ? "paragraph" : "heading");
  const [headingLevel, setHeadingLevel] = useState("2");
  const [marker, setMarker] = useState("");
  const [reason, setReason] = useState(String(evidence.suggestion ?? ""));
  const [feedback, setFeedback] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  const apply = useMutation({
    mutationFn: () => {
      const payload: StructureEditRequest = { kind, reason: reason.trim() };
      if (kind === "relabel_block") {
        Object.assign(payload, {
          block_id: first,
          block_type: blockType,
          heading_level: blockType === "heading" ? Number(headingLevel) : null,
        });
      } else if (kind === "split_block") {
        Object.assign(payload, { block_id: first, second_part_starts_with: marker });
      } else if (kind === "merge_blocks") {
        Object.assign(payload, { first_block_id: first, second_block_id: second });
      } else {
        Object.assign(payload, { caption_block_id: first, artifact_block_id: second });
      }
      return createStructureEdit(documentId, payload);
    },
    onSuccess: async (edit) => {
      const retranslate = edit.retranslate_packet_count ?? 0;
      setFeedback({
        tone: "success",
        text: `已应用${edit.parse_revision_version ? `（解析版本 v${edit.parse_revision_version}）` : ""}${
          retranslate ? `，${retranslate} 个 packet 需要重译` : "，译文无需重译"
        }。确认无误后可将问题标记为已解决。`,
      });
      await queryClient.invalidateQueries({ queryKey: ["issues"] });
    },
    onError: (error) => setFeedback({ tone: "error", text: error instanceof Error ? error.message : String(error) }),
  });

  const twoBlocks = kind === "merge_blocks" || kind === "link_caption";
  const firstLabel = kind === "merge_blocks" ? "前一块" : kind === "link_caption" ? "图注块" : "块";
  const secondLabel = kind === "merge_blocks" ? "后一块" : "图/表块";
  const ready =
    reason.trim().length > 0 && first.trim().length > 0 && (!twoBlocks || second.trim().length > 0) && (kind !== "split_block" || marker.trim().length >= 3);

  return (
    <form
      className={s.form}
      aria-label="结构编辑表单"
      onSubmit={(event) => {
        event.preventDefault();
        setFeedback(null);
        apply.mutate();
      }}
    >
      <label className={s.label} htmlFor="structure-edit-kind">结构编辑</label>
      <select id="structure-edit-kind" className={s.input} value={kind} onChange={(event) => setKind(event.target.value as EditKind)}>
        {(Object.keys(KIND_TEXT) as EditKind[]).map((option) => (
          <option key={option} value={option}>{KIND_TEXT[option]}</option>
        ))}
      </select>
      <label className={s.label} htmlFor="structure-edit-first">{firstLabel}</label>
      <BlockInput id="structure-edit-first" value={first} options={blockIds} onChange={setFirst} />
      {twoBlocks ? (
        <>
          <label className={s.label} htmlFor="structure-edit-second">{secondLabel}</label>
          <BlockInput id="structure-edit-second" value={second} options={blockIds} onChange={setSecond} />
        </>
      ) : null}
      {kind === "relabel_block" ? (
        <div className={s.inline}>
          <label className={s.label} htmlFor="structure-edit-type">新类型</label>
          <select id="structure-edit-type" className={s.input} value={blockType} onChange={(event) => setBlockType(event.target.value)}>
            {BLOCK_TYPES.map((type) => <option key={type} value={type}>{type}</option>)}
          </select>
          {blockType === "heading" ? (
            <>
              <label className={s.label} htmlFor="structure-edit-level">级别</label>
              <select id="structure-edit-level" className={s.input} value={headingLevel} onChange={(event) => setHeadingLevel(event.target.value)}>
                {["1", "2", "3", "4", "5", "6"].map((level) => <option key={level} value={level}>h{level}</option>)}
              </select>
            </>
          ) : null}
        </div>
      ) : null}
      {kind === "split_block" ? (
        <>
          <label className={s.label} htmlFor="structure-edit-marker">第二块开头的原文</label>
          <input id="structure-edit-marker" className={s.input} value={marker} onChange={(event) => setMarker(event.target.value)} placeholder="至少 3 个字符，在块中只出现一次" />
        </>
      ) : null}
      <label className={s.label} htmlFor="structure-edit-reason">理由</label>
      <input id="structure-edit-reason" className={s.input} value={reason} onChange={(event) => setReason(event.target.value)} />
      <div>
        <button type="submit" className="btn btn-sm" disabled={!ready || apply.isPending}>
          {apply.isPending ? "应用中…" : "应用编辑"}
        </button>
      </div>
      {feedback ? <div className={s.feedback} data-tone={feedback.tone}>{feedback.text}</div> : null}
    </form>
  );
}

function BlockInput({ id, value, options, onChange }: { id: string; value: string; options: string[]; onChange: (value: string) => void }) {
  const listId = `${id}-options`;
  return (
    <>
      <input id={id} className={s.input} list={listId} value={value} onChange={(event) => onChange(event.target.value)} placeholder="block id" />
      <datalist id={listId}>
        {options.map((option) => <option key={option} value={option} />)}
      </datalist>
    </>
  );
}
