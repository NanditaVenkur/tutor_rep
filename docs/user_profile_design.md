# User Profile Design

This document defines the first version of the learner profile system for the adaptive learning project.

## Goals

- Capture enough information to personalize the first study session.
- Avoid a long onboarding form.
- Keep the profile updateable over time from quiz behavior and study choices.
- Support multiple subjects per learner.

## Core Idea

Use a two-layer profile:

1. `learner` level data for identity and global preferences.
2. `subject` and `path` level data for topic-specific knowledge, goals, progress, and completion state.

This lets one learner study multiple subjects, such as physics and chemistry, without mixing their progress.

For dashboard retrieval, the schema should answer these questions directly:

- Who is the learner?
- What are their preferences?
- What subject are they studying right now?
- Which roadmap/path is active?
- Which steps are completed?
- Which steps are still pending?
- What was the last quiz result?
- Which topics need review?
- Which exact content chunk should be replayed for this step?

## What To Ask First

For first-time users, ask only the most useful questions:

1. What do you want to learn?
2. What is your current level?
3. What is your goal?
4. How much time do you have per session?
5. What format do you prefer?
6. What explanation style do you like?
7. What quiz style do you prefer?
8. Do you want fast, normal, or slow pacing?

Optional:

- Age group
- Student / working professional / other
- Preferred language
- Any accessibility needs

## Suggested Questions

### Basic

- What should we call you?
- Which age group are you in?
- Are you a student, working professional, or something else?

### Learning Goal

- What topic do you want to study?
- Why are you learning it?
- What outcome do you want?

Example answers:

- Quick revision
- Build fundamentals
- Exam prep
- Interview prep
- Solve one specific problem

### Current Knowledge

- How familiar are you with this topic?

Example answers:

- New to this topic
- Some familiarity
- Comfortable with basics
- Already advanced

### Preferences

- Which format helps you most?

Example answers:

- Short text notes
- Detailed explanation
- Visual examples
- Mixed content

- How do you want explanations?

Example answers:

- Short
- Step by step
- Detailed
- Example driven

- What quiz style do you prefer?

Example answers:

- MCQ
- Short answer
- True / false
- Mixed

- How fast should the lessons move?

Example answers:

- Slow
- Normal
- Fast

### Session Settings

- How long can you study at a time?
- Do you want quizzes after each topic or only at the end?
- Do you want feedback immediately after each question?

## Data Model

### 1. `learners`

Stores one row per user.

Fields:

- `learner_id`
- `full_name`
- `age_group`
- `role`
- `preferred_language`
- `created_at`
- `updated_at`
- 'email'

### 2. `learner_preferences`

Stores global preferences that do not change much.

Fields:

- `preference_id`
- `learner_id`
- `content_format`
- `explanation_style`
- `quiz_style`
- `learning_pace`
- `session_length`
- `feedback_style`
- `accessibility_notes`

### 3. `subjects`

Stores supported subjects or topics.

Fields:

- `subject_id`
- `subject_name`
- `description`

### 4. `topics`

Stores the topic tree for a subject.

Fields:

- `topic_id`
- `subject_id`
- `topic_name`
- `topic_description`
- `prerequisite_topic_id`
- `topic_order`
- `estimated_minutes`

This is the source of truth for what exists in a subject and what comes before what.

### 5. `learner_subject_profiles`

Tracks learner state for each subject and current roadmap.

Fields:

- `profile_id`
- `learner_id`
- `subject_id`
- `active_path_id`
- `current_topic_id`
- `goal_type`
- `current_level`
- `target_level`
- `status`
- `last_assessed_score`
- `mastery_score`
- `confidence_score`
- `path_completion_pct`
- `completed_step_count`
- `total_step_count`
- `next_review_at`
- `last_activity_at`

Use this table for the dashboard’s “current state” card.

### 6. `learning_paths`

Stores each generated roadmap for a learner and subject.

Fields:

- `path_id`
- `learner_id`
- `subject_id`
- `root_topic_id`
- `path_title`
- `path_status`
- `target_outcome`
- `total_steps`
- `completed_steps`
- `created_at`
- `updated_at`
- `last_accessed_at`

### 7. `learning_path_steps`

Stores the ordered steps in a roadmap.

Fields:

- `step_id`
- `path_id`
- `topic_id`
- `step_order`
- `step_title`
- `step_description`
- `step_status`
- `resource_id`
- `estimated_minutes`
- `actual_minutes`
- `started_at`
- `completed_at`

