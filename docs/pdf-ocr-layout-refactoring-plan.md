# PDF OCR & Layout Refactoring Plan

Last Updated: 2026-03-19

## Purpose

这份文档整合以下三类上下文，作为 book-agent 面向英文 PDF 书籍、英文论文翻译的下一阶段升级计划：

- `~/.claude/plans/iterative-dreaming-volcano.md` 中的 OCR 与 Layout 六阶段改造设想
- 仓库内既有 PDF 计划、状态与决策文档
- 当前代码实现里已经完成的 Phase 1 基础设施与仍然存在的真实边界

它的目标不是重复 `docs/pdf-support-implementation-plan.md` 的 P1 文本 PDF 计划，而是在 P1 基础已落地后，把 OCR、图像/公式/版式恢复这条后续路线重写成一份和当前代码一致的执行稿。

## Autopilot Progress

### Step 1: Preserve PDF image blocks in the IR

Status: Done on 2026-03-18

完成内容：

- `src/book_agent/domain/structure/pdf.py`
  - 新增 `PdfImageBlock`
  - `PdfPage` 新增 `image_blocks`
  - `PyMuPDFTextExtractor` 现在会保留 `type == 1` 的图片块 metadata，而不是直接忽略
  - `PdfStructureRecoveryService` 现在会把图片块恢复为 `BlockType.IMAGE`
  - `pdf_page_evidence` 新增 `raw_image_block_count`
- `tests/test_pdf_support.py`
  - 新增 image block recovery 回归测试

验证：

- `uv run python -m py_compile src/book_agent/domain/structure/pdf.py tests/test_pdf_support.py`
- 定向运行时断言通过：`pdf-image-recovery-ok`

限制：

- 这一步只解决“不要静默丢图”
- 还没有把 PDF 图片真正落盘为导出资产
- 本地完整 `pytest/unittest` 链路暂时受环境依赖缺失影响：`pytest`、`httpx`

### Step 2: Wire PDF image assets to export

Status: Partial on 2026-03-18

目标：

- 让 `BlockType.IMAGE` 不再只显示 `[Image]` 占位
- 明确 `DocumentImage` 或等价资产元数据的写入与导出读取链路
- 为后续 figure/image review evidence 和 mixed/OCR 图片页复用打基础

当前进展：

- `src/book_agent/services/export.py`
  - `IMAGE / FIGURE` 现在会被识别成图片工件，而不是 generic protected artifact
  - bilingual / merged HTML 在拿到图片资产路径时会渲染真实 `<img>`
  - export 现在已有一条 PDF 图片资产裁切路径：基于 source PDF + page_number + bbox 在导出时即时裁图

验证：

- `uv run python -m py_compile src/book_agent/services/export.py`
- 定向渲染断言通过：`pdf-image-render-ok`
- 无 `fitz` 环境的降级断言通过：`pdf-image-export-fallback-ok`

当前限制：

- 这条 PDF 裁图路径依赖 `PyMuPDF (fitz)`
- 当前本地环境缺少 `fitz`，所以只完成了语法与渲染层验证，还没做真实 PDF 裁图的端到端验证
- 还没有把资产生命周期正式收口到 `DocumentImage`

### Step 3: Persist recovered PDF image blocks as `DocumentImage`

Status: Done on 2026-03-18

完成内容：

- `src/book_agent/services/bootstrap.py`
  - `ParseArtifacts` / `BootstrapArtifacts` 新增 `document_images`
  - parse 阶段现在会为 `BlockType.IMAGE / FIGURE` 自动生成 `DocumentImage`
  - 当前 `storage_path` 先采用逻辑路径：`document-images/<document_id>/<block_id>.png`
- `src/book_agent/infra/repositories/bootstrap.py`
  - `BootstrapRepository.save()` 现在会持久化 `document_images`
  - `load_document_bundle()` 现在会把 `document_images` 一并读回
- `tests/test_pdf_support.py`
  - 新增 parse 生成 `DocumentImage` 的回归测试
  - 新增 repository 持久化/读取 `DocumentImage` 的回归测试

验证：

- `uv run python -m py_compile src/book_agent/services/bootstrap.py src/book_agent/infra/repositories/bootstrap.py tests/test_pdf_support.py`
- 定向 parse 断言通过：`document-image-parse-ok`
- 定向持久化断言通过：`document-image-persist-ok`

限制：

- 当前 `storage_path` 仍是逻辑路径，不代表图片文件已经物理落盘
- `DocumentImage` 还没有接入 export repository / review package / API
- 真正的资产文件仍然依赖 Step 2 的导出时裁图路径或后续独立 materialization 任务

### Step 4: Expose `DocumentImage` in export evidence and reuse persisted assets

Status: Done on 2026-03-18

完成内容：

- `src/book_agent/infra/repositories/export.py`
  - `ChapterExportBundle` 现在会加载章节对应的 `document_images`
- `src/book_agent/services/export.py`
  - review package 现在会输出 `pdf_image_evidence`
  - bilingual manifest 现在会输出 `pdf_image_evidence`
  - merged document manifest 现在会输出 `pdf_image_summary`
  - 如果 `DocumentImage.storage_path` 指向一个已存在的物化文件，导出时会优先复制并复用该资产
- `tests/test_pdf_support.py`
  - 新增 export evidence 包含 `pdf_image_evidence` 的回归测试
  - 新增“优先复用 persisted document image asset”回归测试

验证：

- `uv run python -m py_compile src/book_agent/infra/repositories/export.py src/book_agent/services/export.py tests/test_pdf_support.py`
- 定向导出 evidence 断言通过：`pdf-image-evidence-export-ok`
- 定向 persisted asset 复用断言通过：`persisted-document-image-asset-ok`

限制：

- 目前 `DocumentImage.storage_path` 默认仍是逻辑路径，只有在外部已物化图片文件时，这条复用路径才会生效
- 真实 PDF 的裁图主路径仍依赖 `fitz`
- `pdf_image_evidence` 当前只进入 export artifacts，尚未进入 API summary 或 operator UI

### Step 5: Expose PDF image recovery summary in document API and operator UI

Status: Done on 2026-03-18

完成内容：

- `src/book_agent/services/workflows.py`
  - `DocumentSummary` 新增 `pdf_image_summary`
  - document summary 现在会直接聚合 `document_images`，输出 `image_count / page_count / page_numbers / stored_asset_count / image_type_counts / chapter_image_counts`
- `src/book_agent/app/api/routes/documents.py`
  - `/v1/documents/{document_id}` 现在会序列化 `pdf_image_summary`
- `src/book_agent/schemas/workflow.py`
  - `DocumentSummaryResponse` 新增 `pdf_image_summary`
- `src/book_agent/app/ui/page.py`
  - document summary / history detail 现在会直接显示 PDF 图片恢复摘要
- `tests/test_pdf_support.py`
  - 新增 API summary 回归测试

验证：

- `uv run python -m py_compile src/book_agent/services/workflows.py src/book_agent/app/api/routes/documents.py src/book_agent/schemas/workflow.py src/book_agent/app/ui/page.py tests/test_pdf_support.py`
- 定向 workflow + API serializer 断言通过：`pdf-image-summary-api-ok`

限制：

- 当前仍是“恢复/资产可见性”层，不代表 OCR、公式识别、figure caption 对齐已经完成
- `stored_asset_count` 只有在图片文件真的被物化后才会增长
- 本地依然缺少 `pytest`、`httpx`、`fitz`，所以还没跑完整 API/test/export 真环境链路

### Step 6: Materialize cropped PDF image assets into stable `DocumentImage` storage

Status: Done on 2026-03-18

完成内容：

- `src/book_agent/services/export.py`
  - export 在成功执行 PDF 裁图时，会把图片物化到稳定路径：`<artifacts-root>/document-images/<document_id>/<block_id>.png`
  - 同时会回写 `DocumentImage.storage_path`
  - `DocumentImage.metadata_json.storage_status` 现在会从 `logical_only` 升级为 `materialized`
  - 后续导出若检测到该稳定资产已存在，会直接复用，不再重复裁图
- `tests/test_pdf_support.py`
  - 新增 fake-`fitz` 回归测试，覆盖“裁图 -> 物化 -> 回写 DocumentImage”路径

验证：

- `uv run python -m py_compile src/book_agent/services/export.py tests/test_pdf_support.py`
- 定向 materialization 断言通过：`pdf-image-materialization-ok`

限制：

- 真实 PDF 裁图的生产路径依旧依赖 `fitz`
- 当前物化时机仍绑定在 export，不是独立后台任务
- 还没有引入 cache eviction / rerun invalidation / checksum 去重

### Step 7: Link PDF image blocks with nearby captions and render them as a single figure unit

Status: Done on 2026-03-18

完成内容：

- `src/book_agent/domain/structure/pdf.py`
  - recovery 现在会按 page geometry 把图片块和附近 `caption` block 关联起来
  - image block 会拿到 `linked_caption_text / linked_caption_source_anchor`
  - caption block 会反向拿到 `caption_for_source_anchor`
- `src/book_agent/services/bootstrap.py`
  - parse 现在会把这种 source-anchor 级关系落成真实 `linked_caption_block_id / caption_for_block_id`
  - `DocumentImage.alt_text` 在缺省时会回退到 linked caption
- `src/book_agent/services/export.py`
  - export render 现在会把 linked image + caption 折叠成一个 `image_anchor_with_translated_caption` figure 单元
  - standalone caption block 不会再和同一张图重复渲染
- `tests/test_pdf_support.py`
  - 新增 recovery link、parse relation materialization、render merge 三条回归测试

验证：

- `uv run python -m py_compile src/book_agent/domain/structure/pdf.py src/book_agent/services/bootstrap.py src/book_agent/services/export.py tests/test_pdf_support.py`
- 定向 figure-caption 断言通过：`pdf-image-caption-link-ok`

限制：

- 当前 caption link 还是启发式，只覆盖“同页、几何位置接近”的 image/caption 对
- 还没有处理跨页 figure caption、subfigure、多图共用 caption
- review 还没有把 caption-link 质量单独做成 issue

### Step 8: Expose figure-caption linkage status in export evidence and operator summary

Status: Done on 2026-03-18

完成内容：

- `src/book_agent/services/export.py`
  - `pdf_image_evidence` 现在会显式输出 `caption_linked_count / uncaptioned_image_count`
  - 单条图片 evidence 现在会输出 `caption_linked / linked_caption_block_id / linked_caption_text`
  - merged manifest 里的 `pdf_image_summary` 现在会显式统计 caption-linked 图片数
- `src/book_agent/services/workflows.py`
  - document summary 里的 `pdf_image_summary` 现在会暴露 `caption_linked_count / uncaptioned_image_count`
- `src/book_agent/app/ui/page.py`
  - operator document summary / history detail 现在会直接显示已链接 caption 的图片数量
- `tests/test_pdf_support.py`
  - 更新既有 image summary/evidence 回归测试
  - 新增 linked-caption summary/evidence 回归测试

验证：

- `uv run python -m py_compile src/book_agent/services/export.py src/book_agent/services/workflows.py src/book_agent/app/ui/page.py tests/test_pdf_support.py`
- 定向 caption observability 断言通过：`pdf-image-caption-summary-ok`

限制：

- `caption_linked_count` 依赖 Step 7 的几何启发式结果，不等同于语义上 100% 正确
- review package 仍未区分“caption 缺失”和“caption 关联不确定”这两类问题

### Step 9: Turn uncaptioned PDF images into reviewable structure issues

Status: Done on 2026-03-18

完成内容：

- `src/book_agent/services/review.py`
  - `ReviewService._pdf_structure_issues()` 现在会识别未关联 caption 的 PDF image/figure block
  - 对 `academic_paper` lane，会产出 `IMAGE_CAPTION_RECOVERY_REQUIRED` advisory issue
  - 对非 academic chapter，仅在“本章已有 caption context 或部分图片已成功关联”的情况下产出低严重度 advisory，尽量减少普通插图误报
  - 这类 issue 会沿用既有 structure routing，自动落到 `ActionType.REPARSE_CHAPTER`
- `tests/test_pdf_support.py`
  - 新增 academic paper uncaptioned image advisory 回归测试
  - 新增 plain image without caption context 不误报回归测试

验证：

- `uv run python -m py_compile src/book_agent/services/review.py tests/test_pdf_support.py`
- 定向 review 断言通过：`pdf-image-caption-review-ok`
- 定向噪声守卫断言通过：`pdf-image-caption-noise-guard-ok`

限制：

- 这仍是基于当前 caption-link 启发式之上的二阶 rule，不是语义级 figure understanding
- 当前只区分“值得提醒 operator 复查”和“暂不报警”，还没有区分 decorative image、跨页 caption、subfigure 等细粒度情形
- issue 目前是 advisory，不会单独阻塞章节通过

### Step 10: Expose chapter-level PDF image summary in document API and operator tables

Status: Done on 2026-03-18

完成内容：

- `src/book_agent/services/workflows.py`
  - `ChapterSummary` 现在会携带 chapter-level `pdf_image_summary`
  - summary 生成时会按 chapter 聚合 `image_count / page_count / stored_asset_count / caption_linked_count / uncaptioned_image_count`
- `src/book_agent/schemas/workflow.py`
  - `ChapterSummaryResponse` 现在会暴露 `pdf_image_summary`
- `src/book_agent/app/api/routes/documents.py`
  - `/v1/documents/{document_id}` 的 chapter payload 现在会序列化 chapter-level `pdf_image_summary`
- `src/book_agent/app/ui/page.py`
  - document summary table 现在新增 `PDF Images` 列
  - history detail table 现在新增 `PDF Images` 列
  - document-level pills / definitions 现在会显式显示 `uncaptioned images`
- `tests/test_pdf_support.py`
  - 更新 document summary API 回归测试
  - 更新 workflow summary 回归测试

验证：

- `uv run python -m py_compile src/book_agent/services/workflows.py src/book_agent/schemas/workflow.py src/book_agent/app/api/routes/documents.py src/book_agent/app/ui/page.py tests/test_pdf_support.py`
- 定向 serializer 断言通过：`chapter-pdf-image-summary-ok`

限制：

- chapter-level summary 仍然只基于当前 `DocumentImage` 与 caption-link 启发式，不代表 figure semantics 已 fully understood
- 这一步只解决“按 chapter 看见问题”，worklist prioritization 仍需独立策略
- 本地仍未跑完整前后端测试链，受 `pytest`、`httpx`、`fitz` 缺失限制

### Step 11: Promote PDF image-caption recovery issues into the chapter worklist queue

Status: Done on 2026-03-18

完成内容：

- `src/book_agent/services/workflows.py`
  - chapter worklist queue 现在会把 `IMAGE_CAPTION_RECOVERY_REQUIRED` 识别为专门信号
  - 当这类 issue 成为 dominant family 且没有 active blocking issue 时，queue priority 会提升为 `high`
  - `queue_driver` 现在会显式输出 `pdf_image_caption_gap`
  - `owner_ready_reason` 现在会显式输出 `pdf_image_caption_issue_detected`
- `tests/test_pdf_support.py`
  - 新增 PDF image-caption issue 驱动 worklist queue 的回归测试

验证：

- `uv run python -m py_compile src/book_agent/services/workflows.py tests/test_pdf_support.py`
- 定向 worklist 断言通过：`pdf-image-caption-worklist-ok`

限制：

- 当前只对 `IMAGE_CAPTION_RECOVERY_REQUIRED` 做了专门 queue 策略，还没有把 `MISORDERING`、`FOOTNOTE_RECOVERY_REQUIRED` 等 PDF structure issue family 一并分层
- queue driver / owner-ready reason 目前仍是 operator-facing code string，不是更高层的 localized 文案
- 本地依旧没有跑完整 API / browser worklist 验证链

### Step 12: Add a resumable real-PDF chapter smoke runner and capture the first real corpus baseline

Status: Done on 2026-03-18

完成内容：

- `scripts/run_pdf_chapter_smoke.py`
  - chapter smoke 脚本现在支持“bootstrap 或复用现有 DB 继续跑”，不再要求每次从头开始
  - 翻译阶段现在按 packet 单独提交，每翻完一个 packet 就写回 report，便于长跑任务中断后续跑
  - CLI 新增 `--packet-limit`，方便只跑 chapter 的前 N 个 packet 做快速诊断
  - 运行时会实时打印 `bootstrapped / resume_existing_document / packet_translated / review_complete / exported` 等 stage，避免真实 LLM 调用看起来像“假死”
- 真实 corpus 基线已经落到计划里：
  - 英文书籍样本：`Agentic Design Patterns A Hands-On Guide to Building Intelligent Systems`
  - 英文论文样本：`Forming Effective Human-AI Teams: Building Machine Learning Models that Complement the Capabilities of Multiple Experts`
  - 论文已有历史全量产物：`artifacts/real-book-live/deepseek-forming-teams-paper-v3/exports/b90a689e-bf00-5a3a-b1e3-0d5c88a12c1b`
- 第一次真实 smoke 结果已经明确区分了两个问题：
  - 书籍样本被 profiler 判定为 `pdf_scan`，当前仍在 parse 阶段被 fail-safe reject，说明 scanned book 的真实阻塞就是 Phase 3 OCR 主路径缺失
  - 论文样本可以正常 bootstrap；chapter 1 一共 14 个 packet，首个真实 packet 调 DeepSeek 的一次调用约耗时 `63.6s`，因此之前看起来像“卡住”的 chapter smoke，本质上是长耗时 + 默认输出缓冲，不是 workflow 死锁

验证：

- `uv run python -m py_compile scripts/run_pdf_chapter_smoke.py`
- 最小 live provider probe 通过
- 真实 paper first-packet probe 通过，首包记录：
  - `token_in=5791`
  - `token_out=2334`
  - `latency_ms=63584`

限制：

- 论文 chapter smoke 虽然现在可观测、可续跑，但全章真实翻译仍然是分钟级长任务
- 书籍样本依旧无法进入翻译阶段，因为当前代码里 `PDF_SCAN` 仍然直接报错要求 OCR
- 本机目前没有 `surya`、`Pillow`、`fitz`、`tesseract` 等 OCR / PDF image 依赖，所以 scanned book 还没有可执行主路径

### Step 13: Introduce a minimal OCR parser path for `pdf_scan` / `pdf_mixed` using a dedicated Python 3.13 runtime

Status: Done on 2026-03-18

完成内容：

- `src/book_agent/domain/structure/ocr.py`
  - 新增 `UvSuryaOcrRunner`
    - 通过 `uv run --python 3.13 --with surya-ocr --with Pillow surya_ocr ...` 启动独立 OCR runtime
    - 这条路不再要求主项目 runtime 直接装进 `surya` 依赖
  - 新增 `OcrPdfTextExtractor`
    - 读取 `surya_ocr` 的 `results.json`
    - 把 OCR `text_lines + bbox` 分组回 `PdfTextBlock`
    - 先支持“chapter heading 单行保留 + 正文多行合并”的最小 block 恢复
  - 新增 `OcrPdfParser`
    - 复用现有 `PdfStructureRecoveryService`
    - 让 scanned / mixed PDF 至少能进入统一 `ParsedDocument` 主链路
- `src/book_agent/services/bootstrap.py`
  - `ParseService` 现在支持注入 `ocr_pdf_parser`
  - `SourceType.PDF_SCAN / PDF_MIXED` 现在不再直接 hard reject，而是路由到 `OcrPdfParser`
- `tests/test_pdf_support.py`
  - 新增 fake runner 回归测试，覆盖 OCR 行到 `PdfTextBlock` 的分组
  - 新增 `pdf_scan` 路由到 OCR parser 的回归测试

真实 runtime 发现：

- 当前主项目原先跑在 Python `3.14` 时，`surya-ocr` 依赖链会卡在 `Pillow 10.4.0` 源码构建，并报缺少 `jpeg` headers
- 使用系统已有的 Python `3.13.5` 时，`uv run --python 3.13 --with surya-ocr --with Pillow ...` 可以成功拉起 `surya` runtime
- 这意味着 scanned PDF 的最小可行路径不是“继续硬装进主 venv”，而是“把 OCR 隔离到兼容 runtime”

验证：

- `uv run python -m py_compile src/book_agent/domain/structure/ocr.py src/book_agent/services/bootstrap.py tests/test_pdf_support.py`
- fake OCR extractor 断言通过：`ocr-fake-extractor-ok`
- Python 3.13 runtime probe 通过：
  - `surya` import 成功
  - `Pillow` import 成功

限制：

- 当前 OCR 首发版还只是 text-line OCR，没有 layout/order/table/equation-aware 恢复
- `surya_ocr` 首次真实运行仍需要下载模型权重，扫描书首次 bootstrap 预计会比较慢
- 这一步只把 scanned PDF 从“直接拒绝”推进到“有最小解析主路径”，还没有完成质量验收与性能验收

### Step 14: Complete the real paper first-chapter smoke end to end

Status: Done on 2026-03-18

完成内容：

- 真实论文样本 `Forming Effective Human-AI Teams: Building Machine Learning Models that Complement the Capabilities of Multiple Experts`
  - chapter 1 真实 packet 总数：`14`
  - chapter 1 已真实翻译完成：`14 / 14`
  - 对应 `translation_run` 总数：`14`
- `scripts/run_pdf_chapter_smoke.py`
  - 继续修正 long-run 过程中暴露的两个真实问题：
    - report serializer 现在可以处理 SQLAlchemy `Export` 记录
    - export progress 日志现在改为读取 `export_record.id`
- 真实 chapter smoke 现已跑到 export 终点：
  - review issue 总数：`0`
  - review package export 已生成
  - bilingual chapter export 已生成

真实产物：

- smoke report:
  - `artifacts/real-book-live/deepseek-forming-teams-paper-v7-first-packet/chapter-smoke.report.json`
- review package export id:
  - `253769ad-18fa-5362-900e-3ee70cb10ef6`
- bilingual export id:
  - `95ecf133-67b6-5166-a8bc-12d257cb5aa0`

验证：

- 真实 rerun 成功输出：
  - `review_package_exported`
  - `bilingual_exported`
  - `fully_translated=true`

限制：

- 这次通过的是真实论文单章，不代表 scanned book 路径已经同等成熟
- paper chapter smoke 当前依赖 DeepSeek 真实调用，成本和时延都明显高于 fixture/test 环境

### Step 15: Verify that the scanned-book OCR runtime now starts for the real corpus, and surface the real first-run cost

Status: Done on 2026-03-18

完成内容：

- 对真实扫描书样本 `Agentic Design Patterns A Hands-On Guide to Building Intelligent Systems` 进行了可见的 `surya_ocr --page_range 0` 探针
- 结果确认：
  - OCR runtime 已经不再停在“缺依赖立即报错”
  - `surya_ocr` 已经开始下载真实模型权重到本机 cache
  - 当前书籍 chapter smoke 长时间无输出的主要原因，是首次 OCR 权重准备而不是 parser 立即失败

真实观测：

- `manifest.json` 已开始拉取
- `text_recognition` 模型开始下载到：
  - `/Users/smy/Library/Caches/datalab/models/text_recognition/2025_09_23`
- 其中 `model.safetensors` 体积约 `1.34G`

这一步的意义：

- 它把 scanned book 的状态从“代码路径不通 / 运行即报错”推进到了“runtime 正常拉起，但首次模型准备非常重”
- 后续如果 book smoke 继续慢，优先怀疑的是权重下载与 OCR 时长，而不是又回到了 `PDF_SCAN` fail-safe reject

限制：

- 这一步只证明 OCR runtime 已经启动并进入真实模型准备，不代表扫描书 chapter smoke 已经在本轮内完整跑完
- 当前 OCR runner 仍会把 `surya_ocr` 子进程输出收在内部命令里，operator 侧可见性还不够好，后续应补进度透传或专门日志文件

### Step 16: Make long-running chapter smoke bootstrap visible before OCR/parse actually finishes

Status: Done on 2026-03-18

完成内容：

- `scripts/run_pdf_chapter_smoke.py`
  - 现在在真正开始 bootstrap 之前就会先落一版 report
  - 新增 `bootstrap_start` stage 输出
  - report 新增：
    - `bootstrap_started_at`
    - `bootstrap_in_progress`
    - `bootstrap_finished_at`

这一步的意义：

- 对扫描书这种“首次 OCR 可能要等很久”的任务，目录里会立刻出现 report 文件，不再像黑盒
- 如果任务中途被切平台或被打断，接手的人可以立刻知道它是否卡在 bootstrap 阶段

验证：

- `uv run python -m py_compile scripts/run_pdf_chapter_smoke.py`

限制：

- 这一步只改善 chapter smoke 的可见性，还没有把 OCR 子进程下载进度直接透传到 report 内
- 已经在运行的旧 session 不会自动获得这次可见性增强，需要下一次 rerun 才会体现

### Step 17: Pin the OCR subprocess runtime to a Surya-compatible `transformers` release

Status: Done on 2026-03-18

完成内容：

- `src/book_agent/domain/structure/ocr.py`
  - 给 `UvSuryaOcrRunner` 增加了显式 runtime pin：
    - `surya-ocr==0.17.1`
    - `transformers==4.56.1`
  - 把命令构造提取成 `_build_command()`，避免后续 OCR runtime 组合继续漂移
- `tests/test_pdf_support.py`
  - 新增回归测试，确保 OCR 子进程命令始终包含这组兼容 pin

这一步的直接背景：

- 真实扫描书样本在默认 runtime 下会稳定报错：
  - `AttributeError: 'SuryaDecoderConfig' object has no attribute 'pad_token_id'`
- 同一份 PDF 在把 OCR 子进程切到 `transformers==4.56.1` 后，`pad_token_id` 异常消失，并且真实 page-0 OCR 成功完成

验证：

- `uv run python -m py_compile src/book_agent/domain/structure/ocr.py tests/test_pdf_support.py`
- 真实探针成功：
  - `uv run --python 3.13 --with surya-ocr==0.17.1 --with Pillow --with requests --with 'transformers==4.56.1' surya_ocr '<book.pdf>' --page_range 0 ...`
  - 结果从 `pad_token_id` 崩溃推进到真实完成 `Detecting bboxes` / `Recognizing Text`

这一步的意义：

- scanned PDF 不再只是“路由打通但 runtime 不稳定”，而是拿到了一个可复现、可落盘的 OCR 依赖组合
- 后续 chapter smoke / full-book OCR 的失败若再出现，就更可能是识别质量、耗时或资源问题，而不是 runtime 兼容性再次漂移

限制：

- 这一步的 pin 是基于真实运行验证得到的稳定组合，而不是上游正式声明的唯一推荐版本
- 目前只验证了 page-0 真实 OCR 与命令级兼容性，还没完成整章扫描书 smoke

### Step 18: Add page-range OCR smoke support and locate the real first chapter boundary for the scanned book

Status: Done on 2026-03-18

完成内容：

- `src/book_agent/domain/structure/ocr.py`
  - `UvSuryaOcrRunner` 现在支持可选 `page_range`
  - 当 smoke 只想验证首章时，可以把 OCR 成本限制在指定页段，而不是整本
- `scripts/run_pdf_chapter_smoke.py`
  - 新增 `--ocr-page-range`
  - 当传入该参数时，chapter smoke 会构造一个带 page-range OCR parser 的定制 bootstrap pipeline
- `tests/test_pdf_support.py`
  - 回归测试扩展为断言 `--page_range` 会被正确带入 OCR 子进程命令

真实探针结果：

- 稀疏探针页段：`0,5,10,15,20`
  - 原始 `page 20` 明确出现：
    - `<b>Chapter 1: Prompt Chaining</b>`
- 边界探针页段：`25,30,35`
  - 原始 `page 25`、`page 30` 仍然处于 Prompt Chaining 内容
  - 原始 `page 35` 已明显进入 Routing 相关内容

由此得到的当前判断：

- 这本书的首章 smoke 最合适的受控 OCR 范围是 `20-32` 左右
- 没必要再为“只测首章”支付整本 OCR 的时间成本

验证：

- `uv run python -m py_compile src/book_agent/domain/structure/ocr.py scripts/run_pdf_chapter_smoke.py tests/test_pdf_support.py`
- `uv run python - <<'PY' ... print('ocr-page-range-command-ok') ... PY`
- 真实 OCR 稀疏探针：
  - `surya_ocr '<book.pdf>' --page_range 0,5,10,15,20 ...`
- 真实 OCR 边界探针：
  - `surya_ocr '<book.pdf>' --page_range 25,30,35 ...`

这一步的意义：

- 单章 smoke 从“理论上能做，但整本 OCR 成本过高”推进到了“可以通过受控页段快速验证首章”
- 之后可以先把首章翻译质量、review 和 export 跑顺，再决定是否进入 full-book OCR

限制：

- 目前的 page-range smoke 仍然是“局部文档子集 bootstrap”，不是完整文档视角下的 chapter 精确裁切
- 页码区间 `20-32` 是基于真实 sparse probe 的工程判断，后续若发现 chapter 1 尾页更靠后，还需微调范围

### Step 19: Finish the scanned-book first-chapter smoke on a controlled OCR page range and capture the first real review failure taxonomy

Status: Done on 2026-03-18

完成内容：

- 修复了 `scripts/run_pdf_chapter_smoke.py` 的 progress 统计 bug
  - `bootstrap_document()` 返回的是 `DocumentSummary`
  - 之前错误地读取了不存在的 `translation_packets` 字段
  - 改为从 `chapters[].packet_count` 聚合 packet 总数
- 用 `--ocr-page-range 20-32` 跑通了扫描书首章真实 smoke：
  - 产物目录：
    - `artifacts/real-book-live/deepseek-agentic-design-book-v4-ch1-range-20-32-smoke`
  - 真实结果：
    - bootstrap 成功
    - 章节识别命中 `<b>Chapter 1: Prompt Chaining</b>`
    - chapter_count=`3`
    - selected chapter packet_count=`74`
    - 整章 `74/74` packets 翻译完成
    - review package / bilingual export 都已产出

首轮真实问题画像：

- review issue 总数：`17`
- issue family：
  - `FORMAT_POLLUTION`: `12`
  - `UNLOCKED_KEY_CONCEPT`: `4`
  - `STALE_CHAPTER_BRIEF`: `1`

这一步的结论：

- 扫描书首章已经不再是“只能 bootstrap，不能翻译”，而是真正走完了 `OCR -> packet -> translation -> review -> export`
- 当前阻塞已经从 runtime/OCR 层下沉到了“源文本污染”和“chapter memory 缺概念锁定”两类上层质量问题

### Step 20: Strip OCR inline style tags at parse time and prove that the main review regressions collapse from `17` to `5`

Status: Done on 2026-03-18

完成内容：

- `src/book_agent/domain/structure/ocr.py`
  - 新增 OCR 文本归一化逻辑
  - 在 `OcrPdfTextExtractor` 中主动剥离 `surya_ocr` 残留的内联样式标签：
    - `<b>`, `</b>`
    - `<strong>`, `<i>`, `<em>`, `<u>` 这类同类标签
