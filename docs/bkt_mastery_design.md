# BKT Mastery Design

## What We Implemented

We use Bayesian Knowledge Tracing (BKT) to update mastery at the concept level after each adaptive quiz answer.

Each quiz question is mapped to a `concept` from the question bank. When the learner answers, we update the row in `concept_mastery` for:

- learner
- subject
- roadmap step
- concept

The adaptive quiz now stores and updates:

- `mastery_probability`: current probability that the learner knows the concept
- `evidence_count`: number of answers observed for that concept
- `correct_count`: number of correct answers for that concept
- `bkt_prior`: starting belief for the concept
- `bkt_transit`: probability the learner learned after one practice opportunity
- `bkt_guess`: probability of answering correctly without knowing the concept
- `bkt_slip`: probability of answering incorrectly despite knowing the concept

The visible step/topic mastery is then rolled up from the average of concept-level BKT probabilities for that step. If no concept mastery rows exist yet, the app falls back to the older quiz-score blend.

## Why BKT

BKT is a good fit because our app is not only grading a quiz. It is trying to estimate what the learner knows over time.

The earlier approach used a simple heuristic:

- correct answer increased mastery by a fixed amount
- wrong answer decreased mastery by a fixed amount

That was easy to understand, but it treated every correct answer as equally strong evidence. It did not model guessing or accidental mistakes.

BKT is better because it separates four ideas:

- A learner may already know the concept before the quiz.
- A learner may learn the concept during practice.
- A learner may guess correctly.
- A learner may slip and answer wrong even if they know it.

This makes mastery more stable and explainable than raw quiz percentage.

## BKT Formula

For each concept, we start with a prior probability:

```text
P(K) = probability learner knows the concept before this answer
```

If the learner answers correctly:

```text
P(K | correct) =
P(K) * (1 - slip)
/
[P(K) * (1 - slip) + (1 - P(K)) * guess]
```

If the learner answers incorrectly:

```text
P(K | wrong) =
P(K) * slip
/
[P(K) * slip + (1 - P(K)) * (1 - guess)]
```

Then we apply the learning transition:

```text
P(K next) = P(K | observation) + (1 - P(K | observation)) * transit
```

This final value becomes the new `mastery_probability`.

## Default Parameters

Current defaults:

```text
prior   = 0.25
transit = 0.12
guess   = 0.20
slip    = 0.10
```

These are conservative starting values:

- `prior = 0.25`: assume the learner may know something, but not the full concept yet.
- `transit = 0.12`: one question can improve mastery, but not magically complete it.
- `guess = 0.20`: close to random guessing for a 4-option multiple choice question.
- `slip = 0.10`: allows occasional mistakes from learners who mostly know the concept.

These values should be calibrated later using real learner data.

## Example

Suppose a learner starts with:

```text
prior = 0.25
guess = 0.20
slip = 0.10
transit = 0.12
```

If they answer correctly:

```text
P(K | correct)
= 0.25 * 0.90 / [0.25 * 0.90 + 0.75 * 0.20]
= 0.225 / 0.375
= 0.60
```

Apply learning:

```text
P(K next) = 0.60 + (1 - 0.60) * 0.12 = 0.648
```

So mastery moves from `25%` to about `65%`.

If they answer wrong from `25%`:

```text
P(K | wrong)
= 0.25 * 0.10 / [0.25 * 0.10 + 0.75 * 0.80]
= 0.025 / 0.625
= 0.04
```

Apply learning:

```text
P(K next) = 0.04 + (1 - 0.04) * 0.12 = 0.155
```

So mastery drops, but does not become zero.

## How We Evaluate Mastery Quality

We can evaluate BKT in three layers.

### 1. Prediction Quality

Check whether concept mastery predicts future quiz correctness.

Useful metrics:

- accuracy bucket check: learners at 80% mastery should answer correctly more often than learners at 40%.
- calibration: predicted mastery should roughly match observed correctness.
- log loss or Brier score: measures whether probabilities are useful, not just pass/fail labels.

### 2. Learning Behavior

Check whether mastery changes sensibly after answers.

Expected behavior:

- correct answers increase concept mastery.
- wrong answers decrease concept mastery.
- repeated correct answers approach mastery gradually.
- one wrong answer should not destroy mastery completely.
- concepts not assessed should not change.

### 3. Product Behavior

Check whether the system gives better learning decisions.

Useful questions:

- Does the app recommend retry when weak concepts remain?
- Does it avoid marking a step complete from one lucky answer?
- Does it show progress after consistent correct answers?
- Does it help choose the next adaptive quiz question better than raw score alone?

## Pros

- Concept-level tracking is more precise than one overall quiz score.
- It handles guessing and careless mistakes.
- It works with small amounts of data.
- It is explainable enough for a capstone demo.
- It fits adaptive quizzes naturally because mastery updates after each answer.
- It can later be calibrated from real student attempts.

## Tradeoffs

- BKT depends on question-to-concept mapping quality.
- Wrong concept tags will produce wrong mastery updates.
- Default parameters are assumptions until we calibrate them.
- Classic BKT assumes one skill per observation, while a real question can involve multiple concepts.
- It does not understand difficulty as deeply as an IRT/CAT model.
- It can overreact when there are very few questions unless parameters are conservative.

## Edge Cases

- A learner guesses several answers correctly and mastery rises too fast.
- A learner knows the concept but misreads a question and mastery drops.
- A question tests multiple concepts, but only one concept is updated.
- The LLM generates an unclear or mismapped concept label.
- A step has no adaptive quiz responses, so there is no concept mastery evidence yet.
- A concept appears across multiple roadmap steps with slightly different names.

## Why Not CAT First

Computerized Adaptive Testing (CAT) is excellent for selecting question difficulty and estimating ability, but it usually needs a calibrated item bank. That means each question should have known difficulty, discrimination, and guessing parameters.

Our current app is still generating many questions dynamically. Because of that, BKT is safer for mastery tracking now:

- BKT needs fewer assumptions than CAT.
- BKT can work with generated questions as long as concept tags are good.
- BKT gives a clear concept-by-concept explanation.

CAT can be added later for better question selection after the quiz bank is more stable.

## Future Improvements

- Allow each question to update multiple concepts with weights.
- Calibrate `prior`, `transit`, `guess`, and `slip` from historical quiz data.
- Normalize concept names so repeated concepts merge cleanly.
- Store concept confidence separately from mastery probability.
- Show learner-facing concept mastery in the dashboard.
- Use BKT mastery to pick the next quiz question from the weakest concept.
