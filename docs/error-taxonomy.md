# Error Taxonomy

## 1. 目的

本文件定义图书翻译 Agent 的标准错误分类体系，用于统一以下场景：

- QA 检测
- 人工审校
- issue 路由
- 重跑决策
- 统计报表
- 回归测试

如果没有统一的错误口径，后续会出现三个典型问题：

1. 同一种问题被不同人用不同名字描述，无法聚合。
2. 下游把上游问题误当成翻译问题修补，导致链路越来越脆。
3. Rerun 范围无法收敛，系统频繁整章甚至整书重跑。

## 2. 分类原则

所有错误都应至少带上以下元信息：

- `error_code`
- `error_name`
- `layer`
- `scope`
- `severity`
- `blocking`
- `recoverability`
- `detector`
- `confidence`

### 2.1 Layer

错误来源层级统一分为：

- `ingest`
- `parse`
- `structure`
- `segment`
- `memory`
- `packet`
- `translation`
- `alignment`
- `review`
- `export`
- `ops`

### 2.2 Scope

错误影响范围统一分为：

- `sentence`
- `block`
- `packet`
- `chapter`
- `document`

### 2.3 Severity

- `low`
  - 不影响完整性，可延后处理。
- `medium`
  - 会影响一致性或自然度，但通常不破坏覆盖率。
- `high`
  - 会影响语义正确性、结构正确性或交付质量。
- `critical`
  - 会污染大段内容、破坏完整性，或使导出不可用。

### 2.4 Blocking

- `true`
  - 问题未关闭前，相关 scope 不得进入最终导出。
- `false`
  - 问题可在导出前或导出后修复，不阻断主流程。

### 2.5 Recoverability

- `target_only`
  - 仅修改目标文本即可修复。
- `rerun_packet`
  - 需要重建 packet 或重译局部。
- `rerun_chapter`
  - 需要重跑章节级流程。
- `reparse_upstream`
  - 必须回到 parse/segment 层修复。
- `manual_only`
  - 当前系统无法可靠自动修复。

## 3. 一级错误大类

本项目统一使用以下一级错误大类：

- `OMISSION`
- `MISTRANSLATION`
- `MISORDERING`
- `TERM_CONFLICT`
- `ENTITY_CONFLICT`
- `STRUCTURE_POLLUTION`
- `FORMAT_POLLUTION`
- `STYLE_DRIFT`
- `CONTEXT_FAILURE`
- `ALIGNMENT_FAILURE`
- `DUPLICATION`
- `EXPORT_FAILURE`
- `PIPELINE_FAILURE`

## 4. 关键错误定义

以下定义是实施期必须冻结的核心口径。

### 4.1 漏译 `OMISSION`

定义：

- 原文中 `translatable=true` 的自然语言单元，在目标结果中没有被翻译、没有被明确保护、也没有被标记为阻塞异常。

常见表现：

- 某句没有任何 `alignment edge`
- 某段只翻了一部分句子
- 脚注、图注、引文整体丢失
- 模型把两句压成一句时，漏掉其中一个命题

不属于漏译的情况：

- 被规则明确标记为 `protected`
- 被标记为 `blocked` 且有可追溯原因
- 句法重组但信息完整且有对齐

推荐默认级别：

- `high`

默认处理：

- 先检查 `alignment`
- 再判断是 `translation` 问题还是 `segment/structure` 问题
- 不允许直接忽略

### 4.2 错译 `MISTRANSLATION`

定义：

- 目标文本覆盖了原句位置，但语义错误、逻辑错误、事实关系错误或关键限定条件错误。

常见表现：

- 否定翻成肯定
- 因果关系翻反
- 范围限定、时间限定、比较关系丢失
- 修饰关系错误导致命题改变

子类：

- `MISTRANSLATION_SEMANTIC`
- `MISTRANSLATION_LOGIC`
- `MISTRANSLATION_REFERENCE`
- `MISTRANSLATION_PRAGMATIC`

推荐默认级别：

- `high`

默认处理：

- 优先判断是否由上下文不足、术语错误或结构污染引起
- 如果上游正常，走 `rerun_packet` 或人工改写