- `tests/test_pdf_support.py`
  - 回归测试升级为显式断言 OCR 提取后的 block text 不再残留 `<b>`

真实对照实验：

- 重跑目录：
  - `artifacts/real-book-live/deepseek-agentic-design-book-v5-ch1-range-20-32-tagstrip`
- 同样的 `20-32` 首章 smoke 结果：
  - 整章 `74/74` packets 再次翻译完成
  - review issue 从 `17` 降到 `5`
  - 剩余 issue family 只剩：
    - `UNLOCKED_KEY_CONCEPT`: `4`
    - `STALE_CHAPTER_BRIEF`: `1`

这一步的意义：

- 证明此前最主要的首章失败不是模型翻译质量问题，而是 OCR 文本里夹带的样式标签污染了下游翻译与 review
- `FORMAT_POLLUTION` 这条问题链已经被实证压平

### Step 21: Use chapter concept locking plus locked-concept brief suppression to close the remaining memory-only issues to zero

Status: Done on 2026-03-18

完成内容：

- `src/book_agent/services/review.py`
  - 调整 `STALE_CHAPTER_BRIEF` 规则：
    - 如果概念已经在 chapter memory / term entries 中被 `LOCKED`
    - 或者该 concept 已经有 `canonical_zh`
    - 则不再要求 chapter brief 必须字面包含它
- `tests/test_persistence_and_review.py`
  - 新增回归测试：
    - 缺失晚出现概念时仍会报 `STALE_CHAPTER_BRIEF`
    - 但当该概念被 `ChapterConceptLockService` 锁定后，不再继续报 stale brief

真实章节收口动作：

- 在 `deepseek-agentic-design-book-v5-ch1-range-20-32-tagstrip` 结果库上，使用现有 `ChapterConceptLockService` 锁定了：
  - `Context Engineering -> 上下文工程`
  - `Prompt Engineering -> 提示工程`
  - `language model -> 语言模型`
  - `language models -> 语言模型`
- 随后直接重跑 chapter review：
  - issue_count=`0`
  - issue_type_counts=`{}`
- 并再次回刷 chapter exports：
  - review package：`review-package-0415641a-c278-55c8-bea1-205ea8bc9161.json`
  - bilingual export：`bilingual-0415641a-c278-55c8-bea1-205ea8bc9161.html`

这一步的现实结论：

- 真实扫描书首章现在已经可以达到：
  - `74/74` packets translated
  - review `0` issues
  - export ready
- 论文样本此前已在单章 smoke 中达到 `0` issues，因此“书籍 + 论文都能正确跑通首章验证”的目标已经成立

仍需后续自动化的点：

- 当前 concept lock 这一步在真实样本上仍是自动驾驶执行的 operator-style动作，而不是 bootstrap/review 后的全自动策略
- 如果要把“首章 0 issue -> 整本自动翻译”变成真正无人值守，还应把 concept auto-lock / brief refresh 收口到正式 workflow 中

### Step 22: Run the first real unattended chapter smoke with `--auto-lock-unlocked-concepts` and capture the true post-review failure modes

Status: Done on 2026-03-18

完成内容：

- `scripts/run_pdf_chapter_smoke.py`
  - 首次把 `--auto-lock-unlocked-concepts` 放进真实扫描书首章 smoke
- 真实运行目录：
  - `artifacts/real-book-live/deepseek-agentic-design-book-v6-ch1-range-20-32-autolock`

真实结果：

- 整章 `74/74` packets 翻译完成
- 初次 chapter review：`6` 个 issue
  - `OMISSION`: `1`
  - `UNLOCKED_KEY_CONCEPT`: `4`
  - `STALE_CHAPTER_BRIEF`: `1`
- 第一轮 unattended auto-lock 没有静默跳过，而是暴露了两个真实系统问题：
  - structured concept resolver 在模型未返回 `source_term` 时直接校验失败
  - heuristic concept resolution 会把 plural/singular 变体误锁成错误 canonical term

这一步的意义：

- 证明“人工 concept lock”已经不再是唯一前进路径，脚本链路真的走到了 auto-lock 阶段
- 同时也把下一批必须修的产品级缺口从抽象风险变成了可复现的真实故障

### Step 23: Harden concept auto-lock and PDF omission handling, then re-run a clean v7 chapter smoke to blocking-zero with final export success

Status: Done on 2026-03-18

完成内容：

- `src/book_agent/services/chapter_concept_autolock.py`
  - 当 structured payload 缺少 `source_term` 时，在校验前自动回填 request term
  - 为 plural/singular 变体增加 variant-key 复用逻辑，例如 `language model` / `language models`
  - 默认 resolver 顺序改为：
    - `OpenAICompatibleConceptResolver`
    - `HeuristicConceptResolver`
  - heuristic fallback 新增“过泛候选词”拒绝规则，避免 `Context Engineering -> 工程` 这类错误 canonical
- `src/book_agent/services/review.py`
  - 对 PDF OCR 低置信度、段首、短 lowercase 残片增加 omission suppression
  - 真实目标是过滤 `format.` 这类 OCR page-fragment，而不是掩盖正常漏译
- `tests/test_persistence_and_review.py`
  - 新增 structured resolver 缺字段回填回归
  - 新增 plural/singular variant-key 回归
  - 新增 heuristic generic-candidate rejection 回归
  - 新增 fragmentary PDF omission suppression 回归

真实运行目录：

- `artifacts/real-book-live/deepseek-agentic-design-book-v7-ch1-range-20-32-autolock-fixes`

v7 真实结果：

- 整章 `74/74` packets 翻译完成
- `concept_auto_lock_complete`
  - `locked_count=4`
- `OMISSION` / `ALIGNMENT_FAILURE` 已不再阻塞
- bilingual export 首次在 unattended path 下成功产出：
  - `exports/67283f52-b775-533f-988b-c7433a22a28f/bilingual-0415641a-c278-55c8-bea1-205ea8bc9161.html`

此时剩余问题：

- open issue 还剩 `2`
- 都是 `STYLE_DRIFT`
- 都集中在 `Context Engineering -> 语境工程` 的字面直译

这一步的意义：

- 书籍样本第一次达到了“自动 OCR + 自动 concept auto-lock + final export succeeded”的真实闭环
- 失败面已经被压缩到两个明确、可 targeted rerun 的 packet 级 style drift

### Step 24: Correct `Context Engineering` with the new structured resolver and clear the final two style-drift packets to zero open issues

Status: Done on 2026-03-18

完成内容：

- 在 `v7` 结果库上直接做 live probe，验证新的 resolver 对真实 chapter examples 的输出为：
  - `Context Engineering -> 上下文工程`
- 使用 `ChapterConceptLockService` 在 `v7` 库上纠正：
  - `Context Engineering -> 上下文工程`
- 执行两个 `STYLE_DRIFT` follow-up action，并打开 `run_followup=true`
  - action `c0578d21-969d-5e68-92d1-0ecc9f3f7abb`
    - rerun packet `d28a51be-85dc-5056-83af-71c3c842f77a`
  - action `03630ee6-6a71-5ce0-ad8d-0448df025ba0`
    - rerun packet `6c98c8bf-0455-56c3-a463-48aeed5281b5`
- rerun 完成后再次 review + export

最终结果：

- `final_issue_count=0`
- `blocking_issue_count=0`
- review package 已刷新：
  - `artifacts/real-book-live/deepseek-agentic-design-book-v7-ch1-range-20-32-autolock-fixes/exports/67283f52-b775-533f-988b-c7433a22a28f/review-package-0415641a-c278-55c8-bea1-205ea8bc9161.json`
- bilingual export 已刷新：
  - `artifacts/real-book-live/deepseek-agentic-design-book-v7-ch1-range-20-32-autolock-fixes/exports/67283f52-b775-533f-988b-c7433a22a28f/bilingual-0415641a-c278-55c8-bea1-205ea8bc9161.html`

这一步的现实结论：

- 扫描书首章现在已经重新达到真正可验收状态：
  - `74/74` packets translated
  - review `0` open issues
  - final bilingual export succeeded
- 论文样本此前已在：
  - `artifacts/real-book-live/deepseek-forming-teams-paper-v7-first-packet`
  - 达到单章 smoke `0` issues
- 因此“书籍 PDF + 论文 PDF 都能正确翻译，并且书籍遵守先首章修完再整本”的门槛现在已经成立

下一步建议：

- 可以开始进入扫描书 full-book translation
- 但若要把这套过程变成真正无人值守，仍建议把“auto-lock 后对 style-drift packet 的 targeted rerun”收口进正式 workflow，而不是继续依赖 operator-style follow-up

### Step 25: Upgrade the full-book live runner so review-stage followups can run unattended with the default concept resolver

Status: Done on 2026-03-18

完成内容：

- `src/book_agent/services/workflows.py`
  - `UNLOCKED_KEY_CONCEPT` 的 review auto-followup 不再强制使用 `HeuristicConceptResolver`
  - 现在改为 `build_default_concept_resolver()`
  - 这意味着：
    - 优先走 OpenAI-compatible structured resolver
    - heuristic 只作为 fallback
- `scripts/run_real_book_live.py`
  - 新增 `--auto-review-followups`
  - live runner 现在可以在 `review_document()` 阶段显式开启 packet-level auto followup
  - report 也会记录：
    - `auto_review_followups`
    - `auto_followup_on_gate`
    - `max_auto_followup_attempts`
- `tests/test_persistence_and_review.py`
  - 新增回归，确认 workflow review auto-followup 确实会调用默认 concept resolver

验证：

- `uv run python -m py_compile src/book_agent/services/workflows.py scripts/run_real_book_live.py tests/test_persistence_and_review.py`
- `uv run python -m unittest tests.test_persistence_and_review.PersistenceAndReviewTests.test_workflow_review_auto_executes_single_packet_unlocked_concept_followups tests.test_persistence_and_review.PersistenceAndReviewTests.test_workflow_review_unlocked_concept_followup_uses_default_concept_resolver`

这一步的意义：

- 整本 runner 不再只在 final export gate 才尝试自动补救
- 现在已经具备“翻译完成 -> review -> auto-lock / style rerun -> export”的更完整无人值守路径

### Step 26: Start the first real full-book unattended scanned-PDF run with review/export auto-followups

Status: In progress on 2026-03-18

已启动命令：

- `uv run python scripts/run_real_book_live.py --source-path '<Agentic Design Patterns PDF>' --database-url 'sqlite+pysqlite:////Users/smy/project/book-agent/artifacts/real-book-live/deepseek-agentic-design-book-v8-full-run-autofollowup/book-agent.db' --export-root '/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-agentic-design-book-v8-full-run-autofollowup/exports' --report-path '/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-agentic-design-book-v8-full-run-autofollowup/report.json' --requested-by 'codex.pdf-ocr-autopilot' --parallel-workers 4 --max-auto-followup-attempts 24 --auto-review-followups --auto-followup-on-gate`

当前状态：

- 工件目录已创建：
  - `artifacts/real-book-live/deepseek-agentic-design-book-v8-full-run-autofollowup`
- `book-agent.db` 已创建
- 当前仍处于 full-book bootstrap / OCR 阶段：
  - 还没有首次写出 `report.json`
  - 也还没有进入 `run_seed / run_id` 落盘阶段

当前判断：

- runner 本身已启动，没有立刻报错退出
- 全书 OCR bootstrap 明显比首章 smoke 更重，因此现在的等待主要是 parse/OCR 成本，而不是 translation/live API 成本

下一次接力时优先检查：

- `artifacts/real-book-live/deepseek-agentic-design-book-v8-full-run-autofollowup/report.json`
- 一旦该文件出现，优先记录：
  - `run_seed.run_id`
  - `translate.translated_packet_count`
  - `translate.remaining_packet_count`

### Step 27: Emit the full-book report before OCR bootstrap completes, then relaunch the unattended run on a stable `v9` artifact root

Status: Done on 2026-03-18

完成内容：

- `scripts/run_real_book_live.py`
  - 在 bootstrap / resume 开始前立即写出初始 `report.json`
  - 新增字段：
    - `bootstrap_in_progress`
    - `bootstrap_finished_at`
    - `resume_in_progress`
    - `resume_ready_at`
- 这样即使 scanned PDF 的全书 OCR bootstrap 很重，也不会再出现“目录和 DB 已创建，但 report 还不存在”的黑盒期

验证：

- `uv run python -m py_compile scripts/run_real_book_live.py`

运行编排调整：

- 终止了没有早期 report 的 `v8` / repro 长跑
- 新的正式 full-book 接力目录改为：
  - `artifacts/real-book-live/deepseek-agentic-design-book-v9-full-run-early-report`

当前 `v9` 状态：

- 已启动命令：
  - `uv run python scripts/run_real_book_live.py --source-path '<Agentic Design Patterns PDF>' --database-url 'sqlite+pysqlite:////Users/smy/project/book-agent/artifacts/real-book-live/deepseek-agentic-design-book-v9-full-run-early-report/book-agent.db' --export-root '/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-agentic-design-book-v9-full-run-early-report/exports' --report-path '/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-agentic-design-book-v9-full-run-early-report/report.json' --requested-by 'codex.pdf-ocr-autopilot' --parallel-workers 4 --max-auto-followup-attempts 24 --auto-review-followups --auto-followup-on-gate`
- `report.json` 已存在，并确认：
  - `bootstrap_in_progress=true`
  - `auto_review_followups=true`
  - `auto_followup_on_gate=true`
  - `max_auto_followup_attempts=24`

下一次接力优先看：

- `artifacts/real-book-live/deepseek-agentic-design-book-v9-full-run-early-report/report.json`
- 当 bootstrap 结束后，继续记录：
  - `bootstrap.document_id`
  - `run_seed.run_id`
  - `translate.translated_packet_count`
  - `translate.remaining_packet_count`

### Step 28: Add an attachable live monitor for long-running full-book runs, then attach it to `v9`

Status: Done on 2026-03-18

完成内容：

- `scripts/watch_real_book_live.py`
  - 新增一个独立 watcher，不侵入主 runner，也不需要重启已经在跑的 full-book 任务
  - 输入为 `report.json`
  - 输出 sidecar：
    - `report.live.json`
  - 每次 probe 会持续写出：
    - `stage`
    - `runner_process` / `ocr_process`
    - `ocr_output`
    - `db_counts`
    - `work_item_status_counts`
    - `translation_packet_status_counts`
    - `elapsed_seconds_since_start`
- 该 watcher 适合 scanned PDF 长时间停留在 OCR/bootstrap 阶段时使用
  - 因为这时主 `report.json` 可能还只有 very early fields
  - 但 watcher 仍可让接力者看见：
    - 主 runner 还活着
    - `surya_ocr` 还活着
    - OCR 临时输出目录是哪一个
    - DB 是否已经开始落 document / chapter / packet

已挂到当前 `v9`：

- 监控命令：
  - `uv run python scripts/watch_real_book_live.py --report-path artifacts/real-book-live/deepseek-agentic-design-book-v9-full-run-early-report/report.json --interval-seconds 30 --stop-when-terminal`
- 当前 live sidecar：
  - `artifacts/real-book-live/deepseek-agentic-design-book-v9-full-run-early-report/report.live.json`

当前 probe 结论：

- `stage=bootstrap_ocr_running`
- 主 runner 仍然存活
- `surya_ocr` 仍然存活
- OCR 临时输出目录已解析出来：
  - `/private/var/folders/m7/gjc7m9yj36l334cdz82h51mw0000gn/T/book-agent-ocr-f1ydldmf`
- 当前 DB 仍然是：
  - `documents=0`
  - `chapters=0`
  - `translation_packets=0`
  - `document_runs=0`
- 这说明 `v9` 仍停留在“全书 OCR 结果尚未回写到主 DB”的阶段，而不是 translate/run-control 阶段

验证：

- `uv run python -m py_compile scripts/watch_real_book_live.py`
- `uv run python scripts/watch_real_book_live.py --report-path artifacts/real-book-live/deepseek-agentic-design-book-v9-full-run-early-report/report.json --once`
- 持续 watcher 已确认连续两次 probe 成功写出

下一次接力优先看：

- `artifacts/real-book-live/deepseek-agentic-design-book-v9-full-run-early-report/report.json`
- `artifacts/real-book-live/deepseek-agentic-design-book-v9-full-run-early-report/report.live.json`
- 一旦 live sidecar 出现以下任一变化，立即继续记录：
  - `db_counts.documents > 0`
  - `ocr_output.results_json_exists = true`
  - `run_seed.run_id` 出现在主 report
  - `translate.translated_packet_count` 开始增长

### Step 29: Make OCR bootstrap write its own status heartbeat, and wire the full-book runner to publish that path

Status: Done on 2026-03-18

完成内容：

- `src/book_agent/domain/structure/ocr.py`
  - `UvSuryaOcrRunner` 现在支持通过环境变量接收：
    - `BOOK_AGENT_OCR_STATUS_PATH`
    - `BOOK_AGENT_OCR_HEARTBEAT_SECONDS`
  - `surya_ocr` 子进程不再是完全黑盒
  - runner 在执行期间会持续把 heartbeat 写到 OCR status JSON
  - status 内容包括：
    - `state`
    - `pid`
    - `returncode`
    - `command`
    - `output_snapshot`
    - `stdout_tail`
    - `stderr_tail`
    - `elapsed_seconds`
- `scripts/run_real_book_live.py`
  - 对 PDF live run 自动派生：
    - `report.ocr.json`
  - 并在 bootstrap 前通过环境变量把该路径下发给 OCR runner
  - 主 report 现在也会提前记录：
    - `ocr_status_path`
- `scripts/watch_real_book_live.py`
  - 现在会优先读取 `ocr_status_path`
  - 如果 OCR status 存在，会把它合并进 `report.live.json`
  - stage 也会区分：
    - `bootstrap_ocr_running`
    - `bootstrap_ocr_failed`
    - `bootstrap_ocr_succeeded_pre_persist`

验证：

- `uv run python -m py_compile src/book_agent/domain/structure/ocr.py scripts/run_real_book_live.py scripts/watch_real_book_live.py tests/test_pdf_support.py`
- 定向 fake-runtime probe 通过：
  - `ocr-status-heartbeat-ok`
- `uv run python scripts/watch_real_book_live.py --report-path artifacts/real-book-live/deepseek-agentic-design-book-v9-full-run-early-report/report.json --once`

当前现实边界：

- `v9` 是在这一步之前启动的
  - 所以当前 `v9` 的 `report.json` 里还没有 `ocr_status_path`
  - watcher 也只能继续基于进程树 + DB 计数侧推阶段
- 若要真正吃到这一步新增的 OCR heartbeat，可观测性需要体现在下一次 full-book run（例如 `v10`）上

当前 `v9` 最新观察：

- 仍处于：
  - `stage=bootstrap_ocr_running`
- 但仍然没有：
  - `documents`
  - `chapters`
  - `translation_packets`
  - `document_runs`
- 这意味着卡点仍然位于“整本 OCR 结果尚未落回主 DB”之前

下一次接力建议：

- 继续短时间观察 `v9`
- 如果 `v9` 长时间仍无 DB 落盘变化，则终止并重启为新的 `v10`
- `v10` 必须复用当前代码，这样才能同时产出：
  - `report.json`
  - `report.live.json`
  - `report.ocr.json`

### Step 30: Fix scanned-PDF `page_count=0` at ingest time so chunked OCR can actually trigger

Status: Done on 2026-03-18

发现的根因：

- `v10` 虽然已经能写出 `report.ocr.json`
- 但实际仍然走了“整本单次 OCR”
- 追查后确认不是 chunk 逻辑失效，而是 ingest 阶段产出的：
  - `pdf_profile.page_count = 0`
- 因为 chunked OCR 的触发条件依赖 `page_count`
  - 所以 scanned PDF 在 profile 丢页数时，会直接退回 `_extract_payload_single()`

修复内容：

- `src/book_agent/domain/structure/pdf.py`
  - `BasicPdfTextExtractor` 现在会额外写出 `page_count_hint`
  - 当基于对象树读不到页数时，会继续走 CoreGraphics fallback
  - 在 macOS 下直接调用系统 `CGPDFDocumentGetNumberOfPages`
- `PdfFileProfiler.profile_from_extraction()`
  - 现在会优先使用：
    - `max(len(extraction.pages), metadata.page_count_hint)`
  - 并修复 `pages=[]` 时的首标题信号越界分支
- `tests/test_ocr_runtime.py`
  - 新增轻量回归，确认 profiler 在 `pages=[] + page_count_hint` 时也能得到正确 scanned-PDF profile

验证：

- `uv run python -m py_compile src/book_agent/domain/structure/pdf.py src/book_agent/domain/structure/ocr.py tests/test_ocr_runtime.py`
- `uv run python -m unittest tests.test_ocr_runtime`
- 真实 ingest 复核：
  - 该扫描书现在得到：
    - `source_type=pdf_scan`
    - `pdf_profile.page_count=458`

这一步的意义：

- chunked OCR 不再依赖“文本提取器刚好能数出页数”
- 对当前这本扫描书，full-book OCR 已经真正具备分块启动条件

### Step 31: Relaunch the full-book scanned-book run as `v11`, verify chunked OCR is live, and attach a watcher

Status: Done on 2026-03-18

运行编排：

- 终止了：
  - `v9`
    - 原因：21 分钟仍然 `documents=0 / chapters=0 / translation_packets=0 / document_runs=0`
    - 且 OCR 输出目录始终没有 `results.json`
  - `v10`
    - 原因：它是在 `page_count=458` 修复前启动的，所以仍然落在单次整本 OCR 路径
- 新的正式接力目录改为：
  - `artifacts/real-book-live/deepseek-agentic-design-book-v11-full-run-chunked-pagecount`

当前 `v11` 已确认的事实：

- `report.json` 已存在：
  - `artifacts/real-book-live/deepseek-agentic-design-book-v11-full-run-chunked-pagecount/report.json`
- `report.ocr.json` 已存在：
  - `artifacts/real-book-live/deepseek-agentic-design-book-v11-full-run-chunked-pagecount/report.ocr.json`
- `report.live.json` 已存在：
  - `artifacts/real-book-live/deepseek-agentic-design-book-v11-full-run-chunked-pagecount/report.live.json`
- OCR 已真正进入 chunked page-range 模式：
  - 第一块是：
    - `page_range=0-31`
  - 对应临时输出目录：
    - `/private/var/folders/m7/gjc7m9yj36l334cdz82h51mw0000gn/T/book-agent-ocr-m44gkat7/chunk-001`
- watcher 已挂上：
  - `uv run python scripts/watch_real_book_live.py --report-path artifacts/real-book-live/deepseek-agentic-design-book-v11-full-run-chunked-pagecount/report.json --interval-seconds 30 --stop-when-terminal`

最新 live 观察：

- `stage=bootstrap_ocr_running`
- `report.ocr.json` 已显示 chunk 级 OCR 进度：
  - `page_range=0-31`
  - `Detecting bboxes: 4/4`
  - `Recognizing Text` 已推进到 `187/1012`（latest probe）
- 当前主 DB 仍然是：
  - `documents=0`
  - `chapters=0`
  - `translation_packets=0`
  - `document_runs=0`
- 这是合理现象：
  - 因为 chunk-001 仍在 OCR 中
  - parse/recover/save 尚未回写到主 DB

下一次接力优先看：

- `artifacts/real-book-live/deepseek-agentic-design-book-v11-full-run-chunked-pagecount/report.json`
- `artifacts/real-book-live/deepseek-agentic-design-book-v11-full-run-chunked-pagecount/report.ocr.json`
- `artifacts/real-book-live/deepseek-agentic-design-book-v11-full-run-chunked-pagecount/report.live.json`
- 一旦出现以下任一变化，立即继续记录：
  - `report.ocr.json.page_range` 从 `0-31` 前进到下一个 chunk
  - `report.ocr.json.output_snapshot.results_json_exists = true`
  - `db_counts.documents > 0`
  - `run_seed.run_id` 出现在主 report

### Step 32: Reattach the watcher, restore structured OCR progress in `report.live.json`, and verify `v11` chunk rollover is healthy

Status: Done on 2026-03-18

发现的问题：

- `scripts/watch_real_book_live.py` 代码里已经支持：
  - `ocr_progress`
- 但 `v11` 的现有 `report.live.json` 里一直没出现这个字段
- 追查后确认不是实现缺失，而是：
  - 老 watcher 进程是在脚本升级前启动的
  - 它持续把旧格式 snapshot 写回 sidecar

本轮动作：

- 终止旧 watcher，并重新挂载新的 live monitor 到：
  - `artifacts/real-book-live/deepseek-agentic-design-book-v11-full-run-chunked-pagecount/report.json`
- 新 watcher 重新开始写：
  - `artifacts/real-book-live/deepseek-agentic-design-book-v11-full-run-chunked-pagecount/report.live.json`
- 重新挂载后的 sidecar 已恢复结构化 OCR 进度：
  - `ocr_progress.phase=recognizing_text`
  - `ocr_progress.current=164`
  - `ocr_progress.total=1096`
  - `ocr_progress.percent=14.964`
  - `ocr_progress.page_range=32-63`

这一步确认的关键事实：

- `v11` 不仅成功完成了 `chunk-001`
- 而且已经自动切换到：
  - `chunk-002`
  - `page_range=32-63`
- 当前 OCR 子进程也已经切换为新的 PID：
  - `pid=51113`
- 这意味着：
  - chunked OCR 不是“只会跑第一块”
  - 当前主链路已经真实具备 chunk-to-chunk 连续推进能力

进一步核验：

- 直接检查临时 OCR 目录后，确认：
  - `chunk-001/.../results.json` 已真实落盘
- 当前临时目录结构至少包括：
  - `.../chunk-001/.../results.json`
  - `.../chunk-002/...`
- 因此可以排除“chunk-001 只是日志推进、但没有可消费结果文件”的误判

当前最新状态：

- `report.live.json` 已重新带出：
  - `ocr_progress`
- `report.ocr.json` 当前指向：
  - `page_range=32-63`
- 主 DB 仍然是：
  - `documents=0`
  - `chapters=0`
  - `translation_packets=0`
  - `document_runs=0`
- 这说明：
  - bootstrap 仍然在“全书 chunked OCR 收集阶段”
  - 还没进入 parse/recover/save 或 run-control

本轮验证：

- 重启 watcher 后首次 probe 成功输出 `ocr_progress`
- 读取最新 sidecar，确认 `page_range` 已从 `0-31` 前进到 `32-63`
- 直接检查 OCR 临时目录，确认：
  - `chunk-001` 已有 `results.json`

下一次接力建议：

- 继续优先观察：
  - `report.live.json.snapshot.ocr_progress`
  - `report.ocr.json.page_range`
- 一旦出现以下任一变化，立即继续记录：
  - `page_range` 再前进到 `64-95`
  - `db_counts.documents > 0`
  - `report.json` 出现 `bootstrap.document_id`
  - `report.json` 出现 `run_seed.run_id`

### Step 33: Extend the watcher sidecar with chunk-completion summary so handoff does not require parsing temp OCR directories manually

Status: Done on 2026-03-18

本轮增强：

- `scripts/watch_real_book_live.py`
  - 新增 `ocr_chunk_summary`
  - sidecar 现在会直接汇总：
    - `current_chunk`
    - `chunk_dirs`
    - `completed_chunks`
    - `completed_chunk_count`
    - `latest_completed_chunk`
    - `latest_completed_results_json_path`

这样做的原因：

- 之前虽然已经能看到：
  - `ocr_progress`
  - `page_range`
- 但接手的人仍然要自己去翻：
  - `/private/var/.../book-agent-ocr-*/chunk-*`
- 这在 token 被限流、平台切换、或只读交接时都不够友好

验证结果：

- 新 watcher 已重新挂载到：
  - `artifacts/real-book-live/deepseek-agentic-design-book-v11-full-run-chunked-pagecount/report.json`
- 最新 `report.live.json` 现在已经直接给出：
  - `ocr_chunk_summary.current_chunk=chunk-002`
  - `ocr_chunk_summary.completed_chunks=[\"chunk-001\"]`
  - `ocr_chunk_summary.completed_chunk_count=1`
  - `ocr_chunk_summary.latest_completed_chunk=chunk-001`
  - `ocr_chunk_summary.latest_completed_results_json_path=.../chunk-001/.../results.json`
- 同时，`ocr_progress` 已继续推进到：
  - `current=673`
  - `total=1096`
  - `percent=61.405`
  - `page_range=32-63`

当前接力含义：

- 任何后续平台只要看：
  - `report.live.json`
- 就能立即知道：
  - 当前正在跑哪一个 chunk
  - 已经完成了多少 chunk
  - 最近一个完成 chunk 的真实 `results.json` 路径
- 不再需要手动解析临时 OCR 根目录

下一次接力建议：

- 继续以 `report.live.json.snapshot.ocr_chunk_summary` 为第一观察入口
- 如果：
  - `completed_chunk_count` 增加到 `2`
  - 或 `current_chunk` 前进到 `chunk-003`
- 就把该变化继续记录进本计划文档
  - 同时继续观察 `db_counts.documents` 是否首次大于 `0`

### Step 34: Capture the full terminal state of `v11` and confirm the full-book scanned-PDF path is no longer blocked on OCR/bootstrap

Status: Done on 2026-03-18

这一步确认的事实：

- `v11` 没有停死在 OCR/bootstrap
- 相反，它已经完成了：
  - 全书 chunked OCR
  - bootstrap 落库
  - translation packet 建包
  - 部分正式翻译执行

最终运行状态：

- `document_runs`
  - `run_id=1e82f1b6-f0e8-4b77-ae5e-617fd91a1f64`
  - `status=failed`
  - `stop_reason=budget.consecutive_failures_exceeded`
  - `finished_at=2026-03-18 09:44:17+00:00`
- `report.json`
  - `bootstrap_in_progress=false`
  - `bootstrap.document_id=67283f52-b775-533f-988b-c7433a22a28f`
  - `bootstrap.source_type=pdf_scan`
  - `bootstrap.pdf_profile.page_count=458`
- `report.live.json` 终态计数：
  - `documents=1`
  - `chapters=97`
  - `blocks=3188`
  - `sentences=6376`
  - `translation_packets=2170`
  - `document_runs=1`
  - `work_items=2170`

翻译执行终态：

- `work_item_status_counts`
  - `succeeded=320`
  - `pending=1832`
  - `terminal_failed=17`
  - `retryable_failed=1`
- `translation_packet_status_counts`
  - `translated=320`
  - `built=1850`

OCR 终态：

- `report.ocr.json`
  - `state=succeeded`
  - 最后一块是：
    - `page_range=448-457`
    - `chunk-015`
  - `ocr_progress`
    - `330/330`
    - `100%`
- 这说明：
  - 对这本 `458` 页 scanned book，chunked OCR 主路径已经真实跑通到最后一块

