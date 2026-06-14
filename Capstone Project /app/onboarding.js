const form = document.getElementById("onboardingForm");
const steps = Array.from(document.querySelectorAll(".step"));
const stepLabel = document.getElementById("stepLabel");
const stepHint = document.getElementById("stepHint");
const progressBar = document.getElementById("progressBar");
const backBtn = document.getElementById("backBtn");
const nextBtn = document.getElementById("nextBtn");
const skipBtn = document.getElementById("skipBtn");
const submitBtn = document.getElementById("submitBtn");
const reviewBox = document.getElementById("reviewBox");
const API_BASE = "http://localhost:8001";

const summary = {
  sumFormat: document.getElementById("sumFormat"),
  sumExplanation: document.getElementById("sumExplanation"),
  sumQuiz: document.getElementById("sumQuiz"),
  sumPace: document.getElementById("sumPace"),
  sumSession: document.getElementById("sumSession")
};

const labels = [
  { title: "Basic details", hint: "Learner setup" },
  { title: "Preferences", hint: "Learning style" },
  { title: "Review", hint: "Confirm and create" }
];

let currentStep = 0;

function getFormValues() {
  const data = new FormData(form);
  return Object.fromEntries(data.entries());
}

function friendly(value, fallback = "Not set") {
  if (!value) return fallback;
  return String(value)
    .replaceAll("_", " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function renderSummary() {
  const values = getFormValues();
  summary.sumFormat.textContent = friendly(values.content_format || "mixed");
  summary.sumExplanation.textContent = friendly(values.explanation_style || "step_by_step");
  summary.sumQuiz.textContent = friendly(values.quiz_style || "mixed");
  summary.sumPace.textContent = friendly(values.learning_pace || "normal");
  summary.sumSession.textContent = values.session_length || "Not set";

  reviewBox.innerHTML = `
    <strong style="display:block; margin-bottom:8px; color:#1f2933;">Review before creating profile</strong>
    <div>Format: ${friendly(values.content_format || "mixed")}</div>
    <div>Explanation: ${friendly(values.explanation_style || "step_by_step")}</div>
    <div>Quiz: ${friendly(values.quiz_style || "mixed")}</div>
    <div>Pace: ${friendly(values.learning_pace || "normal")}</div>
    <div>Session: ${values.session_length || "Not set"}</div>
  `;
}

function showStep(index) {
  currentStep = Math.max(0, Math.min(index, steps.length - 1));

  steps.forEach((step, i) => {
    step.classList.toggle("active", i === currentStep);
  });

  stepLabel.textContent = `Step ${currentStep + 1} of ${steps.length}`;
  stepHint.textContent = labels[currentStep].hint;
  progressBar.style.width = `${((currentStep + 1) / steps.length) * 100}%`;

  backBtn.disabled = currentStep === 0;
  nextBtn.classList.toggle("hidden", currentStep === steps.length - 1);
  skipBtn.classList.toggle("hidden", currentStep !== 2);
  submitBtn.classList.toggle("hidden", currentStep !== steps.length - 1);

  renderSummary();
}

function validateCurrentStep() {
  const values = getFormValues();

  if (currentStep === 0) {
    if (!values.email || !values.full_name || !values.preferred_language) return false;
  }

  if (currentStep === 1) {
    if (!values.session_length) return false;
  }

  return true;
}

function persistDraft() {
  localStorage.setItem("adaptiveTutorOnboarding", JSON.stringify(getFormValues()));
}

async function createProfile() {
  const values = getFormValues();
  const response = await fetch(`${API_BASE}/api/onboarding`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(values)
  });

  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "Failed to create profile");
  }

  localStorage.setItem("adaptiveTutorLearnerId", String(data.learner_id));
  localStorage.setItem("adaptiveTutorLearnerEmail", values.email);
  localStorage.setItem("adaptiveTutorPreferredLanguage", values.preferred_language || "English");
  return data;
}

form.addEventListener("input", () => {
  renderSummary();
  persistDraft();
});

backBtn.addEventListener("click", () => {
  showStep(currentStep - 1);
});

nextBtn.addEventListener("click", () => {
  if (!validateCurrentStep()) {
    alert("Please complete the required fields on this step.");
    return;
  }

  showStep(currentStep + 1);
});

skipBtn.addEventListener("click", () => {
  form.querySelector('[name="accessibility_notes"]').value = "";
  form.requestSubmit(submitBtn);
});

form.addEventListener("submit", (event) => {
  event.preventDefault();

  if (!validateCurrentStep()) {
    alert("Please complete the required fields before creating the profile.");
    return;
  }

  persistDraft();
  createProfile()
    .then(() => {
      window.location.href = "./dashboard.html";
    })
    .catch((error) => {
      alert(error.message);
    });
});

const saved = localStorage.getItem("adaptiveTutorOnboarding");
if (saved) {
  try {
    const data = JSON.parse(saved);
    Object.entries(data).forEach(([key, value]) => {
      const field = form.elements.namedItem(key);
      if (!field) return;

      if (field instanceof RadioNodeList) {
        Array.from(field).forEach((input) => {
          input.checked = input.value === value;
        });
      } else {
        field.value = value;
      }
    });
  } catch {
    localStorage.removeItem("adaptiveTutorOnboarding");
  }
}

const pendingEmail = localStorage.getItem("adaptiveTutorPendingEmail");
if (pendingEmail) {
  const emailField = form.elements.namedItem("email");
  if (emailField) {
    emailField.value = pendingEmail;
  }
}

showStep(0);
