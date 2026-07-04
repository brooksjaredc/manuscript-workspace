const statusEl = document.getElementById("status");
const refreshButton = document.getElementById("refresh");

async function refreshStatus() {
  statusEl.textContent = "Checking local server...";
  statusEl.dataset.kind = "info";
  try {
    const response = await fetch("http://127.0.0.1:8000/local/status");
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(payload?.error?.message || `HTTP ${response.status}`);
    }
    statusEl.textContent = `Connected to ${payload.project_name} (${payload.root_name}). Images save under ${payload.image_asset_root}.`;
    statusEl.dataset.kind = "success";
  } catch (error) {
    statusEl.textContent = `Not connected: ${error.message}`;
    statusEl.dataset.kind = "error";
  }
}

refreshButton.addEventListener("click", () => {
  void refreshStatus();
});

void refreshStatus();