### 4.3 错序 `MISORDERING`

定义：

- 原文单元之间的阅读顺序、句子顺序或脚注归属顺序在目标结果中被错误打乱。

常见表现：

- 双栏 PDF 顺序串栏
- 脚注被插入正文中段
- 引文归属到错误说话人
- 章节标题与正文顺序错位

推荐默认级别：

- `high`，若影响整页或整章则 `critical`

默认处理：

- 优先回查 `parse/structure`
- 不建议仅在目标文本层补丁修复

### 4.4 术语冲突 `TERM_CONFLICT`

定义：

- 同一术语在相同适用范围内出现多种不应并存的译法，或未命中锁定术语。

常见表现：

- `pricing power` 被翻成“定价权”和“定价能力”
- 已锁定术语被模型自由改写
- 缩写展开不一致

不属于术语冲突的情况：

- scope 不同且规则允许不同译法
- 语法屈折或词性变化导致必要变体

推荐默认级别：

- `medium` 到 `high`

默认处理：

- 更新 `termbase` 或触发局部重译

### 4.5 专名冲突 `ENTITY_CONFLICT`

定义：

- 人名、机构名、地名、书名、作品名、角色名等专名在同一实体上出现不一致译法。

常见表现：

- 同一人物名多译
- 同一机构既有音译又有意译
- 别名关系未登记导致冲突误报

推荐默认级别：

- `medium` 到 `high`

默认处理：

- 优先更新 `entity registry`
- 如是全书级影响，触发命中范围回查

### 4.6 结构污染 `STRUCTURE_POLLUTION`

定义：

- 原始结构恢复错误导致不属于正文的内容进入正文翻译链，或正文被错误拆分/归类。

常见表现：

- 页眉页脚进入正文
- 脚注并入正文
- 图注被当正文段落翻译
- 代码块被当自然语言句子切分
- 标题层级识别错误

推荐默认级别：

- `high`，大范围时为 `critical`

默认处理：

- 必须优先回到 `parse/structure/segment`
- 禁止以目标文本后修修补为主路径

### 4.7 格式污染 `FORMAT_POLLUTION`

定义：

- 代码、公式、标签、表格结构、引文标记等非普通叙述内容在翻译或导出中被破坏。

常见表现：

- 公式变量被翻译
- 代码关键字被中文化
- HTML/Markdown 标签残缺
- 表格列顺序破坏

边界说明：

- 如果目标中的标签是源文原样讨论的字面 token，并且 tag token 与源句完全一致，例如正文里直接提到 `<think>`，不应判成 `FORMAT_POLLUTION`

推荐默认级别：

- `medium` 到 `high`

默认处理：

- 优先修 `protected policy` 或 block type 分类

### 4.8 风格漂移 `STYLE_DRIFT`

定义：

- 在全书或章节范围内，译文语气、术语偏好、句式风格、称谓策略明显偏移，超出既定风格 profile。

常见表现：

- 同一本书前半程偏书面，后半程偏口语
- 技术书局部突然文学化
- 角色称谓频繁切换

推荐默认级别：

- `low` 到 `medium`，严重时 `high`

默认处理：

- 优先 chapter-level review 或 rerun packet
- 一般不回到 parse 层

### 4.9 上下文失效 `CONTEXT_FAILURE`

定义：

- 翻译结果错误的主要原因来自上下文不足、实体状态缺失、摘要缺失或 packet 构造不足。

常见表现：

- 代词指代错
- 说话人归属错
- 缩写误解
- 当前句依赖上一段但 packet 未带入

边界说明：

- 零正文 frontmatter 章节如果唯一 open question 是 `missing_chapter_title`，应视为非阻断元数据缺口，而不是交付级 `CONTEXT_FAILURE`

推荐默认级别：

- `medium` 到 `high`

默认处理：

- 重建 packet
- 必要时重建 chapter brief

### 4.10 对齐失败 `ALIGNMENT_FAILURE`

定义：

- 已有翻译结果，但源句与目标句段的对应关系缺失、错误或不完整。

