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
  const diagnosticResult = (() => {
    try {
      return JSON.parse(localStorage.getItem("adaptiveTutorLatestDiagnosticResult") || "null");
    } catch {
      return null;
    }
  })();
  const assessmentPreview = (() => {
    try {
      return JSON.parse(localStorage.getItem("adaptiveTutorAssessmentPreview") || "null");
    } catch {
      return null;
    }
  })();
  const learningPathPreview = (() => {
    try {
      return JSON.parse(localStorage.getItem("adaptiveTutorLearningPathPreview") || "null");
    } catch {
      return null;
    }
  })();
  const studyFlow = (() => {
    try {
      return JSON.parse(localStorage.getItem("adaptiveTutorStudyFlow") || "null");
    } catch {
      return null;
    }
  })();

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
    currentContentBox.innerHTML = `
      <strong>${currentView.rendered_title || "Current content"}</strong>
      <div>${currentView.rendered_summary || "No summary available."}</div>
      <div style="margin-top:8px;">${currentView.rendered_content || "No content available."}</div>
    `;
  } else if (currentStepContent) {
    currentContentBox.innerHTML = `
      <strong>${currentStepContent.source_title || currentStepContent.step_title || "Current content"}</strong>
      <div style="margin-top:8px;">${currentStepContent.chunk_text || currentStepContent.step_description || "No content available."}</div>
    `;
  } else if (learningPathPreview) {
    const previewSteps = Array.isArray(learningPathPreview.steps) ? learningPathPreview.steps : [];
    currentContentBox.innerHTML = `
      <strong>${learningPathPreview.path_title || "Learning path preview"}</strong>
      <div style="margin-top:8px;">${learningPathPreview.summary || "A study path is ready."}</div>
      <div style="margin-top:12px;"><strong>Steps:</strong> ${previewSteps.length}</div>
      <ul style="margin:8px 0 0; padding-left:18px;">
        ${previewSteps.slice(0, 3).map((step) => `<li>${step.step_title || step.title || "Step"}</li>`).join("")}
      </ul>
    `;
  } else if (assessmentPreview) {
    const previewQuestions = Array.isArray(assessmentPreview.questions) ? assessmentPreview.questions : [];
    currentContentBox.innerHTML = `
      <strong>Assessment preview for ${assessmentPreview.topic || "your topic"}</strong>
      <div style="margin-top:8px;">${assessmentPreview.context || "No context available."}</div>
      <div style="margin-top:12px;"><strong>Generated questions:</strong> ${previewQuestions.length}</div>
      <ul style="margin:8px 0 0; padding-left:18px;">
        ${previewQuestions.slice(0, 3).map((question) => `<li>${question.question || question.id || "Question"}</li>`).join("")}
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
      li.textContent = `${step.step_order}. ${step.step_title} - ${step.step_status}`;
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
