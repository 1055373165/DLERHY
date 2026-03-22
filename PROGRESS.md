# 项目进度

## 项目信息
- 项目名称：Book Agent 产品化与 Multi-Agent 升级
- 一句话目标：在现有 Book Agent Web 译制台基础上，将翻译主链路升级为 deterministic multi-agent control plane，Phase 1 交付 EPUB / PDF_TEXT / PDF_MIXED 的 merged markdown 与 bilingual HTML。
- 创建时间：2026-03-20
- 最后更新：2026-03-22
- 协议版本：v2

## 全局指标
- 总阶段数：10
- 总 MDU 数：50
- 已完成 MDU：50
- 整体完成度：100%
- 最大拆解深度：3 层

## 阶段总览
| 阶段 | 状态 | MDU 数 | 已完成 | 完成度 |
|------|------|--------|--------|--------|
| 阶段 1：需求锁定与架构基线 | 已完成 | 2 | 2 | 100% |
| 阶段 2：翻译上下文与 prompt 强化 | 已完成 | 8 | 8 | 100% |
| 阶段 3：质量审查与回归闭环 | 已完成 | 3 | 3 | 100% |
| 阶段 4：真实样本验收与推广 | 已完成 | 11 | 11 | 100% |
| 阶段 5：Web 产品化与用户入口 | 已完成 | 11 | 11 | 100% |
| 阶段 6：Multi-Agent 需求锁定与执行接管 | 已完成 | 3 | 3 | 100% |
| 阶段 7：Memory Service 与 Compiled Context | 已完成 | 3 | 3 | 100% |
| 阶段 8：Chapter-Lane Packet Control | 已完成 | 3 | 3 | 100% |
| 阶段 9：Deterministic Review Gate Cleanup | 已完成 | 3 | 3 | 100% |
| 阶段 10：Layout Validation 与 Phase 1 验收 | 已完成 | 3 | 3 | 100% |

## 近期运行修复
- 2026-03-21：翻译器已将“consistency and care over time”类抽象服务化跑偏下沉为新的 `source-aware literalism` 规则，并在 user prompt contract 中明确“Source-Aware Guardrails 优先于泛化润色”。真实 `deepseek-chat` 复测表明，这层约束能把样本尾句从“长期服务中提供连贯性和关怀”拉回到“长期稳定、周到地照应你”的更具体、更像中文技术书的表达。
- 2026-03-21：翻译器系统提示词已连续完成 `role-style-faithful-v4/v5/v6` 三轮候选实验；真实 `deepseek-chat` A/B 证明仅靠 system prompt 已能部分压低“悉心照料/贴心服务”式润色，但仍无法稳定消除“长期服务 / 关怀 / 连贯性”这类抽象服务话术，后续需要继续下沉到 source-aware literalism guardrails / user prompt contract。
- 2026-03-21：修复 SQLite 下 `translate_full` 首次 seed work item 后更新 pipeline stage 的自锁问题；调用方已持有 session 时，stage 更新改为复用当前事务。
- 2026-03-21：为 run executor 增加过期 lease 自动回收，避免进程中断后页面长期显示“进行中”但没有真实进度。
- 2026-03-21：`Agentic AI` 已用新 retry run `229a6dc6-5072-4ea3-ada7-3cc0db0f8113` 恢复翻译并确认持续推进；`Dedication` 已定位为 review gate 阻塞导出，不是服务掉线。
- 2026-03-21：已将“导出前 blocker 修复”内建到 `translate_full` review 阶段；整书运行现在会先批量修复 active blocking issue，修不干净则停在 review，不再带着 blocker 进入 export。
- 2026-03-21：已将 document-level `title_src / title_tgt` 从首章/frontmatter 语义中剥离，并为 SQLite 老库补自动加列 backfill；`outlined_book` 现只允许真正的顶层章开新 chapter，`Conclusion / References / Key Takeaways` 默认回挂上一章。
- 2026-03-21：已完成代码块识别增强 v2：parser 现可识别纯 JSON / structured-output block，并在 `} prose...` 这类混合块中拆出尾部正文；export 侧同步增加历史混合 code artifact 拆分与 `For example ... JSON object:` 说明句的误升 code 防护。
- 2026-03-21：已完成 merged export 结构保真增强：目标段落拼接现在会保留无序列表换行、参考文献编号边界与 URL 独立成行；对已格式良好的 JSON / 代码块不再激进 reflow；`run_reflection_loop()` 这类裸函数调用也会继续留在代码块内，再从后续正文处正确断开。

## 详细任务清单
### 阶段 1：需求锁定与架构基线
#### 任务 1.1：锁定生产级高保真目标
- 状态：已完成
- 子任务：
  - [x] 明确质量目标优先于吞吐与 token 节省
  - [x] 明确 PDF 书籍与论文共用统一主链路
- 最小开发单元：
  - [x] MDU-1.1.1：读取 `auto-pilot.md` 并锁定执行框架 [依赖：无]
  - [x] MDU-1.1.2：将需求固化为“高保真 PDF 中译生产化”开发主线 [依赖：MDU-1.1.1]

### 阶段 2：翻译上下文与 prompt 强化
#### 任务 2.1：补 section/discourse 级运行时上下文
- 状态：已完成
- 子任务：
  - [x] 确认默认生产 prompt 继续使用 `role-style-v2`
  - [x] 为编译上下文增加 `section_brief`
  - [x] 为编译上下文增加 `discourse_bridge`
  - [x] 让默认 prompt 消费这些新信号
