const API_BASE = "http://localhost:8001";
const DEFAULT_QUIZ_LENGTH = 15;

const form = document.getElementById("adaptiveForm");
const subtitle = document.getElementById("adaptiveSubtitle");
const meta = document.getElementById("adaptiveMeta");
const statusBox = document.getElementById("adaptiveStatus");
const questionNode = document.getElementById("adaptiveQuestion");
const optionsNode = document.getElementById("adaptiveOptions");
const submitButton = document.getElementById("adaptiveSubmit");
const feedbackNode = document.getElementById("adaptiveFeedback");
const continueWrap = document.getElementById("adaptiveContinueWrap");
const continueButton = document.getElementById("adaptiveContinue");
const exitButton = document.getElementById("adaptiveExit");

let session = null;
let questionStartedAt = Date.now();

function isLastQuestion(answered = 0) {
  return answered + 1 >= quizLength();
}

function readJSON(key) {
  try {
    return JSON.parse(localStorage.getItem(key) || "null");
  } catch {
    return null;
  }
}

async function readResponseJSON(response) {
  try {
    return await response.json();
  } catch {
    return {};
  }
}

function escapeHTML(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function saveSession() {
  localStorage.setItem("adaptiveTutorAdaptiveQuizSession", JSON.stringify(session));
}

function quizLength() {
  return Number(session?.quiz_length || DEFAULT_QUIZ_LENGTH);
}

function renderMeta(question, answered = 0) {
  const stepOrder = session?.step_order || session?.step?.step_order;
  const totalSteps = session?.total_steps || session?.step?.total_steps;
  const stepLabel = stepOrder && totalSteps ? `${stepOrder} of ${totalSteps}` : stepOrder ? String(stepOrder) : "Current";

  meta.innerHTML = `
    <div><strong>Roadmap step</strong><span>${escapeHTML(stepLabel)}</span></div>
    <div><strong>Quiz progress</strong><span>${answered + 1} of ${quizLength()}</span></div>
    <div><strong>Difficulty</strong><span>${escapeHTML(question?.difficulty || "medium")}</span></div>
  `;
}

function renderQuestion(question, answered = 0) {
  questionStartedAt = Date.now();
  statusBox.classList.add("hidden");
  feedbackNode.classList.add("hidden");
  continueWrap.classList.add("hidden");
  form.classList.remove("hidden");
  submitButton.classList.remove("hidden");
  submitButton.disabled = false;
  submitButton.textContent = "Submit";
  subtitle.textContent = session.step_title || "Adaptive practice";
  renderMeta(question, answered);
  questionNode.textContent = question.question || "Question unavailable";
  optionsNode.innerHTML = Object.entries(question.options || {}).map(([key, value]) => `
    <label class="diagnostic-option">
      <input type="radio" name="adaptive_answer" value="${escapeHTML(key)}" />
      <span class="diagnostic-option-box" aria-hidden="true"></span>
      <span class="diagnostic-option-content">
        <strong class="diagnostic-option-key">${escapeHTML(key)}</strong>
        <span class="diagnostic-option-text">${escapeHTML(value)}</span>
      </span>
    </label>
  `).join("");
}

function storeStartedSession(context, data) {
  const step = data.step || {};
  session = {
    ...context,
    attempt_id: data.attempt_id,
    step_title: step.step_title || context.step_title,
    step_order: step.step_order || context.step_order,
    total_steps: step.total_steps || context.total_steps,
    preview_terms: step.preview_terms || context.preview_terms || [],
    quiz_length: data.quiz_length || DEFAULT_QUIZ_LENGTH,
    questions_answered: data.questions_answered || 0,
    question: data.question,
  };
  saveSession();
}

async function startQuiz() {
  const context = readJSON("adaptiveTutorAdaptiveQuizContext");
  const saved = readJSON("adaptiveTutorAdaptiveQuizSession");
  if (!context) {
    window.location.href = "/frontend/dashboard.html";
    return;
  }

  if (saved?.attempt_id && saved?.question && saved.step_id === context.step_id) {
    session = saved;
    renderQuestion(session.question, session.questions_answered || 0);
    return;
  }

  const response = await fetch(`${API_BASE}/api/adaptive-quiz/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(context),
  });
  const data = await readResponseJSON(response);
  if (!response.ok) throw new Error(data.error || "Failed to start adaptive quiz");

  storeStartedSession(context, data);
  renderQuestion(session.question, session.questions_answered);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const selected = form.querySelector('input[name="adaptive_answer"]:checked');
  if (!selected) {
    alert("Select an answer before submitting.");
    return;
  }

  submitButton.disabled = true;
  submitButton.classList.add("hidden");
  try {
    const response = await fetch(`${API_BASE}/api/adaptive-quiz/answer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        attempt_id: session.attempt_id,
        question_id: session.question.question_id,
        selected_answer: selected.value,
        time_taken_seconds: Math.round((Date.now() - questionStartedAt) / 1000),
      }),
    });
    const data = await readResponseJSON(response);
    if (!response.ok) throw new Error(data.error || "Failed to submit answer");

    form.classList.add("hidden");
    feedbackNode.classList.remove("hidden");
    feedbackNode.classList.toggle("correct", Boolean(data.feedback?.is_correct));
    feedbackNode.classList.toggle("incorrect", !data.feedback?.is_correct);
    feedbackNode.innerHTML = `
      <h2>${data.feedback?.is_correct ? "Correct" : "Not quite"}</h2>
      <p><strong>Correct answer:</strong> ${escapeHTML(data.feedback?.correct_answer || "")}</p>
      <p>${escapeHTML(data.feedback?.explanation || "No explanation available.")}</p>
    `;
    continueWrap.classList.remove("hidden");

    if (data.quiz_complete) {
      localStorage.removeItem("adaptiveTutorAdaptiveQuizSession");
      localStorage.removeItem("adaptiveTutorAdaptiveQuizContext");
      const result = data.result || {};
      const roadmap = data.roadmap || {};
      const nextStep = roadmap.next_step;
      feedbackNode.innerHTML += `
        <p><strong>Result:</strong> ${result.correct_answers || 0}/${result.total_questions || quizLength()}
        (${Math.round(Number(result.score || 0) * 100)}%)</p>
        <p><strong>Roadmap:</strong> ${roadmap.completed_steps || 0}/${roadmap.total_steps || "?"} steps complete.</p>
        ${nextStep ? `<p><strong>Next step:</strong> ${escapeHTML(nextStep.step_title || "Continue roadmap")}</p>` : "<p><strong>Roadmap complete.</strong></p>"}
      `;
      continueButton.textContent = "Finish";
      continueButton.onclick = () => {
        window.location.href = "/frontend/dashboard.html";
      };
      return;
    }

    session.question = data.next_question;
    session.questions_answered = data.progress?.questions_answered || session.questions_answered + 1;
    session.quiz_length = data.progress?.quiz_length || session.quiz_length;
    if (data.step) {
      session.step_title = data.step.step_title || session.step_title;
      session.step_order = data.step.step_order || session.step_order;
      session.total_steps = data.step.total_steps || session.total_steps;
      session.preview_terms = data.step.preview_terms || session.preview_terms;
    }
    saveSession();
    continueButton.textContent = "Next";
    continueButton.onclick = () => renderQuestion(session.question, session.questions_answered);
  } catch (error) {
    submitButton.classList.remove("hidden");
    submitButton.disabled = false;
    alert(error.message);
  }
});

exitButton.addEventListener("click", () => {
  if (window.history.length > 1) {
    window.history.back();
    return;
  }
  window.location.href = "/frontend/dashboard.html";
});

startQuiz().catch((error) => {
  statusBox.textContent = error.message;
  statusBox.classList.add("error");
});
