/** One place for the Chinese wording of every enum the backend sends.
 *
 * Nothing the reader sees may be a raw enum value (SPEC 禁止事项 2). `src/test/chineseCopy.test.tsx`
 * renders each page and fails on ALL_CAPS values and on English outside a short allow list.
 */

export interface IssueTypeCopy {
  label: string;
  /** One sentence: what it means and whether it needs attention. */
  meaning: string;
}

const ISSUE_TYPES: Record<string, IssueTypeCopy> = {
  OMISSION: { label: "漏译", meaning: "原文里的一段内容在译文里找不到，通常需要处理。" },
  LOW_CONFIDENCE: { label: "把握不大", meaning: "系统对这句译文没有把握，建议你对照原文看一眼。" },
  FORMAT_POLLUTION: { label: "格式串入译文", meaning: "译文里混进了标记或代码之类不该翻译的内容。" },
  TERM_CONFLICT: { label: "术语不一致", meaning: "这里的译法和锁定的术语不一致，会影响全书统一。" },
  UNLOCKED_KEY_CONCEPT: { label: "关键概念未锁定", meaning: "一个反复出现的概念还没有固定译法，建议锁定。" },
  STALE_CHAPTER_BRIEF: { label: "章节摘要过期", meaning: "这一章的上下文摘要落后于正文，可能影响后续翻译。" },
  STYLE_DRIFT: { label: "文风不一致", meaning: "这段的语气和全书设定的文风不一样，不影响准确性。" },
  DUPLICATION: { label: "重复内容", meaning: "译文里出现了重复的句子或段落。" },
  MISORDERING: { label: "顺序错乱", meaning: "译文的句子顺序和原文对不上。" },
  ALIGNMENT_FAILURE: { label: "原文译文没对上", meaning: "这句原文没能和译文对应起来，双语对照会缺内容。" },
  CONTEXT_FAILURE: { label: "上下文不足", meaning: "翻译这句时缺少必要的上下文，可能理解偏差。" },
  STRUCTURE_POLLUTION: { label: "版面杂质", meaning: "正文里混进了页眉页脚之类的版面碎片。" },
  STRUCTURE_SUGGESTION: { label: "版面建议", meaning: "结构代理建议调整块的类型或合并拆分，可在下面直接应用。" },
  FOOTNOTE_RECOVERY_REQUIRED: { label: "脚注待还原", meaning: "识别到脚注没有正确归位，需要重新解析这一部分。" },
  IMAGE_CAPTION_RECOVERY_REQUIRED: { label: "图注待还原", meaning: "图片和它的说明文字没有关联上。" },
  ARTIFACT_GROUP_RECOVERY_REQUIRED: { label: "图表组待还原", meaning: "图表和周围的说明没有组成一组，导出时可能错位。" },
  LAYOUT_VALIDATION_FAILURE: { label: "版面校验未通过", meaning: "导出的版面检查没通过，可能有错位或缺图。" },
  MISTRANSLATION_SEMANTIC: { label: "意思译错", meaning: "审校代理认为这句译文和原文意思不符，建议处理。" },
  MISTRANSLATION_LOGIC: { label: "逻辑译错", meaning: "审校代理认为这句的逻辑关系译反或译乱了。" },
  MISTRANSLATION_REFERENCE: { label: "指代译错", meaning: "审校代理认为这里的指代对象译错了。" },
};

export function issueTypeCopy(issueType: string): IssueTypeCopy {
  return ISSUE_TYPES[issueType] ?? { label: "其他问题", meaning: "系统记录的一个问题，详情见下面的技术细节。" };
}

/** Who acted on an issue. The backend writes service ids; a reader should see what they mean. */
const ACTORS: Record<string, string> = {
  "services.review": "审校复核",
  "services.translation": "翻译",
  "services.export": "导出检查",
  "services.export_qa": "导出前质检",
  "auto.fast_reading_mode": "快速阅读模式",
};

