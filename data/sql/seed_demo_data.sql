-- Demo data for validating the dashboard flow against the UUID schema.
-- This seed is intentionally small and covers one learner, one subject, one roadmap, one quiz, and one content view.

PRAGMA foreign_keys = ON;

DELETE FROM learner_content_views;
DELETE FROM content_chunks;
DELETE FROM content_resources;
DELETE FROM quiz_responses;
DELETE FROM quiz_attempts;
DELETE FROM study_sessions;
DELETE FROM learning_path_steps;
DELETE FROM learning_paths;
DELETE FROM topic_mastery;
DELETE FROM learner_subject_profiles;
DELETE FROM study_requests;
DELETE FROM topics;
DELETE FROM subjects;
DELETE FROM learner_preferences;
DELETE FROM learners;

INSERT INTO learners (
    learner_id, email, full_name, age_group, role, preferred_language, created_at, updated_at
) VALUES (
    'learner-0001',
    'akanksha@example.com',
    'Akanksha',
    '18-24',
    'Student',
    'English',
    datetime('now'),
    datetime('now')
);

INSERT INTO learner_preferences (
    preference_id, learner_id, content_format, explanation_style, quiz_style,
    learning_pace, session_length, feedback_style, accessibility_notes, updated_at
) VALUES (
    'pref-0001',
    'learner-0001',
    'mixed',
    'step_by_step',
    'mixed',
    'normal',
    '30_minutes',
    'immediate',
    'Prefer concise explanations',
    datetime('now')
);

INSERT INTO subjects (
    subject_id, subject_name, description, created_at
) VALUES (
    'subject-0001',
    'MCP Servers',
    'Learning path for Model Context Protocol servers.',
    datetime('now')
);

INSERT INTO topics (
    topic_id, subject_id, topic_name, topic_description, prerequisite_topic_id, topic_order, estimated_minutes, created_at
) VALUES
(
    'topic-0001',
    'subject-0001',
    'Agentic AI Basics',
    'Understand agents and orchestration.',
    NULL,
    1,
    20,
    datetime('now')
),
(
    'topic-0002',
    'subject-0001',
    'MCP Concepts',
    'Understand the protocol, client, and server roles.',
    'topic-0001',
    2,
    25,
    datetime('now')
),
(
    'topic-0003',
    'subject-0001',
    'Build an MCP Server',
    'Apply the concepts by building a simple server.',
    'topic-0002',
    3,
    35,
    datetime('now')
);

INSERT INTO learning_paths (
    path_id, learner_id, subject_id, root_topic_id, path_title, path_status,
    target_outcome, total_steps, completed_steps, created_at, updated_at, last_accessed_at
) VALUES (
    'path-0001',
    'learner-0001',
    'subject-0001',
    'topic-0001',
    'MCP Servers Roadmap',
    'active',
    'Build a working understanding of MCP servers',
    3,
    1,
    datetime('now'),
    datetime('now'),
    datetime('now')
);

INSERT INTO learner_subject_profiles (
    profile_id, learner_id, subject_id, active_path_id, current_topic_id, goal_type,
    current_level, target_level, status, last_assessed_score, mastery_score,
    confidence_score, path_completion_pct, completed_step_count, total_step_count,
    next_review_at, last_activity_at, created_at, updated_at
) VALUES (
    'profile-0001',
    'learner-0001',
    'subject-0001',
    'path-0001',
    'topic-0002',
    'build_fundamentals',
    'some_familiarity',
    'intermediate',
    'active',
    0.62,
    0.54,
    0.51,
    33.33,
    1,
    3,
    datetime('now', '+2 days'),
    datetime('now'),
    datetime('now'),
    datetime('now')
);

INSERT INTO content_resources (
    resource_id, subject_id, topic_id, resource_type, title, source_name, source_uri,
    vector_collection, vector_doc_id, content_version, content_hash, source_revision,
    is_active, embedding_ref, created_at, updated_at
) VALUES (
    'resource-0001',
    'subject-0001',
    'topic-0002',
    'note',
    'MCP Concepts Summary',
    'Textbook Notes',
    'https://example.com/mcp-notes',
    'content_chunks',
    'doc-0001',
    1,
    'hash-res-0001',
    'rev-1',
    1,
    'emb-res-0001',
    datetime('now'),
    datetime('now')
);

INSERT INTO content_chunks (
    chunk_id, resource_id, chunk_order, chunk_text, vector_chunk_id, chunk_hash,
    chunk_version, embedding_ref, created_at, updated_at
) VALUES (
    'chunk-0001',
    'resource-0001',
    1,
    'MCP links tools, clients, and servers through a standard protocol.',
    'vchunk-0001',
    'hash-chunk-0001',
    1,
    'emb-chunk-0001',
    datetime('now'),
    datetime('now')
);

