# Agent Architecture

This project follows the agent set described in the project notes.

## Core Agents

### 1. Knowledge Assessment Agent

Purpose:

- create or update the learner profile baseline
- run a diagnostic quiz. -----?
- estimate the learner's current level
- store quiz performance

Inputs:

- learner profile
- selected subject or topic
- content preferences
- available diagnostic question pool

Outputs:

- quiz attempt record
- quiz responses
- initial subject profile update
- starting skill estimate

Reads from:

- `learners`
- `learner_preferences`
- `subjects`
- `topics`
- vector DB content

Writes to:

- `quiz_attempts`
- `quiz_responses`
- `learner_subject_profiles`
- `topic_mastery`

Handoff to Learning Path Agent:

- `learner_id`
- `email`
- `subject_id`
- `topic`
- `level`
- `attempt_id`
- `score`
- `total_questions`
- `correct_answers`
- `status`
- `responses`
- `weak_points`

See [`agent_handoff.md`](/Users/akankshacheeti/Capstone%20Project%20/docs/agent_handoff.md) for the full contract.

### 2. Learning Path Agent

Purpose:

- find knowledge gaps
- retrieve relevant materials
- build either a detailed roadmap or a quick study path
- attach content references to each step
- cache the learner-facing step content so it is stable on the next dashboard load


Inputs:

- latest quiz result or quick-study request
- learner profile
- topic prerequisite graph.   
- content from vector DB

Outputs:

- `learning_paths` row
- ordered `learning_path_steps`
- step-to-content references
- mode-aware path summary
- optional `learner_content_views` rows for rendered step content

Reads from:

- `learners`
- `learner_preferences`
- `learner_subject_profiles`
- `quiz_attempts`
- `quiz_responses`
- `topics`
- vector DB content
- `content_resources`
- `content_chunks`

Writes to:

- `learning_paths`
- `learning_path_steps`
- `learner_subject_profiles`
- `learner_content_views`

Consumes:

- latest diagnostic result from the Knowledge Assessment Agent
- `learner_id`
- `subject_id`
- `topic`
- `score`
- `responses`
- `weak_points`
- `level`
- `study_mode`

### 3. Adaptive Quiz Agent

Purpose:

- generate or choose the next question
- adjust difficulty based on recent performance
- keep the quiz aligned with the learner’s current state

Inputs:

- active roadmap step
- learner profile
- current mastery
- recent quiz performance

Outputs:

- adaptive question set
- updated quiz attempt
- updated responses

Reads from:

- `learner_subject_profiles`
- `learning_path_steps`
- `topic_mastery`
- `quiz_attempts`
- `quiz_responses`
- vector DB content

Writes to:

- `quiz_attempts`
- `quiz_responses`
- `topic_mastery`
- `learner_subject_profiles`

### 4. Mastery Tracking Agent

Purpose:

- update mastery estimates after quizzes
- decide whether review is needed
- mark topics or steps for revisit

Inputs:

- quiz responses
- quiz score
- current topic
- current subject profile

Outputs:

- updated mastery values
- review due date
- completion or retry recommendation

Reads from:

- `quiz_attempts`
- `quiz_responses`
- `learner_subject_profiles`
- `topic_mastery`
- `learning_path_steps`

Writes to:

- `topic_mastery`
- `learner_subject_profiles`
- `learning_path_steps`

## Support Flows

### Quick Study Flow

Quick Study does not need a separate long-lived agent.

It should reuse:

- Learning Path Agent
- optional Adaptive Quiz Agent for follow-up practice

Flow:

1. learner enters a topic
2. Learning Path Agent builds a short path
3. dashboard shows the compact study path and summary
4. optional quiz follow-up can happen later

### Full Study Flow

Flow:

1. learner logs in
2. Knowledge Assessment Agent runs diagnostic quiz if needed
3. Learning Path Agent builds roadmap
4. Adaptive Quiz Agent serves step quiz
5. Mastery Tracking Agent updates progress
6. loop until mastery threshold is reached

## LangGraph State

Recommended state fields:

- `learner_id`
- `email`
- `subject_id`
- `topic_id`
- `active_path_id`
- `current_step_id`
- `current_quiz_attempt_id`
- `mastery_summary`
- `learning_mode`
- `step_status`
- `next_action`

## Prescribed Agent Order

For this project, keep the order as:

1. Knowledge Assessment Agent
2. Learning Path Agent
3. Adaptive Quiz Agent
4. Mastery Tracking Agent

Socratic Explanation Agent can be added later as a support layer for wrong answers and guided reasoning.
