# Agent Handoff Contract

This document defines the exact handoff between the first two agents in the project.

## Handoff Scope

- Knowledge Assessment Agent produces the diagnostic result.
- Learning Path Agent consumes that diagnostic result and creates the roadmap.
- The boundary is the moment the diagnostic quiz is scored and stored.
- After that, the Learning Path Agent owns roadmap generation.

## Source of Truth

- Learner identity lives in `learners`.
- Preferences live in `learner_preferences`.
- The selected subject lives in `subjects`.
- The topic/profile link lives in `learner_subject_profiles`.
- Diagnostic attempts live in `quiz_attempts`.
- Per-question answers live in `quiz_responses`.
- Roadmaps live in `learning_paths`.
- Roadmap steps live in `learning_path_steps`.

## Diagnostic Output Contract

The Knowledge Assessment Agent must produce a result object with these fields:

- `learner_id`
- `email`
- `subject_id`
- `topic`
- `level`
- `familiarity`
- `attempt_id`
- `score`
- `total_questions`
- `correct_answers`
- `status`
- `responses`
- `weak_points`
- `difficulty_plan`

### Field Details

- `learner_id` is the learner UUID from SQLite.
- `email` is the learner login identity.
- `subject_id` is the UUID of the entered subject.
- `topic` is the exact learner-entered topic string.
- `level` is the estimated starting level, derived from the learner’s familiarity or the diagnostic result.
- `familiarity` is the learner’s self-reported comfort level.
- `attempt_id` is the saved quiz attempt UUID.
- `score` is the diagnostic score as a decimal between 0 and 1.
- `total_questions` is the number of diagnostic questions.
- `correct_answers` is the number answered correctly.
- `status` is the assessment outcome, such as `passed` or `needs_review`.
- `responses` is the full per-question response list.
- `weak_points` is a list of question texts or concepts answered incorrectly.
- `difficulty_plan` is the question mix used to build the diagnostic quiz.

## Learning Path Input Contract

The Learning Path Agent must consume the diagnostic result together with the learner and subject identifiers.

Required inputs:

- `learner_id`
- `subject_id`
- `topic`
- `score`
- `responses`
- `weak_points`
- `level`

Optional inputs:

- `attempt_id`
- `email`
- `correct_answers`
- `total_questions`

## Roadmap Output Contract

The Learning Path Agent must return and persist a roadmap object with these fields:

- `path_id`
- `path_title`
- `mode`
- `root_topic_id`
- `total_steps`
- `estimated_total_minutes`
- `summary`
- `steps`
- `score`
- `weak_points`

### Step Contract

Each roadmap step should contain:

- `step_id`
- `step_order`
- `step_title`
- `step_description`
- `estimated_minutes`
- `topic_id`
- `content_depth`
- `step_focus`

## Mode-Specific Learning Path Contract

The Learning Path Agent uses the same database tables for both study flows, but the output depth changes by mode.

### Roadmap mode

- Purpose: detailed, step-by-step learning path.
- Input: diagnostic result plus learner profile and preferences.
- Output style:
  - more steps
  - more explanation
  - deeper prerequisite coverage
  - longer practice and review sections
- Expected fields:
  - `mode = roadmap`
  - `content_depth = detailed`
  - `summary` explains the full learning journey
  - `estimated_total_minutes` reflects a longer study plan

### Quick study mode

- Purpose: short, high-signal study path for fast revision.
- Input: learner profile and preferences, with optional topic context.
- Output style:
  - fewer steps
  - concise explanations
  - quick recap and essentials only
  - no diagnostic quiz
- Expected fields:
  - `mode = quick_study`
  - `content_depth = concise`
  - `summary` explains the short study sprint
  - `estimated_total_minutes` reflects a compact study plan

### Shared rules

- Both modes must stay topic-specific.
- Both modes must read the learner profile and preferences before building the path.
- Both modes should update `learner_subject_profiles.active_path_id`.
- Both modes should create ordered `learning_path_steps` records.
- Roadmap mode uses diagnostic gaps.
- Quick study mode uses the learner's entered topic and preferences, without diagnostics.

## Behavioral Rules

- The diagnostic quiz must stay topic-specific.
- The Learning Path Agent must only use the diagnostic result for the same subject.
- The Learning Path Agent must not generate quiz questions.
- The Knowledge Assessment Agent must not generate the roadmap.
- The latest diagnostic attempt should be the input to roadmap generation.
- The active roadmap should be stored in `learner_subject_profiles.active_path_id`.
- One active roadmap per learner and subject is the default rule for now.

## Diagnostic Difficulty Map

The diagnostic quiz should use the learner’s self-reported familiarity to choose the question mix.

### New to this

- 5 easy questions
- no medium or hard questions
- goal: confirm a baseline without overwhelming the learner

### Some familiarity

- 4 easy questions
- 1 medium question

### Comfortable with basics

- 3 easy questions
- 2 medium questions

### Already advanced

- 1 easy question
- 2 medium questions
- 2 hard questions

### Additional Rule

- The quiz should still stay topic-specific for all difficulty levels.
- The difficulty mix should control the question style, not the subject.
- The diagnostic result should continue to use the same output contract.

## Practical Python Hand-off

The current implementation uses this shape:

```python
learning_path = build_learning_path(
    conn,
    learner_id,
    subject_id,
    topic,
    diagnostic_result,
    study_mode="roadmap",
)
```

Where `diagnostic_result` is the object returned by the diagnostic save step and should contain the fields listed above.

For quick study, the same function is called with `diagnostic_result=None` and `study_mode="quick_study"`.
