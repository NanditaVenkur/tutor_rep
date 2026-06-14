const subjectForm = document.getElementById("subjectForm");
const backToProfile = document.getElementById("backToProfile");
const topicInput = subjectForm.querySelector('[name="topic"]');
const topicSuggestions = document.getElementById("topicSuggestions");
const API_BASE = "http://localhost:8001";

backToProfile.addEventListener("click", () => {
  window.location.href = "/frontend/dashboard.html";
});

topicSuggestions.addEventListener("click", (event) => {
  const button = event.target.closest("[data-topic]");
  if (!button) return;
  topicInput.value = button.dataset.topic;
  topicInput.focus();
});

subjectForm.addEventListener("submit", (event) => {
  event.preventDefault();

  const values = Object.fromEntries(new FormData(subjectForm).entries());
  const learnerId = localStorage.getItem("adaptiveTutorLearnerId");
  const learnerEmail = localStorage.getItem("adaptiveTutorLearnerEmail");

  fetch(`${API_BASE}/api/topic`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...values,
      learner_id: learnerId || null,
      learner_email: learnerEmail || null
    })
  })
    .then((response) => response.json().then((data) => ({ response, data })))
    .then(({ response, data }) => {
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
      }
      const mode = data.study_flow?.study_mode || values.study_mode || "roadmap";
      alert(`${mode.replaceAll("_", " ")} started for: ${values.topic}`);

      if (data.study_flow?.route === "diagnostic_quiz") {
        window.location.href = "/frontend/diagnostic_quiz.html";
        return;
      }

      if (data.study_flow?.route === "quick_study") {
        window.location.href = "/frontend/dashboard.html";
        return;
      }

      window.location.href = "/frontend/dashboard.html";
    })
    .catch((error) => {
      alert(error.message);
    });
});