INSERT INTO learning_path_steps (
    step_id, path_id, topic_id, resource_id, chunk_id, content_version, step_order,
    step_title, step_description, step_status, estimated_minutes, actual_minutes,
    started_at, completed_at, created_at, updated_at
) VALUES
(
    'step-0001',
    'path-0001',
    'topic-0001',
    NULL,
    NULL,
    1,
    1,
    'Understand agentic AI basics',
    'Learn the basic agent loop and orchestration idea.',
    'completed',
    20,
    18,
    datetime('now', '-3 days'),
    datetime('now', '-3 days', '+18 minutes'),
    datetime('now'),
    datetime('now')
),
(
    'step-0002',
    'path-0001',
    'topic-0002',
    'resource-0001',
    'chunk-0001',
    1,
    2,
    'Learn MCP concepts',
    'Review protocol basics and client-server roles.',
    'in_progress',
    25,
    10,
    datetime('now', '-1 day'),
    NULL,
    datetime('now'),
    datetime('now')
),
(
    'step-0003',
    'path-0001',
    'topic-0003',
    NULL,
    NULL,
    1,
    3,
    'Build an MCP server',
    'Apply the protocol by building a simple server.',
    'not_started',
    35,
    0,
    NULL,
    NULL,
    datetime('now'),
    datetime('now')
);

INSERT INTO learner_content_views (
    view_id, learner_id, path_id, step_id, topic_id, source_resource_id, source_chunk_id,
    source_content_version, rendered_title, rendered_summary, rendered_content,
    rendered_format, reading_level, content_hash, view_status, rendered_at, updated_at
) VALUES (
    'view-0001',
    'learner-0001',
    'path-0001',
    'step-0002',
    'topic-0002',
    'resource-0001',
    'chunk-0001',
    1,
    'MCP Concepts for You',
    'Short step-by-step summary of MCP basics.',
    'MCP is a protocol that helps agents talk to tools and servers in a standardized way.',
    'mixed',
    'intermediate',
    'hash-view-0001',
    'active',
    datetime('now'),
    datetime('now')
);

INSERT INTO study_sessions (
    session_id, learner_id, subject_id, path_id, session_type, session_status,
    started_at, ended_at, session_summary, created_at, updated_at
) VALUES (
    'session-0001',
    'learner-0001',
    'subject-0001',
    'path-0001',
    'study',
    'completed',
    datetime('now', '-1 day'),
    datetime('now', '-1 day', '+35 minutes'),
    'Completed the second step and reviewed MCP concepts.',
    datetime('now'),
    datetime('now')
);

INSERT INTO quiz_attempts (
    attempt_id, learner_id, subject_id, path_id, step_id, quiz_type, difficulty_level,
    score, total_questions, correct_answers, completion_status, mastery_delta,
    started_at, completed_at
) VALUES (
    'quiz-0001',
    'learner-0001',
    'subject-0001',
    'path-0001',
    'step-0002',
    'adaptive',
    'medium',
    80,
    5,
    4,
    'completed',
    0.08,
    datetime('now', '-1 day'),
    datetime('now', '-1 day', '+12 minutes')
);

INSERT INTO quiz_responses (
    response_id, attempt_id, question_id, question_text, selected_answer,
    correct_answer, is_correct, time_taken_seconds, created_at
) VALUES
(
    'resp-0001',
    'quiz-0001',
    'q-1',
    'What does MCP stand for?',
    'Model Context Protocol',
    'Model Context Protocol',
    1,
    14.2,
    datetime('now')
),
(
    'resp-0002',
    'quiz-0001',
    'q-2',
    'What is the role of an MCP client?',
    'It requests tools and data from servers',
    'It requests tools and data from servers',
    1,
    12.6,
    datetime('now')
);

INSERT INTO topic_mastery (
    mastery_id, learner_id, subject_id, topic_id, mastery_probability, last_assessed_score,
    review_due_at, last_practiced_at, mastery_status, created_at, updated_at
) VALUES
(
    'mastery-0001',
    'learner-0001',
    'subject-0001',
    'topic-0001',
    0.85,
    0.88,
    NULL,
    datetime('now', '-3 days'),
    'mastered',
    datetime('now'),
    datetime('now')
),
(
    'mastery-0002',
    'learner-0001',
    'subject-0001',
    'topic-0002',
    0.58,
    0.80,
    datetime('now', '+2 days'),
    datetime('now', '-1 day'),
    'needs_review',
    datetime('now'),
    datetime('now')
),
(
    'mastery-0003',
    'learner-0001',
    'subject-0001',
    'topic-0003',
    0.20,
    0.00,
    datetime('now', '+4 days'),
    NULL,
    'not_started',
    datetime('now'),
    datetime('now')
);

INSERT INTO study_requests (
    request_id, learner_id, topic, study_mode, familiarity, created_at, updated_at
) VALUES (
    'request-0001',
    'learner-0001',
    'MCP Servers',
    'roadmap',
    'Some familiarity',
    datetime('now'),
    datetime('now')
);
