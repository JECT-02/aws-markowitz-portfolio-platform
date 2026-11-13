const STORAGE_KEYS = {
  verifier: "spa_code_verifier",
  accessToken: "spa_access_token",
  refreshToken: "spa_refresh_token",
};

const BASE64URL = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";

function showError(text) {
  const message = document.getElementById("message");
  message.textContent = text;
  message.classList.add("error");
  message.hidden = false;
}

function showInfo(text) {
  const message = document.getElementById("message");
  message.textContent = text;
  message.classList.remove("error");
  message.hidden = false;
}

function formatPercent(value) {
  if (value === null || value === undefined) {
    return "n/a";
  }
  return `${(value * 100).toFixed(1)}%`;
}

function formatDateTime(value) {
  if (!value) {
    return "n/a";
  }
  return new Date(value).toLocaleString();
}

function randomVerifier() {
  const bytes = new Uint8Array(64);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => BASE64URL[byte & 0x3f]).join("");
}

async function sha256Base64Url(text) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  const bytes = new Uint8Array(digest);
  return btoa(String.fromCharCode(...bytes))
    .replaceAll("=", "")
    .replaceAll("+", "-")
    .replaceAll("/", "_");
}

async function startLogin() {
  const config = window.SPA_CONFIG;
  const verifier = randomVerifier();
  sessionStorage.setItem(STORAGE_KEYS.verifier, verifier);
  const challenge = await sha256Base64Url(verifier);
  const redirectUri = window.location.origin;
  const params = new URLSearchParams({
    response_type: "code",
    client_id: config.clientId,
    redirect_uri: redirectUri,
    scope: "openid email profile",
    code_challenge: challenge,
    code_challenge_method: "S256",
  });
  window.location.assign(`${config.cognitoDomain}/oauth2/authorize?${params}`);
}

async function exchangeCode(code) {
  const config = window.SPA_CONFIG;
  const verifier = sessionStorage.getItem(STORAGE_KEYS.verifier);
  if (!verifier) {
    throw new Error("missing code verifier");
  }
  const response = await fetch(`${config.cognitoDomain}/oauth2/token`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      client_id: config.clientId,
      redirect_uri: window.location.origin,
      code: code,
      code_verifier: verifier,
    }),
  });
  if (!response.ok) {
    throw new Error("token exchange failed");
  }
  const tokens = await response.json();
  sessionStorage.setItem(STORAGE_KEYS.accessToken, tokens.access_token);
  sessionStorage.setItem(STORAGE_KEYS.refreshToken, tokens.refresh_token);
  sessionStorage.removeItem(STORAGE_KEYS.verifier);
}

function signOut() {
  sessionStorage.removeItem(STORAGE_KEYS.accessToken);
  sessionStorage.removeItem(STORAGE_KEYS.refreshToken);
  const params = new URLSearchParams({
    client_id: window.SPA_CONFIG.clientId,
    logout_uri: window.location.origin,
  });
  window.location.assign(`${window.SPA_CONFIG.cognitoDomain}/logout?${params}`);
}