export function actorLabel(actor: string): string {
  if (ACTORS[actor]) return ACTORS[actor];
  // human:someone → someone
  const human = actor.match(/^human:(.+)$/);
  return human ? human[1] : actor;
}

/** Notes the backend writes when a pass closes an issue by itself. */
const RESOLUTION_NOTES: Record<string, string> = {
  "Resolved by latest QA pass.": "最近一次复核没有再发现这个问题，已自动关闭。",
  "Resolved by latest export-time alignment check.": "导出前的对齐检查没有再发现这个问题，已自动关闭。",
  "Resolved by latest export-time layout validation check.": "导出前的版面检查没有再发现这个问题，已自动关闭。",
};

export function resolutionNote(note: string): string {
  if (RESOLUTION_NOTES[note]) return RESOLUTION_NOTES[note];
  const retranslated = note.match(/^Translation updated \(attempt (\d+)\); awaiting the next model review\.$/);
  if (retranslated) return `已重新翻译（第 ${retranslated[1]} 次），等下一轮审校再看。`;
  return note;
}

const AGENT_KIND: Record<string, string> = {
  terminology: "术语代理",
  reviewer: "审校代理",
  structure: "结构代理",
  memory: "记忆代理",
  translator: "翻译代理",
};

/** Which agent took the turn. */
export function agentKindLabel(kind: string): string {
  return AGENT_KIND[kind] ?? kind;
}

const TURN_STATUS: Record<string, string> = {
  succeeded: "完成",
  failed: "失败",
  running: "进行中",
  cancelled: "已取消",
  exhausted: "步数用尽",
};

/** How an agent turn ended. */
export function turnStatusLabel(status: string): string {
  return TURN_STATUS[status] ?? status;
}

const QUEUE_PRIORITY: Record<string, string> = {
  immediate: "立即",
  high: "高",
  medium: "中",
  low: "低",
};

/** How urgently a chapter wants a person's eyes. */
export function queuePriorityLabel(priority: string): string {
  return QUEUE_PRIORITY[priority] ?? priority;
}

const SEVERITY: Record<string, string> = { low: "轻微", medium: "一般", high: "严重", critical: "致命" };
const ISSUE_STATUS: Record<string, string> = { open: "待处理", triaged: "已分诊", resolved: "已解决", wontfix: "不修复" };
const DETECTOR: Record<string, string> = { rule: "自动检查", model: "审校代理", human: "人工" };
const ROOT_CAUSE: Record<string, string> = {
  ingest: "导入", parse: "解析", structure: "版面", segment: "断句", memory: "记忆",
  packet: "分段", translation: "翻译", alignment: "对齐", review: "复核", export: "导出", ops: "运行",
};
const ACTION_TYPE: Record<string, string> = {
  EDIT_TARGET_ONLY: "只改译文",
  REALIGN_ONLY: "重新对齐",
  RERUN_PACKET: "重译这一段",
  REBUILD_PACKET_THEN_RERUN: "重建这一段后重译",
  REBUILD_CHAPTER_BRIEF: "重建章节摘要",
  UPDATE_TERMBASE_THEN_RERUN_TARGETED: "更新术语表后重译相关部分",
  UPDATE_ENTITY_REGISTRY_THEN_RERUN_TARGETED: "更新专名表后重译相关部分",
  RESEGMENT_CHAPTER: "重新断句",
  REPARSE_CHAPTER: "重新解析这一章",
  REPARSE_DOCUMENT: "重新解析整本书",
  REEXPORT_ONLY: "只重新导出",
  MANUAL_FINALIZE: "人工确认收尾",
};
const ACTION_STATUS: Record<string, string> = { planned: "待执行", running: "执行中", completed: "已完成", failed: "失败", cancelled: "已取消" };
const SCOPE: Record<string, string> = { sentence: "句子", block: "段落", packet: "翻译段", chapter: "章节", document: "整本书" };
const ISSUE_EVENT: Record<string, string> = {
  opened: "发现", updated: "更新", reopened: "重新打开", seen_while_closed: "关闭后再次发现",
  resolved: "解决", triaged: "分诊", wontfix: "标记不修复", action_replanned: "重新计划修复",
};
/** Machine reasons that rule checks put in the evidence; unknown ones are left to the type's meaning. */
const EVIDENCE_REASON: Record<string, string> = {
  translation_marked_review_required: "翻译时模型自己标记了「需要复核」。",
  model_review: "审校代理在抽样阅读时发现的。",
  export_qa: "导出前的质量检查发现的。",
  export_review: "导出审读代理发现的。",
  export_layout_validation: "导出版面校验发现的。",
  export_time_misalignment: "导出时发现原文和译文对不上。",
  recoverable_alignment_gap: "有句子没有对齐，但可以自动修复。",
  no_alignment_edge: "这句原文没有对应的译文。",
};