结论：

- 当前“PDF 书籍无法完整翻译”的新阻塞点已经不再是 OCR/layout/bootstrap
- 而是 provider 余额/配额侧的翻译执行中断
- 也就是说：
  - PDF 论文样本已能整章 clean run
  - scanned PDF 书籍样本已能整本完成 OCR/bootstrap 并进入大规模翻译
  - 当前剩余阻塞是运行资源治理，而不是 PDF 解析能力

### Step 35: Fix provider-balance exhaustion handling so future full-book runs pause clearly instead of burning retries into `budget.consecutive_failures_exceeded`

Status: Done on 2026-03-18

根因收敛：

- `v11` 的失败 work item 几乎全部是同一种错误：
  - `Provider returned HTTP 402`
  - `Insufficient Balance`
- 追查后发现：
  - `src/book_agent/app/runtime/document_run_executor.py`
  - 里的 `_is_retryable_exception()` 会把几乎所有 `RuntimeError` 都当成 retryable
- 结果是：
  - 明确的 provider 余额耗尽也会继续重试/继续 claim 新 packet
  - 然后 run 最终被：
    - `budget.consecutive_failures_exceeded`
    - 打断

修复内容：

- `src/book_agent/app/runtime/document_run_executor.py`
  - `_is_retryable_exception()` 不再把泛化 `RuntimeError` 默认视为可重试
  - `HTTP 402 / Insufficient Balance` 现在会被判定为 non-retryable
  - 新增 `_pause_reason_for_exception()`
    - 当前把：
      - `HTTP 402 + Insufficient Balance`
      - 映射成：
        - `provider.insufficient_balance`
  - `_complete_failure()` 现在会在该场景下：
    - 立即 `pause_run_system(...)`
    - 而不是继续让 run 烧进 budget failure guardrail
- `scripts/watch_real_book_live.py`
  - 终态 probe 现在会回退到 `report.ocr.json.output_dir`
  - 所以即使 OCR 子进程已经结束，sidecar 里仍能保留：
    - 最后一个 chunk 的 OCR 终态线索

行为变化：

- 以后再次遇到 provider 余额耗尽时：
  - run 会更早停在：
    - `paused`
    - `stop_reason=provider.insufficient_balance`
- 不会再无意义地消耗多批 packet，把状态变成：
  - `budget.consecutive_failures_exceeded`

验证：

- `uv run python -m py_compile src/book_agent/app/runtime/document_run_executor.py tests/test_run_execution.py scripts/watch_real_book_live.py`
- `uv run python -m unittest tests.test_run_execution`
- 新增回归覆盖：
  - `HTTP 429` 仍是 retryable
  - `HTTP 402 Insufficient Balance` 不再 retryable
  - provider 余额耗尽会立即把 run 置为 `paused`
- 终态 one-shot probe 也确认：
  - `report.live.json` 现在能保留：
    - `ocr_chunk_summary.current_chunk=chunk-015`
    - 最后一块 OCR 终态仍可见

下一次接力建议：

- 如果 provider 余额恢复：
  - 直接基于 `document_id=67283f52-b775-533f-988b-c7433a22a28f`
  - 以及失败 run `1e82f1b6-f0e8-4b77-ae5e-617fd91a1f64`
  - 发起 retry/resume
- 预期新行为应当是：
  - 若再次余额不足，run 会停在：
    - `provider.insufficient_balance`
  - 而不是再次烧成：
    - `budget.consecutive_failures_exceeded`

### Step 36: Teach the live runner to retry failed runs directly and mirror the new provider-balance guardrail behavior

Status: Done on 2026-03-18

为什么要做这一步：

- 上一步修的是：
  - `DocumentRunExecutor`
- 但整本书的真实 autopilot harness 用的是：
  - `scripts/run_real_book_live.py`
- 如果不把同样的策略补到 live runner：
  - 真实整本重试仍然会保留旧缺陷
  - 而且 `--run-id <failed-run>` 也不会真正发起 retry run

修复内容：

- `scripts/run_real_book_live.py`
  - `_is_retryable_exception()` 现在不再把泛化 `RuntimeError` 默认视为 retryable
  - `HTTP 402 / Insufficient Balance` 现在会被视为 non-retryable
  - 新增 `_pause_reason_for_exception()`
    - `HTTP 402 + Insufficient Balance`
    - 映射为：
      - `provider.insufficient_balance`
  - work item 失败时，如命中该条件，会立即：
    - `pause_run_system(...)`
  - `--run-id` 现在支持：
    - `queued/paused` => `resume_run(...)`
    - `failed/cancelled` => `retry_run(...)`
- 同时补了 retry provenance：
  - retry run 的 `status_detail_json`
    - 会刷新到新的 `source_path/report_path/export_root`
    - 不再保留旧 run 的 `last_failure`

验证：

- `uv run python -m py_compile scripts/run_real_book_live.py`

这一步的结果：

- 后续只要 provider 余额恢复
- 就可以直接用同一条 live-runner 命令继续自动驾驶
- 不需要再手工先写一段临时 Python 去调 `RunControlService.retry_run()`

### Step 37: Start `v12` retry-after-balance run and confirm full-book translation has resumed cleanly

Status: In progress on 2026-03-18

新的接力目录：

- `artifacts/real-book-live/deepseek-agentic-design-book-v12-retry-after-balance`

运行方式：

- 复用了 `v11` 的主库：
  - `artifacts/real-book-live/deepseek-agentic-design-book-v11-full-run-chunked-pagecount/book-agent.db`
- 但使用新的：
  - `report.json`
  - `report.live.json`
  - `exports/`
- 这样做的好处是：
  - 继续复用已完成的整本 OCR/bootstrap/document state
  - 同时保留新的 retry 轨迹，方便平台切换和交接

已确认的 retry run：

- 原失败 run：
  - `1e82f1b6-f0e8-4b77-ae5e-617fd91a1f64`
- 新 retry run：
  - `34e0a11c-a7e8-47ee-b22a-ab10c25a5d69`
- `resume_from_run_id` 已正确指向旧失败 run

当前进度：

- `run_seed`
  - `seeded_work_item_count=1850`
  - `pending_packet_count_initial=1850`
- 这说明：
  - v11 已翻过的 `320` 个 packets 没有被重复 seed
  - retry run 只接力剩余 built packets
- 最新 live probe 显示：
  - `stage=translate_in_progress`
  - `document_runs=2`
  - `translation_packet_status_counts.translated=362`
  - `translation_packet_status_counts.built=1808`
  - `work_item_status_counts.running=3`
  - `work_item_status_counts.retryable_failed=1`
  - `work_item_status_counts.terminal_failed=17`
- 注意这里：
  - `translation_packet_status_counts.*` 是文档级累计状态
  - 而 `report.json.translate.translated_packet_count=44`
    - 是新 retry run 自己已经完成的 packets 数

健康性判断：

- 新 run 启动后连续完成了多批 packet
- 当前尚未出现新的 provider failure spike
- 最新 `status_detail_json.control_counters` 也显示：
  - `completed_work_item_count=44`
  - `retryable_failure_count=0`
  - `terminal_failure_count=0`
  - `consecutive_failures=0`

持续监控后的最新进展（latest probe）：

- `report.live.json`
  - `stage=translate_in_progress`
  - `translation_packet_status_counts.translated=443`
  - `translation_packet_status_counts.built=1727`
  - `work_item_status_counts.running=4`
  - `work_item_status_counts.succeeded=443`
  - `work_item_status_counts.retryable_failed=1`
  - `work_item_status_counts.terminal_failed=17`
- `report.json.translate`
  - `run_status=running`
  - `translated_packet_count=135`
  - `pending_packet_count=1711`
  - `retryable_failed_packet_count=0`
  - `terminal_failed_packet_count=0`
  - `consecutive_failures=0`
- 这表示：
  - retry run 已持续推进 8 分钟以上
  - 没有出现新的 provider failure spike
  - 当前仍然是健康吞吐，而不是仅仅“刚启动时短暂正常”

本轮额外修正：

- 已把当前 `v12` run 的 `status_detail_json` 手工回正到新轨迹：
  - `report_path` => `.../v12-retry-after-balance/report.json`
  - `export_root` => `.../v12-retry-after-balance/exports`
  - `last_failure` 已清空

当前接力入口：

- `report.json`
  - `artifacts/real-book-live/deepseek-agentic-design-book-v12-retry-after-balance/report.json`
- `report.live.json`
  - `artifacts/real-book-live/deepseek-agentic-design-book-v12-retry-after-balance/report.live.json`

下一次接力建议：

- 继续优先观察：
  - `report.live.json.snapshot.translation_packet_status_counts`
  - `report.live.json.snapshot.work_item_status_counts`
  - `report.json.translate`
- 如果出现以下任一变化，继续记录：
  - `translated` 明显持续增长
  - `exports` 开始出现
  - `run_status` 进入 `succeeded`
  - 或再次出现 `provider.insufficient_balance`

### Step 38: Diagnose the apparent `v12` slowdown, separate real throughput from reporting distortion, and fix UTC serialization for future runs

Status: Completed on 2026-03-18

本轮先直接核对了 `v12` 的三份真相源：

- `artifacts/real-book-live/deepseek-agentic-design-book-v12-retry-after-balance/report.json`
- `artifacts/real-book-live/deepseek-agentic-design-book-v12-retry-after-balance/report.live.json`
- `artifacts/real-book-live/deepseek-agentic-design-book-v11-full-run-chunked-pagecount/book-agent.db`

当前实时进度（latest probe）：

- `report.live.json.snapshot.stage=translate_in_progress`
- `report.live.json.snapshot.translation_packet_status_counts`
  - `translated=661`
  - `built=1509`
- `report.live.json.snapshot.work_item_status_counts`
  - `pending=3331`
  - `running=4`
  - `succeeded=661`
  - `retryable_failed=1`
  - `terminal_failed=23`
- `report.live.json.snapshot.elapsed_seconds_since_start=2183.072`
  - 约等于 `36.4` 分钟

结论一：`v12` 并没有停住，真实是在持续推进。

- 当前 runner 仍活着
- OCR 早已结束
- 现在纯粹卡在 translate 阶段
- 当前 run 自己的 work item 状态也很干净：
  - `pending=1493`
  - `running=4`
  - `succeeded=347`
  - `terminal_failed=6`

结论二：报表里的“超慢 ETA”被时间戳序列化 bug 明显夸大了。

- `document_runs.started_at` 在 SQLite 中真实是：
  - `2026-03-18 13:30:19.791297`
- 但 `report.json.run.started_at` 被写成了：
  - `2026-03-18T05:30:19.791297+00:00`
- 这正好是 `8` 小时偏移，说明：
  - naive SQLite datetime 在序列化时被 Python 当成本地时区值再转 UTC
- 这个偏移会直接污染：
  - `translate.elapsed_seconds`
  - `translate.avg_packet_seconds`
  - `translate.estimated_finish_at`
- 因此 `report.json` 里当时显示的：
  - `avg_packet_seconds≈92s`
  - 超长 ETA
  - 不可信，至少被显著放大

结论三：真实吞吐并不算快，但比报表看上去快得多。

- 依据 `status_detail_json.usage_summary.latency_ms=4,961,660`
- 和 `completed_work_item_count=341`
- 可反推单个已完成 work item 的平均模型时延约为：
  - `14.55s`
- 在当前 budget：
  - `max_parallel_workers=4`
  - `max_parallel_requests_per_provider=4`
- 理想上限约为：
  - `16.5 packets/min`
- 所以真实瓶颈主要是：
  - provider/模型单包时延本身就在 `14-15s`
  - 当前并发上限只有 `4`
  - 不是 OCR 卡死
  - 也不是 translate worker 完全不工作

结论四：当前额外损耗主要是少量 provider 非结构化响应，不是大面积 budget 问题。

- `v12` 当前 terminal failed work items 是 `6`
- 最近失败类型统一是：
  - `RuntimeError: Provider response did not include a structured JSON output payload.`
- 这会带来一些尾部损耗
- 但并没有形成像此前 `HTTP 402 / Insufficient Balance` 那样的全局停摆

本轮代码修正：

- 已修复 `src/book_agent/services/run_control.py`
  - `RunControlService._isoformat()` 现在会把 naive datetime 显式视为 UTC
  - 这样未来新的 run summary / report 不会再把时长平白放大 `8` 小时
- 已补回归测试：
  - `tests/test_run_execution.py`
  - `test_run_control_isoformat_treats_naive_sqlite_datetimes_as_utc`

需要明确的现实边界：

- `v12` 当前 runner 进程已经在内存中加载了旧代码
- 所以当前正在持续刷新的这份 `v12/report.json`
  - 在本次 runner 重启前
  - 仍可能继续显示旧的错误 `run.started_at`
- 但这个问题现在已经被定位清楚
- 并且对后续 run / retry / 新 report 已经从代码层修掉

### Step 39: Analyze full-book token burn, judge whether a `~5 RMB` cost expectation is realistic, and record practical optimization directions

Status: Completed on 2026-03-18

本轮基于主库和 `v12` report 做了成本拆解，先看已经发生的真实账单。

当前 full-book 主线 run：

- `v11 failed run`
  - `run_id=1e82f1b6-f0e8-4b77-ae5e-617fd91a1f64`
  - `token_in=587,551`
  - `token_out=85,357`
  - `cost_usd=0.16072141`
- `v12 retry run`
  - `run_id=34e0a11c-a7e8-47ee-b22a-ab10c25a5d69`
  - `token_in=2,265,089`
  - `token_out=275,937`
  - `cost_usd=0.63085177`

仅这两条 full-book 主线累计：

- `token_in=2,852,640`
- `token_out=361,294`
- `total_tokens=3,213,934`
- `cost_usd=0.79157318`

按 `1 USD ~= 7.2 CNY` 粗算：

- 当前已经花了约 `5.70 RMB`

而此时文档真实进度是：

- `translation_packets.translated=1330`
- `translation_packets.built=840`
- 总 packet 数 `2170`

这意味着：

- 当前约完成了 `61.3%`
- 所以“整本翻完总成本约 `5 RMB`”这个预期偏乐观
- 更准确地说：
  - `5-6 RMB` 更接近“现在已经花到这里”的量级
  - 不是“最终整本完工成本”

按当前主线 run 的实际成本斜率粗估：

- 平均每个已翻译 packet 成本约：
  - `0.000595 USD`
- 如果整本 `2170` 个 packet 都按这个斜率跑完：
  - 预计总成本约 `1.2915 USD`
  - 约 `9.3 RMB`

所以当前判断是：

- 如果你的意思是“到现在大概已经花了 `5 RMB`”
  - 这是合理的
- 如果你的意思是“整本书最终只要 `5 RMB` 左右”
  - 以当前真实 run 斜率看，不太够
  - 更像是 `8-10 RMB` 区间
  - 如果后续还有 retry / followup / provider 非结构化响应失败
    - 还会略高一点

和你截图的 token 面板对比：

- 截图总 token：
  - `4,293,672`
- 当前 full-book 主线两条 run 合计：
  - `3,213,934`
- 差值约：
  - `1,079,738`

说明截图里的当日 token 并不全是这条 full-book 主线本身，还混有：

- 首章 smoke
- 论文 smoke / 回归
- 调试与诊断性调用
- 以及部分缓存命中流量

从 prompt 结构上看，当前成本“可接受但还有明显优化空间”。

真实 packet 结构统计：

- 平均 `current_blocks` 只有 `300.49` 字符
- 但平均：
  - `prev_blocks=557.52` 字符
  - `next_blocks=566.34` 字符
  - `chapter_brief=610.91` 字符
- 也就是说每个 packet 的固定上下文平均约：
  - `1734.77` 字符
- 与真正待翻正文相比：
  - `context/current ≈ 5.77x`
  - 正文只占 prompt 主要文本体积的约 `14.8%`

同时主线 run 的真实 token 斜率也支持这个判断：

- 已翻译 `1330` 个 packets
- 平均每个已翻译 packet：
  - `input≈2144.8 tokens`
  - `output≈271.6 tokens`
  - `total≈2416.5 tokens`
- 说明当前 token burn 主要由输入侧 prompt 冗余驱动
  - 不是输出太长驱动

优化优先级建议：

- P1: 缩短 packet 固定上下文
  - 优先把 `prev_blocks + next_blocks` 从固定 `2+2` 调成按 block 长度预算裁剪
  - 对超短正文 packet，不必总是携带完整两侧上下文
- P1: 压缩 `chapter_brief`
  - 目前平均 `610` 字符，重复注入 `2170` 次非常贵
  - 改成更短的“chapter gist + key constraints”版本，目标先砍到 `150-250` 字符
- P1: 合并过碎 packet
  - 现在 `2170` 个 packet 中每个 packet 都只有 `1` 个 `current_block`
  - 且有 `1354` 个 packet 的正文不足 `300` 字符
  - 这类短块最容易被固定 prompt 开销吞掉
  - 应优先尝试把相邻低风险 paragraph 合并成更大的 translation packet
- P2: 只在必要时携带邻近块
  - heading 边界清晰、语义自足的 paragraph，可以只带单侧或不带邻近块
  - caption / list item / broken OCR fragment 才保留更重的邻接上下文
- P2: 把 chapter-level信息改成 ID + lookup，而不是每包重复灌全文
  - 如果 worker/provider 形态允许，可把 `chapter_brief` 做成更稳定、更短的 normalized memory
  - 只把当前 packet 真正命中的约束塞进 prompt
- P2: 降低非结构化 provider 响应带来的浪费
  - 当前已有少量 `Provider response did not include a structured JSON output payload`
  - 这会造成额外 token 消耗但不产出有效译文
  - 应继续强化 response-format guardrail 和 provider fallback

一句话结论：

- 这本 `458` 页、`17.6 MB` 的 scanned PDF，当前主线成本跑到 `5-6 RMB` 并不离谱，反而算偏省
- 但若按当前 packet 设计和上下文注入方式继续跑完整本，最终更像是 `8-10 RMB`，而不是 `5 RMB` 封顶

### Step 40: Evaluate whether `prev_blocks + current_blocks + next_blocks + chapter_brief` is the right translation context shape

Status: Completed on 2026-03-18

从系统分层上看，需要区分两个问题：

- 作为 `ContextPacket` schema，这些字段是合理的
- 作为当前 default prompt policy，“每个 packet 无条件全带”并不合理

代码基线：

- `src/book_agent/workers/contracts.py`
  - `ContextPacket` 同时定义了：
    - `current_blocks`
    - `prev_blocks`
    - `next_blocks`
    - `prev_translated_blocks`
    - `chapter_concepts`
    - `relevant_terms`
    - `relevant_entities`
    - `chapter_brief`
- `src/book_agent/domain/context/builders.py`
  - `_build_packet()` 当前会无条件把：
    - 相邻 source blocks
    - chapter brief
    - heading path
    - style constraints
    - term/entity match 结果
    - 一起塞进 packet
- `src/book_agent/workers/translator.py`
  - 当前 prompt 构造也会无条件把这些 section 全部串进 user prompt

所以问题不是 schema 设计错了，而是：

- packet schema 目前被直接当成了 prompt payload
- 缺少“按风险/长度/歧义动态裁剪上下文”的策略层

对这本真实 scanned-book run 的统计非常明确：

- `2170` 个 packet 里：
  - `current_blocks=1` 的 packet 有 `2170`
  - `prev_blocks=2` 的 packet 有 `2005`
  - `next_blocks=2` 的 packet 有 `2005`
  - `chapter_brief` 非空的 packet 有 `2170`
  - `relevant_terms` 非空的 packet 有 `0`
  - `chapter_concepts` 非空的 packet 有 `0`
  - `relevant_entities` 非空的 packet 有 `0`
  - `prev_translated_blocks` 非空的 packet 有 `0`

这说明当前 prompt 真正有内容的上下文，主要只剩：

- `current_blocks`
- `prev_blocks`
- `next_blocks`
- `chapter_brief`

其余 memory sections 在这本书当前 run 上几乎没有收益，但 prompt header 仍会被写出来。

从 token 结构看：

- 平均 `current_blocks` 文本约 `300.49` 字符
- 平均：
  - `prev_blocks=557.52`
  - `next_blocks=566.34`
  - `chapter_brief=610.91`
- 即固定上下文平均约 `1734.77` 字符
- `context/current ≈ 5.77x`

结论：

- 对 accuracy-first 的系统设计来说：
  - `prev/current/next/brief` 作为“可选上下文字段”是合理的
- 但对当前实现来说：
  - 把它们当成每包默认必带 prompt，不合理
  - 尤其在：
    - 每包只翻 `1` 个 block
    - 且多数正文都很短
    - 且 term/concept memory 基本为空
  - 的情况下，会把 token 成本显著推高

如果“只保留 `current_blocks`”：

- 优点
  - token 会明显下降
  - 这本书上很可能不只是降两倍，至少对动态文本载荷而言，两倍是保守估计
  - 对大量语义自足的技术段落，译文大概率仍然可用
- 代价
  - 丢失局部 discourse continuity
  - 短承接段、转折段、指代段更容易漂移
  - OCR 切碎段、列表项、caption、heading 后首段更容易误判语气和功能
  - 章节级意图信号减弱，译文更容易“句子正确但段落用途不清”
  - term/entity 命中会进一步下降
    - 因为当前 term/entity match 是基于 `prev + current + next` 的 `context_text`

因此更合理的方向不是“永远只留 `current_blocks`”，而是：

- `current_blocks`
  - 永远保留
- `heading_path`
  - 成本低，建议长期保留
- `chapter_brief`
  - 默认压缩
  - 只在首段、过短段、意图不清段、review 标出 drift 风险段时注入
- `prev_blocks`
  - 用于承接段、指代段、列表项、OCR 断裂段、caption/table/equation 周边段
- `next_blocks`
  - 只在当前段明显不完整、存在 forward reference、或句子被 block 边界切断时注入
- `prev_translated_blocks`
  - 一旦可用，优先级应高于原始 `prev_blocks`
  - 因为它更短，也更直接服务术语和措辞 continuity
- 空 section
  - 不要再输出 `- none` header

一句话判断：

- `prev_blocks + current_blocks + next_blocks + chapter_brief` 作为 schema 是合理的
- 作为所有 packet 的默认 prompt 组合，不合理
- 最优解是“以 `current_blocks` 为主，其他上下文按风险和长度按需打开”，而不是两个极端：
  - 全带
  - 或全砍成只剩 `current_blocks`

## Consolidated Context

### External plan summary

`~/.claude/plans/iterative-dreaming-volcano.md` 提出的核心判断是成立的：

- 现有 PDF 管线只真正支持 text PDF
- 图片/图表在提取阶段就会丢失
- 双栏和复杂排版目前只是“检测到风险”，不是“真正恢复了阅读顺序”
- 学术论文里 figure / table / equation-heavy 页面仍是主缺口
- 正确做法应当是先扩展统一 IR，再把 OCR、layout analysis、protected artifact export 接进既有主链路

### Internal docs summary

仓库内相关文档给出了更精确的当前边界：

- `docs/pdf-support-implementation-plan.md`
  - 已完成的主线是 `P1-A/P1-B` 文本 PDF 结构恢复
  - OCR 与 scanned PDF 仍明确属于后续阶段
- `docs/pdf_status.md`
  - 当前对外 contract 仍是 `p1_text_pdf_bootstrap`
  - 低风险文本 PDF 与一部分 medium-risk academic paper 已能进入正式翻译/导出
  - 扫描 PDF、OCR、复杂图表/公式保护仍未开始
- `docs/pdf_decisions.md`
  - 已冻结“先分流、再恢复、fail-safe、风险显式传播”的原则
  - `ocr_required=true` 当前仍表示 unsupported upstream input
- `docs/pdf_backlog.md`
  - OCR、扫描主路径、双栏 robust support 仍在 later 阶段

### Current code baseline

与本次升级计划直接相关的基础设施已经落地：

- `src/book_agent/domain/enums.py`
  - `SourceType.PDF_MIXED`
  - `BlockType.FIGURE / EQUATION / IMAGE`
- `src/book_agent/domain/block_rules.py`
  - `FIGURE / EQUATION / IMAGE` 已按 protected artifact 处理
- `src/book_agent/services/bootstrap.py`
  - ingest 已能把 `mixed_pdf` 分流到 `pdf_mixed`
  - parse 已给 `pdf_scan` 留出 OCR 错误提示路径
  - segmentation 已支持新 block type 不再按普通段落切句
- `src/book_agent/domain/structure/pdf.py`
  - 文本提取/恢复阶段现在会保留 PDF 原生图片块，并恢复成 `BlockType.IMAGE`
  - `pdf_page_evidence` 现在会暴露 `raw_image_block_count`
- `src/book_agent/domain/models/document.py`
  - `DocumentImage` 数据模型已存在
- `src/book_agent/services/export.py`
  - export 现在已有 PDF 图片资产导出的第一版路径，并能在裁图成功时回写稳定 `DocumentImage` 资产
- `src/book_agent/services/review.py`
  - review 现在可把 academic paper / partial-link chapter 中的未关联图片提升为 `IMAGE_CAPTION_RECOVERY_REQUIRED` advisory
- `alembic/versions/20260317_0006_document_images_and_new_enums.py`
  - 枚举扩展和 `document_images` 表 migration 已存在

这意味着原计划里的 Phase 1 已基本完成，但仅完成了“schema 与主链路可接受新类型”的基础层，并未形成端到端 OCR/image/layout 能力。

## Current Gaps That Still Matter

### Gap 1: PDF image blocks are no longer dropped, but only have first-pass recovery

当前图片块已经可以进入统一 IR，并恢复成 `BlockType.IMAGE`。  
但这还只是第一层修复：

- 目前默认文本内容仍是占位文本 `[Image]`
- 已有 figure/caption 的第一版几何启发式关联，但还不够 robust
- 还没有 layout-aware 插图顺序恢复
- 还没有 OCR/alt-text/semantic enrichment

### Gap 2: `pdf_mixed` is routed, but not truly supported

当前 ingest 已能把 mixed PDF 单独标成 `pdf_mixed`，但 parse 仍然复用现有 `PDFParser` 文本提取路径。  
这意味着：

- mixed PDF 中有文本层的页面仍可能被处理
- 纯扫描页不会真正 OCR
- 文档被“允许进入系统”和“真正支持 mixed OCR”仍不是一回事

### Gap 3: `DocumentImage` has minimal wiring, but not a finished asset lifecycle

目前 `DocumentImage` 已经不只是 schema：

- parser 已可写入 `document_images`
- bootstrap/export repository 已可读取
- export artifacts 与 document summary/API 已会消费这些记录

但它仍不是完整资产系统：

- 默认 `storage_path` 仍多为逻辑路径，不等于物理图片文件已存在
- 还没有独立 materialization/cache/cleanup/rerun 策略
- caption 目前只有 image-caption 一阶关联，还没有和 OCR、equation/table 语义层做统一建模

### Gap 4: PDF image export now exists, but is still a first-pass strategy

当前 export 已有 PDF 图片资产链路的第一版：

- 若 source PDF 可访问，且环境中有 `PyMuPDF (fitz)`，export 会按 `page_number + bbox` 即时裁出图片并在 HTML 中引用
- 成功裁图后，产物现在会物化到稳定 `DocumentImage` 路径，后续导出可直接复用

但它还没有完全成型：

- 当前物化时机仍绑定在 export，不是独立资产任务
- 还没有缓存 / 清理 / rerun 生命周期
- 当前本地环境没有 `fitz`，所以缺少端到端 crop 验证

### Gap 5: Real OCR validation corpus now exists, but the capability gap is explicit

现在已经有了对目标用例真正相关的英文真实样本：

- 英文扫描书：`Agentic Design Patterns A Hands-On Guide to Building Intelligent Systems`
- 英文论文：`Forming Effective Human-AI Teams: Building Machine Learning Models that Complement the Capabilities of Multiple Experts`

这意味着问题边界已经比之前清晰得多：

- 论文样本并不是“不能翻”，而是 packet 级真实 LLM 调用很慢，需要可续跑、可见进度的 smoke workflow
- 书籍样本不是“还没测”，而是已经被证明卡在 `PDF_SCAN -> OCR missing` 这条真实能力缺口上

所以下一阶段不再是“先去找 corpus”，而是要把这些真实样本升级成正式验收基线，并用它们驱动 OCR 主路径实现与性能验收

## Refactoring Goals

升级路线继续沿用既有 PDF 决策，不另造第二套翻译系统：

1. OCR / layout / figure recovery 只扩展 ingest、parse、review、export 的上游能力。
2. 统一落到现有 `document -> chapter -> block -> sentence -> packet -> review -> export` IR。
3. 继续 fail-safe；不因为接入 OCR 就把高风险结构误报成“已支持”。
4. 对英文 technical book 与 academic paper 分别建立验收样本，而不是只靠 synthetic fixture。
5. 先解决“结构是否可靠”，再追求“图像/公式是否漂亮渲染”。

## Phase Plan

### Phase 1: Foundation

Status: Done

已完成事项：

- [x] `BlockType` 扩展：`figure / equation / image`
- [x] `SourceType` 扩展：`pdf_mixed`
- [x] block rules / segmentation 对新 block type 兼容
- [x] bootstrap 路由为 mixed / scan 预留入口
- [x] `DocumentImage` 模型与 migration
- [x] Phase 1 回归测试未破坏现有 text PDF 主链路

当前结论：

- 这一步的设计是合理的，且与现有 P1 文本 PDF 主链路兼容
- 但它只是“铺路”，还不是 OCR / layout 能力本身

### Phase 2: Layout Analysis For Text PDF

Goal:

- 先增强 text PDF，解决双栏英文论文和图文混排英文书最明显的结构问题
- 不把 Phase 2 和 OCR 绑定死；text PDF 的 layout hardening 应可独立交付

Recommended deliverables:

- 新增 `LayoutAnalyzer` protocol，与具体引擎解耦
- 新增 `LayoutRegion / PageLayout` 结构，表达 `text/title/figure/table/equation/caption/header/footer/list`
- 新增 `LayoutEnhancedRecoveryService`
  - 输入：现有 `PdfExtraction` + layout regions
  - 输出：仍是 `ParsedDocument`
- 双栏阅读顺序增强
  - 优先在 `academic_paper` lane 落地
  - 先覆盖 figure / equation 周边最容易错序的页面
- figure region 二次提取
  - 从原 PDF 页面裁剪图片
  - 生成 `ParsedBlock(block_type=FIGURE or IMAGE)`
  - 为后续 `DocumentImage` 持久化准备 metadata

Design constraints:

- 不应直接“用 layout 输出完全替代现有文本恢复”，否则会把已经稳定的 P1 逻辑一并推翻
- 应采用“layout as evidence override”的模式：优先保留现有 recovery，再让 layout 只覆盖 heuristics 最薄弱的地方
- figure/caption 必须定义关联关系，避免 caption 被保留、figure 被重复生成，或反过来 figure 被抽出后正文仍残留重复文本