This is what you use to show completed vs remaining roadmap items.

### 8. `content_resources`

Stores metadata for the shared learning content.

Fields:

- `resource_id`
- `subject_id`
- `topic_id`
- `resource_type`
- `title`
- `source_name`
- `source_uri`
- `vector_collection`
- `vector_doc_id`
- `content_version`
- `content_hash`
- `source_revision`
- `is_active`
- `embedding_ref`

This table does not store the full text when the vector DB already holds the content.

### 9. `content_chunks`

Stores the exact chunks used for retrieval.

Fields:

- `chunk_id`
- `resource_id`
- `chunk_order`
- `chunk_text`
- `vector_chunk_id`
- `chunk_hash`
- `chunk_version`
- `embedding_ref`

Use this table to ensure the same chunk can be retrieved again later.

### 10. `learner_content_views`

Stores the learner-specific rendered version of a chunk or step.

Fields:

- `view_id`
- `learner_id`
- `path_id`
- `step_id`
- `topic_id`
- `source_resource_id`
- `source_chunk_id`
- `source_content_version`
- `rendered_title`
- `rendered_summary`
- `rendered_content`
- `rendered_format`
- `reading_level`
- `content_hash`
- `view_status`
- `rendered_at`
- `updated_at`

### 11. `study_sessions`

Stores each time the learner opens the app to study.

Fields:

- `session_id`
- `learner_id`
- `subject_id`
- `path_id`
- `session_type`
- `session_status`
- `started_at`
- `ended_at`
- `session_summary`

### 12. `quiz_attempts`

Stores each quiz session.

Fields:

- `attempt_id`
- `learner_id`
- `subject_id`
- `path_id`
- `step_id`
- `quiz_type`
- `difficulty_level`
- `score`
- `completion_status`
- `mastery_delta`
- `started_at`
- `completed_at`

### 13. `quiz_responses`

Stores each answer.

Fields:

- `response_id`
- `attempt_id`
- `question_id`
- `question_text`
- `selected_answer`
- `correct_answer`
- `is_correct`
- `time_taken_seconds`

### 14. `topic_mastery`

Stores the current mastery estimate for each learner-topic pair.

Fields:

- `mastery_id`
- `learner_id`
- `subject_id`
- `topic_id`
- `mastery_probability`
- `last_assessed_score`
- `review_due_at`
- `last_practiced_at`
- `mastery_status`

## Profile Update Logic

Update the profile after each session using:

- quiz score
- time spent
- questions answered correctly
- questions answered incorrectly
- content format chosen
- skipped or repeated topics

Rules:

- If the learner answers basic questions correctly, increase difficulty slowly.
- If the learner struggles, reduce difficulty and add more examples.
- If the learner prefers short content, keep summaries short.
- If the learner repeatedly chooses visuals, prioritize visual study material.
- If mastery drops below threshold, schedule review.

## Dashboard Retrieval Map

Use these joins when building the dashboard later:

- Profile card: `learners` + `learner_preferences`
- Current roadmap card: `learner_subject_profiles` + `learning_paths`
- Progress bar: `learning_path_steps`
- Completed vs remaining: `learning_path_steps.step_status`
- Next review: `topic_mastery.review_due_at`
- Last quiz result: `quiz_attempts`
- Weak areas: `topic_mastery` ordered by low `mastery_probability`
- Content to show next: `content_resources` + `content_chunks` + `learner_content_views`

## Validation Assets

To test the schema before building more code, use:

- [`seed_demo_data.sql`](/Users/akankshacheeti/Capstone%20Project%20/data/sql/seed_demo_data.sql)
- [`dashboard_queries.sql`](/Users/akankshacheeti/Capstone%20Project%20/data/sql/dashboard_queries.sql)

These files let you:

- load one complete learner journey
- run the dashboard read queries
- verify step replay
- verify quiz history
- verify mastery and review state

## First Version Flow

1. User opens the app.
2. App asks a short onboarding form.
3. System creates learner record.
4. System creates or updates subject profile.
5. System generates a diagnostic quiz.
6. System stores score and responses.
7. System updates preferences and mastery.
8. System builds the first learning path.
9. System stores the exact content chunk or learner view used for each step.

##  For version1

Start with these fields only:

- name
- subject
- goal
- current knowledge level
- content format
- explanation style
- quiz style
- pace
- session length

Add age, role, and accessibility details as optional fields.
