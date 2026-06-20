const API_BASE = "http://localhost:8001";

const welcomeText = document.getElementById("welcomeText");
const dashName = document.getElementById("dashName");
const dashEmail = document.getElementById("dashEmail");
const dashLanguage = document.getElementById("dashLanguage");
const currentRoadmap = document.getElementById("currentRoadmap");
const currentSubject = document.getElementById("currentSubject");
const currentProgress = document.getElementById("currentProgress");
const roadmapSummary = document.getElementById("roadmapSummary");
const currentContentBox = document.getElementById("currentContentBox");
const latestQuizSummary = document.getElementById("latestQuizSummary");
const latestQuizResponses = document.getElementById("latestQuizResponses");
const masteryList = document.getElementById("masteryList");
const stepList = document.getElementById("stepList");
const sessionList = document.getElementById("sessionList");
const subjectStrip = document.getElementById("subjectStrip");
const dashboardStudyForm = document.getElementById("dashboardStudyForm");
const dashboardTopicInput = document.getElementById("dashboardTopicInput");
const dashboardStudyMode = document.getElementById("dashboardStudyMode");
const dashboardFamiliarity = document.getElementById("dashboardFamiliarity");

const state = {
  data: null,
  selectedSubjectId: null,
  selectedStepId: null,
};

function readJSON(key) {
  try {
    return JSON.parse(localStorage.getItem(key) || "null");
  } catch {
    return null;
  }
}

function text(value, fallback = "Not set") {
  return value || fallback;
}

