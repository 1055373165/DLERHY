# Rerun Policy

## 1. 目的

本文件定义图书翻译 Agent 的重跑与修复策略，用于回答三个核心问题：

1. 这个问题应该修哪一层？
2. 这个问题应该影响多大范围？
3. 什么时候允许只修目标结果，什么时候必须回上游？

本文件默认与 [Error Taxonomy](/Users/smy/project/book-agent/docs/error-taxonomy.md) 配套使用。  
先定性，再决定 rerun。

## 2. 基本原则

### 2.1 根因优先

如果问题根因在上游结构、切分、术语治理或 packet 构造，就不要只在目标文本层打补丁。

### 2.2 最小影响范围

默认采用最小可验证重跑范围：

- 优先 `sentence/packet`
- 再考虑 `chapter`
- 最后才是 `document`

### 2.3 版本驱动失效

所有重跑都应由 artifact version 变化触发，而不是“感觉有问题就重来”。

### 2.4 不允许无证据重跑

触发 rerun 前必须具备以下至少一项：

- review issue
- 上游版本变化
- 人工明确决策
- QA gate 失败

### 2.5 不允许默认全书重跑

全书重跑只能在以下情况触发：

- parser 策略整体变更
- 全书 locked term 核心规范发生系统性变更
- 导出或拼装逻辑发生全局性错误

## 3. 修复动作枚举

统一使用以下动作类型：

- `EDIT_TARGET_ONLY`
- `REALIGN_ONLY`
- `RERUN_PACKET`
- `REBUILD_PACKET_THEN_RERUN`
- `REBUILD_CHAPTER_BRIEF`
- `UPDATE_TERMBASE_THEN_RERUN_TARGETED`
- `UPDATE_ENTITY_REGISTRY_THEN_RERUN_TARGETED`
- `RESEGMENT_CHAPTER`
- `REPARSE_CHAPTER`
- `REPARSE_DOCUMENT`
- `REEXPORT_ONLY`
- `MANUAL_FINALIZE`

## 4. 各层职责边界

### 4.1 Parse / Structure 层

负责：

- block 边界
- 标题层级
- 脚注/图注/正文归类
- 阅读顺序
- source anchor / span

典型问题：

- 脚注并入正文
- 页眉页脚进入正文
- block type 分类错误
- 阅读顺序错乱

如果根因在这里：

- 默认不能只重译 packet
- 应至少 `REPARSE_CHAPTER`

### 4.2 Segment 层

负责：

- 句子切分
- 句 ID 稳定性
- 跨页断句修复

典型问题：

- 缩写误切
- 引文切分错误
- 跨页一句被切成两句

如果根因在这里：

- 默认 `RESEGMENT_CHAPTER`
- 失效下游 `packets / alignments / target segments`

### 4.3 Memory / Termbase 层

负责：

- 锁定术语
- 专名注册表
- 全书与章节级记忆治理

典型问题：

- locked term 未命中
- 同一实体多译
- 新术语需要升级为全书规范

如果根因在这里：

- 不需要重 parse
- 一般为 `UPDATE_TERMBASE_THEN_RERUN_TARGETED` 或 `UPDATE_ENTITY_REGISTRY_THEN_RERUN_TARGETED`

### 4.4 Packet 层

负责：

- 翻译窗口选择
- 局部上下文裁剪
- 相关术语和实体子集注入
- 必要回看片段

典型问题：

- 代词指代错
- 引文说话人识别错
- 缩写后文才解释但 packet 未带入

如果根因在这里：

- `REBUILD_PACKET_THEN_RERUN`
- 必要时先 `REBUILD_CHAPTER_BRIEF`

### 4.5 Translation 层

负责：

- 在既定 packet 内生成译文
- 遵守术语与保护约束
- 输出可对齐结果

典型问题：

- 语义错误
- 漏译
- 锁定术语失误
- 不自然但信息完整

如果根因在这里：

- 优先 `RERUN_PACKET`
- 小范围、低风险时允许 `EDIT_TARGET_ONLY`

### 4.6 Alignment / Export 层

负责：

- 源句与目标句映射
- 双语对齐导出
- EPUB/HTML/审校包组装

典型问题：

- 已翻译但无对齐
- 导出错位
- 锚点丢失

如果根因在这里：

- 优先 `REALIGN_ONLY` 或 `REEXPORT_ONLY`
- 不应误触发重译

## 5. 决策矩阵

### 5.1 什么问题修 packet

满足以下条件时，优先修 `packet`：

- 上游结构和切分可信
- 术语库无硬冲突
- 问题集中在局部上下文不足
- 问题范围局限在单个 packet 或相邻少量 packet

典型问题：

- 代词指代错
- 说话人归属错
- 本段依赖前后段但未带入
- 当前句命中了未决歧义

动作：

- `REBUILD_PACKET_THEN_RERUN`

### 5.2 什么问题修 termbase

满足以下条件时，优先修 `termbase` 或 `entity registry`：

- 错误核心在术语、专名或固定表达不一致
- 源结构和上下文并无明显缺陷
- 该问题可能跨多个章节复现

典型问题：

- locked term 未命中
- 新术语需要全书锁定
- 同一人物或机构多译

动作：

