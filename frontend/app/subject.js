const subjectForm = document.getElementById("subjectForm");
const backToProfile = document.getElementById("backToProfile");
const topicGroundingPanel = document.getElementById("topicGroundingPanel");
const topicGroundingCanonical = document.getElementById("topicGroundingCanonical");
const topicGroundingDefinition = document.getElementById("topicGroundingDefinition");
const topicGroundingSources = document.getElementById("topicGroundingSources");
const topicLearningGoal = document.getElementById("topicLearningGoal");
const confirmGrounding = document.getElementById("confirmGrounding");
const editGrounding = document.getElementById("editGrounding");
const groundTopicButton = document.getElementById("groundTopicButton");
const topicInput = subjectForm.querySelector('[name="topic"]');
const API_BASE = "http://localhost:8001";

let pendingValues = null;
let pendingGrounding = null;

function writeScopedJSON(key, value, learnerEmail) {
  localStorage.setItem(key, JSON.stringify({
    ownerEmail: learnerEmail || "",
    value
  }));
}

function setBusy(isBusy, label = "Generate roadmap") {
  groundTopicButton.disabled = isBusy;
  confirmGrounding.disabled = isBusy;
  editGrounding.disabled = isBusy;
  groundTopicButton.textContent = label;
}

function showGrounding(grounding) {
  topicGroundingCanonical.textContent = grounding.canonical_topic || grounding.raw_topic || "Topic";
  topicGroundingDefinition.textContent = grounding.definition || "Confirm this topic before we create the quiz.";
  topicGroundingSources.innerHTML = "";

  (grounding.sources || []).slice(0, 10).forEach((source) => {
    const item = document.createElement("li");
    const link = document.createElement(source.link ? "a" : "span");
    link.textContent = source.title || source.link || "Source";
    if (source.link) {
      link.href = source.link;
      link.target = "_blank";
      link.rel = "noreferrer";
    }
    item.appendChild(link);
    if (source.snippet) {
      const snippet = document.createElement("p");
      snippet.textContent = source.snippet;
      item.appendChild(snippet);
    }
    topicGroundingSources.appendChild(item);
  });

  topicGroundingPanel.classList.remove("hidden");
}

function routeAfterStudyRequest(data, values, learnerEmail) {
  writeScopedJSON("adaptiveTutorSelectedTopic", values, learnerEmail);
  if (data.study_flow) {
    writeScopedJSON("adaptiveTutorStudyFlow", data.study_flow, learnerEmail);
  }
  if (data.assessment_preview) {
    writeScopedJSON("adaptiveTutorAssessmentPreview", data.assessment_preview, learnerEmail);
  }
  if (data.learning_path) {
    writeScopedJSON("adaptiveTutorLearningPathPreview", data.learning_path, learnerEmail);
  }
  if (data.subject) {
    writeScopedJSON("adaptiveTutorActiveSubject", data.subject, learnerEmail);
  }
  if (data.topic_grounding) {
    writeScopedJSON("adaptiveTutorTopicGrounding", data.topic_grounding, learnerEmail);
  }

  const mode = data.study_flow?.study_mode || values.study_mode || "roadmap";
  alert(`${mode.replaceAll("_", " ")} started for: ${values.topic}`);

  if (data.study_flow?.route === "diagnostic_quiz") {
    window.location.href = "/frontend/diagnostic_quiz.html";
    return;
  }

  if (data.study_flow?.route === "quick_study") {
    if (data.quick_study_session) {
      localStorage.setItem("adaptiveTutorQuickStudySessionId", data.quick_study_session.session_id);
    }
    window.location.href = "/frontend/quick_study.html";
    return;
  }

  if (data.study_flow?.route === "adaptive_quiz") {
    const firstStep = data.learning_path?.steps?.[0];
    if (!data.learner_id || !data.subject?.subject_id || !data.learning_path?.path_id || !firstStep?.step_id) {
      throw new Error("Adaptive quiz context could not be created");
    }
    localStorage.removeItem("adaptiveTutorAdaptiveQuizSession");
    localStorage.setItem(
      "adaptiveTutorAdaptiveQuizContext",
      JSON.stringify({
        learner_id: data.learner_id,
        subject_id: data.subject.subject_id,
        path_id: data.learning_path.path_id,
        step_id: firstStep.step_id,
        step_title: firstStep.step_title,
        step_order: firstStep.step_order,
        total_steps: data.learning_path.steps.length,
        preview_terms: firstStep.preview_terms || [],
      }),
    );
    window.location.href = "/frontend/adaptive_quiz.html";
    return;
  }

  window.location.href = "/frontend/dashboard.html";
}

function startStudyRequest(values, grounding) {
  const learnerId = localStorage.getItem("adaptiveTutorLearnerId");
  const learnerEmail = localStorage.getItem("adaptiveTutorLearnerEmail");
  const enrichedGrounding = grounding
    ? { ...grounding, learning_goal: topicLearningGoal?.value || "General understanding" }
    : null;
  const canonicalTopic = enrichedGrounding?.canonical_topic || values.topic;
  const requestValues = {
    ...values,
    topic: canonicalTopic,
    raw_topic: values.topic,
  };

  setBusy(true, "Creating roadmap...");
  fetch(`${API_BASE}/api/topic`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...requestValues,
      canonical_topic: canonicalTopic,
      topic_grounding: enrichedGrounding,
      learner_id: learnerId || null,
      learner_email: learnerEmail || null
    })
  })
    .then((response) => response.json().then((data) => ({ response, data })))
    .then(({ response, data }) => {
      if (!response.ok) {
        throw new Error(data.error || "Failed to create study request");
      }
      routeAfterStudyRequest(data, requestValues, learnerEmail);
    })
    .catch((error) => {
      alert(error.message);
      setBusy(false);
    });
}

backToProfile.addEventListener("click", () => {
  window.location.href = "/frontend/dashboard.html";
});

subjectForm.addEventListener("submit", (event) => {
  event.preventDefault();

  const values = Object.fromEntries(new FormData(subjectForm).entries());
  if (!values.topic?.trim()) {
    alert("Please enter a topic.");
    return;
  }

  pendingValues = values;
  pendingGrounding = null;
  setBusy(true, "Checking topic...");

  fetch(`${API_BASE}/api/topic/ground`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ topic: values.topic })
  })
    .then((response) => response.json().then((data) => ({ response, data })))
    .then(({ response, data }) => {
      if (!response.ok) {
        throw new Error(data.error || "Failed to ground topic");
      }
      pendingGrounding = data.grounding;
      showGrounding(pendingGrounding);
      setBusy(false, "Check again");
    })
    .catch((error) => {
      alert(error.message);
      setBusy(false);
    });
});

confirmGrounding.addEventListener("click", () => {
  if (!pendingValues) {
    return;
  }
  startStudyRequest(pendingValues, pendingGrounding);
});

editGrounding.addEventListener("click", () => {
  pendingGrounding = null;
  topicGroundingPanel.classList.add("hidden");
  setBusy(false);
  topicInput.focus();
});