const fallback = (value: string | null | undefined, table: Record<string, string>): string =>
  !value ? "" : table[value] ?? `其他（${value}）`;

export const severityLabel = (value: string): string => fallback(value, SEVERITY);
export const issueStatusLabel = (value: string): string => fallback(value, ISSUE_STATUS);
export const detectorLabel = (value: string): string => fallback(value, DETECTOR);
export const rootCauseLabel = (value: string): string => fallback(value, ROOT_CAUSE);
export const actionTypeLabel = (value: string): string => fallback(value, ACTION_TYPE);
export const actionStatusLabel = (value: string): string => fallback(value, ACTION_STATUS);
export const scopeLabel = (value: string): string => fallback(value, SCOPE);
export const issueEventLabel = (value: string): string => fallback(value, ISSUE_EVENT);

/** What this issue is, in one line: the model's own words, else the rule's reason, else the type's meaning. */
export function issueExplanation(issueType: string, evidence: Record<string, unknown> | null | undefined): string {
  const explanation = evidence?.explanation;
  if (typeof explanation === "string" && explanation.trim()) return explanation.trim();
  const reason = evidence?.reason;
  if (typeof reason === "string" && EVIDENCE_REASON[reason]) return EVIDENCE_REASON[reason];
  return issueTypeCopy(issueType).meaning;
}

/** The parse health checks (backend domain/structure/parse_health.py), in a reader's words. */
const PARSE_CHECKS: Record<string, IssueTypeCopy> = {
  chapters_vs_outline: { label: "章节与书签一致", meaning: "按 PDF 书签，每一章都该单独成章；目录里的条目不该变成章节。" },
  chapter_titles: { label: "章节命名", meaning: "「参考文献」「索引」这类标题下不该装着正文章节，章节标题里也不该带页码。" },
  front_matter_sections: { label: "前言分节", meaning: "书签里的前言、致谢、关于本书等应各自成节，不并进相邻的一节。" },
  stray_glyphs: { label: "异常字符", meaning: "段落开头的「¡」、乱码或私用区字符，通常是符号字体没有解码。" },
  pseudo_headings: { label: "伪标题", meaning: "代码注释或等宽字体的行被当成了标题。" },
  line_break_hyphens: { label: "断行单词", meaning: "行尾断开的单词没有接回来，例如「pro-vider」「evalua tion」。" },
  figure_regions: { label: "图片区域", meaning: "图的裁剪范围压到页眉页脚、切断了线条或文字、框进了图注，或整张图没有识别出来。" },
  image_fragments: { label: "碎片图片", meaning: "细条或空白的图片块，导出后是一张看不出内容的图。" },
  caption_folds: { label: "正文混入图注", meaning: "导出时会把图后面的正文段落并进图里，正文里就少了这一段。" },
  duplicated_labels: { label: "图中文字重复", meaning: "图里的标注又作为正文段落出现了一遍。" },
};

export function parseCheckCopy(name: string): IssueTypeCopy {
  return PARSE_CHECKS[name] ?? { label: "其他检查", meaning: "一项解析检查，详情见样例。" };
}

const PARSE_SEVERITY: Record<string, string> = { blocking: "严重", major: "一般", minor: "轻微" };
export const parseSeverityLabel = (value: string): string => fallback(value, PARSE_SEVERITY);