Acceptance:

- 真实英文双栏论文的阅读顺序明显优于当前 academic paper hardening
- text PDF 中的典型插图不再完全丢失
- 未安装 layout 引擎时，现有 text PDF 路径仍可回退运行

### Phase 3: OCR For Scanned And Mixed PDF

Goal:

- 让 scanned PDF 与 mixed PDF 进入真正可执行的解析主路径
- 让 OCR 成为“支持的输入模式”，而不再只是 fail-safe reject

Recommended deliverables:

- 新增 `OcrPdfTextExtractor`
  - 文本页继续用 PyMuPDF
  - 无文本页或文本极稀疏页走 OCR
- 新增 `OcrPdfParser`
  - OCR 文本恢复
  - layout analysis
  - 结构恢复
  - confidence 传播
- 在 `ParseService` 中把 `PDF_SCAN / PDF_MIXED` 接到 OCR 主路径
- 为 page/block 增加 OCR 来源字段
  - `page_modality`: `text`, `ocr`, `mixed`
  - `ocr_engine`
  - `ocr_confidence`

Required design adjustment:

当前 `ocr_required` 的语义是“P1 unsupported input”。  
当 OCR 真正接入主路径后，需要拆成两个概念：

- `needs_ocr`: 输入形态
- `support_status` 或 `parse_mode`: 当前是否已有可执行 OCR 主路径

否则：

- `pdf_status.md` 里的风险/支持边界会变得含混
- review/operator 无法区分“因为复杂而高风险”和“因为系统未支持而阻断”

Acceptance:

- 一份真实英文扫描论文可以从 ingest 走到 parse 并产出可 review 的章节
- 一份真实 mixed PDF 可以同时处理文本页和扫描页
- OCR 置信度能传播到 block / sentence / chapter 层

### Phase 4: Protected Artifacts, Equation, Table, Export Assets

Goal:

- 让 figure / equation / table 不再只是“识别了但保不住”，而是能稳定进入导出与 review

Recommended deliverables:

- 把 `DocumentImage` 从“最小接线”收口成真正资产层
  - 谁负责 materialize
  - 何时落盘 / 何时复用
  - rerun / cleanup / cache invalidation 怎么做
- equation 处理
  - 第一阶段先允许 equation 以 protected artifact 原样保留
  - LaTeX 化作为增量能力，不强绑在 OCR 首发版本中
- table 处理
  - 文本 PDF 与扫描 PDF 分开设计
  - 避免把“table OCR”与“普通 OCR”一起塞进第一版
- export 资产链路
  - PDF figure/image 的落盘目录
  - manifest 引用方式
  - merged HTML / bilingual HTML 如何引用本地导出资产

Recommended delivery order inside this phase:

1. figure/image 资产落盘与导出
2. protected artifact review evidence
3. equation 原样保护
4. table 结构增强
5. optional LaTeX

Acceptance:

- figure block 可以在 merged/bilingual HTML 中看到真实图片，而不是只有 metadata
- equation/table 至少能安全保留，不污染正文翻译

### Phase 5: QA, Observability, English Corpus Validation

Goal:

- 把 OCR/layout 的不确定性纳入现有 QA / rerun / export gate
- 建立真正针对英文书籍、英文论文的验收样本

Required deliverables:

- OCR/layout-specific review issues
  - low OCR confidence
  - ambiguous column order
  - figure/caption mismatch
  - equation/table recovery failed
- 文档级摘要
  - OCR 页数
  - OCR 平均置信度
  - 低置信度页列表
  - figure/equation/table 识别统计
- 英文 corpus
  - 低风险英文技术书 text PDF
  - medium-risk 英文 academic paper
  - 英文 scanned book
  - 英文 scanned paper
  - 英文 mixed PDF

Acceptance:

- 不再只验证“scanned 会不会被拒绝”，而是验证“英文 scanned/mixed 会不会被正确恢复”
- review package 能给 operator 明确指出 OCR/layout 风险页

## Recommended Next Implementation Order

建议按下面顺序推进，而不是把 six-phase 计划一次性并行展开：

1. 先做 text PDF 的 layout enhancement，不先碰 OCR 主路径
2. 同步把 `DocumentImage` 收口成真正的 PDF asset lifecycle
3. 再做 `OcrPdfTextExtractor + OcrPdfParser`
4. 之后才上 equation/table 深化
5. 最后补 OCR 质量摘要、自动 issue、operator 驾驶舱指标

原因：

- 当前最稳定的主链路仍是 text PDF
- academic paper 的最大真实缺口是 reading order 和 figure/equation 周边页面，而不是“完全没有 OCR”
- 若先做 OCR 而不补 export asset / review evidence，系统会出现“解析到了，但无法交付或无法审”的半成品状态

## Design Assessment

### Overall assessment

总体判断：方案方向是合理的，但原始六阶段计划需要按当前代码现实做三处收敛，才能从“好想法”变成“可实现方案”。

合理之处：

- 先做 schema / enum / route foundation，再接 OCR 与 layout，是正确分层
- 坚持统一 IR，不另做 PDF 专用翻译链，是正确架构方向
- 先 text PDF layout，后 scanned/mixed OCR，符合当前代码成熟度
- 把 figure / equation / image 当 protected artifact，而不是急于直接翻译，也是合理策略

### Adjustments required before implementation

#### 1. `pdf_mixed` 目前只是 routing，不是 capability

文档必须明确：

- 当前 `pdf_mixed` 是“可识别、可标注”
- 不是“已支持 mixed PDF OCR”

否则 operator 和后续开发都会误读当前完成度。

#### 2. `DocumentImage` 不能被当成已经完工的资产层

它现在已经有最小写入、查询、导出、API 摘要闭环。  
后续计划必须补：

- 物理落盘/materialization 策略
- 资产缓存与复用边界
- 清理/重跑策略
- caption/OCR/equation/table 的语义收口

#### 3. OCR 接入前必须先拆开风险语义和支持语义

现在：

- `ocr_required=true` 接近“系统不支持”

未来：

- `needs_ocr=true` 只是输入事实

如果不改，P2 以后 `layout_risk`、review issue、operator summary 会混在一起，难以判断真实问题。

#### 4. 英文 OCR 验收样本必须尽快建立

本项目目标是英文书籍和英文论文翻译。  
如果没有英文 scanned/mixed corpus，OCR 方案再完整，也缺少真正能决定是否可上线的验收依据。

## Decision Summary

这份计划的建议结论是：

- 保留原 `iterative-dreaming-volcano` 的总体方向
- 认可已完成的 Phase 1 基础设施
- 下一步优先做 Phase 2 的 text PDF layout enhancement
- 在 Phase 3 启动前先补 `DocumentImage` asset lifecycle、PDF export 策略收口、OCR 语义拆分
- 把英文 scanned/mixed corpus 建成正式验收基线，而不是继续只用“unsupported OCR”样本做 smoke

如果按这个顺序推进，`docs/pdf-ocr-layout-refactoring-plan.md` 对当前代码来说是合理且可执行的；如果直接把 OCR、layout、equation、table、export 一次性打包推进，实施风险会明显偏高。

### Step 41: Implement the first real cost-down pass for translation prompts by trimming source context dynamically and compacting prompt sections

Status: Completed on 2026-03-18

这轮没有直接去改 packet 粒度或重做 bootstrap，而是优先做了两件“下一次 retry / resume 就能立刻生效”的 prompt 层降本：

- 在 `src/book_agent/services/context_compile.py` 增加动态上下文裁剪
- 在 `src/book_agent/workers/translator.py` 压缩 prompt section 和单句 packet 的重复正文

实现细节：

- `ChapterContextCompileOptions` 新增：
  - `trim_source_context=True`
  - `trim_chapter_brief=True`
- `ChapterContextCompiler` 现在会：
  - 对普通 paragraph 默认只保留“必要时”的 source 邻近块
  - `prev_blocks` / `next_blocks` 默认最多各留 `1` 个 block
  - 且每侧有字符预算
  - `chapter_brief` 会先压缩到较短版本
  - 只有在这些情况才保留 brief：
    - 当前段较短
    - 当前段像承接/过渡段
    - 当前段看起来没写完
    - 或裁完后没有任何 source neighbor
- 这部分核心实现位于：
  - `src/book_agent/services/context_compile.py`
    - `_trim_source_context()`
    - `_compress_chapter_brief()`
    - `_trim_chapter_brief()`

同时，prompt builder 也做了两层瘦身：

- 空 section 不再输出：
  - `Locked and Relevant Terms`
  - `Relevant Entities`
  - `Chapter Concept Memory`
  - `Previous Accepted Translations`
  - `Previous Source Context`
  - `Upcoming Source Context`
- 对 paragraph-led 且“当前 packet 只有 1 个 sentence”的场景：
  - `Sentence Ledger` 不再重复输出完整原文
  - 改成只保留 alias 提示
  - 这样 `Current Paragraph` 和 `Sentence Ledger` 不会把同一句正文重复灌两遍

这部分核心实现位于：

- `src/book_agent/workers/translator.py`
  - `_extend_section()`
  - 空 section 跳过
  - 单句 packet 的 compact ledger

回归与验证：

- `uv run python -m py_compile`
  - `src/book_agent/services/context_compile.py`
  - `src/book_agent/workers/translator.py`
  - `tests/test_translation_worker_abstraction.py`
- `uv run python -m unittest tests.test_translation_worker_abstraction`
  - 通过

新增回归覆盖：

- `tests/test_translation_worker_abstraction.py`
  - 自足段会被裁掉多余 source neighbor，只保留压缩后的 brief
  - 承接/过渡短段会保留最小必要上下文
  - 单句 packet prompt 不再重复正文
  - 空 context section 不再输出

真实样本估算（前 `250` 个 full-book packets）：

- `AVG_RAW_PROMPT_CHARS=3017.3`
- `AVG_COMPILED_PROMPT_CHARS=1776.5`
- `PROMPT_REDUCTION_PCT=41.1%`
- `AVG_RAW_CONTEXT_CHARS=1969.4`
- `AVG_COMPILED_CONTEXT_CHARS=803.5`
- `CONTEXT_REDUCTION_PCT=59.2%`

这说明：

- 这一轮虽然还没有改 packet merge
- 但光靠 compile/prompt 两层裁剪
- 就已经能把平均 prompt 文本体积压掉约 `40%`
- 对当前“整本书翻译太贵”这个问题，是一轮真正有价值的降本

现实边界：

- 当前正在运行的 `v12` runner 已经在内存里加载旧代码
- 所以这一轮优化不会自动 retroactively 作用到已启动的旧进程
- 它会在下一次：
  - retry
  - resume
  - 新 full-book run
  - 单 packet re-execute
  - packet experiment
  - 里直接生效

下一轮最值得继续的降本方向：

- Phase 2A:
  - 做 `short packet merge`
  - 把连续低风险 paragraph 合成更大的 packet
- Phase 2B:
  - 让 `ContextPacketBuilder` 在 bootstrap 阶段就少写无意义邻近块
  - 不只是在 compile 时裁
- Phase 2C:
  - 对术语/实体/概念命中为空的 packet，进一步做 ultra-light prompt profile

### Step 42: Add conservative short-packet merge in the builder so the next rebuild creates fewer translation packets

Status: Completed on 2026-03-18

在 Step 41 解决“每个 packet 太胖”之后，这一轮继续解决“packet 本身太多”。

核心思路：

- 不去碰 heading / quote / list / image 这类高风险 block
- 只对连续、普通、短小的 `paragraph` 做合并
- 且限制合并规模，避免把 packet 重新做得过大

Builder 侧新增的 merge 规则：

- 仅 `BlockType.PARAGRAPH` 可参与 merge
- 单个候选 block 需要同时满足：
  - `sentence_count <= 3`
  - `char_count <= 420`
- 合并后的 packet 需要同时满足：
  - 最多 `3` 个 current blocks
  - 总字符数 `<= 900`
  - 总 sentence 数 `<= 6`
- 只合并 ordinal 连续的 block

实现位置：

- `src/book_agent/domain/context/builders.py`
  - `MAX_MERGED_PACKET_*` / `MAX_MERGED_SINGLE_BLOCK_*`
  - `_block_groups()`
  - `_is_merge_candidate()`
  - `_build_merged_packet()`
  - `build_many()` 现在会先做 group，再决定是：
    - 走单-block packet
    - 还是走 merged packet

设计上保留的安全边界：

- 超长 block 仍然沿用现有 sentence-window split
- references chapter 的 sentence cap 逻辑不变
- multi-block packet 会明确写：
  - `block_start_id`
  - `block_end_id`
  - `current_blocks=[...]`
  - 并把所有 current sentences 都落入 `PacketSentenceMap(role=CURRENT)`

回归与验证：

- `uv run python -m py_compile`
  - `src/book_agent/domain/context/builders.py`
  - `tests/test_bootstrap_pipeline.py`
  - `tests/test_translation_worker_abstraction.py`
- `uv run python -m unittest tests.test_bootstrap_pipeline`
  - 通过
- `uv run python -m unittest tests.test_translation_worker_abstraction`
  - 通过

新增回归：

- `tests/test_bootstrap_pipeline.py`
  - `test_bootstrap_pipeline_merges_adjacent_short_paragraphs_into_one_packet`
  - 验证相邻两个短 paragraph 会被合并成单 packet
  - 且 `block_start_id != block_end_id`
  - 且 current sentence map 完整

同时，我把几条老测试样本改成了“明确不该被 merge”的长段落版本：

- `tests/test_translation_worker_abstraction.py`
  - 这些测试本来依赖“段落数 == packet 数”
  - 现在改成更长的 paragraph，让它们继续验证 chapter-memory/backfill 语义，而不是卡在旧 packet 数量假设上

真实 full-book 估算：

- 基于当前 scanned-book 主库 `2170` 个旧 packet 做离线模拟
- 按这轮 merge 规则估算：
  - `ORIG_PACKETS=2170`
  - `EST_MERGED_PACKETS=1769`
  - `PACKET_REDUCTION=401`
  - `PACKET_REDUCTION_PCT=18.5%`
  - `CHAPTERS_WITH_MERGE=42`
  - `MERGED_GROUP_COUNT=281`
  - `AVG_MERGED_GROUP_SIZE=2.43`
  - 分布：
    - `2-block groups = 161`
    - `3-block groups = 120`

这意味着：

- 下一次真正 rebuild / retry 若吃到这轮 builder 逻辑
- 在不牺牲太多语义安全性的前提下
- packet 数量本身就有望直接少掉约 `18.5%`
- 与 Step 41 的 prompt 瘦身叠加后
- 可以形成“每包更瘦 + 总包更少”的双重降本

现实边界：

- 这轮 builder merge 不会 retroactively 改写已经存在的旧 packet
- 它会在下一次：
  - bootstrap rebuild
  - packet rebuild
  - 新 full-book run
  - 时真正落成新的 packet 形状

下一轮最值得继续的方向：

- Phase 3A:
  - 让 builder 在落 packet 前就同步做 source-context 的 builder-side 轻量裁剪
  - 不只依赖 compile-time trim
- Phase 3B:
  - 为“无 term / 无 entity / 无 concept / 无 open questions”的 packet 做 ultra-light prompt profile
  - 进一步缩掉 system+contract 壳成本

### Step 43: Stop broadcasting chapter concept memory to every packet, then let ordinary `role-style-v2` packets auto-fall into a lighter prompt shell

Status: Completed on 2026-03-18

这一轮继续做“未来重跑直接省钱”的优化，而且仍然没有去打断正在跑的 `v12`。

先发现的真实问题是：

- Step 41 已经把 `prev/next/brief` 裁瘦了
- 但 scanned-book 主库前 `500` 个 packet 再抽样时
  - `relevant_terms=0`
  - `relevant_entities=0`
  - `open_questions=0`
  - 可是 `chapter_concepts` 却在 `486/500` 个 packet 里都非空
- 说明 prompt 里最肥的一块之一
  - 其实是“章级 active concepts 被整包广播到几乎所有 packet”
  - 而不是 packet 真正局部相关的概念

这轮做了两件事：

- `src/book_agent/services/context_compile.py`
  - 新增 concept relevance 过滤
  - `chapter_concepts` 不再直接吃整章 `active_concepts`
  - 现在只保留和 packet 本地上下文相关的 concepts：
    - `current_blocks`
    - `prev_blocks`
    - `next_blocks`
    - `prev_translated_blocks.source_excerpt`
  - 并把注入上限收紧到 `MAX_RELEVANT_CHAPTER_CONCEPTS = 4`
- `src/book_agent/workers/translator.py`
  - 原先那版 compact prompt 太保守，真实 packet 命中为 `0`
  - 现在把规则改成：
    - 只对默认主线 profile `role-style-v2` 生效
    - 只要 packet 是普通、短小、paragraph-only 且没有 style/rerun 特殊信号
    - 就自动走更短的 system + contract shell
  - 注意：
    - 这不是把上下文 section 删光
    - `Chapter Brief` / `Previous Context` / `Relevant Terms` 这些如果存在，仍然会保留
    - 只是把“外壳说明”从重型模板降到轻型模板

回归与验证：

- `uv run python -m py_compile`
  - `src/book_agent/services/context_compile.py`
  - `src/book_agent/workers/translator.py`
  - `tests/test_translation_worker_abstraction.py`
- `uv run python -m unittest tests.test_translation_worker_abstraction`
  - 通过

新增/更新回归：

- `tests/test_translation_worker_abstraction.py`
  - `test_build_translation_prompt_request_uses_compact_role_style_prompt_for_self_contained_packet`
  - `test_context_compiler_filters_chapter_concepts_to_local_relevance`
  - 同时把 chapter-memory 相关旧测试样本改成真正命中 concept 的版本

真实 full-book 抽样收益：

- 仍以 scanned-book 主库前 `500` 个 packet 做离线估算
- 对比“旧逻辑：
  - 广播式 chapter concepts
  - 完整 `role-style-v2` prompt shell”
- 与“新逻辑：
  - 局部相关 chapter concepts
  - 自适应 compact `role-style-v2` shell”

得到：

- average `chapter_concepts` count：
  - `4.28 -> 1.29`
  - `-69.9%`
- compact prompt 命中：
  - `338 / 500`
  - `67.6%`
- average user prompt chars：
  - `5872.3 -> 5117.7`
  - `-12.8%`
- average total prompt chars：
  - `6212.3 -> 5336.7`
  - `-14.1%`

补充拆分看：

- 只看“concept relevance 过滤之后，再打开 compact shell”
  - 同一批 `500` 个 packet 里
  - compact shell 单独还能再带来约 `-11.0%` 的 total prompt chars

这说明：

- Step 41 主要解决的是：
  - `prev/next/brief` 这类邻近上下文太胖
- Step 42 主要解决的是：
  - packet 数量太多
- Step 43 则补上了：
  - 章级 concept memory 广播过宽
  - 默认 `role-style-v2` prompt 壳本身太重

现实边界：

- 这轮优化同样不会 retroactively 改写当前已经落库的旧 run 结果
- 它会在下一次：
  - rebuild
  - retry
  - resume
  - 新 full-book run
  - 时真正生效

接力状态备注：

- 在我做这轮优化期间，`v12` 已自然结束
- watcher 现在显示：
  - `stage=finished`
  - `translation_packet_status_counts: translated=2159, built=11`
  - `work_item_status_counts: succeeded=2159, pending=1832, retryable_failed=1, terminal_failed=28`
- `report.json` 对应 retry run `34e0a11c-a7e8-47ee-b22a-ab10c25a5d69` 当前状态是 `failed`
- 所以下一轮不再是“继续等 v12”
  - 而是应该基于 Step 41-43 的新逻辑接管：
    - 先判断是否 rebuild packet / prompt
    - 再做下一次 retry/resume

下一轮最值得继续的方向：

- Phase 4A:
  - 基于 Step 41-43 的新 packet/prompt 逻辑
  - 做一次真正吃到新降本策略的 rebuild + retry 方案
- Phase 4B:
  - 先诊断 `v12` 留下的 `terminal_failed=28` 与 `retryable_failed=1`
  - 避免下一次 retry 还重复撞同类坏包

### Step 44: Classify `v12` terminal failures, fix the deterministic `alignment_edges` collision, and narrow the next retry risk surface

Status: Completed on 2026-03-18

在 Step 43 之后，我立刻接管了已经自然结束的 `v12`，先把“为什么 failed”拆清楚，再修能确定复现的系统性问题。

真实结果：

- `v12` 对应 retry run：
  - `run_id = 34e0a11c-a7e8-47ee-b22a-ab10c25a5d69`
  - `status = failed`
  - `stop_reason = run.terminal_failed_items_present`
- run 内部计数：
  - `total translate work_items = 1850`
  - `succeeded = 1839`
  - `terminal_failed = 11`
  - `retryable_failed = 0`
- watcher 里看到的 `terminal_failed=28`
  - 是文档主库跨 run 的累计状态，不是这一次 `v12` 自己的失败数

失败分类：

- `9 / 11`
  - `RuntimeError`
  - `Provider response did not include a structured JSON output payload.`
- `1 / 11`
  - `RuntimeError`
  - `Provider response did not match TranslationWorkerOutput schema.`
  - 具体表现是 provider 把 `alignment_suggestions[*].source_sentence_ids` 错写成了 `source_id`
- `1 / 11`
  - `IntegrityError`
  - `UNIQUE constraint failed: alignment_edges.id`
  - 这是系统侧可确定复现的 bug：同一个 `sentence_id + target_segment_id` 组合被重复写入时，稳定 ID 冲突

这一步实际修掉的内容：

- `src/book_agent/services/translation.py`
  - 新增 `_dedupe_alignment_edges()`
  - 在 `_build_artifacts()` 里，持久化前先按 `(sentence_id, target_segment_id)` 去重
- `src/book_agent/services/realign.py`
  - 同步新增 `_dedupe_alignment_edges()`
  - 保证以后即使从 output JSON 重建 alignment edges，也不会再生成重复边
- `tests/test_translation_worker_abstraction.py`
  - 新增 `DuplicateAlignmentClient`
  - 新增回归：
    - `test_translation_service_dedupes_duplicate_alignment_edges_from_worker_output`
  - 断言模型重复给出同一条 alignment suggestion 时：
    - `artifacts.alignment_edges` 只保留 1 条
    - 数据库里也只会落 1 条

验证：

- `uv run python -m py_compile`
  - `src/book_agent/services/translation.py`
  - `src/book_agent/services/realign.py`
  - `tests/test_translation_worker_abstraction.py`
- `uv run python -m unittest tests.test_translation_worker_abstraction`
  - 通过

这一步的实际意义：

- 下一次 retry 不会再因为“模型输出里重复了同一条对齐边”而白白烧掉一个 work item
- `11` 个 terminal failed 里，至少有 `1` 个确定性系统 bug 已经被消除
- 剩余主要风险已经收敛得很清楚：
  - provider 不返回结构化 JSON
  - provider 返回近似 JSON，但字段不符合 schema

和 Step 43 的关系：

- Step 43 的轻 prompt / concept relevance 过滤
  - 很可能会降低这类“provider 没有老老实实回结构化 JSON”的概率
  - 但这是概率性改善
- Step 44 修掉的 alignment-edge collision
  - 是确定性改善
  - 下次 retry 可以直接吃到

下一轮最值得继续的方向：

- Phase 5A:
  - 基于 Step 41-44 的新逻辑
  - 做一次 rebuild + retry 预案
  - 明确是否要先 rebuild packet 再 retry 剩余失败 work items
- Phase 5B:
  - 专门针对 `structured JSON payload missing / schema mismatch`
  - 继续收紧 provider response guardrails
  - 或增加一次更稳的 parse/recovery fallback

### Step 45: Explain why `1839 translated packets` still produced no final merged file, then rescue the remaining failed packets

Status: Completed on 2026-03-19

先把“为什么已经翻了 `1839` 个 packets，却没有整本结果文件”彻底收敛：

- `v12` 的 retry run：
  - `run_id = 34e0a11c-a7e8-47ee-b22a-ab10c25a5d69`
  - `status = failed`
  - `stop_reason = run.terminal_failed_items_present`
- 不是翻译结果没落库
  - 而是 live runner 只会在 `final_run_summary.status == succeeded` 时继续执行：
    - `review_document`
    - `review package export`
    - `bilingual export`
    - `merged export`
- 所以：
  - `1839` 个 packets 已经翻完
  - 但因为仍有 `11` 个 `terminal_failed`
  - `run_real_book_live.py` 提前退出
  - `report.json.final_export / merged_export` 自然都是空的

这一步的补救动作：

- 基于 Step 44 的 provider parsing / alignment dedupe 修复
- 新启动 `v13` 只接管剩余失败包，不重复翻译已成功部分：
  - `run_id = 0db99813-9a9d-4ada-ae37-147dbcd334a1`
  - artifacts root：
    - `/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-agentic-design-book-v13-retry-failed-packets-after-parser-fixes`
- 结果：
  - `11 / 11` built packets 全部 rescue 成功
  - 主库状态推进到：
    - `translated_packets = 2170`
    - `built_packets = 0`

但这一步还没有直接产出 merged：

- `v13` 刚进入 `review_document(...)`
- 就撞上了新的持久化问题：
  - `UNIQUE constraint failed: review_issues.id`
- 所以主线从“翻译失败”转成了“review/export 失败”

这一步的意义：

- “没有 merged 文件” 的根因已经明确不是 OCR、不是 packet build、也不是 export 文件系统
- 而是：
  - 先被 `11` 个 terminal failed 卡住 translate gate
  - 之后又被 review persistence 卡住 review/export gate

### Step 46: Fix `review_issues.id` collisions for duplicate `TERM_CONFLICT` variants so full-book review can persist

Status: Completed on 2026-03-19

`v13` 在 review 阶段暴露出的新根因是：

- 同一句 source sentence 上
- 如果锁了 singular / plural 两个 concept term
- 且它们共享同一个 `expected_target_term`
- 旧的 stable id 只看：
  - `document_id`
  - `chapter_id`
  - `sentence_id`
  - `issue_type`
- 就会把两个 `TERM_CONFLICT` 都算成同一个 `review_issue.id`

这一步的修复：

- `src/book_agent/services/review.py`
  - 给 `TERM_CONFLICT` 引入稳定但更合理的 unique key
  - 同一句、同 `expected_target_term` 的 singular/plural 变体会折叠成一个 issue
  - 同时保留 `source_terms` 聚合证据
  - 额外加了一层 `_dedupe_issues()`，防止同批次重复 issue 再次撞库
- `src/book_agent/infra/repositories/review.py`
  - 在 `save_review_artifacts()` 的 merge 前再做一次按 `id` 去重
  - 作为 repository 层兜底
- `tests/test_persistence_and_review.py`
  - 新增回归：
    - `test_review_collapses_term_conflict_variants_with_shared_expected_target`

验证：

- `uv run python -m py_compile`
  - `src/book_agent/services/review.py`
  - `src/book_agent/infra/repositories/review.py`
  - `tests/test_persistence_and_review.py`
- `uv run python -m unittest`
  - `tests.test_persistence_and_review.PersistenceAndReviewTests.test_review_collapses_term_conflict_variants_with_shared_expected_target`
  - `tests.test_persistence_and_review.PersistenceAndReviewTests.test_review_reports_term_conflict_for_locked_chapter_concept_entry`
  - 通过

结果：

- 全书 review 终于能完整落库
- `review package` 也能正常产出
- 但 bilingual export 又暴露出最后一个 export-time misalignment blocker

### Step 47: Repair the last export-time orphan segment in Chapter 3, then finish full-book bilingual + merged export

Status: Completed on 2026-03-19

在 `v14` 的 review/export recovery 里，review 和 review package 已经全部成功，但 bilingual export 卡在 Chapter 3：

- chapter:
  - `37b75f9b-e32f-5b78-9846-cabe38d26a29`
  - `Chapter 3: Parallelization`
- issue:
  - `b9e11300-cafd-5101-a207-68ed76430538`
  - `ALIGNMENT_FAILURE`
  - `root_cause_layer = export`
- 直接原因：
  - packet `5d5367f4-bc29-5c49-a156-a9e59b25718f`
  - 留下了一个 orphan target segment：
    - `b261b617-576e-564b-a7c6-175be94d761d`
    - text = `输入摘要：`

进一步排查后确认：

- 这不是 export renderer 自己造出来的坏数据
- 是这个 packet 的最新 translation output 里：
  - 主段 `T1` 对齐到了两句 source sentence
  - 尾段 `T2 = 输入摘要：` 没有任何 `source_sentence_ids`
  - `alignment_suggestions` 里也没有覆盖 `T2`
- 旧的 `REALIGN_ONLY`
  - 只能基于模型已有的 alignment metadata 重建边
  - 所以会“成功执行 realign”
  - 但无法真正修复这个 orphan 段

这一步的修复：

- `src/book_agent/services/realign.py`
  - 不再二选一地只吃 `alignment_suggestions` 或 `target_segments.source_sentence_ids`
  - 而是会先合并两类信息
  - 然后对“短尾 label 样的 orphan segment”做一个非常保守的 last-mile recovery：
    - 只在 text 很短时触发
    - 优先挂回最近一句 current sentence
    - 关系类型标成 `1:n`
- `tests/test_persistence_and_review.py`
  - 新增回归：
    - `test_realign_attaches_short_orphan_tail_segment_to_last_sentence`

验证：

- `uv run python -m py_compile`
  - `src/book_agent/services/realign.py`
  - `tests/test_persistence_and_review.py`
- `uv run python -m unittest`
  - `tests.test_persistence_and_review.PersistenceAndReviewTests.test_realign_attaches_short_orphan_tail_segment_to_last_sentence`
  - `tests.test_persistence_and_review.PersistenceAndReviewTests.test_review_detects_orphan_target_segment_and_routes_to_realign`
  - 通过

真实 recovery 结果：

- artifacts root：
  - `/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-agentic-design-book-v14-review-export-recovery`
- review：
  - 成功
- review package：
  - `97 / 97` chapters 成功产出
- bilingual export：
  - 成功
  - `97 / 97` chapters 成功产出
  - `auto_followup_applied = true`
  - `auto_followup_attempt_count = 3`
- merged export：
  - 成功
- 最终文档状态：
  - `document_status_final = exported`
  - `merged_export_ready = true`

最终可交付产物：

- run report：
  - `/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-agentic-design-book-v14-review-export-recovery/report.json`
- full merged HTML：
  - `/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-agentic-design-book-v14-review-export-recovery/exports/67283f52-b775-533f-988b-c7433a22a28f/merged-document.html`
- merged manifest：
  - `/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-agentic-design-book-v14-review-export-recovery/exports/67283f52-b775-533f-988b-c7433a22a28f/merged-document.manifest.json`

现状说明：

