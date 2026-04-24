# PDF Translation Pipeline v2 — M1 实施计划

> 依据:`specs/pdf-v2` 六阶段规格文档 · 北极星:**"不知道比错更重要"**
> M1 范围:DocIR 契约化 + 核心失败模式堵死 · 预计 12-18 人天 · 单人 2-3 周

## 里程碑映射

- **M1**:契约与核心失败模式(本文件)
- **M2**:置信度路由 + VLM 兜底 + 文档级术语
- **M3**:模态专家(Table / Equation / Paper subtype)
- **M4**:扫描件硬化 + 长文档 ToC

---

## M1 任务清单(按依赖拓扑)

### M1.1 · DocIR 字段扩展 (Task #29)

- [ ] `ParsedBlock` 加 4 个可选字段:
  - `translatability: str` — 默认 `"translate_all"`
  - `provenance: str` — 默认 `"text_layer"`
  - `confidence_breakdown: dict` — 默认 `{}`(含 `layout_conf` / `text_conf` / `class_conf` / `sanity_ok` 子指标)
  - `style_hints: dict` — 默认 `{}`(`font_family` / `is_mono` / `font_size_rank` / `is_italic` / `is_bold`)
- [ ] 定义 `Translatability` 与 `Provenance` 常量(不用 Enum 以保 frozen dataclass 兼容)
- [ ] 现有 EPUB/PDF parser 默认值覆盖,零代码变更前提下跑通

**验收**:`pytest tests/` 全绿;现有 EPUB 端到端跑通。

---

### M1.2 · Text-Layer Sanity Gate (Task #30)

- [ ] 新模块 `src/book_agent/domain/structure/text_layer_sanity.py`:
  - `SanityReport` dataclass(`ok`, `metrics`, `reason`)
  - `assess(page: fitz.Page) -> SanityReport`
  - 三指标:unicode 字符熵、PUA 比例、词典命中率
- [ ] `PyMuPDFTextExtractor` 每页抽取前先 assess,若 `ok=False` 则页级元数据标 `text_layer_sanity_failed: true`,不信任文本层
- [ ] 注入坏字体金标(构造 PDF:把 "hello" 经 PUA 编码后 embed),验证 gate 100% 触发

**验收**:gate precision ≥0.85 / recall ≥0.90(在小规模构造金标上)。

---

### M1.3 · 多栏阅读序真正重排 (Task #31)

- [ ] 新增 `reading_order_recoverer.py`(或并入 pdf.py 结构恢复服务):
  - `detect_columns(page_layout) -> list[Column]`:vertical whitespace histogram
  - `order_blocks_by_column(blocks, columns) -> list[blocks]`
- [ ] 接入 `PdfStructureRecoveryService`:识别多栏页后调用上面函数,替换原 y-sort
- [ ] 保留 single-column fast path,防止简单页面性能劣化
- [ ] 金标回归:2 栏论文 + 2 栏杂志 + 3 栏 newsletter,人工标注正确阅读序与算法输出对比

**验收**:多栏重排正确率 ≥95%。

---

### M1.4 · 可译性协议执行 (Task #32)

- [ ] PDF parser 在分类时赋值 `translatability`:
  - `CODE` / `EQUATION` / `REFERENCE_ITEM` / URL-like block → `translate_none`
  - 其他 → `translate_all`
- [ ] EPUB parser 同样处理 `<pre>` / `<code>` / `cite` 等
- [ ] 翻译服务入口读 `block.translatability`:
  - `translate_none` → 跳过 LLM,`target = source`,标 provenance
  - `translate_prose_only` → 提取 prose 片段翻译,保留非 prose 原样
- [ ] 翻译后 schema-level 校验:`translate_none` 的 target 含 CJK 即拒绝

**验收**:code/math/references 零误译(M1.5 测试覆盖)。

---

### M1.5 · 不可译泄漏保护 + 测试骨架 (Task #33)

- [ ] 新 `tests/test_translatability_guard.py`:
  - 构造 code/math/reference/URL 块,跑完整翻译流
  - 断言:`target_text` 与 `source_text` 逐字符一致
  - 断言:无 CJK 字符
- [ ] CI 集成:该测试失败即 block merge

**验收**:测试通过;任意后续改动引入 leak 即 red。

---

## 依赖拓扑

```
M1.1 (ParsedBlock 扩字段)
  ├─► M1.2 (sanity gate 把结果写回字段)
  ├─► M1.4 (translatability 协议)
  │      └─► M1.5 (leak guard 测试)
  └─► M1.3 (多栏重排,独立,可并行)
```

M1.1 是全局前提;M1.3 可并行;M1.4 → M1.5 串行。

---

## 执行原则

1. 每个任务单独 PR(或同一 PR 分 commit),便于独立 rollback
2. 任务开始前先读相关现有模块,了解已有契约
3. 每个任务完成后立刻跑全量 `pytest`,任何回归即停
4. 金标回归集初始化(3-5 份 PDF)作为并行旁路任务,不阻塞 M1 主线

---

## 回归集(M1 结束时初始化)

位置:`tests/golden_pdfs/`

- `academic_2col.pdf` — 2 栏论文(多栏重排测试)
- `corrupted_font.pdf` — 构造的 PUA 编码乱码(sanity gate)
- `code_block_book.pdf` — 含 Python/JS 代码块(translatability)
- `math_paper.pdf` — 含公式(equation translatability)
- `clean_book.pdf` — 干净对照组

---

## 非目标(M1 不做)

- VLM 调用 / 路由(M2)
- 表格结构恢复(M3)
- LaTeX 公式还原(M3)
- ToC / cross-ref(M4)
- 扫描件加固(M4)
- 文档级术语表(M2)
