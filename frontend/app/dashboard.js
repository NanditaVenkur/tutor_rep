const API_BASE = "http://localhost:8001";
const SELECTED_SUBJECT_KEY = "adaptiveTutorSelectedSubjectId";

const welcomeText = document.getElementById("welcomeText");
const userMenuButton = document.getElementById("userMenuButton");
const userMenuPanel = document.getElementById("userMenuPanel");
const dashName = document.getElementById("dashName");
const dashEmail = document.getElementById("dashEmail");
const dashLanguage = document.getElementById("dashLanguage");
const subjectList = document.getElementById("subjectList");
const selectedSubjectTitle = document.getElementById("selectedSubjectTitle");
const selectedSubjectTopic = document.getElementById("selectedSubjectTopic");
const selectedSubjectStats = document.getElementById("selectedSubjectStats");
const detailRoadmapText = document.getElementById("detailRoadmapText");
const detailRoadmapMeta = document.getElementById("detailRoadmapMeta");
const detailStepTitle = document.getElementById("detailStepTitle");
const detailStepDescription = document.getElementById("detailStepDescription");
const detailCurrentContent = document.getElementById("detailCurrentContent");
const practiceStepButton = document.getElementById("practiceStepButton");
const viewGraphButton = document.getElementById("viewGraphButton");
const detailStepList = document.getElementById("detailStepList");
const detailSessionList = document.getElementById("detailSessionList");

let dashboardState = null;

function text(value, fallback = "Not set") {
  return value || fallback;
}

function formatPercent(value) {
  if (value === null || value === undefined || value === "") return "Not set";
  const num = Number(value);
  if (Number.isNaN(num)) return String(value);
  const pct = num <= 1 ? num * 100 : num;
  return `${pct.toFixed(0)}%`;
}

function formatScore(value) {
  if (value === null || value === undefined || value === "") return "Not set";
  const num = Number(value);
  if (Number.isNaN(num)) return String(value);
  return `${Math.round(num * 100)}%`;
}

