const API_BASE = "http://localhost:8001";

const sessionList = document.getElementById("sessionList");
const sessionTitle = document.getElementById("sessionTitle");
const sessionMeta = document.getElementById("sessionMeta");
const documentList = document.getElementById("documentList");
const chatHistory = document.getElementById("chatHistory");
const uploadForm = document.getElementById("uploadForm");
const pdfFiles = document.getElementById("pdfFiles");
const chatForm = document.getElementById("chatForm");
const chatInput = document.getElementById("chatInput");
const newSessionButton = document.getElementById("newSessionButton");

const learnerEmail = localStorage.getItem("adaptiveTutorLearnerEmail") || "";
let activeSessionId = localStorage.getItem("adaptiveTutorQuickStudySessionId") || "";
let sessions = [];

function escapeHTML(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

async function apiJSON(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {})
    }
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "Request failed");
  }
  return data;
}

function renderSessions() {
  if (!sessions.length) {
    sessionList.innerHTML = '<p class="empty-state">No quick study sessions yet.</p>';
    return;
  }

  sessionList.innerHTML = sessions.map((session) => `
    <button type="button" class="quick-session-item ${session.session_id === activeSessionId ? "active" : ""}" data-session-id="${escapeHTML(session.session_id)}">
      <strong>${escapeHTML(session.title || session.topic)}</strong>
      <span>${Number(session.document_count || 0)} PDFs · ${Number(session.message_count || 0)} messages</span>
    </button>
  `).join("");

  sessionList.querySelectorAll("[data-session-id]").forEach((button) => {
    button.addEventListener("click", () => loadSession(button.dataset.sessionId));
  });
}

function renderDocuments(documents) {
  if (!documents.length) {
    documentList.innerHTML = '<p class="empty-state">No PDFs uploaded yet.</p>';
    return;
  }

  documentList.innerHTML = documents.map((doc) => `
    <span class="quick-document-pill">
      ${escapeHTML(doc.file_name)}
      <small>${Number(doc.page_count || 0)} pages</small>
    </span>
  `).join("");
}

function confidenceBadge(confidence) {
  if (confidence === undefined || confidence === null) return "";
  const pct = Math.round(Number(confidence) * 100);
  let cls = "confidence-high";
  if (pct < 50) cls = "confidence-low";
  else if (pct < 75) cls = "confidence-mid";
  return `<span class="confidence-badge ${cls}" title="How well retrieved excerpts match the question">${pct}% confidence</span>`;
}

function renderMessage(message) {
  const role = message.role === "user" ? "user" : "assistant";
  const sources = Array.isArray(message.sources) ? message.sources : [];
  const followups = Array.isArray(message.followups) ? message.followups : [];
  const confidence = message.confidence;

  // Render answer text: convert newlines to <br> for basic formatting
  const answerHtml = escapeHTML(message.message_text || "")
    .replace(/\n/g, "<br>");

  return `
    <article class="quick-message ${role}">
      <div class="quick-message-role">
        ${role === "user" ? "You" : "Tutor"}
        ${role === "assistant" ? confidenceBadge(confidence) : ""}
      </div>
      <p class="quick-answer-text">${answerHtml}</p>
      ${sources.length ? `
        <div class="quick-sources">
          <span class="sources-label">Sources</span>
          <div class="sources-chips">
            ${sources.map((source, index) => `
              <details class="source-chip">
                <summary>
                  <span class="source-num">S${index + 1}</span>
                  <span class="source-name">${escapeHTML(source.file_name)}${source.page_number ? ` p.${escapeHTML(String(source.page_number))}` : ""}</span>
                  <span class="source-score">${Math.round((source.score || 0) * 100)}%</span>
                </summary>
                <p class="source-snippet">${escapeHTML(source.snippet || "")}</p>
              </details>
            `).join("")}
          </div>
        </div>
      ` : ""}
      ${followups.length ? `
        <div class="quick-followups">
          ${followups.map((question) => `<button type="button" class="quick-followup">${escapeHTML(question)}</button>`).join("")}
        </div>
      ` : ""}
    </article>
  `;
}

