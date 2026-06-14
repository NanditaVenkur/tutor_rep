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

### 2. Learning Path Agent

Purpose:

- find knowledge gaps
- retrieve relevant materials
- build an ordered roadmap
- attach content references to each step  dsa  -> arrays, struings, linked lists, trees, graphs 


Inputs:

- latest quiz result
- learner profile
- topic prerequisite graph.   
- content from vector DB

Outputs:

- `learning_paths` row
- ordered `learning_path_steps`
- step-to-content references

Reads from:

- `learners`
- `learner_preferences`
- `learner_subject_profiles`
- `quiz_attempts`
- `quiz_responses`
- `topics`
- vector DB content

Writes to:

- `learning_paths`
- `learning_path_steps`
- `learner_subject_profiles`

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
- Adaptive Quiz Agent

Flow:

1. learner enters a topic
2. Learning Path Agent builds a short path
3. Adaptive Quiz Agent gives a quick quiz
4. export or summary is generated

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

