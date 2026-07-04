(() => {
  const SERVER_URL = "http://127.0.0.1:8000";
  const MIN_IMAGE_SIZE = 128;
  const LEGACY_BUTTON_CLASS = "mw-save-image-button";
  const LEGACY_STATUS_CLASS = "mw-save-image-status";
  const BUTTON_CLASS = "mw-v2-save-image-button";
  const STATUS_CLASS = "mw-v2-save-image-status";
  const OVERLAY_ATTR = "data-manuscript-workspace-overlay";
  const INSTANCE_ATTR = "data-manuscript-workspace-instance";
  const CONTROLLER_KEY = "__manuscriptWorkspaceImageSaver";
  const INSTANCE_ID = `${Date.now()}-${Math.random().toString(16).slice(2)}`;

  if (window[CONTROLLER_KEY]?.destroy) {
    window[CONTROLLER_KEY].destroy();
  }

  const overlays = new Map();
  const liveKeys = new Set();
  const cleanupCallbacks = [];

  function cleanupForeignOverlays() {
    document.querySelectorAll(`.${LEGACY_BUTTON_CLASS}, .${LEGACY_STATUS_CLASS}`).forEach((element) => {
      element.remove();
    });
    document.querySelectorAll(`.${BUTTON_CLASS}, .${STATUS_CLASS}, [${OVERLAY_ATTR}]`).forEach((element) => {
      if (element.getAttribute(INSTANCE_ATTR) === INSTANCE_ID) {
        return;
      }
      element.remove();
    });
  }

  function cleanupInactiveOverlays() {
    for (const [key, overlay] of overlays) {
      if (liveKeys.has(key)) {
        continue;
      }
      overlay.button.remove();
      overlay.status.remove();
      window.clearTimeout(overlay.statusTimer);
      overlays.delete(key);
    }
    liveKeys.clear();
  }

  function imageLooksGenerated(img) {
    const src = img.currentSrc || img.src || "";
    const width = img.naturalWidth || img.width || 0;
    const height = img.naturalHeight || img.height || 0;
    return Boolean(src) && width >= MIN_IMAGE_SIZE && height >= MIN_IMAGE_SIZE;
  }

  function sanitizeInput(value) {
    return (value || "").trim();
  }

  function getImageRect(img) {
    const rect = img.getBoundingClientRect();
    if (rect.width < MIN_IMAGE_SIZE || rect.height < MIN_IMAGE_SIZE) {
      return null;
    }
    if (rect.bottom < 0 || rect.right < 0 || rect.top > window.innerHeight || rect.left > window.innerWidth) {
      return null;
    }
    return rect;
  }

  function imageKey(img) {
    const rect = getImageRect(img);
    if (!rect) {
      return null;
    }
    const src = img.currentSrc || img.src || "";
    const left = Math.round(rect.left / 4) * 4;
    const top = Math.round(rect.top / 4) * 4;
    const width = Math.round(rect.width / 4) * 4;
    const height = Math.round(rect.height / 4) * 4;
    return `${src}|${left}|${top}|${width}|${height}`;
  }

  function rectOverlapRatio(a, b) {
    const left = Math.max(a.left, b.left);
    const right = Math.min(a.right, b.right);
    const top = Math.max(a.top, b.top);
    const bottom = Math.min(a.bottom, b.bottom);
    const width = Math.max(0, right - left);
    const height = Math.max(0, bottom - top);
    const overlapArea = width * height;
    const smallerArea = Math.min(a.width * a.height, b.width * b.height);
    return smallerArea > 0 ? overlapArea / smallerArea : 0;
  }

  function selectedImageCandidates() {
    const candidates = Array.from(document.querySelectorAll("img"))
      .filter(imageLooksGenerated)
      .map((img) => ({ img, rect: getImageRect(img), src: img.currentSrc || img.src || "" }))
      .filter((candidate) => candidate.rect && candidate.src)
      .sort((a, b) => (b.rect.width * b.rect.height) - (a.rect.width * a.rect.height));

    const selected = [];
    const selectedSources = new Set();
    for (const candidate of candidates) {
      const sourceKey = candidate.src.split("?")[0];
      const duplicatesExisting =
        selectedSources.has(sourceKey) ||
        selected.some((other) => rectOverlapRatio(candidate.rect, other.rect) > 0.6);
      if (duplicatesExisting) {
        continue;
      }
      selected.push(candidate);
      selectedSources.add(sourceKey);
    }
    return selected;
  }

  function guessFilename(img, blob) {
    const url = img.currentSrc || img.src || "";
    try {
      const parsed = new URL(url, window.location.href);
      const last = parsed.pathname.split("/").filter(Boolean).pop();
      if (last && /\.[A-Za-z0-9]+$/.test(last)) {
        return decodeURIComponent(last).replace(/[^A-Za-z0-9._-]+/g, "-");
      }
    } catch (error) {
      console.debug("Manuscript Workspace: could not parse image URL", error);
    }
    const extensionByType = {
      "image/png": "png",
      "image/jpeg": "jpg",
      "image/webp": "webp",
      "image/gif": "gif"
    };
    const extension = extensionByType[blob.type] || "png";
    return `chatgpt-image-${new Date().toISOString().replace(/[:.]/g, "-")}.${extension}`;
  }

  function setStatus(overlay, message, kind = "info") {
    overlay.status.textContent = message;
    overlay.status.dataset.kind = kind;
    overlay.status.hidden = false;
    if (kind === "success" || kind === "info") {
      window.clearTimeout(overlay.statusTimer);
      overlay.statusTimer = window.setTimeout(() => {
        overlay.status.hidden = true;
      }, kind === "success" ? 8000 : 3500);
    }
  }

  async function fetchImageBlob(img) {
    const url = img.currentSrc || img.src;
    if (!url) {
      throw new Error("No image URL found.");
    }
    const response = await fetch(url, { credentials: "include" });
    if (!response.ok) {
      throw new Error(`Image fetch failed with HTTP ${response.status}.`);
    }
    const blob = await response.blob();
    if (!blob.type.startsWith("image/")) {
      throw new Error(`Fetched content is not an image: ${blob.type || "unknown type"}.`);
    }
    return blob;
  }

  async function saveImage(img, overlay) {
    overlay.button.disabled = true;
    setStatus(overlay, "Fetching image...", "info");
    try {
      const blob = await fetchImageBlob(img);
      const defaultFilename = guessFilename(img, blob);
      const filename = sanitizeInput(window.prompt("Filename to save as:", defaultFilename) || defaultFilename);
      if (!filename) {
        setStatus(overlay, "Save cancelled.", "info");
        return;
      }
      const chapter = sanitizeInput(window.prompt("Chapter folder, optional. Example: chapter-02", "") || "");
      const description = sanitizeInput(window.prompt("Description, optional:", "") || "");

      const form = new FormData();
      form.append("image", blob, defaultFilename);
      form.append("filename", filename);
      if (chapter) form.append("chapter", chapter);
      if (description) form.append("description", description);

      setStatus(overlay, "Saving to Manuscript Workspace...", "info");
      const response = await fetch(`${SERVER_URL}/local/save-generated-image`, {
        method: "POST",
        body: form
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload.ok) {
        const message = payload?.error?.message || `Save failed with HTTP ${response.status}.`;
        throw new Error(message);
      }
      setStatus(overlay, `Saved: ${payload.relative_path}`, "success");
    } catch (error) {
      console.error("Manuscript Workspace image save failed", error);
      setStatus(overlay, `${error.message} Download fallback may be needed.`, "error");
      const url = img.currentSrc || img.src;
      if (url) {
        try {
          window.open(url, "_blank", "noopener,noreferrer");
        } catch (openError) {
          console.debug("Manuscript Workspace: could not open image fallback tab", openError);
        }
      }
    } finally {
      overlay.button.disabled = false;
    }
  }

  function createOverlay(img) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = BUTTON_CLASS;
    button.setAttribute(OVERLAY_ATTR, "true");
    button.setAttribute(INSTANCE_ATTR, INSTANCE_ID);
    button.textContent = "Save to Workspace";
    button.title = "Save this image to Manuscript Workspace";

    const status = document.createElement("div");
    status.className = STATUS_CLASS;
    status.setAttribute(OVERLAY_ATTR, "true");
    status.setAttribute(INSTANCE_ATTR, INSTANCE_ID);
    status.hidden = true;

    const overlay = { button, status, statusTimer: undefined };
    const stop = (event) => {
      event.preventDefault();
      event.stopPropagation();
    };
    button.addEventListener("pointerdown", stop, true);
    button.addEventListener("mousedown", stop, true);
    button.addEventListener("click", (event) => {
      stop(event);
      void saveImage(img, overlay);
    }, true);

    document.documentElement.appendChild(button);
    document.documentElement.appendChild(status);
    return overlay;
  }

  function positionOverlay(img, overlay) {
    const rect = getImageRect(img);
    if (!rect || !document.contains(img)) {
      overlay.button.hidden = true;
      overlay.status.hidden = true;
      return;
    }

    overlay.button.hidden = false;
    // Top-left avoids ChatGPT's download/share controls, which usually sit below or to the right of generated images.
    const top = Math.max(8, rect.top + 10);
    const left = Math.max(8, rect.left + 10);
    overlay.button.style.top = `${top}px`;
    overlay.button.style.left = `${left}px`;
    overlay.status.style.top = `${top + 38}px`;
    overlay.status.style.left = `${left}px`;
    overlay.status.style.maxWidth = `${Math.max(220, Math.min(rect.width - 20, 520))}px`;
  }

  function ensureOverlay(img) {
    const key = imageKey(img);
    if (!key || liveKeys.has(key)) {
      return;
    }
    liveKeys.add(key);
    const overlay = overlays.get(key) || createOverlay(img);
    overlays.set(key, overlay);
    positionOverlay(img, overlay);
  }

  function scan() {
    cleanupForeignOverlays();
    selectedImageCandidates().forEach(({ img }) => ensureOverlay(img));
    cleanupInactiveOverlays();
  }

  let scheduled = false;
  function scheduleScan() {
    if (scheduled) return;
    scheduled = true;
    window.requestAnimationFrame(() => {
      scheduled = false;
      scan();
    });
  }

  const observer = new MutationObserver(scheduleScan);
  cleanupForeignOverlays();
  observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ["src", "style", "class"] });
  window.addEventListener("scroll", scheduleScan, true);
  window.addEventListener("resize", scheduleScan);
  cleanupCallbacks.push(() => window.removeEventListener("scroll", scheduleScan, true));
  cleanupCallbacks.push(() => window.removeEventListener("resize", scheduleScan));
  const intervalId = window.setInterval(scan, 750);
  cleanupCallbacks.push(() => window.clearInterval(intervalId));
  window[CONTROLLER_KEY] = {
    destroy() {
      observer.disconnect();
      cleanupCallbacks.forEach((callback) => callback());
      overlays.forEach((overlay) => {
        overlay.button.remove();
        overlay.status.remove();
        window.clearTimeout(overlay.statusTimer);
      });
      overlays.clear();
    }
  };
  scan();
})();
