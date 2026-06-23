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

const labels = [
  { title: "Basic details", hint: "Learner setup" },
  { title: "Preferences", hint: "Learning style" },
  { title: "Review", hint: "Confirm and create" }
];

let currentStep = 0;

function getFormValues() {
  const data = new FormData(form);
  const values = {};
  for (const [key, value] of data.entries()) {
    if (key === "quiz_style") {
      if (!Array.isArray(values[key])) {
        values[key] = [];
      }
      values[key].push(value);
      continue;
    }
    values[key] = value;
  }
  return values;
}

function friendly(value, fallback = "Not set") {
  if (!value) return fallback;
  return String(value)
    .replaceAll("_", " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function renderSummary() {
  if (!reviewBox) return;
  const values = getFormValues();
  const quizStyles = Array.isArray(values.quiz_style) && values.quiz_style.length
    ? values.quiz_style.map((item) => friendly(item)).join(", ")
    : "MCQ";
  reviewBox.innerHTML = `
    <strong style="display:block; margin-bottom:8px; color:#1f2933;">Review before creating profile</strong>
    <div>Name: ${values.full_name || "Not set"}</div>
    <div>Email: ${values.email || "Not set"}</div>
    <div>Explanation: ${friendly(values.explanation_style || "step_by_step")}</div>
    <div>Quiz: ${quizStyles}</div>
    <div>Notes: ${values.accessibility_notes || "None"}</div>
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

  nextBtn.classList.toggle("hidden", currentStep === steps.length - 1);
  skipBtn.classList.toggle("hidden", currentStep !== 2);
  submitBtn.classList.toggle("hidden", currentStep !== steps.length - 1);

  renderSummary();
}

function validateCurrentStep() {
  const values = getFormValues();

  if (currentStep === 0) {
    if (!values.email) return false;
  }

  if (currentStep === 1) {
    if (!Array.isArray(values.quiz_style) || !values.quiz_style.length) return false;
  }

  return true;
}

function persistDraft() {
  localStorage.setItem("adaptiveTutorOnboarding", JSON.stringify(getFormValues()));
}

function clearStudyStateForEmailChange(nextEmail) {
  const currentEmail = localStorage.getItem("adaptiveTutorLearnerEmail");
  if (!currentEmail || currentEmail === nextEmail) return;

  [
    "adaptiveTutorSelectedTopic",
    "adaptiveTutorStudyFlow",
    "adaptiveTutorAssessmentPreview",
    "adaptiveTutorLearningPathPreview",
    "adaptiveTutorActiveSubject",
    "adaptiveTutorLatestDiagnosticResult",
    "adaptiveTutorActiveSubjectId"
  ].forEach((key) => localStorage.removeItem(key));
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

  clearStudyStateForEmailChange(values.email);
  localStorage.setItem("adaptiveTutorLearnerId", String(data.learner_id));
  localStorage.setItem("adaptiveTutorLearnerEmail", values.email);
  return data;
}

form.addEventListener("input", () => {
  renderSummary();
  persistDraft();
});

backBtn.addEventListener("click", () => {
  if (currentStep === 0) {
    window.location.href = "/frontend/index.html";
    return;
  }
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
      window.location.href = "/frontend/dashboard.html";
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
        if (Array.isArray(value)) {
          Array.from(field).forEach((input) => {
            input.checked = value.includes(input.value);
          });
        } else {
          Array.from(field).forEach((input) => {
            input.checked = input.value === value;
          });
        }
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
