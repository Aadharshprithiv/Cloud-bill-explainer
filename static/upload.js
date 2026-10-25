// Drag-and-drop upload: dropping a file fills the file input.
(function () {
  const zone = document.getElementById("dropzone");
  const input = document.getElementById("bill");
  const nameTag = document.getElementById("file-name");
  const form = document.getElementById("upload-form");
  const button = document.getElementById("submit-btn");
  if (!zone || !input) return;

  function showName() {
    const file = input.files && input.files[0];
    nameTag.textContent = file ? file.name : "";
    nameTag.classList.toggle("hidden", !file);
  }

  ["dragenter", "dragover"].forEach((type) =>
    zone.addEventListener(type, (event) => {
      event.preventDefault();
      zone.classList.add("border-brand-500", "bg-brand-50");
    })
  );
  ["dragleave", "drop"].forEach((type) =>
    zone.addEventListener(type, (event) => {
      event.preventDefault();
      zone.classList.remove("border-brand-500", "bg-brand-50");
    })
  );
  zone.addEventListener("drop", (event) => {
    if (event.dataTransfer.files.length) {
      input.files = event.dataTransfer.files;
      showName();
    }
  });
  input.addEventListener("change", showName);
  form.addEventListener("submit", () => {
    button.disabled = true;
    button.textContent = "Reading your bill...";
  });
  // Re-enable the button if the user comes back with the browser's back button.
  window.addEventListener("pageshow", () => {
    button.disabled = false;
    button.textContent = "Explain my bill";
  });
})();
