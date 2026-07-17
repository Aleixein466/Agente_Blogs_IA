async function postJson(url, data, csrfToken) {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrfToken || "",
    },
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || "No se pudo completar la operacion");
  }
  return response.json();
}

async function deleteResource(url, csrfToken) {
  const response = await fetch(url, {
    method: "DELETE",
    headers: {
      "X-CSRF-Token": csrfToken || "",
    },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || "No se pudo eliminar");
  }
  return response.json();
}

function showStatus(id, message, type = "secondary") {
  const box = document.getElementById(id);
  if (!box) return;
  box.className = `alert alert-${type}`;
  box.textContent = message;
  box.classList.remove("d-none");
}

async function playBackendSpeech(message, statusId) {
  if (!message) return;
  try {
    const response = await fetch("/api/voice/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: message }),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || "TTS no disponible");
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    audio.play().catch(() => {});
    audio.addEventListener("ended", () => URL.revokeObjectURL(url), { once: true });
  } catch (error) {
    if (statusId) {
      showStatus(statusId, `${error.message}. Instala Kokoro para salida de voz real.`, "warning");
    }
  }
}

function bindRecorder(buttonId, inputId, statusId) {
  const button = document.getElementById(buttonId);
  const input = document.getElementById(inputId);
  if (!button || !input || !navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
    return;
  }

  let mediaRecorder = null;
  let stream = null;
  let chunks = [];
  let active = false;

  async function stopAndUpload() {
    if (!mediaRecorder) return;
    mediaRecorder.stop();
    active = false;
    button.textContent = "Grabar voz";
  }

  button.addEventListener("click", async () => {
    if (active) {
      await stopAndUpload();
      return;
    }

    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      chunks = [];
      mediaRecorder = new MediaRecorder(stream);
      mediaRecorder.addEventListener("dataavailable", (event) => {
        if (event.data.size > 0) chunks.push(event.data);
      });
      mediaRecorder.addEventListener("stop", async () => {
        const blob = new Blob(chunks, { type: mediaRecorder.mimeType || "audio/webm" });
        const file = new File([blob], "prompt.webm", { type: blob.type || "audio/webm" });
        const formData = new FormData();
        formData.append("file", file);
        showStatus(statusId, "Transcribiendo audio con faster-whisper...", "info");
        try {
          const response = await fetch("/api/voice/stt", { method: "POST", body: formData });
          if (!response.ok) {
            const payload = await response.json().catch(() => ({}));
            throw new Error(payload.detail || "No se pudo transcribir el audio");
          }
          const payload = await response.json();
          input.value = payload.text || "";
          showStatus(statusId, "Texto capturado desde audio.", "success");
        } catch (error) {
          showStatus(statusId, `${error.message}. Revisa faster-whisper.`, "warning");
        } finally {
          stream?.getTracks().forEach((track) => track.stop());
          stream = null;
          mediaRecorder = null;
        }
      });
      mediaRecorder.start();
      active = true;
      button.textContent = "Detener";
      showStatus(statusId, "Grabando audio desde microfono...", "info");
    } catch (error) {
      showStatus(statusId, "No pude acceder al microfono.", "warning");
    }
  });
}

async function loadVoiceStatus() {
  try {
    const response = await fetch("/api/voice/status");
    if (!response.ok) return;
    const payload = await response.json();
    const lines = [];
    lines.push(payload.stt?.available ? "STT listo" : `STT pendiente: ${payload.stt?.reason || "sin modelo"}`);
    lines.push(payload.tts?.available ? "TTS listo" : `TTS pendiente: ${payload.tts?.reason || "sin modelo"}`);
    const box = document.getElementById("status-box");
    if (box && lines.length) {
      showStatus("status-box", lines.join(" | "), payload.stt?.available && payload.tts?.available ? "success" : "warning");
    }
  } catch (_) {
    // Keep quiet if status cannot be loaded.
  }
}