function formatQuizSummary(quiz) {
  if (!quiz) return "No quiz yet.";
  const total = Number(quiz.total_questions ?? 0);
  const correct = Number(quiz.correct_answers ?? 0);
  return `${correct}/${total || "N/A"} correct`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function clearList(node, emptyLabel) {
  node.innerHTML = "";
  const item = document.createElement("li");
  item.textContent = emptyLabel;
  node.appendChild(item);
}

function renderMarkdown(value, duplicateTitle = "") {
  const lines = String(value ?? "").split(/\r?\n/);
  const html = [];
  let listOpen = false;
  const duplicate = String(duplicateTitle || "").trim().toLowerCase();
  let skippedDuplicate = false;

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
      const normalized = headingText.replace(/^\d+\.\s*/, "").trim().toLowerCase();
      if (!skippedDuplicate && duplicate && normalized === duplicate) {
        skippedDuplicate = true;
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
  const title = currentStep?.step_title || currentView?.rendered_title || "Current content";
  const content = currentView?.rendered_content || currentView?.rendered_summary || "No content available.";

  if (currentView?.rendered_format === "markdown") {
    return `<div class="rendered-content">${renderMarkdown(content, title)}</div>`;
  }

  return `<div class="rendered-content"><p>${escapeHtml(content)}</p></div>`;
}

function renderStepPreviewTerms(terms) {
  if (!Array.isArray(terms) || !terms.length) return "";
  return `
    <div class="roadmap-term-list">
      ${terms.map((term) => `<span>${escapeHtml(term)}</span>`).join("")}
    </div>
  `;
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

function renderContentFocus(currentStep, currentView) {
  if (currentStep?.preview_terms?.length) {
    const description = currentStep.step_description || currentView?.rendered_summary || "Work through the next concepts in this roadmap step.";
    return `
      <div class="next-step-focus">
        <div>
          <div class="content-label">You will cover</div>
          ${renderStepPreviewTerms(currentStep.preview_terms)}
        </div>
        <div class="next-step-note">
          <div class="content-label">Goal</div>
          <p>${escapeHtml(description)}</p>
        </div>
        <div class="next-step-note">
          <div class="content-label">Checkpoint</div>
          <p>${escapeHtml(sentenceFromTerms(currentStep.preview_terms, currentStep.step_title || "this step"))}</p>
        </div>
      </div>
    `;
  }

  if (currentView) {
    return renderCurrentView(currentView, currentStep);
  }

  return `<div class="rendered-content"><p>No content available.</p></div>`;
}

function renderStat(label, value) {
  return `
    <div class="stat-chip">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
    </div>
  `;
}

function renderTopicPills(items, tone) {
  if (!Array.isArray(items) || !items.length) {
    return "";
  }

  return `
    <div class="mastery-pill-group ${escapeHtml(tone)}">
      ${items.map((item) => `<span class="mastery-pill">${escapeHtml(item)}</span>`).join("")}
    </div>
  `;
}

function renderMasteryStepCard(step) {
  const strong = step.topics_strong || [];
  const review = step.topics_to_review || [];
  const practice = step.topics_to_practice || [];
  const subtopics = step.subtopics || [];
  const accuracy = step.accuracy === null || step.accuracy === undefined ? "Not set" : `${step.accuracy.toFixed(1)}%`;
  const answered = Number(step.answered_questions || 0);
  const correct = Number(step.correct_answers || 0);
  const hasAnySignals = strong.length || review.length || practice.length;

  return `
    <li class="mastery-step-card">
      <div class="mastery-step-head">
        <div>
          <p class="mastery-step-label">Step ${escapeHtml(step.step_order ?? "—")}</p>
          <h4>${escapeHtml(step.step_title || "Untitled step")}</h4>
        </div>
        <span class="mastery-step-badge">${escapeHtml(step.step_status || "unknown")}</span>
      </div>
      <p class="mastery-step-summary">${escapeHtml(step.summary || "No summary available.")}</p>
      <div class="mastery-step-metrics">
        <span>${answered} answered</span>
        <span>${correct} correct</span>
        <span>${accuracy} accuracy</span>
      </div>
      ${renderTopicPills(strong, "strong")}
      ${renderTopicPills(review, "review")}
      ${renderTopicPills(practice, "practice")}
      ${!hasAnySignals && Array.isArray(step.preview_terms) && step.preview_terms.length
        ? renderTopicPills(step.preview_terms, "untried")
        : ""}
      {subtopics}
    </li>
  `.replace("{subtopics}", subtopics.length ? `
      <div class="mastery-subtopic-grid">
        ${subtopics.map((topic) => `
          <div class="mastery-subtopic-card ${escapeHtml(topic.bucket)}">
            <div class="mastery-subtopic-title">${escapeHtml(topic.topic)}</div>
            <div class="mastery-subtopic-meta">
              <span>${topic.answered ? `${topic.answered} answer${topic.answered === 1 ? "" : "s"}` : "Not practiced"}</span>
              <span>${topic.correct ? `${topic.correct} correct` : "0 correct"}</span>
              <span>${topic.accuracy === null || topic.accuracy === undefined ? "No score yet" : `${topic.accuracy.toFixed(1)}%`}</span>
            </div>
          </div>
        `).join("")}
      </div>
    ` : "");
}

function renderQuizPanel(step, quizInfo, isCurrentStep) {
  if (!quizInfo?.attempt) {
    return `
      <div class="step-section">
        <div class="step-section-head">
          <span class="step-section-label">Quiz</span>
          <span class="step-section-chip">No quiz yet</span>
        </div>
        <p class="step-section-text">Take the quiz for this step to unlock the review summary.</p>
      </div>
    `;
  }

  const attempt = quizInfo.attempt;
  const reviewHref = `/frontend/quiz_summary.html?attempt_id=${encodeURIComponent(attempt.attempt_id)}`;
  return `
    <div class="step-section">
      <div class="step-section-head">
        <span class="step-section-label">Quiz</span>
        <span class="step-section-chip">${escapeHtml(formatQuizSummary(attempt))}</span>
      </div>
      <div>
        <a class="btn secondary dashboard-link" href="${reviewHref}">Review summary</a>
      </div>
    </div>
  `;
}

function renderMasteryPanel(step, masterySummary) {
  if (!masterySummary) {
    return `
      <div class="step-section">
        <div class="step-section-head">
          <span class="step-section-label">Mastery</span>
          <span class="step-section-chip">No mastery data</span>
        </div>
        <p class="step-section-text">We do not have mastery data for this step yet.</p>
      </div>
    `;
  }

  const strong = masterySummary.topics_strong || [];
  const review = masterySummary.topics_to_review || [];
  const practice = masterySummary.topics_to_practice || [];
  const subtopics = masterySummary.subtopics || [];
  return `
    <div class="step-section">
      <div class="step-section-head">
        <span class="step-section-label">Mastery</span>
        <span class="step-section-chip">${escapeHtml(masterySummary.summary || "Step summary")}</span>
      </div>
      <p class="step-section-text">
        ${escapeHtml((step.preview_terms || []).length ? "Subtopic-level mastery based on the latest quiz answers." : "Mastery details for this step.")}
      </p>
      <div class="mastery-pill-group strong">${strong.map((item) => `<span class="mastery-pill">${escapeHtml(item)}</span>`).join("")}</div>
      <div class="mastery-pill-group review">${review.map((item) => `<span class="mastery-pill">${escapeHtml(item)}</span>`).join("")}</div>
      <div class="mastery-pill-group practice">${practice.map((item) => `<span class="mastery-pill">${escapeHtml(item)}</span>`).join("")}</div>
      ${subtopics.length ? `
        <div class="mastery-subtopic-grid">
          ${subtopics.map((topic) => `
            <div class="mastery-subtopic-card ${escapeHtml(topic.bucket)}">
              <div class="mastery-subtopic-title">${escapeHtml(topic.topic)}</div>
              <div class="mastery-subtopic-meta">
                <span>${topic.answered ? `${topic.answered} answer${topic.answered === 1 ? "" : "s"}` : "Not practiced"}</span>
                <span>${topic.correct ? `${topic.correct} correct` : "0 correct"}</span>
                <span>${topic.accuracy === null || topic.accuracy === undefined ? "No score yet" : `${topic.accuracy.toFixed(1)}%`}</span>
              </div>
            </div>
          `).join("")}
        </div>
      ` : ""}
    </div>
  `;
}

function renderStepAccordion(step, active, quizInfo, masterySummary) {
  const isCurrentStep = active?.current_step?.step_id === step.step_id;
  const isCompleted = step.step_status === "completed";
  const isReview = step.step_status === "needs_review";
  const summaryClass = isCurrentStep ? "current" : isCompleted ? "completed" : isReview ? "review" : "";
  const openAttr = isCurrentStep ? " open" : "";

  return `
    <details class="step-accordion ${summaryClass}"${openAttr}>
      <summary class="step-accordion-summary">
        <div class="step-accordion-main">
          <span class="step-accordion-order">Step ${escapeHtml(step.step_order ?? "—")}</span>
          <strong>${escapeHtml(step.step_title || "Untitled step")}</strong>
          <span class="step-accordion-desc">${escapeHtml(step.step_description || "No description available.")}</span>
        </div>
        <div class="step-accordion-meta">
          <span class="step-accordion-badge">${escapeHtml(step.step_status || "unknown")}</span>
          <span class="step-accordion-chev" aria-hidden="true">⌄</span>
        </div>
      </summary>
      <div class="step-accordion-body">
        <div class="step-card-grid">
          <div class="step-section">
            <div class="step-section-head">
              <span class="step-section-label">Overview</span>
              <span class="step-section-chip">${escapeHtml(isCurrentStep ? "Current step" : step.step_status || "unknown")}</span>
            </div>
            <p class="step-section-text">${escapeHtml(step.step_description || "No description available.")}</p>
            ${renderStepPreviewTerms(step.preview_terms)}
          </div>
          <div class="step-section">
            <div class="step-section-head">
              <span class="step-section-label">Step focus</span>
              <span class="step-section-chip">${escapeHtml((step.preview_terms || []).length ? `${step.preview_terms.length} subtopics` : "No subtopics")}</span>
            </div>
            ${isCurrentStep && active?.current_view ? renderContentFocus(step, active.current_view) : `
              <p class="step-section-text">${escapeHtml(sentenceFromTerms(step.preview_terms, step.step_title || "this step"))}</p>
            `}
            ${isCurrentStep && active?.current_step_content ? `
              <div class="content-box step-current-content">
                <strong>${escapeHtml(active.current_step_content.source_title || active.current_step_content.step_title || "Current content")}</strong>
                <div style="margin-top:8px;">${escapeHtml(active.current_step_content.chunk_text || active.current_step_content.step_description || "No content available.")}</div>
              </div>
            ` : ""}
            ${isCurrentStep && active?.subject_id && active?.active_path?.path_id ? `
              <button class="btn primary step-practice-btn" type="button" data-practice-step="${escapeHtml(step.step_id)}">Practice this step</button>
            ` : ""}
          </div>
          ${renderQuizPanel(step, quizInfo, isCurrentStep)}
          ${renderMasteryPanel(step, masterySummary)}
        </div>
      </div>
    </details>
  `;
}

function setSelectedSubject(selectedSubjectId) {
  if (selectedSubjectId) {
    localStorage.setItem(SELECTED_SUBJECT_KEY, selectedSubjectId);
    localStorage.setItem("adaptiveTutorActiveSubjectId", selectedSubjectId);
  } else {
    localStorage.removeItem(SELECTED_SUBJECT_KEY);
    localStorage.removeItem("adaptiveTutorActiveSubjectId");
  }
}

function getSelectedSubjectId() {
  return localStorage.getItem(SELECTED_SUBJECT_KEY)
    || localStorage.getItem("adaptiveTutorActiveSubjectId")
    || "";
}

async function loadDashboard(selectedSubjectId = getSelectedSubjectId()) {
  const email = localStorage.getItem("adaptiveTutorLearnerEmail");
  if (!email) {
    window.location.href = "/frontend/index.html";
    return;
  }

  const params = new URLSearchParams({ email });
  if (selectedSubjectId) {
    params.set("selected_subject_id", selectedSubjectId);
  }

  const response = await fetch(`${API_BASE}/api/dashboard?${params.toString()}`);
  const data = await response.json();

  if (!response.ok) {
    throw new Error(data.error || "Failed to load dashboard");
  }

  dashboardState = data;
  renderDashboard(data);
  setSelectedSubject(data.selected_subject_id || data.active_subject?.subject_id || "");
}

function renderSubjectCards(subjectProfiles, activeSubjectId) {
  subjectList.innerHTML = "";

  if (!subjectProfiles.length) {
    subjectList.innerHTML = `<p class="empty-state">No subjects yet. Start a new topic to create your first roadmap.</p>`;
    return;
  }

  subjectProfiles.forEach((profile) => {
    const isQuickStudy = profile.goal_type === "quick_study";
    const button = document.createElement("button");
    button.type = "button";
    button.className = `subject-card${profile.subject_id === activeSubjectId ? " active" : ""}`;
    button.innerHTML = `
      <div class="subject-card-top">
        <div>
          <p class="subject-card-label">Subject / topic</p>
          <h3>${escapeHtml(profile.subject_name || profile.subject_id)}</h3>
        </div>
        <span class="subject-card-badge">${escapeHtml(isQuickStudy ? "quick study" : profile.status || "active")}</span>
      </div>
      <div class="subject-card-meta">
        <span>Topic: ${escapeHtml(profile.current_topic_name || profile.current_topic_id || "Not set")}</span>
        ${isQuickStudy
          ? "<span>Mode: PDF chat</span><span>Progress: Not tracked</span>"
          : `<span>Level: ${escapeHtml(profile.current_level || "Not set")}</span><span>Progress: ${escapeHtml(formatPercent(profile.path_completion_pct))}</span>`}
      </div>
    `;
    button.addEventListener("click", () => {
      setSelectedSubject(profile.subject_id);
      loadDashboard(profile.subject_id).catch((error) => {
        welcomeText.textContent = error.message;
      });
    });
    subjectList.appendChild(button);
  });
}

function openQuickStudySession(sessionId) {
  if (sessionId) {
    localStorage.setItem("adaptiveTutorQuickStudySessionId", sessionId);
  }
  window.location.href = "/frontend/quick_study.html";
}

function renderDashboard(data) {
  const learner = data.learner || {};
  const prefs = data.preferences || {};
  const active = data.active_subject || {};
  const roadmap = active.active_path || {};
  const steps = active.path_steps || [];
  const latestQuiz = active.latest_quiz || null;
  const responses = active.latest_quiz_responses || [];
  const mastery = active.topic_mastery || [];
  const masteryBreakdown = active.mastery_breakdown || [];
  const stepQuizMap = active.step_quiz_map || {};
  const currentStep = active.current_step || null;
  const currentView = active.current_view || null;
  const currentStepContent = active.current_step_content || null;
  const sessions = data.recent_sessions || [];
  const isQuickStudy = active.goal_type === "quick_study";
  const quickSessions = active.quick_study_sessions || [];
  const latestQuickSession = active.latest_quick_study_session || quickSessions[0] || null;

  welcomeText.textContent = learner.full_name
    ? `Choose a subject below. We’ll keep you in the selected roadmap.`
    : "Choose a subject below to see the roadmap, next step, quiz, and mastery view.";
  dashName.textContent = text(learner.full_name);
  dashEmail.textContent = text(learner.email);
  dashLanguage.textContent = text(learner.preferred_language);

  renderSubjectCards(data.subject_profiles || [], active.subject_id || data.selected_subject_id || "");

  selectedSubjectTitle.textContent = active.subject_name
    ? `${active.subject_name} ${isQuickStudy ? "quick study" : "roadmap"}`
    : "No subject selected";
  selectedSubjectTopic.textContent = active.subject_id
    ? `Current topic: ${text(active.current_topic_name || active.current_topic_id)}`
    : "Pick a subject card above to view its details.";

  if (isQuickStudy) {
    selectedSubjectStats.innerHTML = [
      renderStat("Mode", "PDF chat"),
      renderStat("Sessions", String(quickSessions.length || 0)),
      renderStat("PDFs", String(latestQuickSession?.document_count || 0)),
      renderStat("Messages", String(latestQuickSession?.message_count || 0)),
    ].join("");

    detailRoadmapText.textContent = latestQuickSession
      ? latestQuickSession.title || `Quick study: ${active.subject_name}`
      : "No quick study session yet.";
    detailRoadmapMeta.innerHTML = `
      <p><strong>Mode:</strong> Persistent RAG chat over uploaded PDFs.</p>
      <p><strong>Indexed chunks:</strong> ${escapeHtml(latestQuickSession?.chunk_count || 0)}</p>
      <p><strong>Last opened:</strong> ${escapeHtml(latestQuickSession?.last_accessed_at || latestQuickSession?.updated_at || "Not opened yet")}</p>
      <button class="btn primary" type="button" id="resumeQuickStudyButton">
        ${latestQuickSession ? "Resume quick study" : "Open quick study"}
      </button>
    `;
    document.getElementById("resumeQuickStudyButton")?.addEventListener("click", () => {
      openQuickStudySession(latestQuickSession?.session_id || "");
    });

    detailStepTitle.textContent = "Document chat";
    detailStepDescription.textContent = "Upload PDFs, ask questions, view cited snippets, and continue the same chat later.";
    detailCurrentContent.innerHTML = `
      <div class="rendered-content">
        <p>Quick Study does not use roadmap steps, diagnostic quizzes, mastery, or adaptive step practice.</p>
        <p>Your progress here is the persistent PDF collection and chat history.</p>
      </div>
    `;
    practiceStepButton.classList.add("hidden");
    practiceStepButton.onclick = null;
    viewGraphButton?.classList.add("hidden");
    if (viewGraphButton) viewGraphButton.onclick = null;

    detailStepList.innerHTML = quickSessions.length
      ? quickSessions.map((session) => `
          <article class="quick-dashboard-session">
            <div>
              <strong>${escapeHtml(session.title || session.topic || "Quick study")}</strong>
              <p>${escapeHtml(session.document_count || 0)} PDFs · ${escapeHtml(session.message_count || 0)} messages · ${escapeHtml(session.chunk_count || 0)} chunks</p>
            </div>
            <button class="btn secondary" type="button" data-quick-session="${escapeHtml(session.session_id)}">Resume</button>
          </article>
        `).join("")
      : `<p class="empty-state">No quick study sessions yet.</p>`;
    detailStepList.querySelectorAll("[data-quick-session]").forEach((button) => {
      button.addEventListener("click", () => openQuickStudySession(button.dataset.quickSession));
    });

    detailSessionList.innerHTML = quickSessions.length
      ? quickSessions.slice(0, 5).map((session) => `
          <li>${escapeHtml(session.title || session.topic || "Quick study")} - ${escapeHtml(session.session_status || "active")}</li>
        `).join("")
      : "<li>No recent quick study sessions.</li>";
    return;
  }

  selectedSubjectStats.innerHTML = [
    renderStat("Roadmap", text(roadmap.path_title, "Not set")),
    renderStat("Progress", active.path_completion_pct !== undefined ? formatPercent(active.path_completion_pct) : "Not set"),
    renderStat("Mastery", formatScore(active.mastery_score)),
    renderStat("Review", text(active.next_review_at, "Not set")),
  ].join("");

  if (roadmap.path_id) {
    detailRoadmapText.textContent = `${text(active.subject_name || roadmap.path_title)} ${roadmap.path_status ? `(${roadmap.path_status})` : ""}`;
    detailRoadmapMeta.innerHTML = `
      <p><strong>Target outcome:</strong> ${escapeHtml(roadmap.target_outcome || "Not set")}</p>
      <p><strong>Steps complete:</strong> ${escapeHtml(`${roadmap.completed_steps || 0}/${roadmap.total_steps || steps.length || 0}`)}</p>
      <p><strong>Current level:</strong> ${escapeHtml(active.current_level || "Not set")}</p>
    `;
  } else {
    detailRoadmapText.textContent = "No active roadmap.";
    detailRoadmapMeta.textContent = "Roadmap details will appear here.";
  }

  if (currentStep) {
    detailStepTitle.textContent = `${currentStep.step_order}. ${currentStep.step_title}`;
    detailStepDescription.textContent = text(currentStep.step_description, "No description available.");
  } else {
    detailStepTitle.textContent = "No active step.";
    detailStepDescription.textContent = "Choose a subject to see the next step.";
  }

  if (currentStep && learner.learner_id && active.subject_id && roadmap.path_id) {
    practiceStepButton.classList.remove("hidden");
    practiceStepButton.onclick = () => {
      const launchContext = {
        learner_id: learner.learner_id,
        subject_id: active.subject_id,
        path_id: roadmap.path_id,
        step_id: currentStep.step_id,
        step_title: currentStep.step_title,
      };
      const savedSession = (() => {
        try {
          return JSON.parse(localStorage.getItem("adaptiveTutorAdaptiveQuizSession") || "null");
        } catch {
          return null;
        }
      })();
      if (savedSession?.step_id !== currentStep.step_id) {
        localStorage.removeItem("adaptiveTutorAdaptiveQuizSession");
      }
      localStorage.setItem("adaptiveTutorAdaptiveQuizContext", JSON.stringify(launchContext));
      window.location.href = "/frontend/adaptive_quiz.html";
    };
  } else {
    practiceStepButton.classList.add("hidden");
    practiceStepButton.onclick = null;
  }

  if (active.subject_id) {
    viewGraphButton?.classList.remove("hidden");
    viewGraphButton.onclick = () => {
      const email = encodeURIComponent(learner.email || localStorage.getItem("adaptiveTutorLearnerEmail") || "");
      const subjectId = encodeURIComponent(active.subject_id);
      window.location.href = `/frontend/roadmap_graph.html?email=${email}&subject_id=${subjectId}`;
    };
  } else if (viewGraphButton) {
    viewGraphButton.classList.add("hidden");
    viewGraphButton.onclick = null;
  }

  const stepCards = steps.length
    ? steps.map((step) => {
        const quizInfo = stepQuizMap[step.step_id] || null;
        const masterySummary = masteryBreakdown.find((item) => item.step_id === step.step_id) || null;
        return renderStepAccordion(step, active, quizInfo, masterySummary);
      }).join("")
    : `<p class="empty-state">No roadmap steps yet.</p>`;
  detailStepList.innerHTML = stepCards;

  detailStepList.querySelectorAll("[data-practice-step]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!currentStep || button.getAttribute("data-practice-step") !== currentStep.step_id) {
        return;
      }
      practiceStepButton.click();
    });
  });

  if (sessions.length) {
    detailSessionList.innerHTML = "";
    sessions.forEach((session) => {
      const li = document.createElement("li");
      li.textContent = `${session.session_type} - ${session.session_status} - ${session.session_summary || session.started_at}`;
      detailSessionList.appendChild(li);
    });
  } else {
    clearList(detailSessionList, "No recent sessions.");
  }

  if (!active.subject_id) {
    detailRoadmapText.textContent = "No subject selected.";
  }

  if (prefs) {
    void prefs;
  }
}

function wireUserMenu() {
  if (!userMenuButton || !userMenuPanel) return;

  const closeMenu = () => {
    userMenuPanel.classList.add("hidden");
    userMenuButton.setAttribute("aria-expanded", "false");
  };

  const toggleMenu = (event) => {
    event.stopPropagation();
    const isOpen = !userMenuPanel.classList.contains("hidden");
    if (isOpen) {
      closeMenu();
      return;
    }
    userMenuPanel.classList.remove("hidden");
    userMenuButton.setAttribute("aria-expanded", "true");
  };

  userMenuButton.addEventListener("click", toggleMenu);
  document.addEventListener("click", (event) => {
    if (!userMenuPanel.contains(event.target) && !userMenuButton.contains(event.target)) {
      closeMenu();
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeMenu();
    }
  });
}

wireUserMenu();

loadDashboard().catch((error) => {
  welcomeText.textContent = error.message;
  selectedSubjectTitle.textContent = "Unable to load dashboard";
  detailCurrentContent.textContent = "Unable to load dashboard data.";
});