- 最小开发单元：
  - [x] MDU-2.1.1：确认“不直接替换默认 profile，而是增强默认上下文” [依赖：MDU-1.1.2]
  - [x] MDU-2.1.2：在运行时上下文中生成 `section_brief` [依赖：MDU-2.1.1]
  - [x] MDU-2.1.3：在运行时上下文中生成 `discourse_bridge` [依赖：MDU-2.1.2]
  - [x] MDU-2.1.4：将新上下文接入 `role-style-v2` / material-aware prompt [依赖：MDU-2.1.3]

#### 任务 2.2：保留可观测性与可实验性
- 状态：已完成
- 子任务：
  - [x] 保持 packet experiment 工位可观测
  - [x] 维持零 schema 迁移
- 最小开发单元：
  - [x] MDU-2.2.1：在实验工位暴露新增上下文来源 [依赖：MDU-2.1.4]

#### 任务 2.3：强化定向 rewrite / rerun guidance
- 状态：已完成
- 子任务：
  - [x] 让 `STYLE_DRIFT` issue 显式携带 `prompt_guidance`
  - [x] 让 rerun plan 注入更窄的 style guidance，而不只是一句 preferred hint
  - [x] 扩一条新的高信号 literalism 规则
- 最小开发单元：
  - [x] MDU-2.3.1：把 `style_drift.prompt_guidance` 写入 review issue evidence [依赖：MDU-3.1.1]
  - [x] MDU-2.3.2：把 `prompt_guidance` 桥接进 rerun hints [依赖：MDU-2.3.1]
  - [x] MDU-2.3.3：新增 `profound sense of responsibility` literalism rule [依赖：MDU-2.3.2]

### 阶段 3：质量审查与回归闭环
#### 任务 3.1：补测试与文档驾驶舱
- 状态：已完成
- 子任务：
  - [x] 回归测试覆盖新上下文编译
  - [x] 回归测试覆盖 prompt 消费
  - [x] 更新翻译质量驾驶舱
- 最小开发单元：
  - [x] MDU-3.1.1：新增 `context_compile` / `translator` 回归 [依赖：MDU-2.1.4]
  - [x] MDU-3.1.2：同步 `translation-quality-refactor-cockpit.md` [依赖：MDU-3.1.1]
  - [x] MDU-3.1.3：补充 root progress / ADR 更新 [依赖：MDU-3.1.2]

### 阶段 4：真实样本验收与推广
#### 任务 4.1：真实 packet 与小范围 rerun 验收
- 状态：已完成
- 子任务：
  - [x] 固定 packet dry-run 验证
  - [x] 必要时做单 packet execute / rerun
  - [x] 决定是否推广到正式默认链路
- 最小开发单元：
  - [x] MDU-4.1.1：对固定 packet 导出新 prompt 工件 [依赖：MDU-3.1.2]
  - [x] MDU-4.1.2：对高价值 packet 做小范围 execute 或 rerun 对照 [依赖：MDU-4.1.1]
  - [x] MDU-4.1.3：根据证据决定是否继续扩到更广样本 [依赖：MDU-4.1.2]

#### 任务 4.2：将 selective rollout 下沉到 chapter smoke 选样
- 状态：已完成
- 子任务：
  - [x] scan 工位暴露 unresolved packet issue 摘要
  - [x] chapter smoke 默认优先 mixed / non-style packet
  - [x] 保留 memory-first 回退开关，便于 A/B
- 最小开发单元：
  - [x] MDU-4.2.1：在 packet scan entries 中加入 issue priority 信号 [依赖：MDU-4.1.3]
  - [x] MDU-4.2.2：在 chapter smoke / CLI 中接入 selective rollout 选样策略 [依赖：MDU-4.2.1]

#### 任务 4.3：将 selective rollout 下沉到 workflow auto-followup 预算分配
- 状态：已完成
- 子任务：
  - [x] workflow auto-followup 候选排序暴露 packet issue priority 信号
  - [x] 在同 issue type 内优先 mixed / non-style packet
  - [x] 保持 `TERM_CONFLICT -> UNLOCKED_KEY_CONCEPT -> STYLE_DRIFT` 的原始语义顺序
- 最小开发单元：
  - [x] MDU-4.3.1：在 workflow candidate ranking 中接入 packet priority tier / non-style weight [依赖：MDU-4.2.2]
  - [x] MDU-4.3.2：补 workflow 级排序回归并同步驾驶舱 / ADR [依赖：MDU-4.3.1]

#### 任务 4.4：稳定 `UNLOCKED_KEY_CONCEPT` auto-lock 的 snapshot / session 行为
- 状态：已完成
- 子任务：
  - [x] 复现并隔离 mixed workflow 中的 `memory_snapshots` flush 冲突
  - [x] 修复 bootstrap/export repository 的 table probe 事务一致性
  - [x] 为 in-memory SQLite 补稳定连接池策略，并补 workflow/export 根因回归
- 最小开发单元：
  - [x] MDU-4.4.1：定位 `_document_images_table_available()` 绕开 session connection 导致的未提交状态漂移 [依赖：MDU-4.3.2]
  - [x] MDU-4.4.2：修复 bootstrap/export/session 层并恢复 `UNLOCKED_KEY_CONCEPT` workflow 回归 [依赖：MDU-4.4.1]

