-- M0-2: existing curated questions stay usable; every new question starts in draft.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'course_kb' AND column_name = 'status'
    ) THEN
        ALTER TABLE course_kb ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'published';
        ALTER TABLE course_kb ALTER COLUMN status SET DEFAULT 'draft';
    END IF;
END $$;

ALTER TABLE course_kb ADD COLUMN IF NOT EXISTS reviewed_by VARCHAR(64);
ALTER TABLE course_kb ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS ix_course_kb_status ON course_kb (tenant_id, status);

-- M2-12 reuses wrong_questions.client_id (exam:<submission_item_id>) and its
-- existing soft-delete columns, so no wrongbook table change is required.
