const subjectForm = document.getElementById("subjectForm");
const backToProfile = document.getElementById("backToProfile");
const API_BASE = "http://localhost:8001";

function writeScopedJSON(key, value, learnerEmail) {
  localStorage.setItem(key, JSON.stringify({
    ownerEmail: learnerEmail || "",
    value
  }));
}

backToProfile.addEventListener("click", () => {
  window.location.href = "/frontend/dashboard.html";
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
          }),
        );
        window.location.href = "/frontend/adaptive_quiz.html";
        return;
      }

      window.location.href = "/frontend/dashboard.html";
    })
    .catch((error) => {
      alert(error.message);
    });
});