async function apiGet(path) {
  const token = sessionStorage.getItem(STORAGE_KEYS.accessToken);
  if (!token) {
    return null;
  }
  const response = await fetch(`${window.SPA_CONFIG.apiEndpoint}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (response.status === 401) {
    sessionStorage.removeItem(STORAGE_KEYS.accessToken);
    return null;
  }
  if (response.status === 404) {
    const body = await response.json();
    return { notFound: true, message: body.message };
  }
  if (!response.ok) {
    throw new Error(`request to ${path} failed with status ${response.status}`);
  }
  return response.json();
}

function summarize(element, fields) {
  element.innerHTML = "";
  for (const [label, value] of fields) {
    const cell = document.createElement("div");
    const strong = document.createElement("strong");
    strong.textContent = label;
    cell.appendChild(strong);
    cell.appendChild(document.createTextNode(value));
    element.appendChild(cell);
  }
}

function renderWeights(weights, actualWeights, status) {
  const symbols = Object.keys(weights);
  const traceProposed = {
    type: "bar",
    name: "Proposed",
    x: symbols,
    y: symbols.map((symbol) => weights[symbol]),
    marker: { color: "#4c8dff" },
  };
  const traceActual = {
    type: "bar",
    name: "Actual",
    x: symbols,
    y: symbols.map((symbol) => actualWeights[symbol] ?? 0),
    marker: { color: "#93a1b4" },
  };
  Plotly.newPlot(
    "weights-chart",
    [traceProposed, traceActual],
    {
      barmode: "group",
      height: 320,
      margin: { t: 10, b: 30, l: 40, r: 10 },
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      font: { color: "#e8edf4" },
    },
    { responsive: true, displayModeBar: false }
  );
}

function renderMetrics(metrics) {
  const container = document.getElementById("metrics");
  const names = Object.keys(metrics);
  if (names.length === 0) {
    container.innerHTML = "<p>No metrics available yet.</p>";
    return;
  }
  const columns = [
    ["annualized_return", "Annualized return"],
    ["annualized_volatility", "Annualized volatility"],
    ["sharpe_ratio", "Sharpe"],
    ["sortino_ratio", "Sortino"],
    ["max_drawdown", "Max drawdown"],
    ["calmar_ratio", "Calmar"],
    ["herfindahl_index", "Herfindahl"],
  ];
  const table = document.createElement("table");
  const headerRow = document.createElement("tr");
  const nameHeader = document.createElement("th");
  nameHeader.textContent = "Allocation";
  headerRow.appendChild(nameHeader);
  for (const [, label] of columns) {
    const th = document.createElement("th");
    th.textContent = label;
    headerRow.appendChild(th);
  }
  table.appendChild(headerRow);
  for (const name of names) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.textContent = name;
    row.appendChild(cell);
    for (const [field] of columns) {
      const td = document.createElement("td");
      td.textContent = metrics[name][field] === undefined ? "n/a" : metrics[name][field].toFixed(4);
      row.appendChild(td);
    }
    table.appendChild(row);
  }
  container.innerHTML = "";
  container.appendChild(table);
}

function renderProposal(proposal) {
  document.getElementById("proposal").hidden = false;
  summarize(document.getElementById("proposal-summary"), [
    ["Proposal", proposal.proposal_id],
    ["Run date", proposal.run_date],
    ["Status", proposal.status],
    ["Drift (L1)", formatPercent(proposal.drift_l1)],
    ["Rebalance", proposal.rebalance_required ? "required" : "not required"],
    ["Window", `${proposal.window_days} days`],
    ["Prices ref", proposal.prices_ref],
    ["Created", formatDateTime(proposal.created_at)],
  ]);
  renderWeights(proposal.weights, proposal.actual_weights, proposal.status);
  renderMetrics(proposal.metrics);
  const excluded = Object.entries(proposal.excluded);
  if (excluded.length > 0) {
    showInfo(`Excluded assets: ${excluded.map(([symbol, reason]) => `${symbol} (${reason})`).join(", ")}`);
  }
}

function renderMonitor(snapshot) {
  document.getElementById("monitor").hidden = false;
  summarize(document.getElementById("monitor-summary"), [
    ["Weights", Object.keys(snapshot.weights).length + " assets"],
    ["Drift (L1)", formatPercent(snapshot.drift_l1)],
    ["Reconciled", formatDateTime(snapshot.reconciled_at)],
    ["From proposal", snapshot.proposal_id],
  ]);
}

function renderBacktest(outcome) {
  document.getElementById("backtest").hidden = false;
  document.getElementById("backtest-summary").textContent =
    outcome.notFound ? outcome.message : "Backtest results available.";
}

function setSessionState(loggedIn) {
  document.getElementById("login").hidden = loggedIn;
  document.getElementById("logout").hidden = !loggedIn;
}

async function main() {
  const loginButton = document.getElementById("login");
  const logoutButton = document.getElementById("logout");
  const params = new URLSearchParams(window.location.search);
  const code = params.get("code");
  const token = sessionStorage.getItem(STORAGE_KEYS.accessToken);

  if (code && !token) {
    try {
      await exchangeCode(code);
      window.history.replaceState({}, "", window.location.pathname);
    } catch (error) {
      showError("Sign in failed. Please try again.");
      console.log("token exchange error", error.message);
    }
  }

  const accessToken = sessionStorage.getItem(STORAGE_KEYS.accessToken);
  if (!accessToken) {
    setSessionState(false);
    showInfo("Sign in to view your portfolio.");
    loginButton.addEventListener("click", startLogin);
    return;
  }
  setSessionState(true);
  logoutButton.addEventListener("click", signOut);
  loginButton.removeEventListener("click", startLogin);

  try {
    const [proposal, monitor, backtest] = await Promise.all([
      apiGet("/proposal"),
      apiGet("/monitor"),
      apiGet("/backtest"),
    ]);
    if (proposal === null || monitor === null) {
      showError("Session expired. Sign in again.");
      setSessionState(false);
      return;
    }
    renderProposal(proposal);
    renderMonitor(monitor);
    renderBacktest(backtest);
  } catch (error) {
    showError("The API is not available right now.");
    console.log("api error", error.message);
  }
}

main();
