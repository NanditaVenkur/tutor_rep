const subjectForm = document.getElementById("subjectForm");
const backToProfile = document.getElementById("backToProfile");
const topicInput = subjectForm.querySelector('[name="topic"]');
const topicSuggestions = document.getElementById("topicSuggestions");
const API_BASE = "http://localhost:8001";

backToProfile.addEventListener("click", () => {
  window.location.href = "./dashboard.html";
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

  fetch(`${API_BASE}/api/topic`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...values,
      learner_id: learnerId ? Number(learnerId) : null
    })
  })
    .then((response) => response.json().then((data) => ({ response, data })))
    .then(({ response, data }) => {
      if (!response.ok) {
        throw new Error(data.error || "Failed to create study request");
      }

      localStorage.setItem("adaptiveTutorSelectedTopic", JSON.stringify(values));
      alert(`Roadmap requested for: ${values.topic}`);
    })
    .catch((error) => {
      alert(error.message);
    });
});
