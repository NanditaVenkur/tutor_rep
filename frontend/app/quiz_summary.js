const API_BASE = "http://localhost:8001";

const quizSidebarScore = document.getElementById("quizSidebarScore");
const quizIndexGrid = document.getElementById("quizIndexGrid");
const quizTipBox = document.getElementById("quizTipBox");
const quizPrevSidebar = document.getElementById("quizPrevSidebar");
const quizNextSidebar = document.getElementById("quizNextSidebar");
const quizTagSubject = document.getElementById("quizTagSubject");
const quizTagMeta = document.getElementById("quizTagMeta");
const quizQuestionHeading = document.getElementById("quizQuestionHeading");
const quizStatusPill = document.getElementById("quizStatusPill");
const quizQuestionText = document.getElementById("quizQuestionText");
const quizOptions = document.getElementById("quizOptions");
const quizExplanationCard = document.getElementById("quizExplanationCard");
const quizExplanationBody = document.getElementById("quizExplanationBody");

let quizState = null;
let currentIndex = 0;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function getAttemptId() {
  const params = new URLSearchParams(window.location.search);
  return params.get("attempt_id") || "";
}

function formatQuestionType(attempt) {
  const type = String(attempt?.quiz_type || "quiz").replaceAll("_", " ");
  const difficulty = String(attempt?.difficulty_level || "").replaceAll("_", " ");
  return difficulty ? `${type} • ${difficulty}` : type;
}

function reviewTip(summary, responses) {
  const wrongItems = responses.filter((item) => !item.is_correct).map((item) => item.index);
  if (!wrongItems.length) {
    return "Strong work. This attempt was fully correct, so use it as a confidence check before moving on.";
  }
  if (wrongItems.length === 1) {
    return `Focus on question ${wrongItems[0]}. The explanation there is the fastest way to fix the gap.`;
  }
  return `Focus on the AI explanations for questions ${wrongItems.join(", ")} before retaking this step.`;
}

function optionEntries(response) {
  const options = response.options || {};
  const entries = Object.entries(options);
  if (entries.length) return entries;

  const synthetic = [];
  if (response.selected_answer && response.selected_answer !== response.correct_answer) {
    synthetic.push([response.selected_answer, response.selected_answer_text || response.selected_answer]);
  }
  if (response.correct_answer) {
    synthetic.push([response.correct_answer, response.correct_answer_text || response.correct_answer]);
  }
  return synthetic.filter((item, index, arr) => arr.findIndex(([key]) => key === item[0]) === index);
}

function renderIndexButtons(responses) {
  quizIndexGrid.innerHTML = "";
  responses.forEach((response, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `quiz-index-button ${response.is_correct ? "correct" : "incorrect"}${index === currentIndex ? " active" : ""}`;
    button.textContent = String(index + 1);
    button.addEventListener("click", () => {
      currentIndex = index;
      renderCurrentQuestion();
    });
    quizIndexGrid.appendChild(button);
  });
}

function renderOptions(response) {
  const entries = optionEntries(response);
  if (!entries.length) {
    quizOptions.innerHTML = `<div class="quiz-option-card">No answer choices were stored for this question.</div>`;
    return;
  }

  quizOptions.innerHTML = entries.map(([key, text]) => {
    const isSelected = response.selected_answer === key;
    const isCorrect = response.correct_answer === key;
    const classes = [
      "quiz-option-card",
      isSelected && !response.is_correct ? "selected-wrong" : "",
      isCorrect ? "selected-correct" : "",
    ].filter(Boolean).join(" ");

    const badge = isSelected && !response.is_correct
      ? `<span class="quiz-option-badge wrong">Your selection</span>`
      : isCorrect
        ? `<span class="quiz-option-badge correct">Correct answer</span>`
        : "";

    const icon = isSelected && !response.is_correct
      ? `<span class="quiz-option-icon wrong">×</span>`
      : isCorrect
        ? `<span class="quiz-option-icon correct">✓</span>`
        : "";

    return `
      <div class="${classes}">
        ${badge}
        <div class="quiz-option-copy">
          <strong>${escapeHtml(key)}</strong>
          <span>${escapeHtml(text)}</span>
        </div>
        ${icon}
      </div>
    `;
  }).join("");
}

function renderExplanation(response) {
  if (response.is_correct || !response.explanation) {
    quizExplanationCard.classList.add("hidden");
    quizExplanationBody.innerHTML = "";
    return;
  }

  quizExplanationCard.classList.remove("hidden");
  quizExplanationBody.innerHTML = `
    <p>${escapeHtml(response.explanation)}</p>
    <div class="quiz-explanation-callouts">
      <div class="quiz-callout correct">
        <strong>Correct answer</strong>
        <p>${escapeHtml(response.correct_answer)}. ${escapeHtml(response.correct_answer_text || response.correct_answer)}</p>
      </div>
      <div class="quiz-callout incorrect">
        <strong>Your answer</strong>
        <p>${escapeHtml(response.selected_answer || "No answer")}. ${escapeHtml(response.selected_answer_text || response.selected_answer || "No answer")}</p>
      </div>
    </div>
  `;
}

function renderCurrentQuestion() {
  const responses = quizState?.responses || [];
  const response = responses[currentIndex];
  if (!response) return;

  const attempt = quizState.attempt || {};
  const summary = quizState.summary || {};

  quizSidebarScore.textContent = `Score: ${summary.correct_answers}/${summary.total_questions} (${summary.score_percent}%)`;
  quizTipBox.textContent = reviewTip(summary, responses);
  quizTagSubject.textContent = attempt.step_title || attempt.subject_name || "Quiz";
  quizTagMeta.textContent = formatQuestionType(attempt);
  quizQuestionHeading.textContent = `Question ${currentIndex + 1} of ${summary.total_questions}`;
  quizStatusPill.textContent = response.is_correct ? "Correct response" : "Incorrect response";
  quizStatusPill.classList.toggle("incorrect", !response.is_correct);
  quizStatusPill.classList.toggle("correct", response.is_correct);
  quizQuestionText.textContent = response.question_text;

  renderIndexButtons(responses);
  renderOptions(response);
  renderExplanation(response);

  quizPrevSidebar.disabled = currentIndex === 0;
  quizNextSidebar.disabled = currentIndex === responses.length - 1;
}

async function loadSummary() {
  const attemptId = getAttemptId();
  if (!attemptId) {
    throw new Error("Missing attempt id");
  }

  const response = await fetch(`${API_BASE}/api/quiz-summary?attempt_id=${encodeURIComponent(attemptId)}`);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "Failed to load quiz summary");
  }

  quizState = data;
  currentIndex = 0;
  renderCurrentQuestion();
}

quizPrevSidebar.addEventListener("click", () => {
  if (currentIndex === 0) return;
  currentIndex -= 1;
  renderCurrentQuestion();
});

quizNextSidebar.addEventListener("click", () => {
  if (!quizState || currentIndex >= (quizState.responses || []).length - 1) return;
  currentIndex += 1;
  renderCurrentQuestion();
});

loadSummary().catch((error) => {
  quizSidebarScore.textContent = error.message;
  quizTipBox.textContent = "Quiz summary could not be loaded.";
  quizQuestionHeading.textContent = "Quiz summary unavailable";
  quizQuestionText.textContent = error.message;
});