#### 任务 4.5：做真实章节 execute，验证最新 workflow/export 改动的实际收益
- 状态：已完成
- 子任务：
  - [x] 新增可复用的 real chapter followup smoke runner
  - [x] 在真实章节副本上完成 review auto-followup + chapter/document export 验收
- 最小开发单元：
  - [x] MDU-4.5.1：新增 `run_real_chapter_followup_smoke.py`，沉淀 cloned-DB 真实章节 followup/export 工位 [依赖：MDU-4.4.2]
  - [x] MDU-4.5.2：在真实章节 `d1ff...` 上执行 followup/export 并收集 before/after 证据 [依赖：MDU-4.5.1]

### 阶段 5：Web 产品化与用户入口
#### 任务 5.1：把后端能力收敛成用户可直接使用的 Web 工作台
- 状态：已完成
- 子任务：
  - [x] 把首页主体验从运维导向切换为用户导向的上传到下载闭环
  - [x] 接入 `bootstrap-upload -> translate_full -> history -> export download` 主路径
  - [x] 同步 autopilot 文档、README 与入口回归
  - [x] 修正历史卡片状态文案并展示最新 run 阶段与翻译进度
  - [x] 让 `translate_full` 执行器真正支持章节级并发，章内保持串行
- 最小开发单元：
  - [x] MDU-5.1.1：读取 `auto-pilot.md`，锁定“通过 Web 界面开放给用户使用”的产品化目标 [依赖：MDU-4.5.2]
  - [x] MDU-5.1.2：重写根路径首页为用户导向的 `Book Agent 译制台` [依赖：MDU-5.1.1]
  - [x] MDU-5.1.3：将上传、整书运行、历史查询与结果下载重新接到现有 `/v1` 接口 [依赖：MDU-5.1.2]
  - [x] MDU-5.1.4：同步 `DECISIONS.md` / `PROGRESS.md` / `README.md`，固化产品化决策与说明 [依赖：MDU-5.1.3]
  - [x] MDU-5.1.5：执行首页回归与 API 主路径回归，验证 Web 主流程未破坏既有接口链路 [依赖：MDU-5.1.4]
  - [x] MDU-5.1.6：修复上传后立刻读取 document 的偶发 404，并补“immediately readable”回归 [依赖：MDU-5.1.5]
  - [x] MDU-5.1.7：补齐 history API 的 run 阶段/进度投影，并把历史卡片改为“已入库/翻译中/复核中/可下载”文案 [依赖：MDU-5.1.6]
  - [x] MDU-5.1.8：将 `translate_full` 改为章节级并发 claim，显式补齐 review/export gate，并新增“8 章节并发、章内串行”回归 [依赖：MDU-5.1.7]
  - [x] MDU-5.1.9：在 review 阶段接入文档级 blocker repair loop，批量修复 TERM_CONFLICT / OMISSION / ALIGNMENT_FAILURE / MISORDERING 等阻塞项，并新增“修完 blocker 后整书导出成功”回归 [依赖：MDU-5.1.8]

#### 任务 5.2：修正文档书名语义与 outlined_book 顶层章节边界
- 状态：已完成
- 子任务：
  - [x] 将 document-level `title_src / title_tgt` 抽成独立逻辑，并让展示层不再依赖首章/frontmatter
  - [x] 为 SQLite 老库补 `documents.title_src / title_tgt` 自动 backfill，避免升级后直接炸表
  - [x] 为 `outlined_book` 增加顶层章节归并规则，只保留真正的章/附录/术语表/少数 frontmatter
- 最小开发单元：
  - [x] MDU-5.2.1：新增 document title 解析与 display helper，并让 bootstrap / workflow / export / API 改读独立书名语义 [依赖：MDU-5.1.9]
  - [x] MDU-5.2.2：在 parser 中收紧 outlined_book 顶层章节判断，并补“文件名书名解析 / 顶层小节回挂 / SQLite 加列”回归 [依赖：MDU-5.2.1]

#### 任务 5.3：将首页从“接口面板页”重写为真正的整书译制工作台
- 状态：已完成
- 子任务：
  - [x] 重新定义首页信息架构，不再沿用旧的平铺面板布局
  - [x] 将当前书籍、运行阶段、交付资产、复核阻塞和历史书库重组成更清晰的主次结构
  - [x] 在前端层补上下文恢复，刷新后自动找回上次查看的 document
  - [x] 同步入口回归和相关 API 主路径验证
- 最小开发单元：
  - [x] MDU-5.3.1：阅读 `page.py` / 入口测试 / `/v1` 接口契约，锁定旧页面的结构性问题与重写边界 [依赖：MDU-5.2.2]
  - [x] MDU-5.3.2：直接替换首页 HTML/CSS/JS，实现“新建书籍任务 + 当前书籍 + 运行总览 + 交付资产 + 书库历史”的新工作台结构 [依赖：MDU-5.3.1]
  - [x] MDU-5.3.3：补前端本地 document 上下文恢复，并让历史侧栏可自动打开仍在处理中的书籍 [依赖：MDU-5.3.2]
  - [x] MDU-5.3.4：更新首页入口回归，并复跑依赖字段的 document history / bootstrap API 定向验证 [依赖：MDU-5.3.3]