function renderMessages(messages) {
  if (!messages.length) {
    chatHistory.innerHTML = '<p class="empty-state">Ask your first question after uploading PDFs.</p>';
    return;
  }
  chatHistory.innerHTML = messages.map(renderMessage).join("");
  chatHistory.querySelectorAll(".quick-followup").forEach((button) => {
    button.addEventListener("click", () => {
      chatInput.value = button.textContent;
      chatInput.focus();
    });
  });
  chatHistory.scrollTop = chatHistory.scrollHeight;
}

async function loadSessions() {
  if (!learnerEmail) {
    window.location.href = "/frontend/index.html";
    return;
  }
  const data = await apiJSON(`/api/quick-study/sessions?email=${encodeURIComponent(learnerEmail)}`);
  sessions = data.sessions || [];
  if (!activeSessionId && sessions.length) {
    activeSessionId = sessions[0].session_id;
    localStorage.setItem("adaptiveTutorQuickStudySessionId", activeSessionId);
  }
  renderSessions();
  if (activeSessionId) {
    await loadSession(activeSessionId);
  }
}

async function loadSession(sessionId) {
  activeSessionId = sessionId;
  localStorage.setItem("adaptiveTutorQuickStudySessionId", activeSessionId);
  renderSessions();
  const data = await apiJSON(`/api/quick-study/session?email=${encodeURIComponent(learnerEmail)}&session_id=${encodeURIComponent(sessionId)}`);
  const session = data.session || {};
  sessionTitle.textContent = session.title || session.topic || "Quick study";
  sessionMeta.textContent = `${data.chunk_count || 0} indexed chunks`;
  renderDocuments(data.documents || []);
  renderMessages(data.messages || []);
}

async function createSession() {
  const topic = prompt("What do you want this quick study session to focus on?", "Quick study");
  if (!topic) {
    return;
  }
  const data = await apiJSON("/api/quick-study/session", {
    method: "POST",
    body: JSON.stringify({ email: learnerEmail, topic, title: `Quick study: ${topic}` })
  });
  activeSessionId = data.session.session_id;
  localStorage.setItem("adaptiveTutorQuickStudySessionId", activeSessionId);
  await loadSessions();
}

uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!activeSessionId) {
    alert("Create or choose a quick study session first.");
    return;
  }
  if (!pdfFiles.files.length) {
    alert("Choose at least one PDF.");
    return;
  }

  const body = new FormData();
  body.append("email", learnerEmail);
  body.append("session_id", activeSessionId);
  Array.from(pdfFiles.files).forEach((file) => body.append("files", file));

  const response = await fetch(`${API_BASE}/api/quick-study/upload`, {
    method: "POST",
    body
  });
  const data = await response.json();
  if (!response.ok) {
    alert(data.error || "Upload failed");
    return;
  }
  pdfFiles.value = "";
  await loadSessions();
  await loadSession(activeSessionId);
});

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = chatInput.value.trim();
  if (!message) {
    return;
  }
  if (!activeSessionId) {
    alert("Create or choose a quick study session first.");
    return;
  }

  // Optimistically show the user message
  const tempUserHtml = `
    <article class="quick-message user">
      <div class="quick-message-role">You</div>
      <p class="quick-answer-text">${escapeHTML(message)}</p>
    </article>
    <article class="quick-message assistant thinking">
      <div class="quick-message-role">Tutor</div>
      <p class="quick-answer-text thinking-dots">Thinking<span>.</span><span>.</span><span>.</span></p>
    </article>
  `;
  chatHistory.insertAdjacentHTML("beforeend", tempUserHtml);
  chatHistory.scrollTop = chatHistory.scrollHeight;

  chatInput.value = "";
  chatInput.disabled = true;
  try {
    await apiJSON("/api/quick-study/chat", {
      method: "POST",
      body: JSON.stringify({ email: learnerEmail, session_id: activeSessionId, message })
    });
    await loadSessions();
    await loadSession(activeSessionId);
  } catch (error) {
    alert(error.message);
  } finally {
    chatInput.disabled = false;
    chatInput.focus();
  }
});

newSessionButton.addEventListener("click", createSession);

loadSessions().catch((error) => {
  chatHistory.innerHTML = `<p class="empty-state">${escapeHTML(error.message)}</p>`;
});