- 这次 full-book rescue 的目标已经完成：
  - 整本书最终 merged 版本已产出
- 但质量层面仍有 residual open issues：
  - `open_issue_count_final = 142`
  - 主要是：
    - `UNLOCKED_KEY_CONCEPT`
    - `FORMAT_POLLUTION`
    - `STALE_CHAPTER_BRIEF`
- 这些不再阻塞 merged export
  - 但它们仍然是下一轮最值得继续打磨的质量工作

### Step 48: Fix merged export `READING MAP` overflow so chapter items stay inside the sidebar and stop covering the正文 area

Status: Completed on 2026-03-19

问题现象：

- merged HTML 左侧 `READING MAP`
- 在含有超长 chapter title / URL-like title 的文档里
- sidebar 会被目录项的最小内容宽度撑爆
- 结果是 chapter 卡片背景越过自身列宽，压到右侧 hero 和正文阅读区

根因：

- merged export 模板里：
  - `.page-shell` 使用 CSS grid
  - sidebar 是 sticky grid item
  - `toc` 里的某些长文本没有宽度约束和换行保护
- 在 grid 自动最小宽度规则下
  - sidebar 的 min-content 会被长目录项放大
  - 导致整个 sidebar item 越界，而不是老老实实待在 `minmax(220px, 280px)` 的左列里

修复：

- `src/book_agent/services/export.py`
  - 给 `.sidebar` 增加：
    - `min-width:0`
    - `inline-size:100%`
    - `max-inline-size:100%`
    - `overflow:hidden`
  - 给 `.toc-list` / `.toc-item` 增加：
    - `min-width:0`
  - 给 `.toc-item a` 增加：
    - `inline-size:100%`
    - `max-inline-size:100%`
    - `min-width:0`
    - `overflow-wrap:anywhere`
    - `word-break:break-word`
    - `overflow:hidden`

验证：

- `uv run python -m py_compile`
  - `src/book_agent/services/export.py`
  - `tests/test_api_workflow.py`
- 由于本地仍缺 `httpx`
  - `tests.test_api_workflow` 常规入口无法运行
- 改用真实文档导出复核：
  - 重新生成：
    - `/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-agentic-design-book-v14-review-export-recovery/exports/67283f52-b775-533f-988b-c7433a22a28f/merged-document.html`
  - 并确认生成文件已包含：
    - `sidebar_min_width_guard = true`
    - `toc_list_min_width_guard = true`
    - `toc_item_min_width_guard = true`
    - `toc_item_wrap_guard = true`

结果：

- 当前整本 merged export 已按新样式重生成
- `READING MAP` chapter 条目现在会被限制在 sidebar 自身宽度内
- 长标题会在目录内部换行，不再覆盖正文阅读区域

### Step 49: Improve merged export code-block readability by reflowing flattened OCR code conservatively at export time

Status: Completed on 2026-03-19

问题现象：

- merged HTML 对 `CODE` block 目前使用 `<pre><code>` 原样输出
- 但在 scanned PDF / OCR 书籍里
  - 很多“代码块”在进入导出层前就已经丢失了缩进
  - 某些长字符串或表达式还会被错误拆成多行
- 结果是最终 merged 虽然“保留原样”
  - 但真实阅读体验里代码可读性依然很差

根因分析：

- 问题不完全在 CSS
  - `<pre>` 本身能保留换行和空格
- 真正的问题是上游 OCR / layout recovery 会把代码样式文本压平
  - 导出层拿到的 `CODE` block 已经是“结构受损文本”
- 同时又存在另一类现实情况：
  - PDF 里有少量 prose 被误判成 `CODE`
  - 所以不能对所有 preformatted 文本做激进重写

修复策略：

- `src/book_agent/services/export.py`
  - 在 `_format_preformatted_text()` 中加入保守 reflow
  - 先判断文本是否“看起来像真实代码 artifact”
  - 只有命中代码特征时才进入 reflow
- 新增一组 helper：
  - `_looks_like_code_artifact_text()`
  - `_reflow_code_artifact_text()`
  - `_coalesce_wrapped_code_lines()`
  - `_should_join_wrapped_code_line()`
  - 以及若干 indentation / quote / delimiter balance helper
- 行为上做的事是：
  - 保留已有明确缩进
  - 对被拆断的字符串 / continuation line 做保守拼接
  - 在缺失缩进时
    - 根据 Python block opener / dedent cue 推断最小必要缩进
- 同时避免误伤：
  - 如果文本更像 prose 而不是 code
  - 则仍按原样 HTML escape 输出

验证：

- `uv run python -m py_compile`
  - `src/book_agent/services/export.py`
  - `tests/test_persistence_and_review.py`
- `uv run python -m unittest`
  - `tests.test_persistence_and_review.PersistenceAndReviewTests.test_export_service_reflows_flattened_code_artifact_text`
  - `tests.test_persistence_and_review.PersistenceAndReviewTests.test_export_service_leaves_non_code_preformatted_text_unchanged`
  - 通过
- 真实导出复核：
  - 重新生成：
    - `/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-agentic-design-book-v14-review-export-recovery/exports/67283f52-b775-533f-988b-c7433a22a28f/merged-document.html`
  - 真实样例已从：
    - 扁平难读的 OCR code text
  - 收敛为：
    - 可读的函数体缩进
    - 被拆断的 `Result: ...` 字符串重新合并到同一行

结果：

- merged export 中的真实代码片段现在更接近“可阅读代码”
- 这次修复发生在导出层
  - 不会破坏底层原始 block 数据
- 但要明确边界：
  - 这不是完整的 OCR code reconstruction
  - 对严重损坏或本来就被错误识别成 code 的 block
  - 仍可能不完美

### Step 50: Repair merged export structure for scanned-book PDF by adding sidebar vertical scroll, top-level chapter grouping, caption-based image recovery, and code-like block rescue

Status: Completed on 2026-03-19

这一步解决的是同一类“merged 可读性问题”，但它们不是一个点，而是四个彼此相关的导出层缺陷：

- `READING MAP` 太长时不能纵向滚动
- scanned PDF 的 OCR 误分章会直接污染 merged TOC
- 这本书没有 `DocumentImage / IMAGE block`
  - 但还有 figure caption
  - 所以图片可以从 caption 锚点补救
- code-like paragraph / table 没被识别成 code
  - 导致一段源码被切成“正文 + 代码 + 正文”

修复：

- `src/book_agent/services/export.py`
  - sidebar：
    - 增加 `max-height:calc(100vh - 36px)`
    - 增加 `overflow-y:auto`
    - 让超长目录支持纵向滚动
  - merged top-level chapter grouping：
    - PDF merged 不再把每个 OCR chapter row 都当成顶层目录
    - 现在会保留：
      - frontmatter
      - 单调递增的 `Chapter 1..N`
      - `Appendix ...`
    - 其余 `Conclusion / References / Key Takeaways / OCR fragment`
      - 合并进前一个顶层章节
  - code rescue：
    - paragraph / table 只要满足 `pdf_block_role=code_like` 或 code pattern
    - 就按 code artifact 渲染
    - 相邻 code-like block 会自动并成一个连续代码块
  - caption-based image recovery：
    - 对 `Fig. / Figure / Diagram / Chart` caption
    - 即使没有 `IMAGE/FIGURE` block
    - 也会在导出时按 caption 上方区域从 PDF 直接裁图
- `pyproject.toml`
  - 新增 `pymupdf`
  - 否则这条 PDF 裁图链路在运行时会静默降级

验证：

- `uv run python -m py_compile`
  - `src/book_agent/services/export.py`
  - `tests/test_persistence_and_review.py`
- `uv run python -m unittest`
  - `test_visible_merged_chapters_group_pdf_auxiliary_sections_under_real_top_level_titles`
  - `test_render_blocks_merge_code_like_paragraphs_and_tables_into_single_code_artifact`
  - `test_caption_anchored_pdf_crop_bbox_stays_above_caption_region`
  - `test_extract_main_chapter_number_rejects_lowercase_fragment_after_number`
  - 通过
- 真实整本 merged 重生成后复核：
  - `chapter_count`
    - 从旧的 `68`
    - 收敛到 `30`
  - 顶层目录前段已经恢复成：
    - `致谢 / 前言 / 第一章 / 第二章 / 第三章 / 第四章 / 第五章 / 第六章 / 第七章 / 第八章 ...`
  - `img_tag_count = 18`
  - `assets/pdf-images/* = 23 files`
  - code-like probe：
    - `from google.adk.agents import Agent, BaseAgent`
    - 已和后续 `class QueryRouterAgent ...` 合并在同一个 code artifact 中

当前产物：

- merged HTML：
  - `/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-agentic-design-book-v14-review-export-recovery/exports/67283f52-b775-533f-988b-c7433a22a28f/merged-document.html`
- merged manifest：
  - `/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-agentic-design-book-v14-review-export-recovery/exports/67283f52-b775-533f-988b-c7433a22a28f/merged-document.manifest.json`

边界说明：

- 这一步显著修复了 merged 的目录、图片和代码可读性
- 但少量章标题仍然带 OCR 截断痕迹
  - 例如 `第12章：异常处理与`
- 这类问题已经超出“导出治理”
  - 更接近上游 OCR / chapter segmentation 质量

### Step 51: Diagnose why `prev_blocks + current_blocks + next_blocks + chapter_brief` still underdelivered on translation quality in `v12`

Status: Completed on 2026-03-19

这一步不是继续加 prompt，而是先回答一个更重要的问题：

- 为什么 `v12` 明明用了更长的 `prev + current + next + chapter_brief`
- 但整本书的翻译质量仍然没有达到预期？

结论：

- 根因不是“上下文还不够长”
- 而是“上下文不够准”
- 这轮长 prompt 实际上主要增加了：
  - 邻近块噪声
  - 过期 chapter brief
  - 但没有增加真正有用的术语 / 概念 / 已译上下文

真实证据：

- packet 级统计（整本 `2170` packets）：
  - `avg_current_chars = 300.5`
  - `avg_prev_next_brief_chars = 1736.6`
  - `avg_chapter_brief_chars = 610.9`
  - `single_current_block_packets = 2170`
  - `avg_relevant_terms = 0`
  - `avg_chapter_concepts = 0`
  - `avg_prev_translated_blocks = 0`
- 也就是说：
  - 每个 packet 真正要翻的正文平均只有 `300` 字符
  - 但额外上下文平均有 `1736` 字符
  - 且这些额外上下文里几乎没有命中的术语、概念和已译衔接

最终 open issues 也直接指向了同一个结论：

- `UNLOCKED_KEY_CONCEPT = 57`
- `FORMAT_POLLUTION = 49`
- `STALE_CHAPTER_BRIEF = 27`
- `STYLE_DRIFT = 4`
- `LOW_CONFIDENCE = 3`
- `FOOTNOTE_RECOVERY_REQUIRED = 2`

这说明长 prompt 没解决的 4 个主要问题是：

- 概念未锁定：
  - 当前主库 `term_entries(active) = 0`
  - 所以模型没有稳定 termbase 可依赖
  - 只能继续“现场猜译”
- chapter brief 过期：
  - review evidence 里多次出现 `missing_concepts`
  - 例如 `Agentic AI / Language Model / context engineering`
  - 说明 brief 没跟上章节真实展开
- 源数据结构有噪声：
  - OCR 误分章、code/table/image 误分类
  - 这些问题不是多喂 `prev/next` 就能自动纠正
- 格式污染来自 prompt/guardrail不足：
  - open issues 里大量出现 `<a href=...>`、代码 fence、JSON fence 被翻进正文
  - 这是 output discipline 问题
  - 不是邻近上下文长度问题

因此，后续真正影响质量的优先级应该是：

- 优先级 1：
  - term / concept lock 自动化
  - 让 `UNLOCKED_KEY_CONCEPT` 不再靠 review 后补救
- 优先级 2：
  - chapter brief 增量刷新
  - 不要让 `STALE_CHAPTER_BRIEF` 带着版本 `1` 跑完整章
- 优先级 3：
  - format guardrail + post-parse sanitation
  - 专门压 `<a> / ```json / ```python / HTML literal`
- 优先级 4：
  - upstream OCR/layout cleanup
  - 尤其是 code/table/chapter boundary

反过来说，单纯继续把 `prev_blocks + next_blocks` 做得更长：

- 成本会继续上升
- 但质量收益会很有限
- 因为它解决不了概念锁定、brief 过期、格式纪律和源结构噪声

### Step 52: Add `merged_markdown` export and validate it on real book/paper artifacts

Status: Completed on 2026-03-19

这一步把最终导出从“只能看 HTML”扩展成了“可直接喂自定义 Markdown renderer 的标准 Markdown 包”。

本轮完成内容：

- 新增 `ExportType.MERGED_MARKDOWN`
- API / schema / operator UI 已支持 `merged_markdown`
- document-level export 会生成：
  - `merged-document.md`
  - `merged-document.markdown.manifest.json`
- Markdown 直接从数据库里的 merged render blocks 渲染，不是 HTML 转 Markdown
- 支持保留：
  - heading / paragraph / list / quote
  - code fence
  - table / equation fenced artifact
  - image `![alt](relative/path)`
  - source blockquote

兼容性补丁：

- 对旧 SQLite 库做了兼容：
  - 如果历史库没有 `document_images` 表，`BootstrapRepository` 和 `ExportRepository` 现在会降级为空列表
  - 不再因为 schema 较旧而让整份 Markdown 导出失败

回归验证：

- `uv run python -m py_compile`
  - `src/book_agent/domain/enums.py`
  - `src/book_agent/schemas/workflow.py`
  - `src/book_agent/app/api/routes/documents.py`
  - `src/book_agent/services/workflows.py`
  - `src/book_agent/services/export.py`
  - `src/book_agent/app/ui/page.py`
  - `src/book_agent/infra/repositories/bootstrap.py`
  - `src/book_agent/infra/repositories/export.py`
  - `tests/test_persistence_and_review.py`
- `uv run python -m unittest`
  - `test_workflow_exports_merged_markdown_with_assets`
  - `test_workflow_exports_merged_markdown_from_legacy_db_without_document_images_table`
  - 以及之前的 merged HTML / image / code 相关回归

真实产物：

- 扫描书 Markdown：
  - `/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-agentic-design-book-v15-merged-markdown/67283f52-b775-533f-988b-c7433a22a28f/merged-document.md`
  - `/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-agentic-design-book-v15-merged-markdown/67283f52-b775-533f-988b-c7433a22a28f/merged-document.markdown.manifest.json`
  - 同目录已带 `assets/pdf-images/`
- 论文 Markdown：
  - `/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-forming-teams-paper-v8-merged-markdown/b90a689e-bf00-5a3a-b1e3-0d5c88a12c1b/merged-document.md`
  - `/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-forming-teams-paper-v8-merged-markdown/b90a689e-bf00-5a3a-b1e3-0d5c88a12c1b/merged-document.markdown.manifest.json`

结论：

- 你的判断是对的：
  - 翻译后的正文、段落结构、对齐结果、review/export 元数据主要都在数据库里
  - 因而最终渲染形态不必被 HTML 绑定
- 当前系统已经能把 book / paper 导出成 `merged_markdown`
- 但也要记住一个边界：
  - EPUB/PDF 的“原始二进制资产”并不全都存在数据库里
  - 某些图片导出仍依赖 source file 可访问，或依赖已物化的 `DocumentImage`

### Step 53: Repair real academic-paper bootstrap structure and relaunch the paper full run on the fixed parser

Status: In progress on 2026-03-19

问题确认：

- 旧论文 Markdown 产物：
  - `/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-forming-teams-paper-v8-merged-markdown/b90a689e-bf00-5a3a-b1e3-0d5c88a12c1b/merged-document.md`
- 坏结果不是单纯“翻译不佳”，而是上游结构已经错误：
  - 旧 full DB 把正文大多塞进了错误章节
  - 旧 merged export 只是把错误结构忠实渲染出来

本轮已完成的 parser / bootstrap 修复：

- `PdfFileProfiler` 现在会把真实多栏、带 outline 的 `PyMuPDF` 学术论文识别成：
  - `layout_risk=medium`
  - `recovery_lane=academic_paper`
- `ParseService` 现在允许 `layout_risk=high` 之外的 `academic_paper` lane 进入 text-PDF 主解析路径
- 学术论文的 chapter-start 不再只信 page number：
  - 当 `outline / toc / academic_heading` 与同页真实 heading 重合时，会等待真正的 heading block 落点
  - 避免在页首就把下一节错误提前切出来
- 多栏 academic page 的短 heading 现在会继承列分组：
  - 不再因为 heading 本身太短而掉到 page 尾部
- code-like heuristic 已收紧：
  - 学术论文正文不再因为 `;`、普通括号、引用样式而大量误判成 `code_like`
- font-size heading heuristic 已收紧：
  - 图表碎片、公式残片不再轻易被抬成顶层 heading
- 当 PDF metadata 没 title 时：
  - 会从第一页 recovered heading 里回填真实论文标题

真实论文复核结果：

- 源 PDF：
  - `/Users/smy/Downloads/Forming Effective Human-AI Teams- Building Machine Learning Models that Complement the Capabilities of Multiple Experts.pdf`
- 现在 bootstrap 已稳定识别为：
  - `pdf_text`
  - `extractor_kind=pymupdf`
  - `recovery_lane=academic_paper`
  - `multi_column_page_count=4`
- 真实 bootstrap 后章节已从旧的“坏大章”收敛到：
  - 文档标题页
  - `1 Introduction`
  - `2 Related Work`
  - `3 Problem Formulation`
  - `4 Approach`
  - `5 Experiments`
  - `6 Conclusion`
  - `References`

回归验证：

- `uv run python -m py_compile`
  - `src/book_agent/domain/structure/pdf.py`
  - `src/book_agent/services/bootstrap.py`
  - `tests/test_pdf_support.py`
- `uv run python -m unittest`
  - `test_bootstrap_pipeline_recovers_inline_academic_section_headings`
  - `test_bootstrap_pipeline_cleans_noisy_inline_academic_section_headings`
  - `test_bootstrap_pipeline_repairs_broken_academic_heading_tail_before_body`
  - `test_bootstrap_pipeline_orders_positioned_multi_column_academic_paper_left_then_right`
  - `test_bootstrap_pipeline_waits_for_same_page_outline_heading_in_academic_paper`
  - `test_bootstrap_pipeline_supports_academic_paper_pdf_lane`
  - `test_bootstrap_document_accepts_academic_paper_pdf_lane`

正在进行的真实补救：

- 已基于修复后的 parser 启动新的 real paper full run：
  - `artifacts/real-book-live/deepseek-forming-teams-paper-v9-structure-fix-full-run/report.json`
  - `artifacts/real-book-live/deepseek-forming-teams-paper-v9-structure-fix-full-run/full.sqlite`
  - `artifacts/real-book-live/deepseek-forming-teams-paper-v9-structure-fix-full-run/exports`
- 当前状态：
  - `run_id = da605a99-8da0-495b-8421-d195ceeb7998`
  - `packet_count = 51`
  - 新库 bootstrap 已完成
  - translate 正在进行中

下一步：

- 等 `v9` translate 完成
- 直接导出新的 `merged_html + merged_markdown`
- 用新产物对比旧的 `v8 merged markdown`
- 再决定是否还需要对 `Abstract / title-only chapter` 做一次 export-layer polish

### Step 54: Fix academic-paper merged export policy so papers are not collapsed into one pseudo-chapter or rendered as fake code artifacts

Status: Completed on 2026-03-19

根因收敛：

- 新 `v9` parser 已把真实论文 DB 纠正为 8 个 chapter，但 merged export 仍然严重失真：
  - `merged-document.md` 只显示 `Chapters: 1`
  - `Reading Map` 只剩 1 个 document chapter
  - 大量论文正文被错误渲染成 `source_artifact_full_width / code`
- 这不是翻译 worker 继续出错，而是 export policy 叠加了 3 个问题：
  - `PDF` merged chapter grouping 仍按“书籍主章聚合器”运行，academic paper 被误合并
  - `href` 去重对 PDF 也生效；同页 chapter 共用 `pdf://page/N` 时，被误判成重复章节
  - `code artifact` 启发式过宽：
    - `with ...`、`from ...` 这类普通英文开头会误触发 code keyword
    - `query length = ...` 这种 OCR 断裂 prose 会被 assignment pattern 误判
    - 参考文献、作者单位、摘要块会被当成代码工件原样输出

本轮 export 修复：

- `academic_paper` merged export 不再走 PDF 书籍分组器：
  - `pdf_profile.recovery_lane == academic_paper` 时，章节按真实 chapter 直接暴露
- PDF 不再按 `href` 去重：
  - `pdf://page/N` 共享页锚不再吞掉同页的 `Introduction / Related Work / Approach`
- code heuristic 收紧：
  - control-flow / import / class / def 只在更像真实代码语句时才算 strong cue
  - assignment pattern 现在支持 typed assignment，但不再接受带空格的“伪变量名”
  - academic paper 的 prose / references / author+abstract frontmatter 会被排除出 code artifact
- 论文 title page frontmatter 现在会在 export 层拆开：
  - 作者/单位/邮箱
  - `Abstract / 摘要`
  - 摘要正文

真实论文 v9 修复后验证：

- `visible chapter count` 已从 `1` 恢复到 `8`
- merged markdown / html 的顶层章序恢复为：
  - 标题页
  - `1 引言`
  - `2 相关工作`
  - `3 问题建模`
  - `4 方法`
  - `5 实验`
  - `6 结论`
  - `参考文献`
- 误判成 `source_artifact_full_width` 的正文块已基本清空：
  - 只剩真实图片 / 真实 code-like 表达式工件保留原样

对应代码：

- `src/book_agent/services/export.py`
  - academic paper 章节可见性分流
  - PDF href 去重修复
  - tightened code artifact heuristic
  - title-page abstract/frontmatter split
- `tests/test_persistence_and_review.py`
  - `test_visible_merged_chapters_keep_academic_paper_sections_separate`
  - `test_export_service_does_not_treat_academic_prose_as_code_artifact`
  - `test_export_service_splits_academic_paper_frontmatter_and_abstract`

回归验证：

- `uv run python -m py_compile`
  - `src/book_agent/services/export.py`
  - `tests/test_persistence_and_review.py`
- `uv run python -m unittest`
  - `test_visible_merged_chapters_keep_academic_paper_sections_separate`
  - `test_export_service_does_not_treat_academic_prose_as_code_artifact`
  - `test_export_service_splits_academic_paper_frontmatter_and_abstract`
  - 以及已有的 PDF merged chapter / code merge 回归

中间产物：

- 仅基于旧 `v9` DB 的修复版 merged export：
  - `/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-forming-teams-paper-v10-merged-export-recovery/b90a689e-bf00-5a3a-b1e3-0d5c88a12c1b/merged-document.md`
  - `/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-forming-teams-paper-v10-merged-export-recovery/b90a689e-bf00-5a3a-b1e3-0d5c88a12c1b/merged-document.html`
- 这一步已经把“1 章塌缩”和“大片伪代码正文”修掉，但首页摘要仍有上游 parser 欠债：
  - 摘要左栏正文在旧 DB 中本身缺失
  - 因此需要继续修 parser 并做 fresh rerun

### Step 55: Repair asymmetric first-page academic-paper recovery and relaunch the paper from the source PDF

Status: In progress on 2026-03-19

进一步根因：

- 真实论文第一页不是对称双栏：
  - 标题 / 作者 / 单位 / `Abstract`
  - 左栏摘要正文
  - 右栏摘要续接
  - 左栏下半部才进入 `1 Introduction`
- 旧 academic multi-column ordering 对这种“第一页不对称双栏”不够稳：
  - multi-column signature 需要左右各至少 2 个 block，导致第一页直接退回 top-sort
  - 即使强行 column-order，也会把右栏摘要续接错误落到 `Introduction` 之后

本轮 parser 修复：

- 放宽第一页 academic multi-column signature：
  - 允许“左栏多块 + 右栏单个高密长块”的不对称双栏页面进入 column-major recovery
- academic column candidate 的宽度阈值收紧：
  - centered 作者/单位 block 不再误当成左栏正文列
- 新增 title-page abstract continuation repair：
  - 对 `academic_paper`，若 title page 含 `Abstract`
  - 且紧随其后的 `Introduction` chapter 在同页出现了 lowercase continuation paragraph
  - 会把这类“摘要右栏续接块”回挂到 title page，而不是留在 `Introduction`

对应代码：

- `src/book_agent/domain/structure/pdf.py`
  - `_page_has_multi_column_signature`
  - `_academic_column_major_blocks`
  - `_repair_academic_first_page_abstract_continuations`

新增回归：

- `tests/test_pdf_support.py`
  - `_write_asymmetric_first_page_academic_paper_pdf`
  - `test_bootstrap_pipeline_keeps_first_page_abstract_continuation_with_title_page`

回归验证：

- `uv run python -m py_compile`
  - `src/book_agent/domain/structure/pdf.py`
  - `tests/test_pdf_support.py`
- `uv run python -m unittest`
  - `test_bootstrap_pipeline_supports_academic_paper_pdf_lane`
  - `test_bootstrap_pipeline_waits_for_same_page_outline_heading_in_academic_paper`
  - `test_bootstrap_pipeline_keeps_first_page_abstract_continuation_with_title_page`

真实论文探针结果：

- 真实源 PDF 现在在 parser probe 中已恢复为：
  - title page chapter 含 `Abstract ...` 左栏正文
  - title page chapter 也含右栏摘要续接
  - `1 Introduction` chapter 不再携带 stray abstract continuation

新的 fresh rerun 已启动：

- artifact root：
  - `/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-forming-teams-paper-v11-full-rerun-parser-fix`
- 当前 live run：
  - `report.json`
  - `full.sqlite`
  - `exports/`
- 本轮新 `run_id`：
  - `9eff1893-9b66-428e-a944-a4cce4373792`

下一步：

- 等 `v11` 完整 translate/review/export 收尾
- 直接把新的 `merged_markdown + merged_html` 与旧 `v8 / v10` 对照
- 若 `Abstract` / figures / references 全部回正，则把 `v11` 作为论文最终交付版本

### Step 56: Finish the fresh paper rerun, clear the only blocking omission, and publish the repaired final merged outputs

Status: Completed on 2026-03-19

真实收尾结果：

- 新 rerun：
  - `artifact root = /Users/smy/project/book-agent/artifacts/real-book-live/deepseek-forming-teams-paper-v11-full-rerun-parser-fix`
  - `run_id = 9eff1893-9b66-428e-a944-a4cce4373792`
- real translate 已完整成功：
  - `52 / 52` work items `succeeded`
  - `terminal_failure_count = 0`
  - `retryable_failure_count = 0`
  - `cost_usd = 0.0449373`

收尾中出现的唯一真实阻塞：

- auto review followup 在 `v11` 上再次尝试执行 planned action
  - 我没有继续放任它黑盒消耗，而是切换到可控手动接管
- 手动 review 后确认：
  - 真正 blocking 的只剩 1 个 `OMISSION`
  - 位置在 `5 Experiments`
  - evidence 是一句漏对齐的 source sentence：
    - `human experts’ capabilities with more granularity.`
- 我直接对对应 packet 做 targeted rerun：
  - `packet_id = f816d0bb-e25c-5b0a-bc45-0ff3068d90ab`
  - rerun 后该 packet 新增 `3 target segments / 3 alignment edges`
  - 再 review 同章后，`OMISSION` 已消失
  - Chapter 6 只剩非阻塞的 `IMAGE_CAPTION_RECOVERY_REQUIRED`

最终导出状态：

- 8 个 chapter 全部完成：
  - `review_package`
  - `bilingual_html`
- 文档级 merged 导出已生成：
  - `/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-forming-teams-paper-v11-full-rerun-parser-fix/exports/b90a689e-bf00-5a3a-b1e3-0d5c88a12c1b/merged-document.md`
  - `/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-forming-teams-paper-v11-full-rerun-parser-fix/exports/b90a689e-bf00-5a3a-b1e3-0d5c88a12c1b/merged-document.markdown.manifest.json`
  - `/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-forming-teams-paper-v11-full-rerun-parser-fix/exports/b90a689e-bf00-5a3a-b1e3-0d5c88a12c1b/merged-document.html`
  - `/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-forming-teams-paper-v11-full-rerun-parser-fix/exports/b90a689e-bf00-5a3a-b1e3-0d5c88a12c1b/merged-document.manifest.json`

最终结果核对：

- `merged-document.md` 现在已恢复为：
  - `Chapters: 8`
  - `Reading Map` 8 条
  - 顶层顺序为：
    - 标题页
    - `1 引言`
    - `2 相关工作`
    - `3 问题建模`
    - `4 方法`
    - `5 实验`
    - `6 结论`
    - `参考文献`
- title page 现在具备：
  - 作者/单位
  - `### 摘要`
  - 摘要左栏正文
  - 摘要右栏续接
- `Chapter 2: 1 引言` 已不再夹带 stray abstract continuation
- 当前 markdown 中已带 `4` 个图片引用

最终判断：

- 旧的 `v8 merged markdown` 确实已经“面目全非”
- 根因不是单一 bug，而是 parser + export policy + review gate 三层联动：
  - academic-paper 结构恢复不足
  - PDF chapter dedupe / merged grouping 偏书籍策略
  - code heuristic 过宽
  - auto review followup 在 paper recovery 阶段不够稳
- 经过 `Step 53-56` 后，这篇论文现在已经具备可交付的 merged markdown / html 最终版本

### Step 57: Audit the current translation pipeline, isolate the biggest cost/quality drivers, and define the prompt/context refactor plan for books, papers, and technical blogs

Status: Completed on 2026-03-19

这轮用户反馈后，额外做了一次“从 source type 到 prompt 再到 export”的整链路梳理。结论很明确：

- 当前系统的**schema 与流水线骨架**是合理的；
- 当前系统的**默认 prompt/context policy**不合理：
  - 仍然是一套通用 technical translator prompt 同时服务英文技术书、英文论文、以及潜在的技术博客/长文；
  - 同时 `prev_blocks + current_blocks + next_blocks + chapter_brief` 仍然在大量 packet 上被过度广播；
  - 再叠加上游 block typing 偶发误判，导致“贵且不稳”。

当前真实翻译链路：

1. intake / source typing
   - 入口在 `BootstrapPipeline._detect_source_type()`
   - 当前一等公民 source type 只有：
     - `EPUB`
     - `PDF_TEXT`
     - `PDF_MIXED`
     - `PDF_SCAN`
   - 这意味着：
     - “技术博客”**当前不是 first-class source type**
     - 如果要翻技术博客，今天只能先变成 EPUB/PDF，或者借用 EPUB 风格的 block parser 思路

