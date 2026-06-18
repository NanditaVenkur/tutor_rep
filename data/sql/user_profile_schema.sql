-- User profile schema for the adaptive learning system
-- UUID-based v2 schema with required email identity

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS learners (
    learner_id TEXT PRIMARY KEY NOT NULL,
    email TEXT NOT NULL UNIQUE,
    full_name TEXT NOT NULL,
    age_group TEXT,
    role TEXT,
    preferred_language TEXT NOT NULL DEFAULT 'English',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS learner_preferences (
    preference_id TEXT PRIMARY KEY NOT NULL,
    learner_id TEXT NOT NULL UNIQUE,
    content_format TEXT NOT NULL DEFAULT 'mixed',
    explanation_style TEXT NOT NULL DEFAULT 'step_by_step',
    quiz_style TEXT NOT NULL DEFAULT 'mixed',
    learning_pace TEXT NOT NULL DEFAULT 'normal',
    session_length TEXT NOT NULL DEFAULT '30_min',
    feedback_style TEXT NOT NULL DEFAULT 'immediate',
    accessibility_notes TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (learner_id) REFERENCES learners(learner_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS subjects (
    subject_id TEXT PRIMARY KEY NOT NULL,
    subject_name TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS topics (
    topic_id TEXT PRIMARY KEY NOT NULL,
    subject_id TEXT NOT NULL,
    topic_name TEXT NOT NULL,
    topic_description TEXT,
    prerequisite_topic_id TEXT,
    topic_order INTEGER NOT NULL DEFAULT 0,
    estimated_minutes INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (subject_id, topic_name),
    FOREIGN KEY (subject_id) REFERENCES subjects(subject_id) ON DELETE CASCADE,
    FOREIGN KEY (prerequisite_topic_id) REFERENCES topics(topic_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_topics_subject_order
ON topics (subject_id, topic_order);

CREATE TABLE IF NOT EXISTS learner_subject_profiles (
    profile_id TEXT PRIMARY KEY NOT NULL,
    learner_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    active_path_id TEXT,
    current_topic_id TEXT,
    goal_type TEXT,
    current_level TEXT DEFAULT 'new',
    target_level TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    last_assessed_score REAL DEFAULT 0,
    mastery_score REAL DEFAULT 0,
    confidence_score REAL DEFAULT 0,
    path_completion_pct REAL DEFAULT 0,
    completed_step_count INTEGER DEFAULT 0,
    total_step_count INTEGER DEFAULT 0,
    next_review_at TEXT,
    last_activity_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (learner_id, subject_id),
    FOREIGN KEY (learner_id) REFERENCES learners(learner_id) ON DELETE CASCADE,
    FOREIGN KEY (subject_id) REFERENCES subjects(subject_id) ON DELETE CASCADE,
    FOREIGN KEY (active_path_id) REFERENCES learning_paths(path_id) ON DELETE SET NULL,
    FOREIGN KEY (current_topic_id) REFERENCES topics(topic_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS learning_paths (
    path_id TEXT PRIMARY KEY NOT NULL,
    learner_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    root_topic_id TEXT,
    path_title TEXT NOT NULL,
    path_status TEXT NOT NULL DEFAULT 'active',
    target_outcome TEXT,
    total_steps INTEGER NOT NULL DEFAULT 0,
    completed_steps INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_accessed_at TEXT,
    FOREIGN KEY (learner_id) REFERENCES learners(learner_id) ON DELETE CASCADE,
    FOREIGN KEY (subject_id) REFERENCES subjects(subject_id) ON DELETE CASCADE,
    FOREIGN KEY (root_topic_id) REFERENCES topics(topic_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_learning_paths_learner_subject
ON learning_paths (learner_id, subject_id);

CREATE TABLE IF NOT EXISTS learning_path_steps (
    step_id TEXT PRIMARY KEY NOT NULL,
    path_id TEXT NOT NULL,
    topic_id TEXT,
    resource_id TEXT,
    chunk_id TEXT,
    content_version INTEGER NOT NULL DEFAULT 1,
    step_order INTEGER NOT NULL,
    step_title TEXT NOT NULL,
    step_description TEXT,
    step_status TEXT NOT NULL DEFAULT 'not_started',
    estimated_minutes INTEGER DEFAULT 0,
    actual_minutes INTEGER DEFAULT 0,
    started_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (path_id) REFERENCES learning_paths(path_id) ON DELETE CASCADE,
    FOREIGN KEY (topic_id) REFERENCES topics(topic_id) ON DELETE SET NULL,
    FOREIGN KEY (resource_id) REFERENCES content_resources(resource_id) ON DELETE SET NULL,
    FOREIGN KEY (chunk_id) REFERENCES content_chunks(chunk_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_path_steps_path_order
ON learning_path_steps (path_id, step_order);

CREATE INDEX IF NOT EXISTS idx_path_steps_path_status
ON learning_path_steps (path_id, step_status);

CREATE TABLE IF NOT EXISTS content_resources (
    resource_id TEXT PRIMARY KEY NOT NULL,
    subject_id TEXT NOT NULL,
    topic_id TEXT,
    resource_type TEXT NOT NULL DEFAULT 'note',
    title TEXT NOT NULL,
    source_name TEXT,
    source_uri TEXT,
    vector_collection TEXT NOT NULL DEFAULT 'content_chunks',
    vector_doc_id TEXT,
    content_version INTEGER NOT NULL DEFAULT 1,
    content_hash TEXT,
    source_revision TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    embedding_ref TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (subject_id) REFERENCES subjects(subject_id) ON DELETE CASCADE,
    FOREIGN KEY (topic_id) REFERENCES topics(topic_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_content_resources_subject_topic
ON content_resources (subject_id, topic_id);

CREATE TABLE IF NOT EXISTS content_chunks (
    chunk_id TEXT PRIMARY KEY NOT NULL,
    resource_id TEXT NOT NULL,
    chunk_order INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    vector_chunk_id TEXT,
    chunk_hash TEXT,
    chunk_version INTEGER NOT NULL DEFAULT 1,
    embedding_ref TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (resource_id) REFERENCES content_resources(resource_id) ON DELETE CASCADE,
    UNIQUE (resource_id, chunk_order)
);

CREATE INDEX IF NOT EXISTS idx_content_chunks_resource_order
ON content_chunks (resource_id, chunk_order);

CREATE TABLE IF NOT EXISTS learner_content_views (
    view_id TEXT PRIMARY KEY NOT NULL,
    learner_id TEXT NOT NULL,
    path_id TEXT,
    step_id TEXT,
    topic_id TEXT,
    source_resource_id TEXT,
    source_chunk_id TEXT,
    source_content_version INTEGER NOT NULL DEFAULT 1,
    rendered_title TEXT,
    rendered_summary TEXT,
    rendered_content TEXT,
    rendered_format TEXT,
    reading_level TEXT,
    content_hash TEXT,
    view_status TEXT NOT NULL DEFAULT 'active',
    rendered_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (learner_id) REFERENCES learners(learner_id) ON DELETE CASCADE,
    FOREIGN KEY (path_id) REFERENCES learning_paths(path_id) ON DELETE SET NULL,
    FOREIGN KEY (step_id) REFERENCES learning_path_steps(step_id) ON DELETE SET NULL,
    FOREIGN KEY (topic_id) REFERENCES topics(topic_id) ON DELETE SET NULL,
    FOREIGN KEY (source_resource_id) REFERENCES content_resources(resource_id) ON DELETE SET NULL,
    FOREIGN KEY (source_chunk_id) REFERENCES content_chunks(chunk_id) ON DELETE SET NULL,
    UNIQUE (learner_id, step_id, source_chunk_id)
);

CREATE INDEX IF NOT EXISTS idx_learner_content_views_learner_step
ON learner_content_views (learner_id, step_id);

CREATE INDEX IF NOT EXISTS idx_learner_content_views_source
ON learner_content_views (source_resource_id, source_chunk_id);

CREATE TABLE IF NOT EXISTS study_sessions (
    session_id TEXT PRIMARY KEY NOT NULL,
    learner_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    path_id TEXT,
    session_type TEXT NOT NULL DEFAULT 'study',
    session_status TEXT NOT NULL DEFAULT 'started',
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at TEXT,
    session_summary TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (learner_id) REFERENCES learners(learner_id) ON DELETE CASCADE,
    FOREIGN KEY (subject_id) REFERENCES subjects(subject_id) ON DELETE CASCADE,
    FOREIGN KEY (path_id) REFERENCES learning_paths(path_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_study_sessions_learner_subject
ON study_sessions (learner_id, subject_id);

CREATE TABLE IF NOT EXISTS quiz_attempts (
    attempt_id TEXT PRIMARY KEY NOT NULL,
    learner_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    path_id TEXT,
    step_id TEXT,
    quiz_type TEXT NOT NULL DEFAULT 'diagnostic',
    difficulty_level TEXT,
    score REAL DEFAULT 0,
    total_questions INTEGER DEFAULT 0,
    correct_answers INTEGER DEFAULT 0,
    completion_status TEXT NOT NULL DEFAULT 'in_progress',
    mastery_delta REAL DEFAULT 0,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT,
    FOREIGN KEY (learner_id) REFERENCES learners(learner_id) ON DELETE CASCADE,
    FOREIGN KEY (subject_id) REFERENCES subjects(subject_id) ON DELETE CASCADE,
    FOREIGN KEY (path_id) REFERENCES learning_paths(path_id) ON DELETE SET NULL,
    FOREIGN KEY (step_id) REFERENCES learning_path_steps(step_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS quiz_responses (
    response_id TEXT PRIMARY KEY NOT NULL,
    attempt_id TEXT NOT NULL,
    question_id TEXT,
    question_text TEXT,
    selected_answer TEXT,
    correct_answer TEXT,
    is_correct INTEGER NOT NULL DEFAULT 0,
    time_taken_seconds REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (attempt_id) REFERENCES quiz_attempts(attempt_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_learner_preferences_learner_id
ON learner_preferences (learner_id);

CREATE INDEX IF NOT EXISTS idx_profiles_learner_subject
ON learner_subject_profiles (learner_id, subject_id);

CREATE INDEX IF NOT EXISTS idx_quiz_attempts_learner_subject
ON quiz_attempts (learner_id, subject_id);

CREATE INDEX IF NOT EXISTS idx_quiz_responses_attempt_id
ON quiz_responses (attempt_id);

CREATE TABLE IF NOT EXISTS study_requests (
    request_id TEXT PRIMARY KEY NOT NULL,
    learner_id TEXT,
    topic TEXT NOT NULL,
    study_mode TEXT NOT NULL DEFAULT 'roadmap',
    familiarity TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (learner_id) REFERENCES learners(learner_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_study_requests_learner_id
ON study_requests (learner_id);

CREATE TABLE IF NOT EXISTS topic_mastery (
    mastery_id TEXT PRIMARY KEY NOT NULL,
    learner_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    topic_id TEXT NOT NULL,
    mastery_probability REAL NOT NULL DEFAULT 0,
    last_assessed_score REAL DEFAULT 0,
    review_due_at TEXT,
    last_practiced_at TEXT,
    mastery_status TEXT NOT NULL DEFAULT 'unknown',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (learner_id, topic_id),
    FOREIGN KEY (learner_id) REFERENCES learners(learner_id) ON DELETE CASCADE,
    FOREIGN KEY (subject_id) REFERENCES subjects(subject_id) ON DELETE CASCADE,
    FOREIGN KEY (topic_id) REFERENCES topics(topic_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_topic_mastery_learner_subject
ON topic_mastery (learner_id, subject_id);
