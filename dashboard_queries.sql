-- Dashboard retrieval queries for the UUID schema.
-- These queries are written to support the "resume where I left off" experience.

PRAGMA foreign_keys = ON;

-- 1. Find learner by email
SELECT
    learner_id,
    email,
    full_name,
    age_group,
    role,
    preferred_language,
    created_at,
    updated_at
FROM learners
WHERE email = :email;

-- 2. Load learner preferences
SELECT
    learner_id,
    content_format,
    explanation_style,
    quiz_style,
    learning_pace,
    session_length,
    feedback_style,
    accessibility_notes,
    updated_at
FROM learner_preferences
WHERE learner_id = :learner_id;

-- 3. Load active subject profiles
SELECT
    lsp.profile_id,
    lsp.learner_id,
    lsp.subject_id,
    s.subject_name,
    lsp.active_path_id,
    lsp.current_topic_id,
    lsp.goal_type,
    lsp.current_level,
    lsp.target_level,
    lsp.status,
    lsp.last_assessed_score,
    lsp.mastery_score,
    lsp.confidence_score,
    lsp.path_completion_pct,
    lsp.completed_step_count,
    lsp.total_step_count,
    lsp.next_review_at,
    lsp.last_activity_at
FROM learner_subject_profiles lsp
JOIN subjects s ON s.subject_id = lsp.subject_id
WHERE lsp.learner_id = :learner_id
ORDER BY lsp.updated_at DESC;

-- 4. Load active roadmap for one subject
SELECT
    path_id,
    learner_id,
    subject_id,
    root_topic_id,
    path_title,
    path_status,
    target_outcome,
    total_steps,
    completed_steps,
    created_at,
    updated_at,
    last_accessed_at
FROM learning_paths
WHERE learner_id = :learner_id
  AND subject_id = :subject_id
  AND path_status = 'active'
ORDER BY last_accessed_at DESC, updated_at DESC
LIMIT 1;

-- 5. Load roadmap steps with completion state
SELECT
    step_id,
    path_id,
    topic_id,
    resource_id,
    chunk_id,
    content_version,
    step_order,
    step_title,
    step_description,
    step_status,
    estimated_minutes,
    actual_minutes,
    started_at,
    completed_at,
    created_at,
    updated_at
FROM learning_path_steps
WHERE path_id = :path_id
ORDER BY step_order ASC;

-- 6. Load latest quiz attempt
SELECT
    attempt_id,
    learner_id,
    subject_id,
    path_id,
    step_id,
    quiz_type,
    difficulty_level,
    score,
    total_questions,
    correct_answers,
    completion_status,
    mastery_delta,
    started_at,
    completed_at
FROM quiz_attempts
WHERE learner_id = :learner_id
  AND subject_id = :subject_id
ORDER BY COALESCE(completed_at, started_at) DESC
LIMIT 1;

-- 7. Load quiz responses for latest attempt
SELECT
    response_id,
    attempt_id,
    question_id,
    question_text,
    selected_answer,
    correct_answer,
    is_correct,
    time_taken_seconds,
    created_at
FROM quiz_responses
WHERE attempt_id = :attempt_id
ORDER BY created_at ASC;

-- 8. Load mastery summary for dashboard
SELECT
    mastery_id,
    learner_id,
    subject_id,
    topic_id,
    mastery_probability,
    last_assessed_score,
    review_due_at,
    last_practiced_at,
    mastery_status,
    created_at,
    updated_at
FROM topic_mastery
WHERE learner_id = :learner_id
  AND subject_id = :subject_id
ORDER BY mastery_probability ASC, updated_at DESC;

-- 9. Load the exact content for the active step
SELECT
    lps.step_id,
    lps.step_title,
    lps.step_status,
    lps.resource_id,
    lps.chunk_id,
    lps.content_version,
    cr.title AS source_title,
    cr.vector_doc_id,
    cr.vector_collection,
    cr.content_version AS resource_version,
    cc.chunk_text,
    cc.vector_chunk_id,
    cc.chunk_version
FROM learning_path_steps lps
LEFT JOIN content_resources cr ON cr.resource_id = lps.resource_id
LEFT JOIN content_chunks cc ON cc.chunk_id = lps.chunk_id
WHERE lps.step_id = :step_id;

-- 10. Load a cached learner-specific rendered view if available
SELECT
    view_id,
    learner_id,
    path_id,
    step_id,
    topic_id,
    source_resource_id,
    source_chunk_id,
    source_content_version,
    rendered_title,
    rendered_summary,
    rendered_content,
    rendered_format,
    reading_level,
    content_hash,
    view_status,
    rendered_at,
    updated_at
FROM learner_content_views
WHERE learner_id = :learner_id
  AND step_id = :step_id
  AND view_status = 'active'
ORDER BY updated_at DESC
LIMIT 1;