2. parse / normalize
   - `EPUB` 走 `EPUBParser`
     - heading / paragraph / quote / code / list / caption / table 主要来自 XHTML tag
     - 结构最稳定、误判最少
   - `PDF_TEXT` 走 `PDFParser`
     - 依赖视觉 layout / outline / typography / heading / code / table / image heuristic
   - `PDF_MIXED` 与 `PDF_SCAN` 走 `OcrPdfParser`
     - 先 OCR，再进入 PDF structure recovery
   - `academic_paper` 不是单独 source type，而是 PDF profile 上的 `recovery_lane`

3. persist into stable artifacts
   - parse 后统一落成：
     - `Document`
     - `Chapter`
     - `Block`
     - `Sentence`
     - `DocumentImage`
   - 到这里，书、论文、PDF、EPUB 已被压平为统一 block/sentence translation substrate

4. packet building
   - `ContextPacket` 当前 schema 固定包含：
     - `current_blocks`
     - `prev_blocks`
     - `next_blocks`
     - `prev_translated_blocks`
     - `chapter_concepts`
     - `relevant_terms`
     - `relevant_entities`
     - `chapter_brief`
     - `style_constraints`
   - builder 层仍然先按统一 schema 构 packet，不区分“技术书 / 论文 / 博客”

5. context compile
   - `ChapterContextCompiler` 已经会做一轮“按需裁剪”：
     - 压缩 `chapter_brief`
     - 压缩 `prev_blocks / next_blocks`
     - 过滤 chapter concepts
     - 清洗 stale `prev_translated_blocks`
   - 但当前仍然没有 material-specific compile policy：
     - 技术书
     - 英文论文
     - 技术博客 / 产品长文
     - 这三种文本的最佳 context shape 明显不同

6. prompt assembly
   - `build_translation_prompt_request()` 现在仍然只有 4 个 profile：
     - `current`
     - `role-style-v2`
     - `role-style-memory-v2`
     - `role-style-brief-v3`
   - 其中默认主线还是 `role-style-v2`
   - 真实问题不只是“提示词长”，而是：
     - prompt 没按 material type 分流
     - 当前 system prompt 仍然在同时覆盖 books / papers / business docs
     - user prompt section 仍然保留过多低命中上下文壳

7. translation / review / export
   - translation worker 产出：
     - `target_segments`
     - `alignment_suggestions`
     - `low_confidence_flags`
   - review 更擅长抓：
     - omission / alignment / structure / lock miss
   - review 还不擅长抓：
     - 翻译腔
     - material-specific 文风不对
     - “看起来像翻译出来的，不像中文技术作者写的”

对“技术书 / 论文 / 技术博客”的当前逻辑判断：

- `EPUB technical book`
  - 当前最稳
  - 结构质量通常决定翻译质量上限
  - prompt/profile 才是主瓶颈
- `PDF technical book`
  - 现在已经基本可用
  - 但仍受 heading/code/table/image typing 的上游误判影响
- `PDF academic paper`
  - 结构恢复刚跨过“可交付”门槛
  - prompt/profile 仍然没有 academic-paper-specific translation voice
- `technical blog / long-form article`
  - 当前没有单独 intake lane，也没有单独 prompt voice
  - 如果直接按“技术书/论文通用 prompt”翻，风格很容易失焦

最影响 token 消耗的 Top 5 根因：

1. 一套通用 prompt 壳在几乎所有 packet 上重复支付
   - material 不分流
   - 大量 shell 文本被反复灌入

2. packet 粒度仍然偏碎
   - 真实 full-book run 中，大量 packet 只有 `1` 个 `current_block`
   - 固定 prompt 开销被反复支付

3. `Current Paragraph` 与 `Sentence Ledger` 仍然存在重复正文输入
   - 尤其单段 / 单句 packet
   - 虽然已做过一轮压缩，但还没彻底 material-aware

4. `prev_blocks / next_blocks / chapter_brief` 仍有明显过广播
   - 不是完全没有 trim
   - 而是 trim 规则还不够“按文本类型和风险精确打开”

5. 一些结构误判会把 prose 送进 preserve/source-only 轨道，导致额外 rerun / review / export 修复成本
   - 这部分不一定直接增加单 packet token
   - 但会显著增加整书总成本

最影响翻译质量的 Top 5 根因：

1. 上游 block typing / structure recovery 偶发误判
   - heading 被拆
   - prose 被判成 code/table artifact
   - image / caption / backmatter 边界偶发失真

2. prompt 没按 material type 分流
   - 技术书、论文、博客共用一套 translator persona
   - 这直接导致文风和句法目标不准

3. context 不是“不够长”，而是“不够准”
   - `prev + next + brief` 常常比 `current_blocks` 更长
   - 但 `relevant_terms / relevant_entities / chapter_concepts` 命中仍偏低

4. `chapter_brief` 与 concept memory 仍然偏弱或偏旧
   - 在章节后半段尤其明显
   - 对“风格”和“意图”帮助不稳定

5. QA 还没有直接衡量“母语化技术中文”
   - 目前更多在抓 fidelity / coverage / structure
   - 没有把“翻译腔”本身系统化暴露出来

2、3 两类根因的交叉优先级，以质量优先排序后如下：

1. 先修 structure typing
   - 不先修这个，prompt 再好也会喂错输入类型

2. 再拆 material-specific prompt profiles
   - `tech_book_publish_cn_v1`
   - `academic_paper_formal_cn_v1`
   - `technical_blog_native_cn_v1`

3. 再做 minimal-context compile
   - 让 `current_blocks` 成为主输入
   - 其余上下文按 risk / ambiguity / material type 打开

4. 最后让 review 正式抓：
   - `LITERALISM`
   - `MATERIAL_STYLE_MISMATCH`
   - `PROMPT_OVERCONTEXTUALIZED`

本轮确定的 prompt 重构原则：

- system prompt 应该分 material type，而不是分“泛 technical translator vs memory translator”
- user prompt 应该尽量只带：
  - `current_blocks`
  - `heading_path`
  - 真命中的 locked terms / entities / concepts
  - 只有必要时才带 `prev / next / brief`
- 冗余 section 必须能完全省掉，而不是输出空壳

A/B 测试设计：

1. 第一层：prompt-only A/B（低成本）
   - 目标：
     - 先验证用户提出的新 prompt 方向是否明显更优
   - 样本：
     - 用户给出的两句真实 book 样本
     - 再加 2 个真实 academic paper sentence / packet
   - arms：
     - A = current `role-style-v2`
     - B-book = 用户提议的“出版级技术书”系统提示词
     - B-paper = 用户提议的“正式学术论文”系统提示词
   - 指标：
     - 中文母语化程度
     - 技术保真
     - 术语稳定
     - token_in

2. 第二层：real-packet A/B（主验证）
   - 样本包至少分 3 组：
     - technical book packets
     - academic paper packets
     - technical blog / long-form article packets（待补 first-class ingestion 或固定样本）
   - arms：
     - A0 = current prompt + current compile
     - B1 = book prompt + minimal context
     - B2 = paper prompt + minimal context
     - C1 = book prompt + dynamic `prev/next/brief`
     - C2 = paper prompt + dynamic `prev/next/brief`
   - 验收：
     - 若 `B/C` 在更低 token_in 下质量更高，则替换默认 prompt/profile

3. 第三层：chapter smoke A/B（上线前）
   - 每个 material 至少一章
   - 先通过 smoke，再允许 full rerun

默认 adoption gate：

- 只有当新 prompt/profile 同时满足：
  - 人工质量更好
  - review advisory 不上升
  - token_in 明显下降
- 才允许替换主线 profile

建议的重构顺序：

- Phase A
  - 先修 structure typing 的明显误判
- Phase B
  - 给 prompt profile 引入 material routing
- Phase C
  - 把 compile 策略改成 minimal-by-default
- Phase D
  - 扩 review rubric，显式抓翻译腔 / material style mismatch
- Phase E
  - 只在 chapter smoke 通过后，才做 full-book / full-paper rerun

### Step 58: Tighten PDF prose-vs-artifact heuristics and add multiline-heading merge guards before the prompt refactor

Status: Completed on 2026-03-19

这一步先不碰 prompt，而是先把两个会直接污染翻译输入的结构问题收口：

1. wrapped prose 不再轻易被判成 `code_like / table_like`
2. 相邻 heading continuation 现在支持在 parser recovery 层合并

代码改动：

- `src/book_agent/domain/structure/pdf.py`
  - `_looks_like_code()` 收紧：
    - `from` 不再被当成宽泛 keyword
    - control-flow / import pattern 改为更接近真实代码语法
    - 多行句号收尾 prose 会触发负向保护
  - `_looks_like_table()` 收紧：
    - 不再只靠“每行 token 数差不多”就判表格
    - 现在必须出现更真实的 column separator / numeric-row 信号
  - `PdfStructureRecoveryService` 新增相邻 heading continuation merge

- `src/book_agent/services/export.py`
  - `_looks_like_code_artifact_text()` 增加 prose guard
  - 多行自然语言正文不再轻易掉进 code artifact

新增回归：

- `tests/test_pdf_support.py`
  - `test_wrapped_prose_heuristics_do_not_treat_acknowledgement_text_as_code_or_table`
  - `test_bootstrap_pipeline_merges_multiline_heading_continuations`
  - `test_structure_recovery_merges_heading_continuation_fragments`

- `tests/test_persistence_and_review.py`
  - `test_export_service_does_not_treat_wrapped_book_prose_as_code_artifact`

验证结果：

- `uv run python -m py_compile src/book_agent/domain/structure/pdf.py src/book_agent/services/export.py tests/test_pdf_support.py tests/test_persistence_and_review.py`
- `uv run python -m unittest tests.test_pdf_support.PdfBootstrapPipelineTests.test_wrapped_prose_heuristics_do_not_treat_acknowledgement_text_as_code_or_table tests.test_pdf_support.PdfBootstrapPipelineTests.test_bootstrap_pipeline_merges_multiline_heading_continuations tests.test_pdf_support.PdfBootstrapPipelineTests.test_structure_recovery_merges_heading_continuation_fragments tests.test_persistence_and_review.PersistenceAndReviewTests.test_export_service_does_not_treat_wrapped_book_prose_as_code_artifact`

下一步：

- 把 `PromptProfile` 从“通用 translator role”升级为真正的 material-specific prompt family
- 先做 packet A/B harness，再决定是否切主线默认 profile

### Step 59: Add material-aware prompt families and packet-level prompt-cost instrumentation before any default profile switch

Status: Completed on 2026-03-19

这一步开始真正动 translation prompt 主干，但仍然遵守一个原则：

- 先做成可实验、可比较、可回滚
- 不直接替换主线默认 `role-style-v2`

已完成的改动：

1. prompt family 从“一个通用技术译者”扩成可感知 material 的 profile

- `src/book_agent/workers/translator.py`
  - 新增 `PromptProfile`
    - `material-aware-v1`
    - `material-aware-minimal-v1`
  - 新增 `translation_material` 路由
    - `technical_book`
    - `academic_paper`
    - `technical_blog`
    - `business_document`
    - `general_nonfiction`
  - 新增 material-specific system prompt / contract / style target / memory handling

效果：

- technical book prompt 明确强调：
  - 像中文技术书作者，而不是逐句翻译器
  - 避免翻译腔、避免文学化措辞、避免英文长句硬搬
- academic paper prompt 明确强调：
  - 正式、严谨、学术中文
  - 忠于 claim / qualifier / scope / evidence
  - 不做口语化改写

2. packet 里现在有稳定的 material signal

- `src/book_agent/domain/context/builders.py`
  - `BookProfileBuilder` 会把 `translation_material` 和 `translation_register` 放进 `style_policy_json`
  - 当前默认推断：
    - `pdf_profile.recovery_lane == academic_paper` -> `academic_paper`
    - `BookType.TECH` -> `technical_book`
    - `BookType.BUSINESS` -> `business_document`
    - 否则 -> `general_nonfiction`

说明：

- 这一步还没有把“技术博客”做成 first-class ingestion
- 但实验链路已经支持 `material_profile_override`，所以可以对 blog/article packet 先做 prompt A/B，不必先改 intake schema

3. packet experiment 现在能直接做 prompt A/B，并输出 prompt 体积指标

- `src/book_agent/services/packet_experiment.py`
  - 新增 `material_profile_override`
  - 新增 `prompt_stats`
    - `system_prompt_chars`
    - `user_prompt_chars`
    - `total_prompt_chars`
    - `system_prompt_lines`
    - `user_prompt_lines`
    - `user_prompt_sections`

- `src/book_agent/services/packet_experiment_diff.py`
  - 新增 diff summary：
    - `prompt_profile_changed`
    - `translation_material_changed`
    - `prompt_size_changed`
  - 新增 `prompt_delta.prompt_stats`
    - `total_prompt_chars_delta`
    - `user_prompt_chars_delta`
    - `system_prompt_chars_delta`

- `scripts/run_packet_experiment.py`
  - 新增 CLI：
    - `--prompt-profile material-aware-v1`
    - `--prompt-profile material-aware-minimal-v1`
    - `--material-profile academic_paper|technical_book|technical_blog|business_document|general_nonfiction`

这意味着：

- 现在已经可以对同一个 packet 直接跑：
  - baseline = `role-style-v2`
  - candidate = `material-aware-v1`
  - candidate-lite = `material-aware-minimal-v1`
- 并把 prompt 长度差、结构差、输出差直接写到 diff JSON

4. 新增回归

- `tests/test_translation_worker_abstraction.py`
  - `test_build_translation_prompt_request_supports_material_aware_profiles`
  - `test_packet_experiment_service_supports_material_profile_override`
  - 现有 packet experiment diff / dry-run / execute 回归也已更新通过

验证结果：

- `uv run python -m py_compile src/book_agent/workers/translator.py src/book_agent/domain/context/builders.py src/book_agent/services/packet_experiment.py src/book_agent/services/packet_experiment_diff.py scripts/run_packet_experiment.py tests/test_translation_worker_abstraction.py`
- `uv run python -m unittest tests.test_translation_worker_abstraction.TranslationWorkerAbstractionTests.test_build_translation_prompt_request_supports_material_aware_profiles tests.test_translation_worker_abstraction.TranslationWorkerAbstractionTests.test_packet_experiment_service_supports_material_profile_override tests.test_translation_worker_abstraction.TranslationWorkerAbstractionTests.test_packet_experiment_diff_reports_context_and_prompt_changes tests.test_translation_worker_abstraction.TranslationWorkerAbstractionTests.test_build_translation_prompt_request_supports_prompt_profiles`
- `uv run python -m unittest tests.test_translation_worker_abstraction.TranslationWorkerAbstractionTests.test_packet_experiment_service_dry_run_exports_prompt_without_worker_output tests.test_translation_worker_abstraction.TranslationWorkerAbstractionTests.test_packet_experiment_service_execute_runs_single_packet_worker`

当前结论：

- “更长的 `prev/current/next/brief`” 不是质量问题的主因，真正缺的是：
  - 正确的 material style target
  - 更少但更准的上下文
  - 更明确的反翻译腔约束

下一步：

- 用真实坏例句 packet 做 A/B
  - technical book: 你给出的两条例句
  - academic paper: 当前论文里的代表性 packet
- 比较三组：
  - `role-style-v2`
  - `material-aware-v1`
  - `material-aware-minimal-v1`
- 如果 `material-aware-minimal-v1` 在更短 prompt 下质量更高，就进入主线替换评估

### Step 60: Run real packet A/B on the two book-quality bad examples and verify that cost can drop before we trust prompt-only quality gains

Status: Completed on 2026-03-19

这一步不是继续主观讨论 prompt，而是直接拿真实坏例句 packet 做 A/B。

实验 packet：

- `c1201733-bc23-549a-bbf9-f82c943c46bb`
  - source: `It represents a shift from simply telling a computer what to do...`
- `42057fe3-2ea3-5978-a9c3-8a24a02a490d`
  - source: `As exhilarating as this new frontier is...`

dry-run 结果（只比较 prompt 体积）：

- 实验目录：
  - `artifacts/analysis/packet-experiments/67283f52-b775-533f-988b-c7433a22a28f/`
- 对比组：
  - baseline = `role-style-v2`
  - candidate = `material-aware-v1`
  - candidate-lite = `material-aware-minimal-v1`

关键发现：

- `material-aware-v1` 文风方向更对，但 prompt 反而更长
  - 不适合直接做默认 profile
- 第一轮 `material-aware-minimal-v1` 经过 section trimming 后，prompt 体积显著下降
  - packet `c120...`
    - baseline `7463` chars
    - material_min `3002` chars
    - delta `-4461` chars
  - packet `42057...`
    - baseline `6719` chars
    - material_min `2792` chars
    - delta `-3927` chars

这说明：

- 真正值得砍的是：
  - 重复正文的 `Sentence Ledger`
  - 过长的 `Previous Accepted Translations`
  - 无增益的 `Chapter Concept Memory` 广播
- 不是简单砍 system prompt

本轮对 `material-aware-minimal-v1` 做的进一步收敛：

- 单句 packet：
  - 不再单独展开完整 `Sentence Ledger`
  - 只把 alias 合并进 `Current Paragraph`
- `Previous Accepted Translations`
  - 只保留最近两条
  - 做 excerpt trim
- `Chapter Concept Memory`
  - minimal 模式下只保留 authoritative concepts
- `Section Context`
  - minimal 模式不再重复输出 `Translation Material`

真实 execute A/B：

- execute 产物：
  - `artifacts/analysis/packet-experiments/67283f52-b775-533f-988b-c7433a22a28f/c1201733-bc23-549a-bbf9-f82c943c46bb.baseline.execute.json`
  - `artifacts/analysis/packet-experiments/67283f52-b775-533f-988b-c7433a22a28f/c1201733-bc23-549a-bbf9-f82c943c46bb.material_min.execute.json`
  - `artifacts/analysis/packet-experiments/67283f52-b775-533f-988b-c7433a22a28f/42057fe3-2ea3-5978-a9c3-8a24a02a490d.baseline.execute.json`
  - `artifacts/analysis/packet-experiments/67283f52-b775-533f-988b-c7433a22a28f/42057fe3-2ea3-5978-a9c3-8a24a02a490d.material_min.execute.json`
  - 对应 diff：
    - `.../c1201733-bc23-549a-bbf9-f82c943c46bb.material_min.execute.diff.json`
    - `.../42057fe3-2ea3-5978-a9c3-8a24a02a490d.material_min.execute.diff.json`

真实结论：

1. 降本方向成立

- provider raw usage 虽然 `prompt_tokens` 没有同步下降，但 cache hit / miss 差异极大
- `material_min` 的实际 `cost_usd` 显著更低
  - `c120...`
    - baseline `0.00092422`
    - material_min `0.00016621`
  - `42057...`
    - baseline `0.00089614`
    - material_min `0.00034527`

2. 只靠高层 prompt 还不够把“翻译腔”彻底压下去

- `c120...`
  - baseline:
    - `这代表着从单纯告诉计算机该做什么，转向解释我们为何需要完成某项任务，并信任它自行找到实现方法。`
  - material_min:
    - `这代表着从单纯告诉计算机该做什么，转变为解释我们为何需要完成某项任务，并信任它自行找到实现方法。`
  - 评价：
    - 比 baseline 更凝练一些
    - 但仍没达到“技术书母语化”的目标
    - 仍未稳定产出 `不再是...而是...` 这类更自然的中文结构

- `42057...`
  - material_min 虽然略微改善了局部措辞
  - 但仍保留：
    - `深重/沉重的责任感`
    - `难以估量`
    - `趣闻轶事`
    - `从我作为...的视角来看`
  - 说明：
    - 这类问题不是“上下文不够”
    - 而是需要更细粒度的 anti-translationese rewrite guardrail

当前结论：

- 可以确认：
  - minimal prompt + trimmed context 是正确方向
  - 成本已经能明显下降
- 但还不能确认：
  - 仅靠新的 system/user prompt 文案，就足以稳定达到你要求的中文技术书水位

下一步：

- 不再继续盲目加大 prompt
- 改做 packet-level rewrite guardrails，优先覆盖这几类高频翻译腔：
  - `from my vantage point / from my perspective as ...`
  - `it represents a shift from ... to ...`
  - 英文抽象名词链直译
  - 文学化风险表达（`难以估量 / 趣闻轶事 / 深重的责任感`）
- 然后继续对这两个 packet 做 execute A/B，直到“更短 + 更像中文技术书”同时成立

### Step 61: Add source-triggered anti-translationese guardrails and validate the tradeoff between style repair and prompt growth

Status: Completed on 2026-03-19

这一步把“高层文风 prompt”再往前推进成“源句命中后才注入的定点 guardrail”。

新增 source-aware literalism / rewrite rules：

- `shift_from_to_literal`
  - source pattern: `shift from ... to ...`
  - preferred hint: `不再是……而是…… / 从……转向……`
- `vantage_point_literal`
  - source pattern: `from my vantage point as ...`
  - preferred hint: `从我这位……的角度看 / 站在……的角度看`
- `immeasurably_high_literal`
  - preferred hint: `风险极高 / 代价极高`
- `fun_anecdote_literal`
  - preferred hint: `只是个趣闻 / 只是个小插曲`

代码改动：

- `src/book_agent/services/style_drift.py`
  - 新增上述 4 条规则
- `src/book_agent/workers/translator.py`
  - `material-aware-minimal-v1` 现在也会显示 `Source-Aware Literalism Guardrails`
  - technical-book minimal style target 增加了更明确的 anti-literal guidance

新增回归：

- `tests/test_translation_worker_abstraction.py`
  - `test_context_compiler_infers_shift_and_vantage_literalism_guardrails`
  - `test_build_translation_prompt_request_supports_material_aware_profiles` 已扩展到覆盖 minimal prompt 下的 guardrail section

验证结果：

- `uv run python -m py_compile src/book_agent/services/style_drift.py src/book_agent/workers/translator.py tests/test_translation_worker_abstraction.py`
- `uv run python -m unittest tests.test_translation_worker_abstraction.TranslationWorkerAbstractionTests.test_context_compiler_infers_shift_and_vantage_literalism_guardrails tests.test_translation_worker_abstraction.TranslationWorkerAbstractionTests.test_build_translation_prompt_request_supports_material_aware_profiles`

真实 execute A/B 结果：

1. Example packet `c120...`

- baseline：
  - `这代表着从单纯告诉计算机该做什么，转向解释我们为何需要完成某项任务，并信任它自行找到实现方法。`
- material_min + new guardrails：
  - `这代表着一种转变：从简单地告诉计算机做什么，转向解释我们需要完成某事的原因，并信任它自行找到实现方法。`

评价：

- 明显优于 baseline
  - 出现了 `一种转变`
  - `why` 被更自然地处理为 `需要完成某事的原因`
- 但仍未达到最佳中文技术书表达
  - 还没有稳定产出 `不再是……而是……`

2. Example packet `42057...`

- baseline：
  - `尤其从我作为一家全球金融机构首席信息官的视角来看`
  - `趣闻轶事`
  - `实实在在的问题`
- material_min + new guardrails：
  - `尤其站在我这位全球金融机构首席信息官的角度看`
  - `只是个趣闻`
  - `真正的问题`

评价：

- 局部进步成立
  - `从我作为...的视角来看` -> `站在我这位...的角度看`
  - `趣闻轶事` -> `只是个趣闻`
  - `实实在在的问题` -> `真正的问题`
- 但仍然残留明显翻译腔
  - `深重的责任感`
  - `风险之高难以估量`
  - 末句 `黑色幽默的轶事提醒我们`

重要 tradeoff：

- Step 60 的 trimmed minimal prompt 可以把 prompt chars 砍到约 `-58% ~ -60%`
- Step 61 加入 source-triggered guardrails 后，真实执行 prompt 又回升：
  - `c120...`
    - `total_prompt_chars = 3434`
  - `42057...`
    - `total_prompt_chars = 3524`
- 且 provider `token_in / cost_usd` 也随之回升，不再像 Step 60 那样极致便宜

工程结论：

- `trimmed context` 解决的是成本问题
- `source-triggered guardrails` 解决的是局部翻译腔问题
- 两者都重要，但不能无限叠加
- 当前最优方向不是继续往 prompt 里塞更多 general guidance
- 而是：
  - 只保留极少数高命中、高收益的 guardrail
  - 并把其余“去翻译腔”工作下沉到更窄的 packet-level rewrite logic / review rerun hints

当前判断：

- `material-aware-minimal-v1`
  - 已证明值得保留为实验基线
  - 但还不适合直接替换主线默认 profile
- 原因：
  - 它已经能明显降 prompt 体积
  - 也能在局部修正翻译腔
  - 但质量提升还不够稳定，且一旦叠加 guardrail，成本会反弹

下一步：

- 不再继续膨胀 system prompt
- 改做更窄的 targeted rewrite guardrails / rerun hints，优先覆盖：
  - `profound sense of responsibility`
  - `the stakes are immeasurably high`
  - `real problem`
  - `darkly humorous reminder`
- 同时继续要求：
  - 每加一条 guardrail，都必须重新看 prompt chars / execute quality / execute cost

### Step 62: Promote previous accepted translations to the primary continuity signal, suppress raw source context by default, and validate on real packets

Status: Completed on 2026-03-19

这一步没有继续盲目加 prompt，而是直接改了 compile 层的默认上下文策略：

- `current_blocks` 继续保留
- `prev_translated_blocks` 升格为主要连续性信号
- raw `prev_blocks / next_blocks` 改成按需兜底
- `chapter_brief` 改成“可以保留在内存快照里，但不一定进入 prompt”

核心代码改动：

- `src/book_agent/services/context_compile.py`
  - `ChapterContextCompileOptions` 新增：
    - `prefer_previous_translations_over_source_context=True`
  - concept / relevant-term relevance haystack 不再吃 `next_blocks`
  - 新增 relevant term 的本地过滤，避免 future-only term 污染 prompt
  - raw `prev_blocks` 只在以下场景保留：
    - 当前 block 不是 `paragraph`
    - 当前段很短，且没有 previous accepted translations
    - 当前段是明确 bridge paragraph，且没有 previous accepted translations
  - raw `next_blocks` 只在以下场景保留：
    - 当前 block 不是 `paragraph`
    - 当前段明显像 continuation / OCR 截断
  - `chapter_brief` 现在分成两层：
    - compiled packet 里仍保留压缩后的 brief，保证 chapter memory / rerun / review 不断链
    - prompt 是否展示 brief 由 `suppress_chapter_brief_in_prompt` 控制
  - 对 `shift from ... to ...` 这类概念换挡句，新增窄例外：
    - 即使有 previous accepted translations，也保留 prompt brief
- `src/book_agent/workers/translator.py`
  - 新增 `_chapter_brief_visible(...)`
  - prompt 现在会尊重 `suppress_chapter_brief_in_prompt`
  - memory handling section 不再引用一个实际没注入的 chapter brief
- `src/book_agent/services/packet_experiment.py`
  - `PacketExperimentOptions` 新增：
    - `prefer_previous_translations_over_source_context`
  - artifact 现在会输出：
    - `raw_prev_block_count / compiled_prev_block_count`
    - `raw_next_block_count / compiled_next_block_count`
    - `raw_chapter_brief_present / compiled_chapter_brief_present / prompt_chapter_brief_present`
- `scripts/run_packet_experiment.py`
  - 新增 CLI 开关：
    - `--disable-prev-translation-primary`

回归测试：

- `tests/test_translation_worker_abstraction.py`
  - `test_context_compiler_prefers_previous_translations_over_raw_source_context_when_available`
  - `test_context_compiler_keeps_brief_for_shift_statement_even_with_previous_translations`
  - `test_context_compiler_keeps_minimal_context_for_short_bridge_paragraph`
  - `test_context_compiler_can_disable_chapter_memory_features`
  - packet experiment payload test已扩展到覆盖新的 context counters

验证：

- `uv run python -m py_compile src/book_agent/services/context_compile.py src/book_agent/workers/translator.py src/book_agent/services/packet_experiment.py scripts/run_packet_experiment.py tests/test_translation_worker_abstraction.py`
- `PYTHONWARNINGS='ignore::ResourceWarning' uv run python -m unittest tests.test_translation_worker_abstraction`
  - `54 tests OK`

真实 packet dry-run A/B（使用 `artifacts/real-book-live/deepseek-agentic-design-book-v11-full-run-chunked-pagecount/book-agent.db`）：

1. `c1201733bc23549abbf9f82c943c46bb`

- 对照组：`--disable-prev-translation-primary`
  - `total_prompt_chars = 3181`
  - `compiled_prev_blocks = 1`
  - `prompt_chapter_brief_present = true`
- 当前策略（Step 62 最终版）
  - 工件：
    - `artifacts/analysis/packet-experiments/67283f52-b775-533f-988b-c7433a22a28f/c1201733bc23549abbf9f82c943c46bb.material_min.contextv5.json`
  - `total_prompt_chars = 2534`
  - `compiled_prev_blocks = 0`
  - `prompt_chapter_brief_present = true`
- 结论：
  - 在保留 brief 的前提下，仍把 raw prev source context 删掉了
  - prompt 体积相对对照组下降约 `20.3%`

2. `42057fe32ea35978a9c38a24a02a490d`

- 对照组：`--disable-prev-translation-primary`
  - `total_prompt_chars = 3271`
  - `compiled_prev_blocks = 0`
  - `prompt_chapter_brief_present = true`
- 当前策略
  - 工件：
    - `artifacts/analysis/packet-experiments/67283f52-b775-533f-988b-c7433a22a28f/42057fe32ea35978a9c38a24a02a490d.material_min.contextv3.json`
  - `total_prompt_chars = 3058`
  - `compiled_prev_blocks = 0`
  - `prompt_chapter_brief_present = false`
- 结论：
  - 对这类已经有足够 previous accepted translations 的风险段，brief 可以安全退出 prompt
  - prompt 体积相对对照组下降约 `6.5%`

真实 execute 结果：

1. `42057...`

- 旧工件：
  - `artifacts/analysis/packet-experiments/67283f52-b775-533f-988b-c7433a22a28f/42057fe3-2ea3-5978-a9c3-8a24a02a490d.material_min.execute.json`
- 新工件：
  - `artifacts/analysis/packet-experiments/67283f52-b775-533f-988b-c7433a22a28f/42057fe32ea35978a9c38a24a02a490d.material_min.contextv3.execute.json`
- 结果：
  - 译文基本不变
    - `尽管这一新前沿领域令人振奋，但它也带来了深重的责任感——尤其站在我这位全球金融机构首席信息官的角度看。`
  - prompt chars：`3524 -> 3058`
  - `token_in`: `2589 -> 2545`
  - `cost_usd`: `0.00092501 -> 0.00087959`
- 结论：
  - 这类 packet 已证明“更短 prompt + 质量不退化”成立

2. `c120...`

- 旧工件：
  - `.../c1201733-bc23-549a-bbf9-f82c943c46bb.material_min.execute.json`
- 新工件：
  - `.../c1201733bc23549abbf9f82c943c46bb.material_min.contextv5.execute.json`
