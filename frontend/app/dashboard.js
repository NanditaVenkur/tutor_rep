const API_BASE = "http://localhost:8001";

const welcomeText = document.getElementById("welcomeText");
const dashName = document.getElementById("dashName");
const dashEmail = document.getElementById("dashEmail");
const dashLanguage = document.getElementById("dashLanguage");
const currentRoadmap = document.getElementById("currentRoadmap");
const currentSubject = document.getElementById("currentSubject");
const currentProgress = document.getElementById("currentProgress");
const currentStepTitle = document.getElementById("currentStepTitle");
const currentStepDescription = document.getElementById("currentStepDescription");
const currentContentBox = document.getElementById("currentContentBox");
const latestQuizSummary = document.getElementById("latestQuizSummary");
const latestQuizResponses = document.getElementById("latestQuizResponses");
const masteryList = document.getElementById("masteryList");
const stepList = document.getElementById("stepList");
const sessionList = document.getElementById("sessionList");

function text(value, fallback = "Not set") {
  return value || fallback;
}

function formatPercent(value) {
  if (value === null || value === undefined || value === "") return "Not set";
  const num = Number(value);
  return Number.isNaN(num) ? String(value) : `${num.toFixed(0)}%`;
}

function formatQuizSummary(quiz) {
  if (!quiz) return "No quiz yet.";
  const total = Number(quiz.total_questions ?? 0);
  const correct = Number(quiz.correct_answers ?? 0);
  return `${correct}/${total || "N/A"}`;
}

function clearList(node, emptyLabel) {
  node.innerHTML = "";
  const item = document.createElement("li");
  item.textContent = emptyLabel;
  node.appendChild(item);
}

