# Test baseline (pre-refactor, commit 2890024)

Collected 2026-09-14 by running each test file in its own process (full single-process run segfaults in test_api_workflow).
Totals over files that completed: **1043 passed / 44 failed / 5 errors**; 2 files fail at collection; test_api_workflow segfaults.

Any failure NOT listed here is a regression.

## tests/test_api_deps.py

- summary: `2 failed, 2 passed in 12.22s`
- FAILED ApiDepsTests::test_non_sqlite_database_error_message_keeps_pg_guidance
- FAILED ApiDepsTests::test_sqlite_database_locked_error_message_is_specific

## tests/test_api_workflow.py

- summary (original): segfault after 43 tests (StaticPool shared one sqlite3 connection across executor threads)
- after P0.1/P0.2 (file SQLite without StaticPool, executor uses resolved worker): `11 failed, 43 passed`
- pre-existing failures (all verified failing on the original code as well):
- FAILED test_export_download_bundles_multi_chapter_exports_as_zip
- FAILED test_export_download_includes_epub_asset_sidecars
- FAILED test_failed_review_persists_partial_repair_progress
- FAILED test_image_only_cover_chapter_does_not_block_review_or_export
- FAILED test_merged_html_export_backfills_translated_document_title_and_uses_human_download_name
- FAILED test_merged_html_export_renders_prose_and_code_with_different_modes
- FAILED test_merged_html_export_renders_structured_artifacts_with_special_modes
- FAILED test_retry_run_recovers_stale_failed_stage_run_still_marked_running
- FAILED test_translate_executor_defaults_to_single_worker_on_sqlite_without_budget_override
- FAILED test_translate_full_run_executes_review_and_exports_in_background (run now succeeds; merged export not ready)
- FAILED test_translate_full_run_repairs_document_blockers_before_export (now times out in blocker repair loop)

## tests/test_app_runtime.py

- summary: `1 error in 12.40s`
ERROR tests/test_app_runtime.py

## tests/test_forge_migrate.py

- summary: `1 failed, 1 passed in 3.46s`
- FAILED ForgeMigrateTests::test_migration_imports_state_and_syncs_framework_docs

## tests/test_minimal_pipeline_smoke.py

- summary: `2 errors in 17.19s`
- ERROR MinimalPipelineSmokeTests::test_minimal_epub_full_pipeline_smoke
- ERROR MinimalPipelineSmokeTests::test_minimal_pdf_full_pipeline_smoke

## tests/test_minimal_pipeline_smoke_script.py

- summary: `1 failed in 17.10s`
- FAILED MinimalPipelineSmokeScriptTests::test_script_runs_all_cases_and_writes_report

## tests/test_patch_review.py

- summary: `5 failed, 5 errors in 21.97s`
- ERROR PatchReviewServiceTests::test_approve_emits_event_and_stamps_fields
- ERROR PatchReviewServiceTests::test_empty_reviewer_rejected
- ERROR PatchReviewServiceTests::test_list_and_get_roundtrip
- ERROR PatchReviewServiceTests::test_reject_terminal
- ERROR PublishGateTests::test_validated_patch_awaiting_review_is_blocked
- FAILED PatchReviewServiceTests::test_approve_emits_event_and_stamps_fields
- FAILED PatchReviewServiceTests::test_empty_reviewer_rejected
- FAILED PatchReviewServiceTests::test_list_and_get_roundtrip
- FAILED PatchReviewServiceTests::test_reject_terminal
- FAILED PublishGateTests::test_validated_patch_awaiting_review_is_blocked

## tests/test_pdf_scan_corpus_acceptance.py

- summary: `2 failed in 0.58s`
- FAILED PdfScanCorpusAcceptanceTests::test_locked_larger_corpus_acceptance_passes_phase3_thresholds
- FAILED PdfScanCorpusAcceptanceTests::test_locked_larger_corpus_acceptance_snapshot_records_frozen_baseline_values

## tests/test_pdf_support.py

