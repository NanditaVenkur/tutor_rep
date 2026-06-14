const API_BASE = "http://localhost:8001";
const entryForm = document.getElementById("entryForm");

entryForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  const values = Object.fromEntries(new FormData(entryForm).entries());
  const email = (values.email || "").trim().toLowerCase();

  if (!email) {
    alert("Please enter your email address.");
    return;
  }

  try {
    const response = await fetch(`${API_BASE}/api/learner?email=${encodeURIComponent(email)}`);
    const data = await response.json();

    localStorage.setItem("adaptiveTutorPendingEmail", email);

    if (response.ok && data.exists) {
      localStorage.setItem("adaptiveTutorLearnerId", String(data.learner.learner_id));
      localStorage.setItem("adaptiveTutorLearnerEmail", data.learner.email);
      localStorage.setItem("adaptiveTutorLearnerName", data.learner.full_name || "");
      localStorage.setItem("adaptiveTutorPreferredLanguage", data.learner.preferred_language || "English");
      window.location.href = "./dashboard.html";
      return;
    }

    if (response.status === 404 || (response.ok && !data.exists)) {
      window.location.href = "./onboarding_profile_form.html";
      return;
    }

    throw new Error(data.error || "Unable to verify email");
  } catch (error) {
    alert(error.message);
  }
});