常见表现：

- 目标段存在，但没有 sentence 映射
- 目标段本身存在，但成了 orphan target segment，未被任何 active alignment edge 引用
- 映射方向错误
- 一段译文覆盖多句，但未记录 `n:1`

推荐默认级别：

- `high`

默认处理：

- 如果只是对齐记录缺失，可补对齐
- 如果对齐错误来自切句或翻译重组异常，应重译 packet 或回上游

### 4.11 重复翻译 `DUPLICATION`

定义：

- 同一原文信息在目标结果中重复出现，或相邻句/段被重复翻译。

常见表现：

- 翻译窗口重叠导致重复输出
- 模型复制上一句译文
- 脚注和正文重复出现在同一导出位置

推荐默认级别：

- `medium`

默认处理：

- 优先检查 packet overlap、alignment、导出组装逻辑

### 4.12 导出失败 `EXPORT_FAILURE`

定义：

- 翻译内容正确，但在装配、回填、打包、格式导出时发生损坏或缺失。

常见表现：

- 章节漏导
- 锚点丢失
- EPUB 导航错乱
- 双语对照错位

推荐默认级别：

- `high`

默认处理：

- 优先修 export 层，不要误触发翻译层重跑

### 4.13 流水线失败 `PIPELINE_FAILURE`

定义：

- 任务执行层面的失败，不一定是译文质量问题，但会阻断流程或污染状态。

常见表现：

- job 超时
- 队列积压
- 状态未提交
- 重试不幂等

推荐默认级别：

- `medium` 到 `critical`

默认处理：

- 修 orchestrator、job state、重试策略

## 5. 错误判定优先级

当一个问题可归为多个类别时，按以下优先级归因：

1. `STRUCTURE_POLLUTION`
2. `SEGMENT/ALIGNMENT_FAILURE`
3. `CONTEXT_FAILURE`
4. `TERM_CONFLICT` / `ENTITY_CONFLICT`
5. `MISTRANSLATION`
6. `STYLE_DRIFT`
7. `EXPORT_FAILURE`

原则：

- 优先归因上游根因，而不是下游表象。
- 不要把结构错误伪装成风格问题。
- 不要把术语治理问题伪装成“模型偶然失误”。

## 6. 默认阻断规则

以下问题默认 `blocking=true`：

- `OMISSION`
- `MISTRANSLATION` 中的逻辑性和事实性错误
- `MISORDERING`
- `TERM_CONFLICT` 涉及 locked term
- `ENTITY_CONFLICT` 涉及核心专名
- `STRUCTURE_POLLUTION`
- `ALIGNMENT_FAILURE`
- `EXPORT_FAILURE`

以下问题默认可不阻断，但应留档：

- 轻度 `STYLE_DRIFT`
- 非核心 `TERM_CONFLICT`
- 轻微 `DUPLICATION`

## 7. 检测责任分配

### 7.1 规则优先检测

- 漏译
- 对齐缺失
- 锁定术语未命中
- 格式污染
- 重复翻译
- 导出错位

### 7.2 模型优先检测

- 错译
- 风格漂移
- 上下文失效
- 复杂专名冲突

### 7.3 人工优先检测

- 高文学性错译
- 高价值术语裁决
- 结构恢复可疑但系统证据不足
- 重要引文和译名定稿

## 8. Issue 命名规范

建议统一命名格式：

`<LAYER>_<CATEGORY>_<DETAIL>`

例如：

- `TRANSLATION_OMISSION_SENTENCE_MISSING`
- `STRUCTURE_POLLUTION_FOOTNOTE_MERGED`
- `PACKET_CONTEXT_FAILURE_PRONOUN`
- `MEMORY_TERM_CONFLICT_LOCKED_MISS`
- `EXPORT_FAILURE_BILINGUAL_MISALIGN`

## 9. 与 Rerun Policy 的关系

本文件负责定义“问题是什么”。  
`Rerun Policy` 负责定义“应该修哪一层、重跑哪一层”。

任何实现和运营讨论都应先用本文件对 issue 定性，再进入 rerun 决策。
