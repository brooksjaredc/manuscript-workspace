(() => {
  const SERVER_URL = "http://127.0.0.1:8000";
  const MIN_IMAGE_SIZE = 128;
  const BUTTON_CLASS = "mw-hover-save-image-button";
  const STATUS_CLASS = "mw-hover-save-image-status";
  const CONTROLLER_KEY = "__manuscriptWorkspaceImageSaver";

  if (window[CONTROLLER_KEY]?.destroy) {
    window[CONTROLLER_KEY].destroy();
  }

  let activeImage = null;
  let hideTimer = null;
  let statusTimer = null;

  const button = document.createElement("button");
  button.type = "button";
  button.className = BUTTON_CLASS;
  button.textContent = "Save to Workspace";
  button.title = "Save this image to Manuscript Workspace";
  button.hidden = true;

  const status = document.createElement("div");
  status.className = STATUS_CLASS;
  status.hidden = true;

  document.documentElement.appendChild(button);
  document.documentElement.appendChild(status);

  function removeLegacyElements() {
    document.querySelectorAll(".mw-save-image-button, .mw-save-image-status, .mw-v2-save-image-button, .mw-v2-save-image-status").forEach((element) => {
      element.remove();
    });
  }

  function imageLooksGenerated(img) {
    const src = img.currentSrc || img.src || "";
    const width = img.naturalWidth || img.width || 0;
    const height = img.naturalHeight || img.height || 0;
    return Boolean(src) && width >= MIN_IMAGE_SIZE && height >= MIN_IMAGE_SIZE;
  }

  function getImageFromEvent(event) {
    const path = event.composedPath ? event.composedPath() : [];
    for (const item of path) {
      if (item instanceof HTMLImageElement && imageLooksGenerated(item)) {
        return item;
      }
    }
    const target = event.target;
    return target instanceof HTMLImageElement && imageLooksGenerated(target) ? target : null;
  }

  function getVisibleRect(img) {
    if (!img || !document.contains(img)) return null;
    const rect = img.getBoundingClientRect();
    if (rect.width < MIN_IMAGE_SIZE || rect.height < MIN_IMAGE_SIZE) return null;
    if (rect.bottom < 0 || rect.right < 0 || rect.top > window.innerHeight || rect.left > window.innerWidth) return null;
    return rect;
  }

  function positionForImage(img) {
    const rect = getVisibleRect(img);
    if (!rect) {
      hideButton();
      return;
    }
    const top = Math.max(8, rect.top + 10);
    const left = Math.max(8, rect.left + 10);
    button.style.top = `${top}px`;
    button.style.left = `${left}px`;
    status.style.top = `${top + 38}px`;
    status.style.left = `${left}px`;
    status.style.maxWidth = `${Math.max(220, Math.min(rect.width - 20, 520))}px`;
  }

  function showForImage(img) {
    window.clearTimeout(hideTimer);
    removeLegacyElements();
    activeImage = img;
    positionForImage(img);
    button.hidden = false;
  }

  function hideButton() {
    activeImage = null;
    button.hidden = true;
    if (status.dataset.kind !== "error" && status.dataset.kind !== "success") {
      status.hidden = true;
    }
  }

  function delayedHide() {
    window.clearTimeout(hideTimer);
    hideTimer = window.setTimeout(() => {
      if (!button.matches(":hover")) {
        hideButton();
      }
    }, 650);
  }

  function setStatus(message, kind = "info") {
    status.textContent = message;
    status.dataset.kind = kind;
    status.hidden = false;
    window.clearTimeout(statusTimer);
    if (kind === "success" || kind === "info") {
      statusTimer = window.setTimeout(() => {
        status.hidden = true;
      }, kind === "success" ? 8000 : 3500);
    }
  }

  function sanitizeInput(value) {
    return (value || "").trim();
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

  async function fetchImageBlob(img) {
    const url = img.currentSrc || img.src;
    if (!url) throw new Error("No image URL found.");
    const response = await fetch(url, { credentials: "include" });
    if (!response.ok) throw new Error(`Image fetch failed with HTTP ${response.status}.`);
    const blob = await response.blob();
    if (!blob.type.startsWith("image/")) throw new Error(`Fetched content is not an image: ${blob.type || "unknown type"}.`);
    return blob;
  }

  async function saveImage() {
    const img = activeImage;
    if (!img) return;
    button.disabled = true;
    setStatus("Fetching image...", "info");
    try {
      const blob = await fetchImageBlob(img);
      const defaultFilename = guessFilename(img, blob);
      const filename = sanitizeInput(window.prompt("Filename to save as:", defaultFilename) || defaultFilename);
      if (!filename) {
        setStatus("Save cancelled.", "info");
        return;
      }
      const chapter = sanitizeInput(window.prompt("Chapter folder, optional. Example: chapter-02", "") || "");
      const description = sanitizeInput(window.prompt("Description, optional:", "") || "");

      const form = new FormData();
      form.append("image", blob, defaultFilename);
      form.append("filename", filename);
      if (chapter) form.append("chapter", chapter);
      if (description) form.append("description", description);

      setStatus("Saving to Manuscript Workspace...", "info");
      const response = await fetch(`${SERVER_URL}/local/save-generated-image`, {
        method: "POST",
        body: form
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload.ok) {
        throw new Error(payload?.error?.message || `Save failed with HTTP ${response.status}.`);
      }
      setStatus(`Saved: ${payload.relative_path}`, "success");
    } catch (error) {
      console.error("Manuscript Workspace image save failed", error);
      setStatus(`${error.message} Download fallback may be needed.`, "error");
    } finally {
      button.disabled = false;
    }
  }

  function onPointerOver(event) {
    const img = getImageFromEvent(event);
    if (img) showForImage(img);
  }

  function onPointerOut(event) {
    const related = event.relatedTarget;
    if (related === button || button.contains(related)) return;
    delayedHide();
  }

  function onScrollOrResize() {
    removeLegacyElements();
    if (activeImage) positionForImage(activeImage);
  }

  function onClick(event) {
    event.preventDefault();
    event.stopPropagation();
    void saveImage();
  }

  document.addEventListener("pointerover", onPointerOver, true);
  document.addEventListener("pointerout", onPointerOut, true);
  window.addEventListener("scroll", onScrollOrResize, true);
  window.addEventListener("resize", onScrollOrResize);
  button.addEventListener("pointerover", () => window.clearTimeout(hideTimer));
  button.addEventListener("pointerout", delayedHide);
  button.addEventListener("click", onClick, true);

  const cleanupInterval = window.setInterval(removeLegacyElements, 1500);
  removeLegacyElements();

  window[CONTROLLER_KEY] = {
    destroy() {
      document.removeEventListener("pointerover", onPointerOver, true);
      document.removeEventListener("pointerout", onPointerOut, true);
      window.removeEventListener("scroll", onScrollOrResize, true);
      window.removeEventListener("resize", onScrollOrResize);
      window.clearInterval(cleanupInterval);
      window.clearTimeout(hideTimer);
      window.clearTimeout(statusTimer);
      button.remove();
      status.remove();
    }
  };
})();