function readScopedJSON(key, learnerEmail) {
  try {
    const parsed = JSON.parse(localStorage.getItem(key) || "null");
    if (!parsed) return null;

    if (Object.prototype.hasOwnProperty.call(parsed, "ownerEmail")) {
      return parsed.ownerEmail === learnerEmail ? parsed.value : null;
    }

    return null;
  } catch {
    return null;
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function normalizeHeading(value) {
  return String(value ?? "")
    .replace(/^\d+\.\s*/, "")
    .trim()
    .toLowerCase();
}

function renderMarkdown(value, duplicateTitle = "") {
  const lines = String(value ?? "").split(/\r?\n/);
  const html = [];
  let listOpen = false;
  let skippedDuplicateTitle = false;
  const duplicate = normalizeHeading(duplicateTitle);

  function closeList() {
    if (listOpen) {
      html.push("</ul>");
      listOpen = false;
    }
  }

  lines.forEach((line) => {
    const trimmed = line.trim();

    if (!trimmed) {
      closeList();
      return;
    }

    const heading = trimmed.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      const headingText = heading[2].trim();
      if (!skippedDuplicateTitle && duplicate && normalizeHeading(headingText) === duplicate) {
        skippedDuplicateTitle = true;
        return;
      }
      closeList();
      const level = Math.min(heading[1].length + 2, 4);
      html.push(`<h${level}>${escapeHtml(headingText)}</h${level}>`);
      return;
    }

    if (trimmed.startsWith("- ")) {
      if (!listOpen) {
        html.push("<ul>");
        listOpen = true;
      }
      html.push(`<li>${escapeHtml(trimmed.slice(2))}</li>`);
      return;
    }

    closeList();
    html.push(`<p>${escapeHtml(trimmed)}</p>`);
  });

  closeList();
  return html.join("");
}

function renderCurrentView(currentView, currentStep) {
  const title = currentStep?.step_title || currentView.rendered_title || "Current content";
  const content = currentView.rendered_content || currentView.rendered_summary || "No content available.";

  if (currentView.rendered_format === "markdown") {
    return `<div class="rendered-content">${renderMarkdown(content, title)}</div>`;
  }

  return `<div class="rendered-content"><p>${escapeHtml(content)}</p></div>`;
}

function sentenceFromTerms(terms, fallbackTopic = "this step") {
  if (!Array.isArray(terms) || !terms.length) {
    return `Build a clearer mental model for ${fallbackTopic}.`;
  }

  const visibleTerms = terms.slice(0, 3);
  if (visibleTerms.length === 1) {
    return `Understand ${visibleTerms[0]} well enough to explain it in your own words.`;
  }

  const last = visibleTerms[visibleTerms.length - 1];
  const first = visibleTerms.slice(0, -1).join(", ");
  return `Connect ${first} and ${last} so the step feels practical, not just memorized.`;
}

function renderStepPreviewTerms(terms) {
  if (!Array.isArray(terms) || !terms.length) {
    return "";
  }

  return `
    <div class="roadmap-term-list">
      ${terms.map((term) => `<span>${escapeHtml(term)}</span>`).join("")}
    </div>
  `;
}

function renderCurrentStepFocus(currentStep, currentView) {
  if (!currentStep?.preview_terms?.length) {
    return currentView
      ? renderCurrentView(currentView, currentStep)
      : "<div class=\"rendered-content\"><p>No content available.</p></div>";
  }

  const terms = currentStep.preview_terms;
  const description = currentStep.step_description || currentView?.rendered_summary || "Work through the next concepts in this roadmap step.";

  return `
    <div class="next-step-focus">
      <div>
        <div class="content-label">You will cover</div>
        ${renderStepPreviewTerms(terms)}
      </div>
      <div class="next-step-note">
        <div class="content-label">Goal</div>
        <p>${escapeHtml(description)}</p>
      </div>
      <div class="next-step-note">
        <div class="content-label">Checkpoint</div>
        <p>${escapeHtml(sentenceFromTerms(terms, currentStep.step_title || "this step"))}</p>
      </div>
    </div>
  `;
}

function renderSummary(data) {
  const learner = data.learner || {};
  const prefs = data.preferences || {};
  const active = data.active_subject || {};
  const roadmap = active.active_path || {};
  const steps = active.path_steps || [];
  const latestQuiz = active.latest_quiz || null;
  const responses = active.latest_quiz_responses || [];
  const mastery = active.topic_mastery || [];
  const currentStep = active.current_step || null;
  const currentView = active.current_view || null;
  const currentStepContent = active.current_step_content || null;
  const sessions = data.recent_sessions || [];
  const learnerEmail = learner.email || localStorage.getItem("adaptiveTutorLearnerEmail") || "";
  const diagnosticResult = readScopedJSON("adaptiveTutorLatestDiagnosticResult", learnerEmail);
  const assessmentPreview = readScopedJSON("adaptiveTutorAssessmentPreview", learnerEmail);
  const learningPathPreview = readScopedJSON("adaptiveTutorLearningPathPreview", learnerEmail);
  const studyFlow = readScopedJSON("adaptiveTutorStudyFlow", learnerEmail);

  welcomeText.textContent = learner.full_name ? `Welcome back, ${learner.full_name}.` : "Welcome back.";
  dashName.textContent = text(learner.full_name);
  dashEmail.textContent = text(learner.email);
  dashLanguage.textContent = text(learner.preferred_language);

  currentSubject.textContent = text(active.subject_name);
  currentRoadmap.textContent = roadmap.path_title
    ? `${roadmap.path_title} ${roadmap.path_status ? `(${roadmap.path_status})` : ""}`
    : "No active roadmap.";
  if (studyFlow) {
    currentRoadmap.textContent += ` • ${studyFlow.description || studyFlow.study_mode || "selected flow"}`;
  }
  currentProgress.textContent = active.path_completion_pct !== undefined
    ? `${formatPercent(active.path_completion_pct)} complete`
    : "Not set";

  if (currentStep) {
    currentStepTitle.textContent = `${currentStep.step_order}. ${currentStep.step_title}`;
    currentStepDescription.textContent = text(currentStep.step_description, "No description available.");
  } else {
    currentStepTitle.textContent = "No active step.";
    currentStepDescription.textContent = "Choose a topic to begin.";
  }

  if (currentView) {
    currentContentBox.innerHTML = renderCurrentStepFocus(currentStep, currentView);
  } else if (currentStepContent) {
    currentContentBox.innerHTML = currentStep?.preview_terms?.length
      ? renderCurrentStepFocus(currentStep, null)
      : `
        <strong>${escapeHtml(currentStepContent.source_title || currentStepContent.step_title || "Current content")}</strong>
        <div style="margin-top:8px;">${escapeHtml(currentStepContent.chunk_text || currentStepContent.step_description || "No content available.")}</div>
      `;
  } else if (learningPathPreview) {
    const previewSteps = Array.isArray(learningPathPreview.steps) ? learningPathPreview.steps : [];
    currentContentBox.innerHTML = `
      <strong>${escapeHtml(learningPathPreview.path_title || "Learning path preview")}</strong>
      <div style="margin-top:8px;">${escapeHtml(learningPathPreview.summary || "A study path is ready.")}</div>
      <div style="margin-top:12px;"><strong>Steps:</strong> ${previewSteps.length}</div>
      <ul style="margin:8px 0 0; padding-left:18px;">
        ${previewSteps.slice(0, 3).map((step) => `<li>${escapeHtml(step.step_title || step.title || "Step")}</li>`).join("")}
      </ul>
    `;
  } else if (assessmentPreview) {
    const previewQuestions = Array.isArray(assessmentPreview.questions) ? assessmentPreview.questions : [];
    currentContentBox.innerHTML = `
      <strong>Assessment preview for ${escapeHtml(assessmentPreview.topic || "your topic")}</strong>
      <div style="margin-top:8px;">${escapeHtml(assessmentPreview.context || "No context available.")}</div>
      <div style="margin-top:12px;"><strong>Generated questions:</strong> ${previewQuestions.length}</div>
      <ul style="margin:8px 0 0; padding-left:18px;">
        ${previewQuestions.slice(0, 3).map((question) => `<li>${escapeHtml(question.question || question.id || "Question")}</li>`).join("")}
      </ul>
    `;
  } else if (studyFlow?.route === "quick_study") {
    currentContentBox.innerHTML = `
      <strong>Quick study mode</strong>
      <div style="margin-top:8px;">A concise explanation will appear here for the selected topic.</div>
    `;
  } else {
    currentContentBox.textContent = "Content will appear here.";
  }

  if (latestQuiz) {
    latestQuizSummary.textContent = formatQuizSummary(latestQuiz);
    latestQuizResponses.innerHTML = "";
    if (responses.length) {
      responses.forEach((response) => {
        const li = document.createElement("li");
        li.textContent = `${response.is_correct ? "✓" : "✕"} ${response.question_text || response.question_id || "Question"}`
          + (response.selected_answer ? ` - ${response.selected_answer}` : "");
        latestQuizResponses.appendChild(li);
      });
    } else {
      clearList(latestQuizResponses, "No response details yet.");
    }
  } else {
    latestQuizSummary.textContent = diagnosticResult ? formatQuizSummary(diagnosticResult) : "No quiz yet.";
    clearList(latestQuizResponses, "No quiz responses yet.");
  }

  if (mastery.length) {
    masteryList.innerHTML = "";
    mastery.forEach((item) => {
      const li = document.createElement("li");
      li.textContent = `${item.topic_id}: ${formatPercent(item.mastery_probability)} (${item.mastery_status || "unknown"})`;
      masteryList.appendChild(li);
    });
  } else {
    clearList(masteryList, "No mastery data yet.");
  }

  if (steps.length) {
    stepList.innerHTML = "";
    steps.forEach((step) => {
      const li = document.createElement("li");
      li.className = "roadmap-step-item";
      li.innerHTML = `
        <div class="roadmap-step-line">
          <span>${escapeHtml(step.step_order)}. ${escapeHtml(step.step_title)}</span>
          <span class="roadmap-step-status">${escapeHtml(step.step_status)}</span>
        </div>
        ${renderStepPreviewTerms(step.preview_terms)}
      `;
      stepList.appendChild(li);
    });
  } else {
    clearList(stepList, "No roadmap steps yet.");
  }

  if (sessions.length) {
    sessionList.innerHTML = "";
    sessions.forEach((session) => {
      const li = document.createElement("li");
      li.textContent = `${session.session_type} - ${session.session_status} - ${session.session_summary || session.started_at}`;
      sessionList.appendChild(li);
    });
  } else {
    clearList(sessionList, "No recent sessions.");
  }
}

async function loadDashboard() {
  const email = localStorage.getItem("adaptiveTutorLearnerEmail");
  if (!email) {
    window.location.href = "/frontend/index.html";
    return;
  }

  const response = await fetch(`${API_BASE}/api/dashboard?email=${encodeURIComponent(email)}`);
  const data = await response.json();

  if (!response.ok) {
    throw new Error(data.error || "Failed to load dashboard");
  }

  renderSummary(data);
}

loadDashboard().catch((error) => {
  welcomeText.textContent = error.message;
  currentContentBox.textContent = "Unable to load dashboard data.";
});