#### 任务 5.4：把首页从“双栏工作台”收敛成“主流程全宽 + 次要信息下沉”的专业译制界面
- 状态：已完成
- 子任务：
  - [x] 将上传、当前书籍、运行总览、交付资产与阻塞解释改为主流程全宽堆栈
  - [x] 将书库历史从右侧窄栏移到底部全宽区域，彻底修复长标题/长路径截断
  - [x] 收敛视觉系统：移除过暖的奶油背景与重柔光，改为更冷静的专业出版工作台配色与无衬线主字体
  - [x] 复跑入口与 history / bootstrap API 定向回归，确认新布局未破坏契约
- 最小开发单元：
  - [x] MDU-5.4.1：审视第二版工作台的右侧窄栏截断和主流程层级问题，锁定“全宽主栈 + 底部历史”的重排方向 [依赖：MDU-5.3.4]
  - [x] MDU-5.4.2：重写首页布局网格与关键 surface，把高优先级内容改为 full-width，把辅助区与历史区下沉到底部 [依赖：MDU-5.4.1]
  - [x] MDU-5.4.3：更新字体、背景、色板与路径容器样式，修复书库历史长文案截断 [依赖：MDU-5.4.2]
  - [x] MDU-5.4.4：复跑入口与 history / bootstrap API 定向验证，并同步 ADR / handoff 文档 [依赖：MDU-5.4.3]

### 阶段 6：Multi-Agent 需求锁定与执行接管
#### 任务 6.1：锁定 Phase 1 multi-agent 升级边界
- 状态：已完成
- 子任务：
  - [x] 锁定 Phase 1 输入范围、输出范围与非目标
  - [x] 输出 repo-aligned 最终实施稿
  - [x] 将实施稿接入 autopilot 接管流程
- 最小开发单元：
  - [x] MDU-6.1.1：锁定 deterministic control plane、Markdown working view、Phase 1 产物边界 [依赖：MDU-5.4.4]
  - [x] MDU-6.1.2：新增 `docs/multi-agent-final-implementation-plan.md`，固化模块边界、状态机、数据增量与 Phase 1 顺序 [依赖：MDU-6.1.1]
  - [x] MDU-6.1.3：读取 `auto-pilot.md`，将 multi-agent 升级切换到全周期 autopilot 接手模式 [依赖：MDU-6.1.2]

### 阶段 7：Memory Service 与 Compiled Context
#### 任务 7.1：显式化翻译上下文与 memory read 路径
- 状态：已完成
- 子任务：
  - [x] 新增 `MemoryService` 作为 chapter memory 读入口
  - [x] 引入 `CompiledTranslationContext`
  - [x] 将 `TranslationService` 接到显式 compiled context，并补回归测试
- 最小开发单元：
  - [x] MDU-7.1.1：新增 `src/book_agent/services/memory_service.py`，统一 chapter memory 读取与 compiled context 装配 [依赖：MDU-6.1.3]
  - [x] MDU-7.1.2：在 `workers/contracts.py` / `context_compile.py` / `translation.py` 中接入 `CompiledTranslationContext` 与版本元数据 [依赖：MDU-7.1.1]
  - [x] MDU-7.1.3：新增 `tests/test_memory_service.py`，并通过 compiled context 与 translation metadata 定向回归 [依赖：MDU-7.1.2]

### 阶段 8：Chapter-Lane Packet Control
#### 任务 8.1：在 run control 层落实“章间并行、章内串行”
- 状态：已完成
- 子任务：
  - [x] 锁定 chapter lane 下的 packet active 规则
  - [x] 在 executor / run execution 中实现按 chapter 的 active packet 约束
  - [x] 补 chapter-lane serialization 回归
- 最小开发单元：
  - [x] MDU-8.1.1：定义 packet runtime substate 与 per-chapter active packet 选择规则 [依赖：MDU-7.1.3]
  - [x] MDU-8.1.2：在 `document_run_executor.py` / `run_execution.py` 中只允许每章一个 active translate packet [依赖：MDU-8.1.1]
  - [x] MDU-8.1.3：新增 chapter-lane serialization 定向回归，证明同章不会并发翻译 [依赖：MDU-8.1.2]

### 阶段 9：Deterministic Review Gate Cleanup
#### 任务 9.1：收紧 issue family、rerun stop rule 与 manual hold 升级
- 状态：已完成
- 子任务：
  - [x] 统一 review issue family 与 rerun action 映射
  - [x] 加入 repeated-failure stop rule
  - [x] 明确 manual hold 升级条件
- 最小开发单元：
  - [x] MDU-9.1.1：在 `review.py` / `rule_engine.py` 中收敛 deterministic issue family 和 action 映射 [依赖：MDU-8.1.3]
  - [x] MDU-9.1.2：在 rerun / run control 路径加入 repeated-failure stop rule 与 manual hold 升级逻辑 [依赖：MDU-9.1.1]
  - [x] MDU-9.1.3：补 issue routing 与 stop rule 回归，证明 rerun 不会同参无限循环 [依赖：MDU-9.1.2]

### 阶段 10：Layout Validation 与 Phase 1 验收
#### 任务 10.1：在 export 前增加 layout gate，并完成 Phase 1 回归收口
- 状态：已完成
- 子任务：
  - [x] 新增 `layout_validate.py`
  - [x] 将 export 变为 review + layout 双 gate
  - [ ] 跑完最小回归语料并更新文档快照