function escapeHTML(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
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

function getStoredSubjectId() {
  return (
    new URLSearchParams(window.location.search).get("subject_id")
    || localStorage.getItem("adaptiveTutorDashboardSubjectId")
    || ""
  );
}

function setStoredSubjectId(subjectId) {
  if (subjectId) {
    localStorage.setItem("adaptiveTutorDashboardSubjectId", subjectId);
  } else {
    localStorage.removeItem("adaptiveTutorDashboardSubjectId");
  }
}

function updateLocation(subjectId) {
  const base = "/frontend/dashboard.html";
  const nextUrl = subjectId ? `${base}?subject_id=${encodeURIComponent(subjectId)}` : base;
  window.history.replaceState({}, "", nextUrl);
}

function normalizeStudyMode(value) {
  return String(value || "roadmap").trim().toLowerCase().replaceAll(" ", "_");
}

function renderSubjects(subjectProfiles, activeSubjectId) {
  if (!subjectStrip) return;
  if (!Array.isArray(subjectProfiles) || !subjectProfiles.length) {
    subjectStrip.innerHTML = `<div class="subject-empty">No saved subjects yet. Start a new topic below.</div>`;
    return;
  }

  subjectStrip.innerHTML = subjectProfiles.map((profile) => {
    const isActive = profile.subject_id === activeSubjectId;
    return `
      <button type="button" class="subject-pill ${isActive ? "active" : ""}" data-subject-id="${escapeHTML(profile.subject_id)}">
        <span class="subject-pill-name">${escapeHTML(profile.subject_name || "Subject")}</span>
        <span class="subject-pill-meta">
          ${escapeHTML(profile.current_level || "new")}
          ${profile.last_assessed_score !== null && profile.last_assessed_score !== undefined ? `• ${formatPercent(profile.last_assessed_score)}` : ""}
        </span>
      </button>
    `;
  }).join("");

  subjectStrip.querySelectorAll("[data-subject-id]").forEach((button) => {
    button.addEventListener("click", () => {
      const subjectId = button.dataset.subjectId;
      if (subjectId) {
        loadDashboard(subjectId);
      }
    });
  });
}

function buildPathViews(activeSubject) {
  const views = Array.isArray(activeSubject?.path_views) ? activeSubject.path_views : [];
  return new Map(views.map((view) => [view.step_id, view]));
}

function buildPathSteps(activeSubject) {
  return Array.isArray(activeSubject?.path_steps) ? activeSubject.path_steps : [];
}

function getCurrentStep(activeSubject, selectedStepId) {
  const steps = buildPathSteps(activeSubject);
  if (!steps.length) return null;
  if (selectedStepId) {
    const selected = steps.find((step) => step.step_id === selectedStepId);
    if (selected) return selected;
  }
  return null;
}

function renderRoadmapSteps(activeSubject) {
  const steps = buildPathSteps(activeSubject);
  const pathViews = buildPathViews(activeSubject);

  if (!steps.length) {
    clearList(stepList, "No roadmap steps yet.");
    return;
  }

  stepList.innerHTML = "";
  steps.forEach((step) => {
    const view = pathViews.get(step.step_id);
    const li = document.createElement("li");
    li.className = "roadmap-step-item";

    const button = document.createElement("button");
    button.type = "button";
    button.className = `roadmap-step-card ${step.step_id === state.selectedStepId ? "active" : ""}`;
    button.innerHTML = `
      <span class="roadmap-step-order">${step.step_order}</span>
      <span class="roadmap-step-body">
        <strong>${escapeHTML(step.step_title || "Step")}</strong>
        <span>${escapeHTML(step.step_description || "No description available.")}</span>
        <em>${escapeHTML(step.step_status || "not_started")} • ${escapeHTML(String(step.estimated_minutes || 0))} min</em>
        ${view?.rendered_summary ? `<small>${escapeHTML(view.rendered_summary)}</small>` : ""}
      </span>
    `;
    button.addEventListener("click", () => {
      state.selectedStepId = step.step_id;
      renderSummary(state.data);
    });

    li.appendChild(button);
    stepList.appendChild(li);
  });
}

function renderStepContent(activeSubject, selectedStep) {
  const pathViews = buildPathViews(activeSubject);
  const view = selectedStep ? pathViews.get(selectedStep.step_id) : null;
  const studyFlow = readJSON("adaptiveTutorStudyFlow");
  const selectedSource = view || null;
  const contentTitle = selectedSource?.rendered_title
    || selectedStep?.step_title
    || "Select a roadmap step";
  const summary = selectedSource?.rendered_summary
    || selectedStep?.step_description
    || "Click a roadmap step to view the lesson content.";
  const contentBody = selectedSource?.rendered_content
    || selectedSource?.chunk_text
    || "Select a roadmap step above to see the saved content for that lesson.";
  const contentMeta = selectedSource
    ? [
        selectedSource.rendered_format ? `Format: ${selectedSource.rendered_format}` : null,
        selectedSource.reading_level ? `Level: ${selectedSource.reading_level}` : null,
        selectedStep?.estimated_minutes ? `Est. ${selectedStep.estimated_minutes} min` : null,
      ].filter(Boolean).join(" • ")
    : selectedStep?.estimated_minutes
      ? `Est. ${selectedStep.estimated_minutes} min`
      : "";

  currentContentBox.innerHTML = `
    <div class="content-card">
      <p class="eyebrow">Step content</p>
      <h3>${escapeHTML(contentTitle)}</h3>
      <p class="subtle">${escapeHTML(summary)}</p>
      ${contentMeta ? `<p class="content-meta">${escapeHTML(contentMeta)}</p>` : ""}
      <div class="content-rich">${escapeHTML(contentBody).replaceAll("\n", "<br />")}</div>
      ${!selectedSource ? `
        <div class="content-preview-note">
          ${studyFlow?.route === "diagnostic_quiz"
            ? "Take the diagnostic quiz first, then click a roadmap step to open its lesson."
            : "Click any roadmap step to open its lesson content."}
        </div>
      ` : ""}
    </div>
  `;
}

function renderQuizAndMastery(activeSubject) {
  const latestQuiz = activeSubject?.latest_quiz || null;
  const responses = activeSubject?.latest_quiz_responses || [];
  const mastery = activeSubject?.topic_mastery || [];
  const sessions = state.data?.recent_sessions || [];

  if (latestQuiz) {
    latestQuizSummary.textContent = formatQuizSummary(latestQuiz);
    latestQuizResponses.innerHTML = "";
    if (responses.length) {
      responses.forEach((response) => {
        const li = document.createElement("li");
        li.textContent = `${response.is_correct ? "✓" : "✕"} ${response.question_text || response.question_id || "Question"}${response.selected_answer ? ` - ${response.selected_answer}` : ""}`;
        latestQuizResponses.appendChild(li);
      });
    } else {
      clearList(latestQuizResponses, "No response details yet.");
    }
  } else {
    latestQuizSummary.textContent = "No quiz yet.";
    clearList(latestQuizResponses, "No quiz responses yet.");
  }

  if (mastery.length) {
    masteryList.innerHTML = "";
    mastery.forEach((item) => {
      const li = document.createElement("li");
      li.innerHTML = `
        <strong>${escapeHTML(item.topic_name || item.topic_id || "Topic")}</strong>
        <span>${escapeHTML(formatPercent(item.mastery_probability))} • ${escapeHTML(item.mastery_status || "unknown")}</span>
      `;
      masteryList.appendChild(li);
    });
  } else {
    clearList(masteryList, "No mastery data yet.");
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

function renderSummary(data) {
  state.data = data;
  const learner = data.learner || {};
  const prefs = data.preferences || {};
  const subjectProfiles = data.subject_profiles || [];
  const active = data.active_subject || {};
  const roadmap = active.active_path || {};
  const assessmentPreview = readJSON("adaptiveTutorAssessmentPreview");
  const learningPathPreview = readJSON("adaptiveTutorLearningPathPreview");
  const selectedSubjectId = active.subject_id || state.selectedSubjectId || data.selected_subject_id || null;
  state.selectedSubjectId = selectedSubjectId;
  const selectedStep = getCurrentStep(active, state.selectedStepId);

  welcomeText.textContent = learner.full_name ? `Welcome back, ${learner.full_name}.` : "Welcome back.";
  dashName.textContent = text(learner.full_name);
  dashEmail.textContent = text(learner.email);
  dashLanguage.textContent = text(learner.preferred_language);

  currentSubject.textContent = text(active.subject_name);
  currentRoadmap.textContent = roadmap.path_title
    ? `${roadmap.path_title} ${roadmap.path_status ? `(${roadmap.path_status})` : ""}`
    : "No active roadmap.";
  currentProgress.textContent = active.path_completion_pct !== undefined
    ? `${formatPercent(active.path_completion_pct)} complete`
    : "Not set";
  roadmapSummary.textContent = roadmap.target_outcome
    || learningPathPreview?.summary
    || assessmentPreview?.context
    || active.goal_type
    || "Select a subject or start a new topic to build a roadmap.";

  renderSubjects(subjectProfiles, selectedSubjectId);
  renderRoadmapSteps(active);
  renderStepContent(active, selectedStep);
  renderQuizAndMastery(active);

  localStorage.setItem("adaptiveTutorDashboardSubjectId", selectedSubjectId || "");
}

async function loadDashboard(subjectId = "") {
  const email = localStorage.getItem("adaptiveTutorLearnerEmail");
  if (!email) {
    window.location.href = "/frontend/index.html";
    return;
  }

  if (subjectId) {
    state.selectedSubjectId = subjectId;
    state.selectedStepId = null;
    setStoredSubjectId(subjectId);
  }

  const search = new URLSearchParams({ email });
  if (subjectId) {
    search.set("subject_id", subjectId);
  }

  const response = await fetch(`${API_BASE}/api/dashboard?${search.toString()}`);
  const data = await response.json();

  if (!response.ok) {
    throw new Error(data.error || "Failed to load dashboard");
  }

  updateLocation(subjectId);
  renderSummary(data);
}

async function submitDashboardStudy(event) {
  event.preventDefault();
  const values = Object.fromEntries(new FormData(dashboardStudyForm).entries());
  const learnerId = localStorage.getItem("adaptiveTutorLearnerId");
  const learnerEmail = localStorage.getItem("adaptiveTutorLearnerEmail");

  try {
    const response = await fetch(`${API_BASE}/api/topic`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...values,
        learner_id: learnerId || null,
        learner_email: learnerEmail || null,
      }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Failed to create study request");
    }

    localStorage.setItem("adaptiveTutorSelectedTopic", JSON.stringify(values));
    if (data.study_flow) {
      localStorage.setItem("adaptiveTutorStudyFlow", JSON.stringify(data.study_flow));
    }
    if (data.assessment_preview) {
      localStorage.setItem("adaptiveTutorAssessmentPreview", JSON.stringify(data.assessment_preview));
    }
    if (data.learning_path) {
      localStorage.setItem("adaptiveTutorLearningPathPreview", JSON.stringify(data.learning_path));
    }
    if (data.subject) {
      localStorage.setItem("adaptiveTutorActiveSubject", JSON.stringify(data.subject));
      setStoredSubjectId(data.subject.subject_id);
    }

    const mode = normalizeStudyMode(data.study_flow?.study_mode || values.study_mode || "roadmap");
    alert(`${mode.replaceAll("_", " ")} started for: ${values.topic}`);

    if (data.study_flow?.route === "diagnostic_quiz") {
      window.location.href = "/frontend/diagnostic_quiz.html";
      return;
    }

    await loadDashboard(data.subject?.subject_id || state.selectedSubjectId || "");
  } catch (error) {
    alert(error.message);
  }
}

dashboardStudyForm?.addEventListener("submit", submitDashboardStudy);

(async function bootstrap() {
  const selectedSubjectId = getStoredSubjectId();
  try {
    await loadDashboard(selectedSubjectId);
  } catch (error) {
    welcomeText.textContent = error.message;
    currentContentBox.textContent = "Unable to load dashboard data.";
  }
})();
