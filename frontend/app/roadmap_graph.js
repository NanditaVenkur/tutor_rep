const API_BASE = "http://localhost:8001";

const graphTitle = document.getElementById("graphTitle");
const graphSubtitle = document.getElementById("graphSubtitle");
const graphSummary = document.getElementById("graphSummary");
const graphImage = document.getElementById("graphImage");
const graphOrderList = document.getElementById("graphOrderList");
const graphNodes = document.getElementById("graphNodes");
const graphEdges = document.getElementById("graphEdges");

const TIER_LABELS = ["Foundational", "Core build", "Advanced application"];

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function readParams() {
  const params = new URLSearchParams(window.location.search);
  return {
    email: params.get("email") || localStorage.getItem("adaptiveTutorLearnerEmail") || "",
    subjectId: params.get("subject_id") || localStorage.getItem("adaptiveTutorActiveSubjectId") || "",
  };
}

function renderSummary(data) {
  const subject = data.subject || {};
  const path = data.path || {};
  const graph = data.graph || {};
  const topicGraph = buildTopicGraph(graph);
  graphTitle.textContent = subject.subject_name
    ? `${subject.subject_name} roadmap graph`
    : "Roadmap graph";
  graphSubtitle.textContent = subject.current_topic_name
    ? `Viewing dependency flow for ${subject.current_topic_name} using preview topics as graph nodes.`
    : "Viewing stored roadmap graph details using preview topics as graph nodes.";

  graphSummary.innerHTML = `
    <p><strong>Subject:</strong> ${escapeHtml(subject.subject_name || "Not set")}</p>
    <p><strong>Path:</strong> ${escapeHtml(path.path_title || "Not set")}</p>
    <p><strong>NetworkX available:</strong> ${escapeHtml(graph.networkx_available ? "Yes" : "No")}</p>
    <p><strong>Graph valid:</strong> ${escapeHtml(graph.graph_valid ? "Yes" : "No")}</p>
    <p><strong>Step nodes:</strong> ${escapeHtml(graph.node_count ?? 0)}</p>
    <p><strong>Step edges:</strong> ${escapeHtml(graph.edge_count ?? 0)}</p>
    <p><strong>Preview topic nodes:</strong> ${escapeHtml(topicGraph.nodes.length)}</p>
    <p><strong>Preview topic edges:</strong> ${escapeHtml(topicGraph.edges.length)}</p>
  `;
}

function normalizeTopicText(value) {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .replace(/\s+/g, " ");
}

function tokenizeTopic(value) {
  return normalizeTopicText(value)
    .split(" ")
    .filter((token) => token && token.length > 2);
}

function topicSimilarity(a, b) {
  const left = tokenizeTopic(a);
  const right = tokenizeTopic(b);
  if (!left.length || !right.length) return 0;

  const rightSet = new Set(right);
  let score = 0;
  left.forEach((token) => {
    if (rightSet.has(token)) {
      score += 3;
      return;
    }
    const partial = right.find((candidate) => candidate.startsWith(token) || token.startsWith(candidate));
    if (partial) {
      score += 1;
    }
  });
  return score;
}

function buildTopicGraph(graph) {
  const stepNodes = Array.isArray(graph.nodes) ? [...graph.nodes] : [];
  const stepEdges = Array.isArray(graph.edges) ? graph.edges : [];
  const orderedIds = Array.isArray(graph.topological_order) ? graph.topological_order : [];
  const rank = new Map(orderedIds.map((stepId, index) => [stepId, index]));
  const orderedSteps = stepNodes.sort((a, b) => {
    const aRank = rank.has(a.step_id) ? rank.get(a.step_id) : Number(a.step_order || 0);
    const bRank = rank.has(b.step_id) ? rank.get(b.step_id) : Number(b.step_order || 0);
    return aRank - bRank;
  });

  const stepCount = orderedSteps.length || 1;
  const nodes = [];
  const nodesByStep = new Map();

  orderedSteps.forEach((step, stepIndex) => {
    const terms = Array.isArray(step.preview_terms) && step.preview_terms.length
      ? step.preview_terms
      : [step.step_title || `Step ${stepIndex + 1}`];
    const tier = Math.min(2, Math.floor((stepIndex / stepCount) * 3));
    const topicNodes = terms.map((term, termIndex) => ({
      id: `${step.step_id}::${termIndex}`,
      label: term,
      stepId: step.step_id,
      stepTitle: step.step_title,
      stepOrder: step.step_order,
      stepStatus: step.step_status,
      tier,
      termIndex,
      prerequisiteStepIds: step.prerequisite_step_ids || [],
    }));
    nodes.push(...topicNodes);
    nodesByStep.set(step.step_id, topicNodes);
  });

  const edges = [];
  const edgeKeys = new Set();
  const pushEdge = (from, to, reason) => {
    if (!from || !to || from.id === to.id) return;
    const key = `${from.id}->${to.id}`;
    if (edgeKeys.has(key)) return;
    edgeKeys.add(key);
    edges.push({
      from: from.id,
      to: to.id,
      fromLabel: from.label,
      toLabel: to.label,
      fromStepTitle: from.stepTitle,
      toStepTitle: to.stepTitle,
      reason,
    });
  };

  nodesByStep.forEach((topicNodes) => {
    topicNodes.forEach((node, index) => {
      if (index > 0) {
        pushEdge(topicNodes[index - 1], node, "within-step flow");
      }
    });
  });

  stepEdges.forEach((edge) => {
    const fromTopics = nodesByStep.get(edge.from_step_id) || [];
    const toTopics = nodesByStep.get(edge.to_step_id) || [];
    if (!fromTopics.length || !toTopics.length) return;

    const matchedTargets = new Set();
    toTopics.forEach((targetTopic, targetIndex) => {
      let bestSource = null;
      let bestScore = -1;
      fromTopics.forEach((sourceTopic, sourceIndex) => {
        const similarity = topicSimilarity(sourceTopic.label, targetTopic.label);
        const fallbackBias = sourceIndex === fromTopics.length - 1 ? 0.25 : 0;
        const score = similarity + fallbackBias;
        if (score > bestScore) {
          bestScore = score;
          bestSource = sourceTopic;
        }
      });

      if (bestSource && (bestScore > 0 || (targetIndex === 0 && fromTopics.length))) {
        pushEdge(bestSource, targetTopic, bestScore > 0 ? "cross-step dependency" : "step prerequisite");
        matchedTargets.add(targetTopic.id);
      }
    });

    if (!matchedTargets.size) {
      pushEdge(fromTopics[fromTopics.length - 1], toTopics[0], "step prerequisite");
    }
  });

  return { nodes, edges, orderedSteps };
}

