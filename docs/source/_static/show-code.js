// Reveal copyable input from rendered IPython examples without a UI library.
function extractIpythonInput(text) {
  const source = [];
  for (const line of text.split(/\r?\n/)) {
    const input = line.match(/^\s*In \[\d+\]: ?(.*)$/);
    if (input) {
      source.push(input[1]);
      continue;
    }
    const continuation = line.match(/^\s*\.{3}: ?(.*)$/);
    if (continuation) source.push(continuation[1]);
  }
  return source.join("\n").trimEnd();
}

document.addEventListener("DOMContentLoaded", () => {
  const blocks = document.querySelectorAll(".highlight-ipython");
  if (!blocks.length) return;

  const dialog = document.createElement("dialog");
  dialog.className = "h11-code-dialog";
  dialog.setAttribute("aria-labelledby", "h11-code-title");

  const header = document.createElement("div");
  header.className = "h11-code-dialog-header";
  const title = document.createElement("h2");
  title.id = "h11-code-title";
  title.textContent = "Python example";
  const close = document.createElement("button");
  close.type = "button";
  close.textContent = "Close";
  close.addEventListener("click", () => dialog.close());
  header.append(title, close);

  const textarea = document.createElement("textarea");
  textarea.readOnly = true;
  textarea.rows = 12;
  textarea.wrap = "off";
  textarea.setAttribute("aria-label", "Copyable Python source");
  dialog.append(header, textarea);
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
  document.body.appendChild(dialog);

  for (const block of blocks) {
    const source = extractIpythonInput(block.textContent || "");
    if (!source) continue;
    const action = document.createElement("button");
    action.type = "button";
    action.className = "h11-view-code";
    action.textContent = "View code";
    action.setAttribute("aria-haspopup", "dialog");
    action.addEventListener("click", () => {
      textarea.value = source;
      textarea.rows = Math.min(16, Math.max(4, source.split("\n").length + 1));
      dialog.showModal();
      textarea.focus();
      textarea.select();
    });
    block.appendChild(action);
  }
});
