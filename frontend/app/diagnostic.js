const API_BASE = "http://localhost:8001";
const diagnosticForm = document.getElementById("diagnosticForm");
const diagnosticQuestions = document.getElementById("diagnosticQuestions");
const diagnosticContext = document.getElementById("diagnosticContext");
const diagnosticMeta = document.getElementById("diagnosticMeta");
const quizSubtitle = document.getElementById("quizSubtitle");
const backToDashboard = document.getElementById("backToDashboard");

function readJSON(key) {
  try {
    return JSON.parse(localStorage.getItem(key) || "null");
  } catch {
    return null;
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

function renderQuiz(preview) {
  const questions = Array.isArray(preview?.questions) ? preview.questions : [];
  const topic = preview?.topic || "your topic";
  const level = preview?.level || "beginner";

  quizSubtitle.textContent = `Diagnostic quiz for ${topic}`;
  diagnosticMeta.innerHTML = `
    <div><strong>Topic</strong><span>${escapeHTML(topic)}</span></div>
    <div><strong>Level</strong><span>${escapeHTML(level)}</span></div>
    <div><strong>Questions</strong><span>${questions.length}</span></div>
  `;
  diagnosticContext.innerHTML = `
    <h2>Context</h2>
    <p>${escapeHTML(preview?.context || "No context available.")}</p>
  `;

  if (!questions.length) {
    diagnosticQuestions.innerHTML = "<p>No diagnostic questions available.</p>";
    return;
  }

  diagnosticQuestions.innerHTML = questions.map((question, index) => {
    const options = question.options || {};
    const optionEntries = Object.entries(options);
    return `
      <section class="diagnostic-question">
        <h3>Q${index + 1}. ${escapeHTML(question.question || "")}</h3>
        <div class="diagnostic-options">
          ${optionEntries.map(([key, value]) => `
            <label class="diagnostic-option">
              <input type="radio" name="question_${escapeHTML(String(question.id || index))}" value="${escapeHTML(key)}" />
              <span class="diagnostic-option-box" aria-hidden="true"></span>
              <span class="diagnostic-option-content">
                <strong class="diagnostic-option-key">${escapeHTML(key)}</strong>
                <span class="diagnostic-option-text">${escapeHTML(value)}</span>
              </span>
            </label>
          `).join("")}
        </div>
      </section>
    `;
  }).join("");
}

function collectAnswers(preview) {
  const questions = Array.isArray(preview?.questions) ? preview.questions : [];
  return questions.map((question, index) => {
    const name = `question_${question.id ?? index}`;
    const checked = diagnosticForm.querySelector(`input[name="${CSS.escape(name)}"]:checked`);
    return {
      question_id: String(question.id ?? index),
      answer: checked ? checked.value : ""
    };
  });
}

function validateAnswers(preview) {
  const questions = Array.isArray(preview?.questions) ? preview.questions : [];
  for (const question of questions) {
    const name = `question_${question.id}`;
    if (!diagnosticForm.querySelector(`input[name="${CSS.escape(name)}"]:checked`)) {
      return false;
    }
  }
  return true;
}

const preview = readJSON("adaptiveTutorAssessmentPreview");
const subject = readJSON("adaptiveTutorActiveSubject");
const learnerEmail = localStorage.getItem("adaptiveTutorLearnerEmail");
const studyFlow = readJSON("adaptiveTutorStudyFlow");

if (!preview) {
  window.location.href = "/frontend/subject_topic_entry.html";
  throw new Error("Diagnostic preview is missing");
}

renderQuiz(preview);

backToDashboard.addEventListener("click", () => {
  window.location.href = "/frontend/dashboard.html";
});

diagnosticForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  if (!validateAnswers(preview)) {
    alert("Please answer all questions before submitting.");
    return;
  }

  try {
    const response = await fetch(`${API_BASE}/api/diagnostic/submit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: learnerEmail,
        topic: preview.topic || subject?.subject_name || "",
        level: preview.level || "beginner",
        study_mode: studyFlow?.study_mode || "roadmap",
        questions: preview.questions || [],
        answers: collectAnswers(preview)
      })
    });

    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Failed to submit diagnostic quiz");
    }

    localStorage.setItem("adaptiveTutorLatestDiagnosticResult", JSON.stringify(data.result || {}));
    if (data.subject_id) {
      localStorage.setItem("adaptiveTutorActiveSubjectId", data.subject_id);
    }
    window.location.href = "/frontend/dashboard.html";
  } catch (error) {
    alert(error.message);
  }
});