- 最小开发单元：
  - [x] MDU-10.1.1：新增 `src/book_agent/services/layout_validate.py`，实现 heading / figure / footnote / table 的 preflight 结构校验 [依赖：MDU-9.1.3]
  - [x] MDU-10.1.2：将 `export.py` 接入 layout validation gate，并把失败映射为显式阻断 issue [依赖：MDU-10.1.1]
  - [x] MDU-10.1.3：跑通 EPUB + text PDF + mixed PDF 最小回归语料，更新 `docs/multi-agent-final-implementation-plan.md`、`DECISIONS.md` 与 `PROGRESS.md`，完成 Phase 1 收口 [依赖：MDU-10.1.2]

## 当前位置
- 当前阶段：无（Phase 1 已收口）
- 当前任务：无（等待 Phase 2 或新目标）
- 当前最小开发单元：无
- 整体完成度：100%

## 变更记录
| 时间 | 类型 | 描述 | 影响范围 |
|------|------|------|----------|
| 2026-03-20 | 初始化 | 建立 autopilot 进度面，锁定“PDF 高保真中译生产化”主线 | 根目录治理 |
| 2026-03-20 | 实现 | 运行时编译上下文已接入 `section_brief + discourse_bridge`，并正式进入 `role-style-v2` / material-aware prompt | 翻译主链路 |
| 2026-03-20 | 实现 | `STYLE_DRIFT` 已开始携带 `prompt_guidance`，rerun plan 会注入更窄的 style guidance，新增 `profound sense of responsibility` 直译腔规则 | review / rerun / prompt |
| 2026-03-20 | 验证 | 已完成 context/prompt/style-drift 定向回归与 lint，并同步驾驶舱与 ADR | 测试与项目治理 |
| 2026-03-20 | 验证 | 实验工位已支持 `review_issue_id -> rerun prompt` 还原，并在真实 packet `2e26...` 上完成 dry-run / execute 对照 | 实验工位与真实样本 |
| 2026-03-20 | 决策 | 已完成第二个真实 packet `b4c1...` 复验，收敛出“issue-driven rerun 继续保留，但只对 mixed/high-value packet 扩样，不做 blanket 推广” | 真实样本推广策略 |
| 2026-03-20 | 实现 | chapter smoke 默认已切到 selective rollout 选样：优先 mixed / non-style packet，同时保留 `--disable-issue-priority` 回退 | smoke 工位与推广策略 |
| 2026-03-20 | 实现 | workflow review auto-followup 预算已接入 selective rollout：保持 issue type 优先级不变，但在同类型内优先 mixed / non-style packet | workflow 级自动纠偏 |
| 2026-03-20 | 修复 | 已修复 `UNLOCKED_KEY_CONCEPT` auto-lock 的 snapshot 稳定性：bootstrap/export repository 的 document-image table probe 均改为走当前 session connection，并为 `:memory:` SQLite 补 `StaticPool` | workflow / export / bootstrap / 测试基础设施 |
| 2026-03-20 | 验证 | 已完成更广的 workflow/export 稳定性扩回归，确认事务内 schema probe 只剩 bootstrap/export 两处且均已切到 session connection；另发现 2 条 merged markdown 标题期望与当前标题翻译行为不一致的旁路失败 | workflow / export / merged markdown |
| 2026-03-20 | 验证 | 已在真实章节副本 `d1ff...` 上完成 review auto-followup + bilingual/review/merged export execute：open issues `12 -> 5`，`TERM_CONFLICT + STYLE_DRIFT` 已清空，导出成功，章节进入 `qa_checked` 且 `blocking_issue_count=0` | 真实章节验收 / workflow / export |
| 2026-03-20 | 实现 | 根路径首页已重写为用户导向的 `Book Agent 译制台`，主路径收敛为上传书籍、启动整书转换、查看进度、下载中文结果与回看历史 | Web 产品入口 / FastAPI UI |
| 2026-03-22 | 决策 | 已锁定 multi-agent Phase 1 边界，并新增 `docs/multi-agent-final-implementation-plan.md` 作为 repo-aligned 最终实施稿 | multi-agent 计划面 |
| 2026-03-22 | 实现 | 已引入 `MemoryService + CompiledTranslationContext`，让翻译调用显式携带 `context_compile_version` 与 `memory_version_used` | translation / context / memory |
| 2026-03-22 | 治理 | 已读取 `auto-pilot.md` 并将 multi-agent 升级切换到 autopilot 接手流程；当前执行起点锁定为 `MDU-8.1.1` | 根目录治理 / 进度面 |
| 2026-03-22 | 实现 | 已为 packet 写入 `packet_ordinal + runtime_state + input_version_bundle`，并将 translate claim 规则改为显式 chapter-front 选择；新增“反向 work item 顺序仍只会 claim 章内 front packet”回归，当前执行已推进到 `MDU-8.1.2` | run control / context builder / packet runtime |
| 2026-03-22 | 实现 | translate stage 已改为 frontier seeding：初次只 seed 每章 front packet，front packet 完成后才为该章补 seed 下一 packet；新增“只为未阻塞章节推进 frontier”回归，当前执行已推进到 `MDU-8.1.3` | run executor / chapter-lane control |
| 2026-03-22 | 验证 | 已补 `retry/reclaim` 路径的 chapter serialization 回归：front packet 进入 `retryable_failed` 后，同章 follow-up packet 不会被 seed 或 claim，系统只会重试同一 front packet；Phase 8 因此收口完成，当前执行已切到 `MDU-9.1.1` | run executor / chapter-lane serialization |
| 2026-03-22 | 实现 | 已将 `ALIGNMENT_FAILURE` 的 `REALIGN_ONLY / RERUN_PACKET` 决策收敛进 `rule_engine.resolve_action(...)`，`review.py` 不再保留分叉路由；同时校正“无 source sentence mapping 的 orphan target”集成预期为 `RERUN_PACKET` | review gate / action routing / alignment recovery |
| 2026-03-22 | 实现 | 已在 `DocumentWorkflowService` 中加入基于 audit history 的 repeated-failure stop rule：review auto followup、document blocker repair、export auto followup 会在同一 action 连续失败达到阈值后停止自动 rerun，并以 `manual_hold_required` 留在人工处理队列 | workflow / rerun governance / manual hold escalation |
| 2026-03-22 | 验证 | 已补 workflow 级 stop-rule 回归：review auto followup、document blocker repair、export auto followup 三条链路都已验证 repeated-failure 后会进入 `manual_hold_required`，同时复跑正常 review/export followup 成功路径，确认未误伤可恢复链路；Phase 9 因此收口完成并切到 `MDU-10.1.1` | workflow / review gate / export gate / autopilot progress |
| 2026-03-22 | 实现 | 已新增 `layout_validate.py`，将 layout gate 的首版规则收敛为 heading / figure / footnote / table 的 deterministic preflight 校验，并补独立单测覆盖合法/非法结构样本；当前执行已推进到 `MDU-10.1.2` | export preflight / layout validation / autopilot progress |
| 2026-03-22 | 实现 | 已将 `layout_validate` 接入 `export.py` 的 `_enforce_gate(...)`：final export 现会同步落库 `LAYOUT_VALIDATION_FAILURE` 阻断 issue，并生成 `REPARSE_CHAPTER` followup action；同时补 gate 级回归，确认问题修复后 issue 会自动 resolved，当前执行已推进到 `MDU-10.1.3` | export gate / issue routing / layout validation / autopilot progress |
| 2026-03-22 | 验证 | 已跑通 Phase 1 最小回归语料：EPUB 端到端 `translate_full`、低风险 `PDF_TEXT` bootstrap、`PDF_MIXED` OCR/mixed 路由与 PDF figure/caption export provenance；并同步更新实施稿、ADR 与进度面，Phase 1 正式收口 | multi-agent Phase 1 验收 / 文档治理 / autopilot progress |
| 2026-03-20 | 验证 | 首页入口回归与 `bootstrap-upload + translate_full` API 主路径回归已通过，确认产品化界面未破坏现有后端工作流 | 前端入口 / API 工作流 |
| 2026-03-21 | 修复 | 修复 Web 上传后立刻读取 document 的偶发 404：`bootstrap` / `bootstrap-upload` 现会在响应前显式提交事务，前端对新 document 同步增加短重试，并补 `test_bootstrap_upload_document_is_immediately_readable` 回归 | Web 产品入口 / API 一致性 |
| 2026-03-21 | 修复 | history API 已补最新 run 阶段与进度字段，历史卡片改为“已入库/翻译中/复核中/可下载”等用户态文案，并在翻译阶段显示实时 packet 进度 | Web 产品入口 / 历史查询体验 |
| 2026-03-21 | 修复 | merged export 已补列表/参考文献结构拼接与保守 code reflow 规则，并收紧裸函数调用识别，解决无序列表连行、参考文献编号串行、JSON 缩进被重排和 `run_reflection_loop()` 漏出代码块的问题 | PDF/EPUB merged export / 结构恢复 / 回归测试 |
| 2026-03-21 | 实现 | `translate_full` 现已支持章节级并发调度：默认最多并发 8 个章节，`max_parallel_workers` 预算真正生效，同一章节始终保持 1 个 packet 串行执行 | 运行时执行器 / 整书翻译吞吐 |
| 2026-03-21 | 验证 | 已通过“8 章节并发但章内不重叠”回归和原有整书 translate/review/export 主路径回归；另补测试 cleanup，避免 executor 线程在 teardown 边缘访问数据库 | 执行器调度 / API 工作流测试 |
| 2026-03-21 | 实现 | 已为 `translate_full` review work item 接入文档级 blocker repair loop：review 后会批量执行 packet/chapter 级修复并在 blocker 清零后才进入 export；若仍有 blocker，则 run 在 review 阶段直接失败并给出剩余数量 | review / repair / export gate |
| 2026-03-21 | 验证 | 已新增“文档先被翻成带 TERM_CONFLICT + OMISSION 的中间态，再由 `translate_full` 自动修复并成功导出”的回归；同时复跑 run execution 与既有 export auto-followup 回归，均通过 | API 工作流测试 / 运行时回归 |
| 2026-03-21 | 实现 | 已将 document-level `title_src / title_tgt` 抽成独立语义，bootstrap 现在会独立解析书名并写入 `document_title` metadata；显示层统一优先 `title_tgt -> title_src`，不再把首章/frontmatter 当整本书名 | bootstrap / workflow / export / API |
| 2026-03-21 | 实现 | `outlined_book` 现已收紧顶层开章规则，并在 parser 末端补“辅助小节回挂上一章”的后处理；`Conclusion / References / Key Takeaways` 不再轻易切成顶层 chapter | PDF parser / structured outline recovery |
| 2026-03-21 | 验证 | 已通过“source filename 解析书名 / outlined_book 顶层小节回挂 / SQLite title 列 backfill / bootstrap API 入口”定向回归 | parser / app runtime / API |
| 2026-03-21 | 修复 | 已补单行 JSON/structured-output 代码块尾随正文拆分：parser 与 export 均支持 `单行 code + prose suffix` 历史样本，不再把尾部说明文吞进代码块 | PDF parser / exporter / 真实 PDF 导出 |
| 2026-03-21 | 修复 | EPUB parser 已开始过滤空 `titlepage` 与 TOC-like spine 文档，减少无效章节、review 噪音和 token 浪费；真实 `build-an-ai-agent.epub` 样本章节数已收敛 | EPUB parser / 真实 EPUB 关键路径 |
| 2026-03-21 | 修复 | SQLite 老库 title backfill 已改为真正调用 `resolve_document_titles(...)`，历史 `Dedication / Foreword / Preface` 级 document title 会按源文件名自动纠偏，不再把脏标题固化进 `title_src` | SQLite 兼容 / document title 语义 |
| 2026-03-21 | 修复 | 已打通历史 PDF 的 `refresh + fragment repair` 闭环：same-anchor code continuation 先合并、再拆出 `trailing prose suffix`；structure refresh 会把 split fragment metadata 回写到原 block，repair 为 fragment 补译中文，真实 `Dedication` 导出已验证生效 | PDF parser / structure refresh / exporter / 真实历史产物修复 |
| 2026-03-21 | 实现 | 首页已从旧的平铺面板页重写为“整书译制工作台”：主列围绕当前书籍和运行阶段组织，侧栏聚焦书库历史，视觉系统改为更克制的出版工作台语言 | Web UI / FastAPI 直出页面 |
| 2026-03-21 | 实现 | 前端已补 `localStorage` 上下文恢复：刷新后优先回到上次查看的 document，没有缓存时会自动打开仍在处理中的书籍 | Web UI / 当前 document 恢复体验 |
| 2026-03-21 | 验证 | 首页入口回归已按新结构更新并通过；`document history` 与 `bootstrap upload` 相关 API 定向回归也已复跑通过 | Web UI / API 契约验证 |
| 2026-03-21 | 实现 | 首页已完成第二轮布局收敛：上传、当前书籍、运行总览与交付阻塞全部改为全宽主流程栈，章节注意区/最近活动下沉，书库历史从右侧窄栏改到底部全宽展示 | Web UI / 信息架构 |
| 2026-03-21 | 修复 | 已收敛字体与配色到更冷静的专业工作台体系，并为书库历史长路径增加独立 `history-path` 容器，解决窄栏截断与信息挤压问题 | Web UI / 视觉系统 / 历史可读性 |
| 2026-03-21 | 验证 | 第二轮首页优化已通过入口回归与 `document history` / `bootstrap upload` 定向 API 回归，确认新布局仍兼容现有 `/v1` 契约 | Web UI / API 契约验证 |
| 2026-03-21 | 修复 | 运行总览现已把“阶段失败”视为高优先级状态：即使 run 总状态尚未收敛，主按钮也会切成“刷新并准备重试”，并在当前 run / 历史卡片中明确暴露 retry 入口，修复 API key 失败后用户找不到重试入口的问题 | Web UI / Run 恢复体验 |
| 2026-03-21 | 验证 | 已复跑首页入口回归与 `retry_run_restarts_pipeline_with_previous_lineage`、history 相关 API 回归，确认前端新增重试入口仍对应现有 `/v1/runs/{run_id}/retry` 语义 | Web UI / Run control 契约 |
| 2026-03-21 | 修复 | `/v1/runs/{run_id}/retry` 现已支持“stale failed-stage run”恢复：若 pipeline 阶段已失败且心跳/更新时间超过阈值，系统会先把旧 run 收敛为 `clean_retry_after_stale_run`，再立即拉起新的 lineage run；前端会把这类 run 直接视为可重试 | Run control / Web UI / Stale run 恢复 |
| 2026-03-21 | 验证 | 已新增 stale failed-stage retry 回归，并与首页入口、普通 retry 路径合跑通过，确认“普通 retry + stale retry + 首页入口”三条链路一致 | API 工作流测试 / Web UI 回归 |
| 2026-03-21 | 修复 | 后端的 OpenAI-compatible provider key 现已强制只从项目根 `.env` 读取：`translation_openai_api_key` 会忽略 shell 环境变量，只接受显式传参或 `/Users/smy/project/book-agent/.env` 中的 `OPENAI_API_KEY` | 配置加载 / 运行时凭据来源 |
| 2026-03-21 | 修复 | EPUB parser 现已只读取 `toc` 导航并忽略 `page-list` 对章节标题的污染，同时支持主标题+副标题合并为 `document_title_src`；历史 EPUB 可通过 `refresh_epub_structure(...)` 回写修正后的书名和章节标题 | EPUB parser / 历史结构修复 |
| 2026-03-21 | 修复 | merged export 现会在导出前自动回填 `document.title_tgt`，并在导出目录生成中文书名别名文件；下载接口对整书包、中文阅读稿、双语章节包和审校包统一改用 `《书名》-...` 命名 | exporter / 下载命名 / 文档标题语义 |
| 2026-03-21 | 验证 | 已通过 EPUB page-list 污染、历史结构刷新、中文下载名三组定向回归；真实文档 `cf32d839...` 已完成 structure refresh + merged re-export，书名回正为 `代理人工智能：理论与实践`，导出目录新增 `《代理人工智能：理论与实践》-中文阅读稿.html` | EPUB 真实样本 / export 真实验收 |
| 2026-03-21 | 修复 | book-PDF merged export 现已识别 `Chapter 1_ ...` 这类下划线主章标题，不再把第 1–21 章整体吞进 `Introduction`；同时对“heading target 明显像正文”的前言页 heading 自动降级为段落，并让 `Introduction` 章节头稳定回退为 `介绍` | PDF merged export / TOC 重建 / frontmatter heading 修复 |
| 2026-03-21 | 验证 | 已通过 underscore 主章分组、frontmatter prose-heading 降级和既有 merged title fallback 回归；真实文档 `67283f52...` 已重导出为 31 项目录（`致谢 / 前言 / 介绍 / 第1–21章 / 附录A–G`），`欢迎阅读《智能体设计模式...》` 已从 heading 降为正文 | PDF 真实样本 / merged export 目录修复 |
| 2026-03-21 | 修复 | PDF merged export 现已补“标签化正文豁免 + 代码续行恢复”双保险：`What/Why/Rule of Thumb` 不再被误拆成伪代码，跨页 LangChain 代码里的 `RunnableBranch` / 注释续行也会在导出时恢复回完整代码块 | PDF 结构恢复 / merged export / 第二章路由真实样本 |
| 2026-03-21 | 修复 | PDF merged export 现已补“参考文献逻辑条目拆分 + list target 换行保真 + bullet 误升代码豁免 + refresh code fragment 引号续行恢复”，真实 `67283f52...` 样本中的参考文献 `1/2/3`、多条 Prompt 列表、单条 `● Prompt 1` 和第 7–9 页跨页 LangChain 代码均已在最终 html/md 中恢复正确结构 | PDF merged export / Markdown 保真 / 真实整书重导出 |
| 2026-03-21 | 修复 | merged export 现已统一 html/md 的代码块归一化入口，并把 code reflow 改为“保守修复 + 结构保留”：修复跨页代码的字符串/注释续行、终止语句后的误缩进、docstring `Returns:` 误判、相邻 code block 重叠去重，同时收窄“stable structured”豁免，避免长 Python 示例被误当 JSON 跳过修复 | PDF merged export / code fidelity / 真实整书代码块保真 |
| 2026-03-21 | 修复 | translation context compile 现已把 `Previous Accepted Translations` 收紧成条件注入：只有短句、承接句、续接句或 shift 结构才会保留最近 2 条，否则对自洽长段落直接清空，避免为低收益局部连贯性额外消耗 token | 翻译 prompt / context compile / token ROI |
| 2026-03-21 | 修复 | `agentic AI` 旧锁定译法 `智能体式AI` 已加入运行时归一化：compile / review / rerun / chapter concept lock 全链路统一回写为 `智能体AI`，并新增 source-aware guardrail 显式压制 `智能体式AI` 这类翻译腔术语 | 翻译术语策略 / review / rerun / chapter memory |
| 2026-03-21 | 验证 | 已在真实 packet `670447b0...` 上完成上下文收紧后的生产链路复测：当前默认 `role-style-v2` 下 `prev_translated_blocks 4 -> 0`、`token_in 1927 -> 1561`，输出从 `智能体式AI` 收敛为 `智能体AI`，且未再回退到抽象服务化尾句 | 真实翻译样本 / prompt 成本与质量复测 |
| 2026-03-22 | 实现 | 已将系统默认翻译 prompt profile 从 `role-style-v2` 切换为 `role-style-faithful-v6`，并同步更新 `Settings`、`LLMTranslationWorker`、packet experiment 与 chapter smoke 的默认入口，避免默认链路分叉 | 翻译 prompt 默认配置 / 实验入口一致性 |
| 2026-03-22 | 验证 | 已用真实 packet `670447b0...` 在新默认 `role-style-faithful-v6` 下复测：`prev_translated_blocks=0`、`relevant_terms` 已归一到 `Agentic AI => 智能体AI`，输出稳定为“长期稳定、周到地照应你”，未再回到“连贯性和关怀/贴心服务”式抽象尾句 | 真实翻译样本 / 新默认验收 |
| 2026-03-22 | 修复 | `PdfProseArtifactRepairService.repair_mixed_code_prose_blocks(...)` 已改为基于真实 render blocks 扫描 mixed code/prose，而不是只看原始 block 文本；这样跨页代码在 render 阶段恢复出来的 `trailing/leading prose` 也能被补译并持久化回原 block | PDF prose artifact repair / render-time mixed code recovery |
| 2026-03-22 | 验证 | 已对真实文档 `67283f52...` 从第 5 章到末章重跑 mixed-code prose repair + merged html/md re-export；Chapter 6/7/10/19 中原本泄漏到代码块后的英文正文已回填为中文，render residual 已收敛到仅剩 Glossary 的 2 个非章节块 | 真实 PDF 样本 / Chapter 5+ 跨页代码验收 |
| 2026-03-22 | 修复 | EPUB merged export 现已为“非严格 XML 的章节 XHTML”补上 HTMLParser figure fallback：当 `ElementTree` 无法解析章节时，导出仍能从 `<figure>/<img>/<figcaption>` 里恢复图片路径，不再因为 `ParseError` 导致整章图片全部丢失 | EPUB exporter / image asset recovery / malformed XHTML fallback |
| 2026-03-22 | 验证 | 已重导出真实 EPUB 文档 `cf32d839...`；`merged-document.html` 现在包含 44 个 `<img>` 标签，`artifacts/exports/cf32.../assets/` 下已实际落出 44 个图片文件，缺图问题已消除 | 真实 EPUB 样本 / merged HTML 图片验收 |
