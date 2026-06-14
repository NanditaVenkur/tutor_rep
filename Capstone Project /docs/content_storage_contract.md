# Content Storage Contract

This document defines how content should be stored so a learner sees the same step content tomorrow that they saw today.

## Goal

- keep learning content stable across dashboard loads
- store the learner-facing rendering once
- reuse the stored rendering instead of regenerating it
- support textbook-first math content, with web fallback when needed

## Core Principle

Split content into three layers:

1. **Source layer**
   - textbook chunks, notes, web snippets, or other source material
   - stored in the vector DB and referenced from SQLite

2. **Roadmap layer**
   - the step plan created by the Learning Path Agent
   - stored in SQLite

3. **Rendered learner layer**
   - the exact explanation shown to the learner
   - stored in SQLite so it can be replayed later

## What Goes Into ChromaDB

Use ChromaDB for the source layer only.

Each chunk should store:

- `resource_id`
- `chunk_id`
- `subject_id`
- `topic_id`
- `chunk_order`
- `chunk_text`
- `vector_doc_id`
- `vector_chunk_id`
- `content_version`
- `content_hash`
- `source_name`
- `source_uri`
- `source_type`
- `source_revision`
- `is_active`

### Math example

If the learner is studying math:

- one PDF chapter becomes one `content_resource`
- each section or paragraph becomes a `content_chunk`
- embeddings are stored in ChromaDB for similarity search

### Good source types for math

- textbook PDF
- lecture notes
- curated chapter summary
- worked example sheet
- official educational site
- Wikipedia only when the topic is general and the content is non-sensitive

### Recommended rule for web fallback

- use trusted sources
- collect the source URL and source name
- store the retrieved snippet as a source chunk
- do not regenerate from the web on every dashboard load

## What Stays in SQLite

SQLite should store the identity, roadmap, and replay state.

### `content_resources`

Use this as the metadata index for each source document.

Store:

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

### `content_chunks`

Use this for chunk-level metadata.

Store:

- `chunk_id`
- `resource_id`
- `chunk_order`
- `chunk_text` only if you want a small local preview
- `vector_chunk_id`
- `chunk_hash`
- `chunk_version`
- `embedding_ref`

### `learning_path_steps`

Each roadmap step should link to a source chunk when content is available.

Store:

- `resource_id`
- `chunk_id`
- `content_version`

### `learner_content_views`

This is the replay cache.

Store the exact rendered learner-facing version here so tomorrow’s dashboard can show the same content.

Store:

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

## Replay Rule

When the dashboard opens a step:

1. Look for an active row in `learner_content_views`.
2. If found, show that exact rendered content.
3. If not found, retrieve the source chunk from ChromaDB.
4. Render the content once.
5. Save the rendered result into `learner_content_views`.

This makes the content stable across days.

## How Content Is Generated

### Roadmap mode

- use the diagnostic gaps
- use learner preferences
- use the source chunk(s)
- generate a detailed explanation
- include examples and a short recap

### Quick study mode

- use learner preferences
- use the source chunk(s)
- generate a short summary
- keep the explanation concise
- focus on the essentials only

## When the Textbook Does Not Cover a Topic Well

Use a fallback retrieval tool at generation time, not at dashboard time.

Suggested fallback order:

1. textbook chunk retrieval
2. curated notes
3. trusted web source lookup
4. Wikipedia or educational site only if the topic is general and the source is suitable

After fallback retrieval:

- normalize the source text into a chunk-like object
- store it as a `content_resource` / `content_chunk`
- render the learner view
- cache the rendered output

## What Not to Do

- do not regenerate the learner view on every dashboard load
- do not store all full textbooks in SQLite
- do not overwrite old rendered views unless the source version changes or the learner’s preferences change
- do not rely on an unrelated fallback topic

## Recommended Workflow for Math

1. Load math textbook PDFs.
2. Chunk the text by section or concept.
3. Store chunks in ChromaDB.
4. Save metadata in SQLite.
5. Build the learning path step.
6. Retrieve matching chunk(s).
7. Render learner-specific content.
8. Store the rendered output in `learner_content_views`.
9. Reuse the stored view on the next login.

