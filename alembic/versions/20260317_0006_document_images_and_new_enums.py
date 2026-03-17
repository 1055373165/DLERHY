"""Add document_images table and extend source_type/block_type enums.

Revision ID: 20260317_0006
Revises: 20260315_0005
Create Date: 2026-03-17 01:30:00
"""

from alembic import op


revision = "20260317_0006"
down_revision = "20260315_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Extend source_type check constraint to include 'pdf_mixed' ---
    op.execute("ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_source_type_check;")
    op.execute(
        """
        ALTER TABLE documents
        ADD CONSTRAINT documents_source_type_check
        CHECK (source_type IN ('epub', 'pdf_text', 'pdf_scan', 'pdf_mixed'));
        """
    )

    # --- Extend block_type check constraint to include 'figure', 'equation', 'image' ---
    op.execute("ALTER TABLE blocks DROP CONSTRAINT IF EXISTS blocks_block_type_check;")
    op.execute(
        """
        ALTER TABLE blocks
        ADD CONSTRAINT blocks_block_type_check
        CHECK (block_type IN (
            'heading', 'paragraph', 'quote', 'footnote', 'caption',
            'code', 'table', 'list_item', 'figure', 'equation', 'image'
        ));
        """
    )

    # --- Create document_images table ---
    op.execute(
        """
        CREATE TABLE document_images (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            block_id UUID REFERENCES blocks(id) ON DELETE SET NULL,
            page_number INTEGER NOT NULL,
            image_type TEXT NOT NULL,
            storage_path TEXT NOT NULL,
            bbox_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            ocr_text TEXT,
            latex TEXT,
            alt_text TEXT,
            width_px INTEGER,
            height_px INTEGER,
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        "CREATE INDEX ix_document_images_document_id ON document_images (document_id);"
    )
    op.execute(
        "CREATE INDEX ix_document_images_block_id ON document_images (block_id);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS document_images;")

    # Restore original block_type check constraint
    op.execute("ALTER TABLE blocks DROP CONSTRAINT IF EXISTS blocks_block_type_check;")
    op.execute(
        """
        ALTER TABLE blocks
        ADD CONSTRAINT blocks_block_type_check
        CHECK (block_type IN (
            'heading', 'paragraph', 'quote', 'footnote', 'caption',
            'code', 'table', 'list_item'
        ));
        """
    )

    # Restore original source_type check constraint
    op.execute("ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_source_type_check;")
    op.execute(
        """
        ALTER TABLE documents
        ADD CONSTRAINT documents_source_type_check
        CHECK (source_type IN ('epub', 'pdf_text', 'pdf_scan'));
        """
    )