function renderVisualizationImage() {
  if (!graphImage) return;
  const { email, subjectId } = readParams();
  if (!email || !subjectId) {
    graphImage.removeAttribute("src");
    graphImage.alt = "Roadmap graph is unavailable";
    return;
  }
  const params = new URLSearchParams({ email, subject_id: subjectId });
  graphImage.src = `${API_BASE}/api/roadmap-graph-image?${params.toString()}`;
  graphImage.alt = "Python-generated roadmap graph";
}

function renderOrder(graph) {
  const topicGraph = buildTopicGraph(graph);
  graphOrderList.innerHTML = "";
  const grouped = topicGraph.orderedSteps.map((step, index) => ({
    index,
    topics: Array.isArray(step.preview_terms) && step.preview_terms.length
      ? step.preview_terms
      : [step.step_title || step.step_id],
  }));

  if (!grouped.length) {
    graphOrderList.innerHTML = "<li>No ordered steps available.</li>";
    return;
  }

  grouped.forEach((group, index) => {
    const item = document.createElement("li");
    item.textContent = `${index + 1}. ${group.topics.join(", ")}`;
    graphOrderList.appendChild(item);
  });
}

function renderNodes(graph) {
  const nodes = Array.isArray(graph.nodes) ? graph.nodes : [];
  if (!nodes.length) {
    graphNodes.innerHTML = `<p class="empty-state">No nodes found.</p>`;
    return;
  }

  graphNodes.innerHTML = nodes.map((node) => `
    <article class="step-card" style="margin-bottom: 16px;">
      <div class="step-card-head">
        <div>
          <p class="step-label">Step ${escapeHtml(node.step_order || "-")}</p>
          <h3>${escapeHtml(node.step_title || node.step_id)}</h3>
        </div>
        <span class="step-status">${escapeHtml(node.step_status || "unknown")}</span>
      </div>
      <div class="step-section">
        <div class="step-section-head">
          <span class="step-section-label">Node id</span>
          <span class="step-section-chip">${escapeHtml(node.step_id || "Not set")}</span>
        </div>
        <p class="step-section-text"><strong>Depends on:</strong> ${escapeHtml((node.prerequisite_step_ids || []).join(", ") || "None")}</p>
        <p class="step-section-text"><strong>Terms:</strong> ${escapeHtml((node.preview_terms || []).join(", ") || "None")}</p>
      </div>
    </article>
  `).join("");
}

function renderEdges(graph) {
  const topicGraph = buildTopicGraph(graph);
  graphEdges.innerHTML = "";
  const edges = topicGraph.edges;
  if (!edges.length) {
    graphEdges.innerHTML = "<li>No dependency edges found.</li>";
    return;
  }

  edges.forEach((edge) => {
    const item = document.createElement("li");
    item.textContent = `${edge.fromLabel} -> ${edge.toLabel} (${edge.reason})`;
    graphEdges.appendChild(item);
  });
}

async function loadGraph() {
  const { email, subjectId } = readParams();
  if (!email || !subjectId) {
    graphSummary.textContent = "Missing learner email or subject id.";
    return;
  }

  const params = new URLSearchParams({ email, subject_id: subjectId });
  const response = await fetch(`${API_BASE}/api/roadmap-graph?${params.toString()}`);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "Failed to load roadmap graph");
  }

  renderSummary(data);
  renderVisualizationImage();
  renderOrder(data.graph || {});
  renderNodes(data.graph || {});
  renderEdges(data.graph || {});
}

loadGraph().catch((error) => {
  graphSummary.textContent = error.message;
  if (graphImage) {
    graphImage.removeAttribute("src");
    graphImage.alt = "Unable to load graph visualization";
  }
  graphOrderList.innerHTML = "<li>Unable to load graph order.</li>";
  graphNodes.innerHTML = `<p class="empty-state">Unable to load nodes.</p>`;
  graphEdges.innerHTML = "<li>Unable to load edges.</li>";
});