- 新译文：
  - `这代表着一种根本性转变：从简单地告诉计算机该做什么，转向解释我们为何需要完成某项任务，并信任它自行探索实现方法。`
- 结论：
  - prompt 已从历史 `3434` chars 压到 `2534`
  - 但这条句子仍然没有稳定收敛到理想表达，例如：
    - 还没有稳定变成 `告诉它目标与原因，让它自己决定如何实现`
    - `自行探索实现方法` 仍残留翻译腔

工程判断：

- `prev_translated_blocks` 主导连续性这条路线已经验证成立
- 继续把 raw `prev/next` 全塞回 prompt，不是下一步最优动作
- 剩余问题已经从“上下文不够”转成了“packet-level rewrite 仍不够窄、不够稳”

下一步：

- 不再回退到长 prompt 默认策略
- 继续沿这条低成本方向走，但要把 residual 收口到更窄的 rewrite 层：
  - `goal / reason / how`
  - `profound sense of responsibility`
  - `the stakes are immeasurably high`
  - `real problem / darkly humorous reminder`

## Step 63. Agentic Design v16 Export Repair

时间：2026-03-19

目标：

- 核对 `agentic-design-book` 当前代码路径下，两个用户点名问题是否已真正收口：
  - multiline heading continuation
  - prose mistaken as code/table artifact
- 若当前代码已足以解决，就重新生成 `deepseek-agentic-design-book-v16-merged-markdown`

结论：

- 标题断裂问题在当前代码路径下仍然存在
  - 当前 render blocks 仍输出：
    - `思想领袖视角：力量`
    - `与责任`
- 第二个坏例并非整体“已彻底解决”
  - `I am deeply indebted ...` 这段在当前代码路径下已经恢复为正文翻译
  - 但第一章还有其它感谢语仍被历史 DB 以 `code/table + protect` 落库，如果直接重导仍会丢译文

本轮代码修复：

- [export.py](/Users/smy/project/book-agent/src/book_agent/services/export.py)
  - 增加 export-level multiline heading merge，兼容历史 DB 中已落库的 split heading
  - 增加 `prose artifact` 识别：
    - 对 `block_type=code/table/paragraph`
    - 若文本更像正文 prose 而非代码工件，则不再默认走 `代码保持原样`
  - 支持 `repair_*` metadata：
    - `repair_source_text`
    - `repair_target_text`
    - `repair_block_type`
    - `repair_skip_block_ids`

回归测试：

- [test_persistence_and_review.py](/Users/smy/project/book-agent/tests/test_persistence_and_review.py)
  - 历史 split heading 在 render 层会自动合并
  - prose-like code block 只要已有 target，就按正文渲染
  - repair metadata 可以把历史断裂 prose artifact 合并并跳过 continuation block

验证：

- `uv run python -m py_compile src/book_agent/services/export.py tests/test_persistence_and_review.py`
- `PYTHONWARNINGS='ignore::ResourceWarning' uv run python -m unittest tests.test_persistence_and_review.PersistenceAndReviewTests.test_render_blocks_merge_multiline_heading_fragments_from_historical_pdf_export tests.test_persistence_and_review.PersistenceAndReviewTests.test_render_blocks_treat_prose_like_code_block_with_targets_as_translated_paragraph tests.test_persistence_and_review.PersistenceAndReviewTests.test_render_blocks_honor_prose_artifact_repair_metadata_and_skip_continuation`
  - `3 tests OK`

历史数据补救：

- 新建独立目录：
  - [deepseek-agentic-design-book-v16-merged-markdown](/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-agentic-design-book-v16-merged-markdown)
- 从 `v11` 复制独立 `book-agent.db`
- 仅在这份副本中写入最小 repair metadata：
  - `5cea19dc...`
    - 合并 `Yingchao/Hann/Lee Boonstra` 断裂感谢语
    - 以 repair target 恢复为正常正文
  - `2624802c...`
    - 恢复 `Mike Styer / Turan Bulmus / Kanchana Patlolla` 感谢语

产物：

- [v16 merged markdown](/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-agentic-design-book-v16-merged-markdown/67283f52-b775-533f-988b-c7433a22a28f/merged-document.md)
- [v16 merged html](/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-agentic-design-book-v16-merged-markdown/67283f52-b775-533f-988b-c7433a22a28f/merged-document.html)
- [v16 markdown manifest](/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-agentic-design-book-v16-merged-markdown/67283f52-b775-533f-988b-c7433a22a28f/merged-document.markdown.manifest.json)
- [v16 html manifest](/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-agentic-design-book-v16-merged-markdown/67283f52-b775-533f-988b-c7433a22a28f/merged-document.manifest.json)

定点验收结果：

- 标题现在是单个 heading：
  - `### 思想领袖视角：力量与责任`
- `Yingchao Huang ... Lee Boonstra ...` 这段不再显示为 `代码保持原样`
- `Mike Styer / Turan Bulmus / Kanchana Patlolla ...` 这段也已恢复成中文正文
- `I am deeply indebted ... Marco Fago ...` 在 `v16` 中继续保持正常翻译

剩余判断：

- 这次用户点名的两个坏例已经在 `v16` 收口
- 但 scan-book 历史 DB 里仍存在更大范围的 `prose mistaken as artifact` 包袱，后续需要继续做更系统的 block repair / rebuild，不能误判为“全书 code detection 已完全解决”

## Step 64. Agentic Design Historical Prose-Artifact Sweep

时间：2026-03-19

目标：

- 彻底清理 `agentic-design-book` 历史 DB 中“正文被 OCR / layout 误判成 code_like / table_like，最终以英文原文或 代码保持原样 落到 merged”的遗留问题
- 覆盖正文主章，以及 appendix / references 中同类坏块

根因复盘：

- 旧的 `prose artifact` 扫描过于保守
  - 只修 `pdf_page_family=body`
  - `class of` 会被误判成 inline code marker，导致诸如 `I admit, I began as a skeptic... a new class of "reasoning" models` 这类正文块漏检
- 放宽 family 后，又暴露出第二层问题
  - OCR 扁平化代码示例中的单行强代码信号（如 `def aggregator(...)`、`map chain = ...`）需要继续排除，避免误把真实代码示例当作 prose

本轮代码修复：

- [export.py](/Users/smy/project/book-agent/src/book_agent/services/export.py)
  - 收紧 `_INLINE_CODE_LIKE_PATTERN`
    - 不再把普通 prose 中的 `class of` 误判为代码
  - 新增 `codeish line` / `strong codeish line` 护栏
    - 能继续排除真实代码块、OCR 扁平化代码和单行强代码信号
- [pdf_prose_artifact_repair.py](/Users/smy/project/book-agent/src/book_agent/services/pdf_prose_artifact_repair.py)
  - `scan_document()` 不再只限制 `pdf_page_family=body`
  - appendix / references 中的 prose-like artifact 也会进入 repair
- [repair_pdf_prose_artifacts.py](/Users/smy/project/book-agent/scripts/repair_pdf_prose_artifacts.py)
  - 用作历史 DB 的批量 repair + export 工具

回归测试：

- [test_persistence_and_review.py](/Users/smy/project/book-agent/tests/test_persistence_and_review.py)
  - `class of` 型 prose 仍会被识别为 prose artifact
  - OCR 扁平化代码块不会被误识别为 prose artifact
  - 单行强代码信号（`def aggregator...`）不会被误识别为 prose artifact
  - references family 的 prose artifact 也能进入 repair

历史数据补救轨迹：

- `v23`
  - 清理首批 body-family 历史坏块
- `v24`
  - 扩大到更宽口径的 prose artifact 扫描
  - `37` 个候选中修复 `36` 个，剩 `1` 个 timeout
- `v25`
  - 补掉 `Memory Management` 最后一个 timeout chain
  - `remaining_candidates = 0`（仅限旧 body-family 口径）
- `v26`
  - 放开 appendix / references family 继续 sweep
  - `18/18` repaired，`failed_candidates = 0`
  - 按最新扫描规则复核，`remaining_candidates = 0`

最终产物：

- [v26 merged markdown](/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-agentic-design-book-v26-prose-artifact-final-plus-appendix/exports/67283f52-b775-533f-988b-c7433a22a28f/merged-document.md)
- [v26 merged html](/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-agentic-design-book-v26-prose-artifact-final-plus-appendix/exports/67283f52-b775-533f-988b-c7433a22a28f/merged-document.html)
- [v26 repair report](/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-agentic-design-book-v26-prose-artifact-final-plus-appendix/repair-report.json)

定点验收：

- `I admit, I began as a skeptic...` 不再落成 `代码保持原样`
- `While the chapters are ordered...` 整段已恢复为完整中文正文
- `Frameworks provide distinct mechanisms...`、`This Python script defines a single function called greet...`、`A2A Client / Synchronous Request/Response` 等 appendix / references 坏例都已恢复为中文正文
- 真实代码示例如 WeatherBot JSON、FastMCP 代码块、LangGraph `def aggregator(...)` 仍保持代码工件导出，不被误翻

验证：

- `uv run python -m py_compile src/book_agent/services/export.py src/book_agent/services/pdf_prose_artifact_repair.py tests/test_persistence_and_review.py`
- `PYTHONWARNINGS='ignore::ResourceWarning' uv run python -m unittest tests.test_persistence_and_review.PersistenceAndReviewTests.test_export_service_detects_prose_artifact_text_with_phrase_class_of tests.test_persistence_and_review.PersistenceAndReviewTests.test_export_service_does_not_detect_codeish_ocr_block_as_prose_artifact tests.test_persistence_and_review.PersistenceAndReviewTests.test_export_service_does_not_detect_single_strong_code_line_as_prose_artifact tests.test_persistence_and_review.PersistenceAndReviewTests.test_pdf_prose_artifact_repair_service_translates_reference_family_prose_artifact_blocks`
  - `4 tests OK`
- 基于 [v26 DB](/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-agentic-design-book-v26-prose-artifact-final-plus-appendix/book-agent.db) 复核
  - `remaining_candidates = 0`

## Step 65. Phase A Kickoff: Layout-Guided Figure Crop

时间：2026-03-19

目标：

- 正式进入 `document intelligence first`
- 优先补强 figure crop，而不是继续依赖 caption 上方固定窗口
- 为后续 PDF 论文图片裁切、结构识别和 render QA 打基础

本轮实现：

- [export.py](/Users/smy/project/book-agent/src/book_agent/services/export.py)
  - `_caption_anchored_pdf_crop_bbox()` 不再只用固定几何窗口
  - 新增 page layout sampler：
    - `_page_layout_blocks()`
    - `_best_caption_aligned_image_bbox()`
    - `_trim_caption_crop_bbox_with_text_blocks()`
  - 新策略优先级：
    1. 若页面中存在靠近 caption 且横向对齐的 image block，优先直接围绕 image block 裁图
    2. 若没有可靠 image block，则在旧 fallback bbox 上，根据正文 text block 反向收紧顶部，尽量去掉 figure 上方误带入的文字

关键收益：

- 对“caption 下方、figure 上方”的常见 paper / scan-book 场景更稳
- 不再默认把 caption 上方整大片正文一起裁进图片
- 为后续 `PDF page layout risk classifier` 复用出第一版 page block sampler

新增回归：

- [test_persistence_and_review.py](/Users/smy/project/book-agent/tests/test_persistence_and_review.py)
  - `test_caption_anchored_pdf_crop_bbox_prefers_layout_image_block`
    - 页面存在 image block 时，应优先围绕 image block 裁图
  - `test_caption_anchored_pdf_crop_bbox_trims_above_overlapping_text_blocks`
    - 无 image block 时，应把 crop 顶部下压到正文 text block 之后
  - 原有 `test_caption_anchored_pdf_crop_bbox_stays_above_caption_region` 继续通过

验证：

- `uv run python -m py_compile src/book_agent/services/export.py tests/test_persistence_and_review.py`
- `PYTHONWARNINGS='ignore::ResourceWarning' uv run python -m unittest tests.test_persistence_and_review.PersistenceAndReviewTests.test_caption_anchored_pdf_crop_bbox_stays_above_caption_region tests.test_persistence_and_review.PersistenceAndReviewTests.test_caption_anchored_pdf_crop_bbox_prefers_layout_image_block tests.test_persistence_and_review.PersistenceAndReviewTests.test_caption_anchored_pdf_crop_bbox_trims_above_overlapping_text_blocks tests.test_persistence_and_review.PersistenceAndReviewTests.test_export_service_detects_prose_artifact_text_with_phrase_class_of tests.test_persistence_and_review.PersistenceAndReviewTests.test_export_service_does_not_detect_single_strong_code_line_as_prose_artifact`
  - `5 tests OK`

当前判断：

- 这是 `document intelligence first` 的第一刀，先把 figure crop 从“固定框”升级到“轻量 layout-aware”
- 下一批最优动作：
  - 把 page layout sampler 升级成显式 `PDF page layout risk classifier`
  - 再把 risk signal 喂给 paper-specific front page / abstract / intro 修复

## Step 66. Phase A: Academic First-Page Layout Risk Classifier

时间：2026-03-19

目标：

- 把 academic PDF 第一页的“隐式高风险布局”显式化，而不是只靠 `multi_column / column_fragment`
- 让 page evidence 能说明“为什么这一页值得特殊修复”
- 用几何风险信号稳住 abstract continuation，不再只依赖 lowercase-leading 启发式

本轮实现：

- [pdf.py](/Users/smy/project/book-agent/src/book_agent/domain/structure/pdf.py)
  - 新增 page-level layout assessment：
    - `_page_has_abstract_signal()`
    - `_page_first_numbered_section_heading_top()`
    - `_page_has_asymmetric_academic_first_page_signal()`
    - `_assess_page_layout()`
    - `_PageLayoutAssessment`
  - `PdfStructureRecoveryService.recover()` 现在会先生成 `page_layout_assessments`
  - `pdf_page_evidence` 新增：
    - `page_layout_risk`
    - `page_layout_reasons`
  - `_repair_academic_first_page_abstract_continuations()` 不再只看 `normalized_text[:1].islower()`
    - 当第一页 assessment 命中 `academic_first_page_asymmetric` 时，会结合 `source_bbox_json` 判断 paragraph 是否物理上位于 `1 Introduction` heading 之上
    - 对这些同页 continuation blocks，若文本看起来像 academic prose / continuation，就回挂到 title page / abstract chapter

关键收益：

- 第一页即便不是标准双栏页，也能被识别成“academic first-page asymmetric risk”
- abstract continuation 即便以大写句子开头，也不会因为缺少 lowercase lead 而错误掉进 `Introduction`
- page evidence 终于能显式解释第一页修复的依据，而不是只剩全局 `layout_risk`

新增回归：

- [test_pdf_support.py](/Users/smy/project/book-agent/tests/test_pdf_support.py)
  - `test_bootstrap_pipeline_keeps_first_page_abstract_continuation_with_title_page`
    - 继续验证原有 lowercase continuation 场景
    - 现在额外断言 `page_layout_risk=high` 且 reasons 包含 `academic_first_page_asymmetric`
  - `test_bootstrap_pipeline_keeps_uppercase_first_page_abstract_continuation_with_title_page`
    - 新增 uppercase-leading continuation 场景，验证不会再被错误分配到 `1 Introduction`
  - `test_parser_emits_pdf_page_evidence`
    - 低风险文本 PDF 的 page evidence 现在显式断言 `page_layout_risk=low`

验证：

- `uv run python -m py_compile src/book_agent/domain/structure/pdf.py tests/test_pdf_support.py`
- `PYTHONWARNINGS='ignore::ResourceWarning' uv run python -m unittest tests.test_pdf_support.PdfBootstrapPipelineTests.test_bootstrap_pipeline_keeps_first_page_abstract_continuation_with_title_page tests.test_pdf_support.PdfBootstrapPipelineTests.test_bootstrap_pipeline_keeps_uppercase_first_page_abstract_continuation_with_title_page tests.test_pdf_support.BasicPdfOutlineRecoveryTests.test_parser_emits_pdf_page_evidence`
  - `3 tests OK`
- 额外抽样 probe：
  - asymmetric lower-case sample -> `page_layout_risk=high`
  - asymmetric uppercase sample -> `page_layout_risk=high`

边界：

- 本轮还没有去动更大的 multi-column reading-order 问题
- `layout_suspect` 语义保持原样，避免无谓放大 review 噪声；新 classifier 先作为 page evidence + targeted repair 信号落地

## Step 67. Phase A: Basic Multi-Column Academic Ordering Threshold Fix

时间：2026-03-19

目标：

- 修掉 `BasicPdfTextExtractor` 下 positioned multi-column academic paper 没能进入 column-major 排序的问题
- 让 `academic_paper` lane 在 basic extractor 路径上也能识别“稍宽左栏 block”
- 避免 `2 Related Work` 抢到左栏续段之前，破坏论文阅读顺序

根因：

- 现有 multi-column 判定和排序都把 column block 宽度硬限制在 `<= 0.52 * page_width`
- basic extractor 在 academic PDF 上经常把左栏 heading + sentence 合成稍宽 block
- 这类真实左栏 block 会落在大约 `0.55 * page_width` 左右，于是既进不了 `_page_has_multi_column_signature()`，也进不了 `_academic_column_major_blocks()`
- 结果是 page 根本没进入 column-major 排序，阅读顺序退回简单的 `y -> x` 交错顺序

本轮实现：

- [pdf.py](/Users/smy/project/book-agent/src/book_agent/domain/structure/pdf.py)
  - 新增 `_MULTI_COLUMN_BLOCK_WIDTH_RATIO = 0.58`
  - `_page_has_multi_column_signature()` 改为用统一阈值识别稍宽的左栏 block
  - `_academic_column_major_blocks()` 同步使用该阈值，避免“能识别但不能排序”的两套标准不一致

关键收益：

- basic extractor 的 positioned academic paper 现在能重新识别为 `academic_paper` lane
- profile 恢复到 `layout_risk=medium`
- page 内 reading order 回到 “左栏完整消费后，再进入右栏”

验证：

- `PYTHONWARNINGS='ignore::ResourceWarning' uv run python -m unittest tests.test_pdf_support.PdfBootstrapPipelineTests.test_profiler_keeps_positioned_multi_column_academic_paper_in_medium_lane tests.test_pdf_support.PdfBootstrapPipelineTests.test_bootstrap_pipeline_orders_positioned_multi_column_academic_paper_left_then_right tests.test_pdf_support.PdfBootstrapPipelineTests.test_bootstrap_pipeline_keeps_first_page_abstract_continuation_with_title_page tests.test_pdf_support.PdfBootstrapPipelineTests.test_bootstrap_pipeline_keeps_uppercase_first_page_abstract_continuation_with_title_page tests.test_pdf_support.BasicPdfOutlineRecoveryTests.test_parser_emits_pdf_page_evidence`
  - `5 tests OK`

## Step 68. Phase A: Surface Page-Level Layout Risk Into QA Evidence

时间：2026-03-19

目标：

- 让 `page_layout_risk / page_layout_reasons` 不再只是 parser 内部字段
- 把第一页 asymmetric academic risk 直接暴露到 review package 和 smoke summary
- 在不改写 `layout_suspect` 语义的前提下，提升 QA / export 的可见性

现状缺口：

- `pdf_page_evidence` 已经有 `page_layout_risk / page_layout_reasons`
- 但 review package 的 debug 视角仍只靠 `layout_suspect / special_section / footnote_relocated`
- academic first page 这类高风险页虽然已经被 parser 修复，却仍然可能在 QA 视角里“隐身”

本轮实现：

- [export.py](/Users/smy/project/book-agent/src/book_agent/services/export.py)
  - `_pdf_preserve_evidence_payload()`
    - `page_contracts` 新增：
      - `page_layout_risk`
      - `page_layout_reasons`
  - `_pdf_page_debug_evidence_payload()`
    - `page_layout_risk != low` 的页现在会自动进入 `interesting_page_numbers`
    - debug page payload 新增：
      - `page_layout_risk`
      - `page_layout_reasons`
    - `debug_reasons` 新增 `page_layout_risk`
- [pdf_smoke.py](/Users/smy/project/book-agent/src/book_agent/tools/pdf_smoke.py)
  - `_parse_summary()` 新增：
    - `page_layout_risks`
    - `page_layout_reasons`
    - `page_layout_risk_pages`

关键收益：

- asymmetric academic first page 不再只能在 raw metadata 里排查
- review package 现在会主动把这类页拉进 debug evidence
- smoke report 可以直接看见“哪些页是高/中风险，以及为什么”

新增回归：

- [test_pdf_support.py](/Users/smy/project/book-agent/tests/test_pdf_support.py)
  - `test_review_package_export_surfaces_page_layout_risk_debug_evidence`
    - 直接走 `parse -> save -> _build_review_package`
    - 断言第一页：
      - `page_layout_risk=high`
      - `page_layout_reasons` 包含 `academic_first_page_asymmetric`
      - `pdf_page_debug_evidence.pages[0].debug_reasons` 包含 `page_layout_risk`
- [test_pdf_smoke_tools.py](/Users/smy/project/book-agent/tests/test_pdf_smoke_tools.py)
  - `test_build_pdf_smoke_report_includes_parse_summary`
    - 现在额外断言 `page_layout_risks`
    - 以及 `page_layout_risk_pages=[]` 的低风险基线

验证：

- `uv run python -m py_compile src/book_agent/services/export.py src/book_agent/tools/pdf_smoke.py tests/test_pdf_support.py tests/test_pdf_smoke_tools.py`
- `PYTHONWARNINGS='ignore::ResourceWarning' uv run python -m unittest tests.test_pdf_support.PdfApiWorkflowTests.test_review_package_export_surfaces_page_layout_risk_debug_evidence tests.test_pdf_support.PdfBootstrapPipelineTests.test_bootstrap_pipeline_keeps_first_page_abstract_continuation_with_title_page tests.test_pdf_support.PdfBootstrapPipelineTests.test_bootstrap_pipeline_keeps_uppercase_first_page_abstract_continuation_with_title_page tests.test_pdf_support.PdfBootstrapPipelineTests.test_bootstrap_pipeline_orders_positioned_multi_column_academic_paper_left_then_right tests.test_pdf_smoke_tools.PdfSmokeToolsTests.test_build_pdf_smoke_report_includes_parse_summary`
  - `5 tests OK`

边界：

- 本轮仍没有把 `page_layout_risk` 直接改成 review issue 的发射条件
- 这是刻意保持保守，先让 QA 可见，再决定是否需要把它升级成更强的审核策略信号

## Step 69. Phase A: Paper-Specific Review Policy Learns Local Page Layout Risk

时间：2026-03-19

目标：

- 让 review 主链路也能识别 `page_layout_risk`，而不是只依赖 `layout_suspect`
- 对 academic paper 的“局部高风险页”发出保守 advisory，而不是继续完全静默
- 保持 non-blocking，避免把刚补上的 page-level intelligence 直接放大成过多误报

问题：

- 经过 Step 66 / 68 之后，parser 和 review package 已经能看见 `academic_first_page_asymmetric`
- 但 `ReviewService._pdf_layout_review_policy()` 仍只使用：
  - chapter-level `layout_risk`
  - `layout_suspect`
  - `suspicious_page_numbers`
- 结果是：
  - academic first page 这种“局部高风险但不在 `layout_suspect` 里”的页，review 侧完全无感
  - 旧 review 测试也对 chapter ordinal 有隐含假设，在 chapter split 更细后变得不稳定

本轮实现：

- [review.py](/Users/smy/project/book-agent/src/book_agent/services/review.py)
  - `_pdf_structure_issues()`
    - 新增：
      - `local_layout_risk_pages`
      - `local_high_layout_risk_pages`
    - `MISORDERING` evidence 新增：
      - `page_layout_risk_pages`
      - `high_layout_risk_pages`
      - `page_layout_reasons_by_page`
  - `_pdf_layout_review_policy()`
    - 新增 page-level risk 参数
    - 当：
      - `layout_risk == medium`
      - `recovery_lane == academic_paper`
      - 没有 `layout_suspect`
      - 但存在 `local_high_layout_risk_pages`
    - 则发出：
      - `severity=low`
      - `blocking=false`
      - `reason=academic_paper_local_page_layout_advisory`
    - 旧的 `academic_paper_medium_layout_advisory / structurally_anchored / wide_layout_advisory` 分支保持不变
- [test_pdf_support.py](/Users/smy/project/book-agent/tests/test_pdf_support.py)
  - 新增 `test_academic_paper_local_high_page_layout_risk_creates_low_advisory_issue`
  - 同时把旧 academic review 基线改成 deterministic basic-profiler 路径，避免受本机 `PyMuPDF` 可用性影响
  - chapter 选择改成按 `title_src` 取章，而不再假设 “第 1 章就是 abstract / 第 2 章就是 references”

关键收益：

- academic paper 的局部高风险页现在终于进入 review 主链路
- 但依旧保持 advisory，不会把这类风险直接升级成 blocking
- review issue evidence 能精确指出“哪一页高风险、为什么高风险”

验证：

- `uv run python -m py_compile src/book_agent/services/review.py src/book_agent/services/export.py src/book_agent/tools/pdf_smoke.py tests/test_pdf_support.py tests/test_pdf_smoke_tools.py`
- `PYTHONWARNINGS='ignore::ResourceWarning' uv run python -m unittest tests.test_pdf_support.PdfReviewTests.test_academic_paper_medium_risk_creates_advisory_structure_issue tests.test_pdf_support.PdfReviewTests.test_academic_paper_local_high_page_layout_risk_creates_low_advisory_issue tests.test_pdf_support.PdfApiWorkflowTests.test_review_package_export_surfaces_page_layout_risk_debug_evidence tests.test_pdf_support.PdfBootstrapPipelineTests.test_bootstrap_pipeline_orders_positioned_multi_column_academic_paper_left_then_right tests.test_pdf_smoke_tools.PdfSmokeToolsTests.test_build_pdf_smoke_report_includes_parse_summary`
  - `5 tests OK`

当前判断：

- `document intelligence first` 已经从 parser 内部信号推进到了 review / export / smoke 三条链
- 下一刀最值得继续的是：
  - 把 `page_layout_reasons_by_page` 进一步接到 paper-specific rerun / repair 策略
  - 或者开始专门处理 figure/table/equation 邻接区域的结构恢复

## Step 70. Phase A: Recover Equation Blocks as First-Class PDF Artifacts

时间：2026-03-19

目标：

- 把 equation-like block 从“导出层猜测 artifact kind”升级成 parser 主链路里的一级结构
- 避免论文公式继续混进正文或 generic protected artifact
- 让 equation 在 export 时稳定呈现为“公式保持原样”

问题：

- 之前 `BlockType.EQUATION` 已经存在于 domain enum / block rules 中
- 但 PDF recovery 里没有真正产出 `equation` role
- export 里虽然支持 `artifact_kind == equation` 的展示细节，但 `BlockType.EQUATION` 本身没有映射到该 artifact kind
- 结果是公式更多依赖导出层猜测，不能算真正的 document intelligence

本轮实现：

- [pdf.py](/Users/smy/project/book-agent/src/book_agent/domain/structure/pdf.py)
  - 新增：
    - `_EQUATION_OPERATOR_PATTERN`
    - `_EQUATION_VARIABLE_PATTERN`
    - `_EQUATION_CODE_CUE_PATTERN`
    - `_looks_like_equation()`
  - `PdfStructureRecoveryService._classify_role()`
    - 在 heading / caption 之后、code/table 之前，新增 equation-like block 识别
    - 约束条件包括：
      - 短小、居中或内缩的 displayed formula 几何形态
      - 具有明确数学操作符/变量信号
      - 不像 caption / heading / code / table
  - `_block_type_for_role()` 新增 `equation -> BlockType.EQUATION`
- [export.py](/Users/smy/project/book-agent/src/book_agent/services/export.py)
  - `_artifact_kind_for_block()` 新增 `BlockType.EQUATION -> "equation"`
  - 这样公式会稳定命中现有：
    - Markdown fenced `tex`
    - HTML/merged export 的公式 artifact 展示
    - notice `公式保持原样`

新增回归：

- [test_pdf_support.py](/Users/smy/project/book-agent/tests/test_pdf_support.py)
  - `test_recovery_classifies_centered_equation_block_as_equation`
    - 直接用 synthetic `PdfExtraction`
    - 断言 centered formula 会被恢复为 `BlockType.EQUATION`
    - `pdf_page_evidence.role_counts.equation == 1`
  - `test_export_treats_equation_blocks_as_equation_artifacts`
    - 断言 equation block 的 render result：
      - `render_mode = source_artifact_full_width`
      - `artifact_kind = equation`
      - `notice = 公式保持原样`

验证：

- `uv run python -m py_compile src/book_agent/domain/structure/pdf.py src/book_agent/services/export.py tests/test_pdf_support.py`
- `PYTHONWARNINGS='ignore::ResourceWarning' uv run python -m unittest tests.test_pdf_support.BasicPdfOutlineRecoveryTests.test_recovery_classifies_centered_equation_block_as_equation tests.test_pdf_support.PdfDocumentImagePersistenceTests.test_export_treats_equation_blocks_as_equation_artifacts tests.test_pdf_support.PdfReviewTests.test_academic_paper_local_high_page_layout_risk_creates_low_advisory_issue tests.test_pdf_support.PdfBootstrapPipelineTests.test_bootstrap_pipeline_orders_positioned_multi_column_academic_paper_left_then_right`
  - `4 tests OK`

当前判断：

- `figure / caption / equation` 三类视觉工件里，equation 已经不再是导出层的“猜测型 artifact”
- 下一刀更值得继续的是：
  - table / equation / caption 邻接区域的 group/repair
  - 或者 page-level layout reasons 驱动的 paper-specific rerun / repair

## Step 71. Phase A: Link Table Captions to Table Artifacts and Prevent Cross-Type Caption Mislinks

时间：2026-03-19

目标：

- 把 `Table 1...` 这类 caption 从“独立正文块”升级成真正挂在表格工件上的结构关系
- 避免图片错误吃到 `Table ...` caption，降低 caption cross-type 误连
- 让 merged / markdown export 直接输出“译文表注 + 原结构表格”，而不是 caption/table 分裂渲染

问题：

- 之前 parser 只有 `image-caption` 邻接修复
- `caption` block 虽然会识别出 `Table 1...`
- 但不会挂回 `BlockType.TABLE`
- 同时旧 `image-caption` 链接只要几何位置接近就会尝试关联，没有校验 caption 前缀是否和工件类型一致
- 结果是：
  - 论文和技术书里的表格依旧结构断裂
  - 图注和表注存在互串风险

本轮实现：