- summary: `14 failed, 177 passed, 20 warnings, 6 subtests passed in 1614.59s (0:26:54)`
- FAILED BasicPdfOutlineRecoveryTests::test_recovery_prefers_academic_heading_split_for_embedded_numbered_section
- FAILED BasicPdfOutlineRecoveryTests::test_uv_surya_ocr_runner_writes_status_snapshots_during_execution
- FAILED PdfBootstrapPipelineTests::test_bootstrap_pipeline_classifies_page_families_and_splits_special_sections
- FAILED PdfBootstrapPipelineTests::test_bootstrap_pipeline_cleans_noisy_inline_academic_section_headings
- FAILED PdfBootstrapPipelineTests::test_bootstrap_pipeline_labels_frontmatter_before_first_intro_chapter
- FAILED PdfBootstrapPipelineTests::test_bootstrap_pipeline_recovers_chapters_when_intro_cue_is_embedded_in_body_block
- FAILED PdfBootstrapPipelineTests::test_bootstrap_pipeline_recovers_inline_academic_section_headings
- FAILED PdfBootstrapPipelineTests::test_bootstrap_pipeline_repairs_broken_academic_heading_tail_before_body
- FAILED PdfBootstrapPipelineTests::test_bootstrap_pipeline_uses_toc_entries_to_recover_chapters
- FAILED PdfBootstrapPipelineTests::test_parser_recovers_title_and_references_for_single_column_research_paper
- FAILED PdfBootstrapPipelineTests::test_recovery_merges_cross_page_code_continuations_across_footer_separators
- FAILED PdfBootstrapPipelineTests::test_toc_recovery_reconciles_printed_page_numbers_with_pdf_offset
- FAILED PdfProfilerTests::test_bootstrap_pipeline_resolves_document_book_title_from_source_filename_for_outlined_book
- FAILED PdfReviewTests::test_medium_risk_pdf_creates_structure_review_issue

## tests/test_persistence_and_review.py

- summary: `11 failed, 128 passed in 105.29s (0:01:45)`
- FAILED PersistenceAndReviewTests::test_export_service_rebuilt_epub_rejects_non_epub_source_document
- FAILED PersistenceAndReviewTests::test_export_service_reflow_splits_inline_call_keyword_arguments_after_open_paren
- FAILED PersistenceAndReviewTests::test_render_blocks_demote_reference_listing_code_block_and_preserve_entry_breaks
- FAILED PersistenceAndReviewTests::test_render_blocks_promote_wrapped_shell_command_to_code_and_split_trailing_prose
- FAILED PersistenceAndReviewTests::test_review_skips_image_only_cover_packet_missing_title_context_failure
- FAILED PersistenceAndReviewTests::test_workflow_exports_merged_markdown_with_assets
- FAILED PersistenceAndReviewTests::test_workflow_review_auto_executes_multi_packet_unlocked_concept_followups_without_chapter_rerun
- FAILED PersistenceAndReviewTests::test_workflow_review_auto_executes_packet_scoped_stale_brief_followups_when_concept_autolock_fails
- FAILED PersistenceAndReviewTests::test_workflow_review_auto_executes_single_packet_unlocked_concept_followups
- FAILED PersistenceAndReviewTests::test_workflow_review_does_not_run_stale_brief_followup_when_concept_autolock_succeeds
- FAILED PersistenceAndReviewTests::test_workflow_review_unlocked_concept_followup_uses_default_concept_resolver

## tests/test_phase3_integration_gate.py

- summary: `2 failed, 1 passed in 9.38s`
- FAILED Phase3IntegrationGateTests::test_phase3_integration_gate_keeps_locked_larger_corpus_acceptance_green
- FAILED Phase3IntegrationGateTests::test_phase3_integration_snapshot_records_lane_acceptance_matrix_and_contract_coverage

## tests/test_req_ex_02_export_misrouting_self_heal.py

- summary: `1 failed in 12.39s`
- FAILED ReqEx02ExportMisroutingSelfHealTests::test_req_ex_02_export_misrouting_self_heal_closes_loop

## tests/test_run_execution.py

- summary: `3 failed, 20 passed in 5.03s`
- FAILED RunExecutionServiceTests::test_process_translate_stage_cancels_stale_legacy_translate_item_and_advances_to_review
- FAILED RunExecutionServiceTests::test_recover_export_misrouting_rebinds_run_to_effective_bundle_revision
- FAILED RunExecutionServiceTests::test_run_execution_success_lifecycle_updates_usage_and_terminal_state

## tests/test_translate_agent_benchmark_execution.py

- summary: `5 warnings, 1 error in 0.24s`
ERROR tests/test_translate_agent_benchmark_execution.py

## tests/test_translation_worker_abstraction.py

- summary: `2 failed, 67 passed in 22.76s`
- FAILED TranslationWorkerAbstractionTests::test_packet_experiment_scan_ranks_memory_rich_packets_first
- FAILED TranslationWorkerAbstractionTests::test_packet_experiment_service_dry_run_exports_prompt_without_worker_output