- `UPDATE_TERMBASE_THEN_RERUN_TARGETED`
- `UPDATE_ENTITY_REGISTRY_THEN_RERUN_TARGETED`

### 5.3 什么问题必须回上游 parse

满足以下任一条件时，不允许只修 packet 或译文：

- block type 错误
- 句切分错误导致 coverage 失真
- 脚注、图注、页眉页脚进入正文
- 阅读顺序错乱
- source anchor/span 不可信
- 章节边界错误

动作：

- `RESEGMENT_CHAPTER`
- `REPARSE_CHAPTER`
- 问题全局化时 `REPARSE_DOCUMENT`

## 6. 常见错误到修复动作映射

| 错误类型 | 根因层 | 默认动作 | 默认范围 |
| --- | --- | --- | --- |
| 句子漏译但结构正常 | translation / alignment | `RERUN_PACKET` | packet |
| 句子漏译且源句无稳定对齐 | segment | `RESEGMENT_CHAPTER` | chapter |
| 锁定术语未命中 | memory | `UPDATE_TERMBASE_THEN_RERUN_TARGETED` | packet/chapter |
| 同一人名多译 | memory | `UPDATE_ENTITY_REGISTRY_THEN_RERUN_TARGETED` | chapter/document subset |
| 代词指代错误 | packet | `REBUILD_PACKET_THEN_RERUN` | packet |
| 说话人归属错误 | packet / structure | 先判根因，优先 `REBUILD_PACKET_THEN_RERUN`，结构异常则 `REPARSE_CHAPTER` | packet/chapter |
| 脚注并入正文 | structure | `REPARSE_CHAPTER` | chapter |
| 双语导出错位 | export / alignment | `REALIGN_ONLY` 或 `REEXPORT_ONLY` | chapter/document |
| 公式被翻译 | structure / format policy | `REPARSE_CHAPTER` 或修 policy 后 `RERUN_PACKET` | chapter/packet |
| 风格漂移 | translation / memory | `RERUN_PACKET` 或 chapter-level review | packet/chapter |

## 7. 失效传播规则

### 7.1 触发重建 packet 的情况

- `chapter_brief_version` 变化
- 当前 packet 相关术语版本变化
- 当前 packet 相关实体版本变化
- review issue 标记 `context_insufficient`

失效范围：

- 当前 packet
- 可选地扩展到相邻 packet

### 7.2 触发章节重切分的情况

- segmentation 规则升级
- 句 ID 变化
- 原 block 边界变化
- 跨页断句修复策略变化

失效范围：

- 当前章节下所有 packet
- 当前章节下所有 alignment
- 当前章节下所有 target segment

### 7.3 触发章节重解析的情况

- 标题层级变化
- 脚注/图注归类变化
- 阅读顺序变化
- block type 变化

失效范围：

- 当前章节所有下游 artifact

### 7.4 触发文档级重解析的情况

- parser 主策略升级
- 目录/导航结构整体错误
- 文档级锚点体系变化

失效范围：

- 整个 document 下游 artifact

## 8. 人工修改后的政策

### 8.1 允许只改目标文本的情况

仅当满足以下全部条件时，可使用 `EDIT_TARGET_ONLY`：

- 问题明确局限于译文措辞
- 不涉及术语锁定、专名治理、上下文缺失、结构错误
- 修改不会影响相邻句或全章一致性

典型场景：

- 某个中文表达不够自然
- 标点风格需要轻微调整
- 个别句式更符合目标风格

### 8.2 人工修改必须回写治理层的情况

以下人工修订不能只停留在目标文本：

- 新增或修改 locked term
- 确认新的专名规范
- 确认某类固定表达
- 明确某章风格策略

动作：

- 同步更新 `termbase / entity registry / style profile`
- 评估是否触发 targeted rerun

## 9. Rerun 执行流程

统一流程建议如下：

1. 根据 `Error Taxonomy` 定性 issue。
2. 定位根因层。
3. 判断最小可验证修复动作。
4. 生成失效范围。
5. 锁定将被替换的旧 artifact 版本。
6. 执行 rerun。
7. 重新跑对应 QA gate。
8. 关闭 issue 或升级问题层级。

## 10. 禁止事项

以下做法应明确禁止：

- 结构错误仅在目标文本层修补
- 因单个 packet 的问题触发默认整书重跑
- 人工改了专名却不回写 entity registry
- packet 明显上下文不足却反复同参数重译
- export 错误触发 translation 重跑
- 缺乏 issue 证据直接 rerun

## 11. 与工程实现的对应关系

实现上，`Rerun Policy` 至少应落到以下能力：

- job 级别的 `rerun_reason`
- artifact 级别的 `invalidated_by`
- packet/chapter/document 级别的失效传播
- review issue 到 rerun action 的映射表
- 重跑后的 QA gate 自动复检

## 12. 推荐落地顺序

P0 建议先固化以下 rerun 能力：

1. `RERUN_PACKET`
2. `REBUILD_PACKET_THEN_RERUN`
3. `UPDATE_TERMBASE_THEN_RERUN_TARGETED`
4. `RESEGMENT_CHAPTER`
5. `REPARSE_CHAPTER`
6. `REEXPORT_ONLY`

这样已经足够覆盖第一版 EPUB-only 系统的主要返工路径。