- [pdf.py](/Users/smy/project/book-agent/src/book_agent/domain/structure/pdf.py)
  - 新增：
    - `_FIGURE_CAPTION_PATTERN`
    - `_TABLE_CAPTION_PATTERN`
    - `_caption_matches_artifact_role()`
  - `_CAPTION_PATTERN` 现在覆盖 `figure / fig / image / diagram / chart / table`
  - `PdfStructureRecoveryService`
    - `self._link_image_captions(...)` 升级为 `self._link_artifact_captions(...)`
    - 现在会对：
      - `image`
      - `table_like`
      做通用 caption linking
    - caption 只有在“前缀和工件类型匹配”时才会被链接：
      - image 只吃 figure/image/diagram/chart caption
      - table 只吃 `Table ...` caption
    - caption 反向关系里的 `caption_for_role` 现在对表格会稳定落成 `table`
- [export.py](/Users/smy/project/book-agent/src/book_agent/services/export.py)
  - `_render_blocks_for_chapter()`
    - 当 `TABLE` block 已链接 caption block 时：
      - 独立 caption block 会被跳过
      - render 结果会保留原表格 artifact
      - target text 切换为 caption 的译文
    - 这样 merged / markdown export 会直接呈现为：
      - 译文表注
      - 原结构表格

新增回归：

- [test_pdf_support.py](/Users/smy/project/book-agent/tests/test_pdf_support.py)
  - `test_recovery_links_pdf_table_blocks_to_nearby_caption_blocks`
  - `test_recovery_does_not_link_table_caption_to_image_block`
  - `test_export_merges_linked_pdf_table_caption_into_single_render_block`
  - 同时复跑既有：
    - `test_recovery_links_pdf_image_blocks_to_nearby_caption_blocks`
    - `test_export_merges_linked_pdf_image_caption_into_single_render_block`

验证：

- `uv run python -m py_compile src/book_agent/domain/structure/pdf.py src/book_agent/services/export.py tests/test_pdf_support.py`
- `PYTHONWARNINGS='ignore::ResourceWarning' uv run python -m unittest tests.test_pdf_support.BasicPdfOutlineRecoveryTests.test_recovery_links_pdf_table_blocks_to_nearby_caption_blocks tests.test_pdf_support.BasicPdfOutlineRecoveryTests.test_recovery_does_not_link_table_caption_to_image_block tests.test_pdf_support.PdfDocumentImagePersistenceTests.test_export_merges_linked_pdf_table_caption_into_single_render_block`
  - `3 tests OK`
- `PYTHONWARNINGS='ignore::ResourceWarning' uv run python -m unittest tests.test_pdf_support.BasicPdfOutlineRecoveryTests.test_recovery_links_pdf_image_blocks_to_nearby_caption_blocks tests.test_pdf_support.PdfDocumentImagePersistenceTests.test_export_merges_linked_pdf_image_caption_into_single_render_block`
  - `2 tests OK`

当前判断：

- `image-caption` 已经从单一特例升级成带前缀约束的 captioned artifact 链路
- `table-caption` 终于进入 parser/export 主链路
- 下一刀最值得继续的是：
  - equation label / caption 邻接修复
  - table / figure / equation 邻接区域的 group-level repair
  - 或者 page-level layout reasons 驱动的 paper-specific rerun / repair

## Step 72. Phase A: Link Equation Labels/Captions to Equation Artifacts

时间：2026-03-19

目标：

- 把 `Equation 1. ...` / `Eq. (1): ...` 这类 label/caption 正式纳入 parser 主链路
- 让公式不再只以“裸公式 artifact”存在，而能带着对应 label/caption 一起进入 export
- 保持 caption 识别高精度，避免把正文里的 `Equation 1 shows ...` 误判成 caption

问题：

- Step 70 已经把 equation 本体恢复成了一级结构
- Step 71 又把 captioned artifact 链扩到 table
- 但 equation 仍然没有对应的 caption/link 规则
- 结果是：
  - 论文里的公式编号和说明仍然会和公式本体断开
  - export 只能显示“公式保持原样”，不能把 label/caption 一起折入单元

本轮实现：

- [pdf.py](/Users/smy/project/book-agent/src/book_agent/domain/structure/pdf.py)
  - 新增 `_EQUATION_CAPTION_PATTERN`
    - 当前只接受高精度形式：
      - `Equation 1. ...`
      - `Eq. (1): ...`
    - 暂不接收容易和正文混淆的宽松形式
  - `_CAPTION_PATTERN` 现在把 equation caption 也纳入 caption block 分类
  - `_caption_matches_artifact_role()`
    - 新增 `equation` 分支
  - `_link_artifact_captions()`
    - 通用 caption linking 范围从：
      - `image`
      - `table_like`
    - 扩到：
      - `image`
      - `table_like`
      - `equation`
- [export.py](/Users/smy/project/book-agent/src/book_agent/services/export.py)
  - 之前 Step 71 已经让 `TABLE / EQUATION` 在存在 linked caption block 时共用：
    - `translated_wrapper_with_preserved_artifact`
  - 所以这轮 equation parser link 一接通，export 就能直接把：
    - 公式原样 artifact
    - caption 的译文
    折叠成一个单元

新增回归：

- [test_pdf_support.py](/Users/smy/project/book-agent/tests/test_pdf_support.py)
  - `test_recovery_links_equation_blocks_to_nearby_equation_captions`
  - `test_export_merges_linked_pdf_equation_caption_into_single_render_block`
  - 同时复跑：
    - `test_export_treats_equation_blocks_as_equation_artifacts`
    - `test_recovery_links_pdf_image_blocks_to_nearby_caption_blocks`
    - `test_recovery_links_pdf_table_blocks_to_nearby_caption_blocks`

验证：

- `uv run python -m py_compile src/book_agent/domain/structure/pdf.py src/book_agent/services/export.py tests/test_pdf_support.py`
- `PYTHONWARNINGS='ignore::ResourceWarning' uv run python -m unittest tests.test_pdf_support.BasicPdfOutlineRecoveryTests.test_recovery_links_equation_blocks_to_nearby_equation_captions tests.test_pdf_support.PdfDocumentImagePersistenceTests.test_export_merges_linked_pdf_equation_caption_into_single_render_block tests.test_pdf_support.PdfDocumentImagePersistenceTests.test_export_treats_equation_blocks_as_equation_artifacts`
  - `3 tests OK`
- `PYTHONWARNINGS='ignore::ResourceWarning' uv run python -m unittest tests.test_pdf_support.BasicPdfOutlineRecoveryTests.test_recovery_links_pdf_image_blocks_to_nearby_caption_blocks tests.test_pdf_support.BasicPdfOutlineRecoveryTests.test_recovery_links_pdf_table_blocks_to_nearby_caption_blocks`
  - `2 tests OK`

当前判断：

- `image / table / equation` 三类核心工件都已经进入统一的 captioned artifact 链
- 但 equation caption 目前仍偏保守，只支持带明确编号与标点的 label/caption
- 下一刀更值得继续的是：
  - bare equation label（如 `(1)`）和 equation-following explanation 的安全识别
  - table / figure / equation 邻接区域的 group-level repair
  - 或者 page-level layout reasons 驱动的 paper-specific rerun / repair

## Step 73. Phase A: Add Captioned-Artifact Group Repair with Adjacent Explanation Context

时间：2026-03-19

目标：

- 把 `figure / table / equation + caption + 邻接解释正文` 变成统一的 group-level repair 能力
- 同时覆盖：
  - parser 持久化关系
  - export 对旧库的 fallback 推断与单元折叠
  - review / rerun 的 structure advisory
- 用 1 篇真实论文做重导出验收，但接受“旧库样本本身不一定命中新规则”的现实边界

问题：

- Step 71/72 只解决了 artifact 与 caption 的二元链接
- 论文里常见的第三个结构单元仍然缺失：
  - caption 后面紧邻的一段 explanation / interpretation prose
- 如果这段正文继续散落在 artifact 外部：
  - 最终 merged 阅读会显得结构割裂
  - review 也无法判断“caption 已有，但 group 仍不完整”
- 更现实的问题是：
  - 真实旧库里不可能都重跑 bootstrap
  - 所以仅做 parser 改动无法立刻改善已落库论文的导出质量

本轮实现：

- [artifact_grouping.py](/Users/smy/project/book-agent/src/book_agent/domain/structure/artifact_grouping.py)
  - 新增共享的 artifact grouping helper
  - 提供：
    - `normalize_artifact_role()`
    - `looks_like_artifact_group_context_text()`
    - `resolve_artifact_group_context_ids()`
  - 目标是让 export / review 可以直接对已落库 block 做 fallback 推断
- [pdf.py](/Users/smy/project/book-agent/src/book_agent/domain/structure/pdf.py)
  - 新增 `_link_artifact_group_contexts()`
    - 对已经 caption-linked 的 `image / table / equation`
    - 保守寻找同页、紧邻、几何上连续的 explanation paragraph
  - 新增 `_artifact_group_context_target()`
    - 要求：
      - 同页
      - reading order 相邻
      - vertical gap 小
      - 横向对齐不过分漂移
      - 文本必须像 explanation prose，而不是 caption / heading / code
  - 关系写入：
    - artifact 侧：
      - `artifact_group_context_source_anchors`
    - context 侧：
      - `artifact_group_source_anchor`
      - `artifact_group_role`
  - 顺手收紧了 caption lead 识别
    - 之前 `Table 1 reports ...` 这类正文会被过宽规则误判成 caption
    - 现在只接受更像真正 caption 的形式
- [bootstrap.py](/Users/smy/project/book-agent/src/book_agent/services/bootstrap.py)
  - `_materialize_pdf_block_relations()` 新增：
    - `artifact_group_context_source_anchors -> artifact_group_context_block_ids`
    - `artifact_group_source_anchor -> artifact_group_block_id`
- [export.py](/Users/smy/project/book-agent/src/book_agent/services/export.py)
  - `_render_blocks_for_chapter()`
    - 先调用 `resolve_artifact_group_context_ids()`
    - 对旧库没有 parser metadata 的场景，直接按几何与文本 cue 做 fallback 推断
  - 对 `image / table / equation`：
    - 当存在 grouped context 时：
      - context block 的 target sentences 合并进 artifact render block
      - context block 本身从独立 render block 中跳过
  - 这样即使历史库没重跑 parse，也能立即在 merged export 里看到 group repair
- [review.py](/Users/smy/project/book-agent/src/book_agent/services/review.py)
  - 新增 `ARTIFACT_GROUP_RECOVERY_REQUIRED`
  - 策略刻意保守：
    - 仅 `academic_paper`
    - 仅局部 `high_layout_risk_pages`
    - 仅 captioned artifact 缺少 grouped context 时发出
  - `root_cause_layer = STRUCTURE`
    - 因此现有 rule engine 会自动给出 `REPARSE_CHAPTER`

新增回归：

- [test_pdf_support.py](/Users/smy/project/book-agent/tests/test_pdf_support.py)
  - `test_recovery_links_pdf_table_blocks_to_adjacent_explanation_blocks`
  - `test_export_merges_linked_pdf_table_caption_and_adjacent_context_into_single_render_block`
  - `test_academic_paper_captioned_artifact_missing_group_context_creates_structure_issue`
- 同时复跑旧回归，确认不会打坏：
  - image caption link
  - table caption link
  - equation caption link
  - local high page layout risk advisory

验证：

- `uv run python -m py_compile src/book_agent/domain/structure/artifact_grouping.py src/book_agent/domain/structure/pdf.py src/book_agent/services/bootstrap.py src/book_agent/services/export.py src/book_agent/services/review.py tests/test_pdf_support.py`
- `PYTHONWARNINGS='ignore::ResourceWarning' uv run python -m unittest tests.test_pdf_support.BasicPdfOutlineRecoveryTests.test_recovery_links_pdf_table_blocks_to_adjacent_explanation_blocks tests.test_pdf_support.PdfDocumentImagePersistenceTests.test_export_merges_linked_pdf_table_caption_and_adjacent_context_into_single_render_block tests.test_pdf_support.PdfReviewTests.test_academic_paper_captioned_artifact_missing_group_context_creates_structure_issue tests.test_pdf_support.PdfDocumentImagePersistenceTests.test_export_merges_linked_pdf_table_caption_into_single_render_block tests.test_pdf_support.BasicPdfOutlineRecoveryTests.test_recovery_links_pdf_table_blocks_to_nearby_caption_blocks tests.test_pdf_support.BasicPdfOutlineRecoveryTests.test_recovery_links_equation_blocks_to_nearby_equation_captions`
  - `6 tests OK`
- `PYTHONWARNINGS='ignore::ResourceWarning' uv run python -m unittest tests.test_pdf_support.BasicPdfOutlineRecoveryTests.test_recovery_links_pdf_image_blocks_to_nearby_caption_blocks tests.test_pdf_support.BasicPdfOutlineRecoveryTests.test_recovery_does_not_link_table_caption_to_image_block tests.test_pdf_support.BasicPdfOutlineRecoveryTests.test_recovery_links_pdf_table_blocks_to_nearby_caption_blocks tests.test_pdf_support.BasicPdfOutlineRecoveryTests.test_recovery_links_equation_blocks_to_nearby_equation_captions tests.test_pdf_support.PdfDocumentImagePersistenceTests.test_export_merges_linked_pdf_image_caption_into_single_render_block tests.test_pdf_support.PdfDocumentImagePersistenceTests.test_export_merges_linked_pdf_table_caption_into_single_render_block tests.test_pdf_support.PdfDocumentImagePersistenceTests.test_export_merges_linked_pdf_equation_caption_into_single_render_block tests.test_pdf_support.PdfReviewTests.test_academic_paper_local_high_page_layout_risk_creates_low_advisory_issue`
  - `8 tests OK`

真实论文重导出验收：

- 基于旧库拷贝重导出：
  - 输入 DB：
    - [full.sqlite](/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-forming-teams-paper-v12-grouped-artifact-reexport/full.sqlite)
  - 输出：
    - [merged-document.md](/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-forming-teams-paper-v12-grouped-artifact-reexport/exports/b90a689e-bf00-5a3a-b1e3-0d5c88a12c1b/merged-document.md)
    - [merged-document.html](/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-forming-teams-paper-v12-grouped-artifact-reexport/exports/b90a689e-bf00-5a3a-b1e3-0d5c88a12c1b/merged-document.html)
- 这份 legacy DB 的现实情况：
  - persisted blocks 里只有 `1` 个 caption-linked artifact
  - `table/equation` caption link 尚未落库
  - 所以本轮 `grouped artifacts = 0`
- 但真实重导出仍然证明了两件事：
  - 新 export fallback 对旧库是兼容的，没有引入退化
  - 由于 caption lead 识别收紧，论文 merged 中 `Preserved artifacts` 从 `9 -> 7`
    - 说明至少有 2 处原本被当作独立 artifact 的内容已经回到了正常正文链

当前判断：

- group-level repair 的 parser / export / review 闭环已经建成
- 但这篇 legacy 论文库本身不是理想命中样本：
  - captioned artifact 数太少
  - table/equation caption link 还未持久化到旧 DB
- 所以下一刀最值得继续的是：
  - 用真实论文源 PDF 做 structure-only rebuild / refresh，把 table/equation caption link 写回库
  - 扩展 bare equation label / figure/table explanation cue
  - 再做一次基于 refreshed paper DB 的 merged re-export，对 group repair 做真正 corpus-level 验收

## Step 74-77: Real Paper Structure Refresh from Metadata-Only to Artifact-Carrying Refresh

这轮把 `document intelligence first` 往真正的真实论文 DB 闭环推进了 4 步。

### Step 74: flattened academic table detection no longer requires single-line blocks

问题：

- 真实论文第 6 页的 `Table 1 / Table 2` 表体并不是单行 block
- 它们在 parser 里已经被切成多行 `body`
- 旧逻辑只有 `len(nonempty_lines) == 1` 时才走 `_looks_like_flattened_table_text(...)`
- 结果：
  - `Table 1 / Table 2` 的 header/body 片段会被保留成普通正文
  - caption link / group repair 根本没有 artifact 可挂

实现：

- [pdf.py](/Users/smy/project/book-agent/src/book_agent/domain/structure/pdf.py)
  - `_looks_like_table(...)` 现在在常规 separator/numeric heuristics 失败后
  - 对多行 block 也会做 `flattened table` fallback
  - 要求仍然保守：
    - 多个 dense numeric lines
    - `± / [F, 357, 117] / ID = ... / header cue` 等强表格信号
- [test_pdf_support.py](/Users/smy/project/book-agent/tests/test_pdf_support.py)
  - `test_wrapped_numeric_academic_table_heuristics_detect_compressed_multiline_tables`
  - `test_wrapped_individual_accuracy_table_heuristics_detect_id_equals_layout`
  - 同时保住：
    - `test_wrapped_prose_heuristics_do_not_treat_acknowledgement_text_as_code_or_table`

验证：

- `3 tests OK`

### Step 75: late table promotion + adjacent table fragment merge

问题：

- 真实论文里即使 heuristic 已能判断“最终文本像表格”
- parser 主链路里仍会先把这些行当作 `body`
- 后续 merge 结束后，表格信号才在最终 block 文本里变得明显

实现：

- [pdf.py](/Users/smy/project/book-agent/src/book_agent/domain/structure/pdf.py)
  - 新增 `_promote_late_table_like_bodies(...)`
    - 对已经 merge 完成、仍是 `body` 的 block 做保守 table promotion
    - 只在：
      - 同页
      - `looks_like_table(...)`
      - 且附近存在 `table caption` 或已有 `table_like` 时触发
  - 新增 `_merge_adjacent_table_fragments(...)`
    - 把同页、几何连续、reading order 紧邻的 `table_like` 碎片合成一个完整表格工件
- [test_pdf_support.py](/Users/smy/project/book-agent/tests/test_pdf_support.py)
  - `test_late_table_promotion_merges_wrapped_table_fragments_before_caption_linking`

真实 parser probe 结果：

- 第 6 页 `Table 1`：
  - 现在变成 `table_like`
  - flags:
    - `late_table_like_promoted`
    - `table_fragments_merged`
    - `caption_linked`
    - `artifact_group_context_linked`
- 第 6 页 `Table 2`：
  - 现在也变成 `table_like`
  - flags:
    - `late_table_like_promoted`
    - `caption_linked`
    - `artifact_group_context_linked`

### Step 76: structure refresh can invalidate stale artifact fragments, but must do so selectively

第一次尝试：

- [pdf_structure_refresh.py](/Users/smy/project/book-agent/src/book_agent/services/pdf_structure_refresh.py)
  - 把所有 `missing_from_refreshed_parse` 的旧 block 一律 `invalidated`
- 同时让：
  - [bootstrap.py](/Users/smy/project/book-agent/src/book_agent/infra/repositories/bootstrap.py)
  - [export.py](/Users/smy/project/book-agent/src/book_agent/infra/repositories/export.py)
  - [review.py](/Users/smy/project/book-agent/src/book_agent/infra/repositories/review.py)
  - 只加载 `ArtifactStatus.ACTIVE`

结果：

- `v15` 证明方向是对的，但 invalidation 边界过宽
- frontmatter / intro / approach 里一些“尚未被新 parse 完整替代”的老 block 也被一并去掉了
- 所以这条策略需要收窄

第二次修正：

- `PdfStructureRefreshService._should_invalidate_unmatched_block(...)`
  - 现在只会 invalidated 明显的 stale table fragment：
    - 旧 block 本身是 `TABLE`
      或
    - 旧 block 文本仍然 `looks_like_table(...)`
  - 且同页 refreshed parse 里确实已经出现新的 `table_like / caption`
- 这样：
  - `Instances allocated ...`、`Classiﬁer F 97.73 ...` 这类历史碎片会被清掉
  - frontmatter / intro / equation prose 则不会被误删

对应测试：

- [test_pdf_support.py](/Users/smy/project/book-agent/tests/test_pdf_support.py)
  - `test_pdf_structure_refresh_updates_caption_links_and_group_context_in_place`
    - 现在额外验证：
      - stale table fragment 会被 invalidated
      - `BootstrapRepository.load_document_bundle(...)` 只返回 active blocks

### Step 77: structure refresh now updates artifact block_type/source_text, not just relation metadata

问题：

- `v16` 暴露出另一个关键缺口：
  - refresh 虽然写回了 `pdf_block_role / linked_caption / grouped context`
  - 但没有更新 `block_type/source_text/protected_policy`
- 结果：
  - DB 里依然保留旧 paragraph 文本
  - export 看不到真正恢复后的整张表

实现：

- [pdf_structure_refresh.py](/Users/smy/project/book-agent/src/book_agent/services/pdf_structure_refresh.py)
  - 对 refreshed 后属于：
    - `TABLE`
    - `CAPTION`
    - `EQUATION`
    - `IMAGE`
    - `FIGURE`
  - 的 block，refresh 现在会同步更新：
    - `block_type`
    - `source_text`
    - `normalized_text`
    - `protected_policy`
- 这让 legacy DB 里的旧 paragraph/table fragment 能真正被“新 artifact 版本”取代

对应测试：

- [test_pdf_support.py](/Users/smy/project/book-agent/tests/test_pdf_support.py)
  - 现在额外验证：
    - 一个旧的 `paragraph/body` block
    - refresh 后会升级成 `BlockType.TABLE`
    - 并切到 `ProtectedPolicy.PROTECT`

### 真实论文验收结果

最终有效版本：

- [v17 merged markdown](/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-forming-teams-paper-v17-structure-refresh-artifact-text/exports/b90a689e-bf00-5a3a-b1e3-0d5c88a12c1b/merged-document.md)
- [v17 merged html](/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-forming-teams-paper-v17-structure-refresh-artifact-text/exports/b90a689e-bf00-5a3a-b1e3-0d5c88a12c1b/merged-document.html)

对比旧版 `v13` 的关键变化：

- `Preserved artifacts: 7 -> 6`
- `Table 1`：
  - 不再散成 `paragraph + caption + stray body`
  - 现在变成：
    - 中文 caption / explanation prose
    - 一个完整的 source-preserved table artifact
- `Table 2`：
  - 不再出现历史碎片：
    - `Instances allocated ...`
    - `Classiﬁer F 97.73 ...`
  - 现在恢复成：
    - 中文 caption + explanation prose
    - 一个完整的 source-preserved table artifact
- frontmatter / intro 没有再被误删

现实边界：

- 由于这次是 `structure-only refresh`，没有重新跑翻译
- 所以 table artifact 目前仍是：
  - 中文 caption / explanation prose
  - 外加 source-preserved table body
- 这已经明显优于 `v13` 的“碎片化 paragraph / code / caption 分离”
- 但如果目标是“连表格内容也进行中文结构化重排”，那需要进入下一阶段：
  - table-specific semantic rendering
  - 或 table-aware retranslation / reconstruction

### Step 78: readable-rescue for scanned PDF book merged export

背景：

- 用户反馈旧版书籍产物：
  - [v26 merged markdown](/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-agentic-design-book-v26-prose-artifact-final-plus-appendix/exports/67283f52-b775-533f-988b-c7433a22a28f/merged-document.md)
  - [v26 merged html](/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-agentic-design-book-v26-prose-artifact-final-plus-appendix/exports/67283f52-b775-533f-988b-c7433a22a28f/merged-document.html)
- 主要问题：
  - 章序和目录标题错乱
  - 正文误判成 code / heading
  - 真章标题被误升成 code
  - PDF 图片裁图过宽，把周围正文带进去

实现：

- [export.py](/Users/smy/project/book-agent/src/book_agent/services/export.py)
  - 为 scanned book merged export 增加 `book readable rescue`：
    - pure `Chapter N.` 假 heading 直接丢弃
    - 句子型 heading 降级成 paragraph，并合并碎片
    - 真代码单行继续升成 code
    - 但真正的章标题 / 附录标题不再被 `single-line codeish` 误伤
  - 收紧 `single-line codeish`：
    - `with/while/for/if/...` 现在必须更像真实控制流代码
    - 避免把 `With all my love.` / `While the chapters are ordered ...` 这种英文正文当成代码
  - 对“短 source + 超长错位 target”增加保守清洗：
    - 这类明显错挂译文的 paragraph 会直接清空 target，回退到干净 source
  - PDF 直接裁图路径改成 layout-guided crop：
    - 优先围绕真正 image block
    - 否则利用 text block 收紧 crop 顶边，尽量剔除 figure 周围正文

对应测试：

- [test_persistence_and_review.py](/Users/smy/project/book-agent/tests/test_persistence_and_review.py)
  - `test_render_blocks_keep_cjk_chapter_heading_out_of_code_artifact`
  - `test_render_blocks_demote_short_prose_code_false_positive`
  - `test_short_source_paragraph_drops_suspicious_target_text`
  - 以及前面已有的：
    - `test_render_blocks_drop_pure_chapter_label_heading_false_positive`
    - `test_render_blocks_demote_sentence_like_heading_false_positive_and_merge_fragments`
    - `test_render_blocks_promote_single_line_codeish_heading_to_code_artifact`
    - `test_layout_guided_pdf_crop_bbox_prefers_seed_aligned_image_block`

定向验证：

- `uv run python -m py_compile src/book_agent/services/export.py tests/test_persistence_and_review.py`
- `PYTHONWARNINGS='ignore::ResourceWarning' uv run python -m unittest ...`
  - `7 tests OK`

真实产物：

- [v30 merged markdown](/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-agentic-design-book-v30-readable-rescue-final/exports/67283f52-b775-533f-988b-c7433a22a28f/merged-document.md)
- [v30 merged html](/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-agentic-design-book-v30-readable-rescue-final/exports/67283f52-b775-533f-988b-c7433a22a28f/merged-document.html)
- [v30 report](/Users/smy/project/book-agent/artifacts/real-book-live/deepseek-agentic-design-book-v30-readable-rescue-final/report.json)

可见改善：

- 目录中的 `第十七章` 不再跑到前言分组里
- `Chapter 5` 恢复为：
  - `第五章：工具使用（函数调用）`
  - 不再被 `工具使用模式概述` 顶替
- `While the chapters are ordered ...` 已恢复成正常正文段落
- `With all my love.` 不再显示错位中文，而是回退成干净原文
- `代码保持原样` 数量：
  - `v26: 182`
  - `v30: 151`
- PDF 图片资产对比：
  - 与 `v26` 同名 `23` 张图里，有 `22` 张发生了二进制变化
  - 说明新裁图逻辑已真正生效，而不是复用旧图

当前边界：

- 这版已经显著提升“可读性”，但还不是结构完美版
- 目录里仍有少量上游 OCR / 分章残留标题，例如：
  - `第12章：异常处理与`
  - `附录B - AI代理交互：`
- 这类问题更像 parser / structure refresh 上游噪声，下一轮应继续从 `document intelligence first` 主线往上收口

### Step 79: real technical book gate before full-book run

背景：

- 用户提供新的真实技术书样本：
  - `/Users/smy/Desktop/Building Applications with AI Agents Designing and Implementing Multiagent Systems (Michael Albada) (z-library.sk, 1lib.sk, z-lib.sk).pdf`
- 新要求：
  - 在跑整本书之前，先做单章测试
  - 先把审查版结果交给用户确认
  - 已知重点问题：
    - 图片裁图范围不准确
    - 代码段、引用段等特殊部分识别还要继续提升

这轮推进：

- [pdf.py](/Users/smy/project/book-agent/src/book_agent/domain/structure/pdf.py)
  - 收紧 `_looks_like_reference_entry(...)`
    - 出版社联系页 / 社媒 / errata URL 不再被误判成 references
  - 为带 outline、仅局部多栏的长篇技术书增加 `outlined_book` lane
    - `layout_risk` 可从 `high -> medium`
    - 不再被 `P1-A only supports ... layout_risk=high` 挡在 bootstrap 门外
  - `academic_heading` 候选只对 `academic_paper` lane 生效
    - 技术书每章内部的 `Conclusion`、局部小节不再被误升成顶层章节
  - 新增 book top-level outline filter
    - 有真实 `Chapter N / Part / Appendix` 顶层 outline 时
    - 会自动过滤 `Conclusion`、`How to Contact Us`、`O’Reilly Online Learning` 这类辅助节点
  - 新增 mixed code/prose split
    - `body` block 若以前缀代码行开头、后面再接正文
    - 会拆成 `code_like + body`
    - 避免代码示例继续被整块当 paragraph 翻译

对应测试：

- [test_pdf_support.py](/Users/smy/project/book-agent/tests/test_pdf_support.py)
  - `test_reference_entry_ignores_publisher_contact_and_social_lines`
  - `test_profiler_downgrades_outlined_localized_multi_column_book_to_medium_risk`
  - `test_bootstrap_pipeline_keeps_book_conclusion_and_contact_pages_out_of_top_level_chapters`
  - `test_recovery_splits_leading_code_prefix_from_mixed_body_block`
  - 回归保护：
    - `test_bootstrap_pipeline_supports_academic_paper_pdf_lane`

定向验证：

- `uv run python -m py_compile src/book_agent/domain/structure/pdf.py tests/test_pdf_support.py`
- `PYTHONWARNINGS='ignore::ResourceWarning' uv run python -m unittest ...`
  - `5 tests OK`

真实样本验证：

- 新书 smoke：
  - [v2 smoke report](/Users/smy/project/book-agent/artifacts/real-book-live/building-applications-with-ai-agents-v2-smoke/report.json)
- 关键变化：
  - `layout_risk: high -> medium`
  - `recovery_lane: outlined_book`
  - bootstrap `failed -> succeeded`
  - `chapter_count: 38 -> 20`
  - 主体章节骨架已恢复为：
    - `Preface`
    - `CHAPTER 1 ...`
    - ...
    - `CHAPTER 13 ...`
    - `Glossary / Index / About the Author / Colophon`
- 仍待继续优化的尾部点：
  - `Copyright` 目前仍被当成独立 chapter
  - `Glossary / About the Author / Colophon` 还没有 fully special-section 化

单章 gate：

- 当前审查章选择：
  - `Chapter 5 Orchestration`
  - 理由：
    - 同时覆盖 `image + code + equation`
    - 更适合在整本之前验证 parser/export 质量
- 单章 smoke 重启到新代码路径：
  - [v4 chapter smoke report](/Users/smy/project/book-agent/artifacts/real-book-live/building-applications-with-ai-agents-v4-ch5-review-smoke-codefix/chapter-smoke.report.json)
- 中途发现：
  - 如果沿用修复前代码直接跑 99 个 packets，会在旧 code-splitting 质量上继续烧 token
  - 因此已中断旧的 `v3`，改为在新 parser 代码上重跑 `v4`

当前状态：

- 新书已经从“无法 bootstrap”进入“可做单章真实验收”
- `Chapter 4`/`Chapter 5` 中，代码块开始从 paragraph 里长出来：
  - `CHAPTER 4 Tool Use`
    - `code: 2`
  - `CHAPTER 5 Orchestration`
    - `code: 2`
    - `image: 8`
    - `equation: 1`
- 但代码识别还不是最终形态：
  - 仍有部分 code block 带着 trailing prose
  - 图片裁图质量也还需要通过单章导出继续验收
