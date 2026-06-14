# Data Architecture

This project works best when content and learner state are separated.

## 1. Content Layer

Use the vector database for learning material.

Store here:

- raw text chunks
- embeddings
- source documents
- topic tags
- subject tags
- chunk metadata

This layer answers:

- What can the learner study?
- Which chunks are relevant to a topic?
- Which source material should we retrieve?

Recommended data items:

- `resource_id`
- `chunk_id`
- `subject_id`
- `topic_id`
- `vector_doc_id`
- `vector_chunk_id`
- `source_name`
- `source_uri`

For stable replay of the same step later, also keep:

- `content_version`
- `content_hash`
- `source_revision`
- `is_active`

## 2. Identity Layer

Use SQLite for learner identity and profile data.

Store here:

- learner email
- learner UUID
- name
- age group
- role
- language
- preferences

This layer answers:

- Who is the learner?
- What do they prefer?
- Is this a returning user?

Recommended tables:

- `learners`
- `learner_preferences`

## 3. Learning State Layer

Use SQLite for progress and roadmap state.

Store here:

- active subject
- active roadmap
- completed steps
- pending steps
- current topic
- mastery values
- review schedule

This layer answers:

- What is the learner currently studying?
- What is done?
- What remains?
- What should happen next?

Recommended tables:

- `subjects`
- `topics`
- `learner_subject_profiles`
- `learning_paths`
- `learning_path_steps`
- `topic_mastery`
- `study_sessions`

## 4. Quiz Layer

Use SQLite for quiz history and response tracking.

Store here:

- quiz attempts
- question responses
- score
- difficulty
- time taken
- completion status

This layer answers:

- What quiz was taken?
- Which questions were right or wrong?
- What should be reviewed?

Recommended tables:

- `quiz_attempts`
- `quiz_responses`

## 5. Retrieval Flow

When the learner opens the app:

1. Look up learner by email in SQLite.
2. Load preferences.
3. Load active subject and roadmap state.
4. Read the next pending step.
5. Use topic and step metadata to query the vector DB.
6. Retrieve the best content chunks.
7. Render the content for this learner.

## 6. Dashboard Flow

The dashboard should not read from the vector DB directly for everything.

It should read from SQLite first:

- learner identity
- preferences
- active roadmap
- completion status
- mastery summary
- latest quiz score

Then, when the user opens a specific step, the app fetches content from the vector DB.

## 7. Personalized Content Flow

Personalization should happen as a rendering step, not as a separate copy of all content.

Pattern:

1. Retrieve shared content chunk from vector DB.
2. Read learner preferences from SQLite.
3. Adjust the explanation style, depth, and pacing.
4. Show the learner-specific view.

This means:

- shared knowledge stays shared
- learner state stays learner-specific

To guarantee the same step content later, store both:

- the exact source reference in `learning_path_steps`
  - `resource_id`
  - `chunk_id`
  - `content_version`
- the optional rendered learner copy in `learner_content_views`

## 8. Recommended Rule

Do not store full learning text in SQLite if the vector DB is already the content source.

Store only references and progress data in SQLite.

This keeps the system:

- simpler
- cleaner
- easier to update
- easier to scale