document.addEventListener("DOMContentLoaded", () => {
  bindRecorder("voice-prompt-btn", "prompt-input", "status-box");
  bindRecorder("voice-instruction-btn", "instruction-input", "detail-status");
  loadVoiceStatus();

  const generateBtn = document.getElementById("generate-btn");
  if (generateBtn) {
    generateBtn.addEventListener("click", async () => {
      const prompt = document.getElementById("prompt-input")?.value.trim();
      if (!prompt) {
        showStatus("status-box", "Escribe o dicta un prompt para generar el blog.", "warning");
        return;
      }
      showStatus("status-box", "Generando blog...", "info");
      try {
        const blog = await postJson("/api/blogs", { prompt, owner_username: "admin" }, generateBtn.dataset.csrf);
        showStatus("status-box", `Blog creado: ${blog.title}. Recargando panel...`, "success");
        await playBackendSpeech(`He creado tu blog ${blog.title}`, "status-box");
        window.setTimeout(() => window.location.reload(), 900);
      } catch (error) {
        showStatus("status-box", error.message, "danger");
        await playBackendSpeech("No pude crear el blog", "status-box");
      }
    });
  }

  const editBtn = document.getElementById("edit-btn");
  if (editBtn) {
    editBtn.addEventListener("click", async () => {
      const instruction = document.getElementById("instruction-input")?.value.trim();
      if (!instruction) {
        showStatus("detail-status", "Escribe o dicta una instruccion antes de editar.", "warning");
        return;
      }
      showStatus("detail-status", "Aplicando cambios...", "info");
      try {
        const version = await postJson(`/api/blogs/${editBtn.dataset.blogId}/edit`, { instruction }, editBtn.dataset.csrf);
        showStatus("detail-status", `Version ${version.version_number} generada. Recargando vista...`, "success");
        await playBackendSpeech(`He actualizado tu blog a la version ${version.version_number}`, "detail-status");
        window.setTimeout(() => window.location.reload(), 900);
      } catch (error) {
        showStatus("detail-status", error.message, "danger");
        await playBackendSpeech("No pude actualizar el blog", "detail-status");
      }
    });
  }

  const publishBtn = document.getElementById("publish-btn");
  if (publishBtn) {
    publishBtn.addEventListener("click", async () => {
      showStatus("detail-status", "Publicando blog...", "info");
      try {
        const blog = await postJson(`/api/blogs/${publishBtn.dataset.blogId}/publish`, { publish: true }, publishBtn.dataset.csrf);
        showStatus("detail-status", `Publicado en ${blog.published_url}`, "success");
      } catch (error) {
        showStatus("detail-status", error.message, "danger");
      }
    });
  }

  const deleteBtn = document.getElementById("delete-btn");
  if (deleteBtn) {
    deleteBtn.addEventListener("click", async () => {
      const confirmed = window.confirm("Se eliminara este blog y sus versiones. Deseas continuar?");
      if (!confirmed) return;
      showStatus("detail-status", "Eliminando blog...", "warning");
      try {
        await deleteResource(`/api/blogs/${deleteBtn.dataset.blogId}`, deleteBtn.dataset.csrf);
        showStatus("detail-status", "Blog eliminado. Volviendo al dashboard...", "success");
        window.setTimeout(() => {
          window.location.href = "/dashboard";
        }, 900);
      } catch (error) {
        showStatus("detail-status", error.message, "danger");
      }
    });
  }

  document.querySelectorAll(".delete-blog-btn").forEach((button) => {
    button.addEventListener("click", async () => {
      const confirmed = window.confirm("Se eliminara este blog. Deseas continuar?");
      if (!confirmed) return;
      showStatus("status-box", "Eliminando blog...", "warning");
      try {
        await deleteResource(`/api/blogs/${button.dataset.blogId}`, button.dataset.csrf || "");
        showStatus("status-box", "Blog eliminado. Recargando panel...", "success");
        window.setTimeout(() => window.location.reload(), 700);
      } catch (error) {
        showStatus("status-box", error.message, "danger");
      }
    });
  });
});
