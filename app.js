// Start page: runs zeitung_reader.py unchanged inside the browser (Pyodide + PyMuPDF)
// and replaces this page with the generated large-print reader. The PDF never leaves the computer.

// Pyodide 0.28.3 is the last release that ships PyMuPDF; newer ones (314.x) do not.
const PYODIDE_URL = "https://cdn.jsdelivr.net/pyodide/v0.28.3/full/";
const PY_DIR = "/home/pyodide/";
const SOURCES = ["zeitung_reader.py", "reader_template.html"];

const button = document.getElementById("open");
const input = document.getElementById("file");
const status = document.getElementById("status");

try { document.documentElement.dataset.theme = localStorage.getItem("zr-theme") || "light"; } catch {}

function say(text, error = false) {
  status.textContent = text;
  status.classList.toggle("error", error);
}

// Start loading immediately, so Python is usually ready by the time a file is chosen.
const ready = (async () => {
  const { loadPyodide } = await import(PYODIDE_URL + "pyodide.mjs");
  const py = await loadPyodide({ indexURL: PYODIDE_URL });
  await py.loadPackage("pymupdf");
  for (const name of SOURCES) {
    const res = await fetch(name, { cache: "no-cache" });
    if (!res.ok) throw new Error(`${name}: HTTP ${res.status}`);
    py.FS.writeFile(PY_DIR + name, await res.text());
  }
  py.runPython(`import sys; sys.path.insert(0, "${PY_DIR}"); import zeitung_reader`);
  return py;
})();

ready.then(
  () => { if (!button.disabled) say("Bereit."); },
  err => {
    console.error(err);
    say("Das Programm konnte nicht geladen werden. Bitte die Internetverbindung prüfen und die Seite neu laden.", true);
  },
);

async function openPdf(file) {
  if (!file) return;
  if (!/\.pdf$/i.test(file.name) && file.type !== "application/pdf") {
    say("Das ist keine PDF-Datei. Bitte die Zeitung als PDF auswählen.", true);
    return;
  }
  button.disabled = true;
  say("Die Zeitung wird vorbereitet … bitte einen Moment warten.");
  try {
    const py = await ready;
    await new Promise(r => setTimeout(r, 50));   // let the message paint; Python blocks the page for a few seconds
    // keep the file name: parse_pdf reads the date from it
    const path = "/tmp/" + file.name.replace(/[^\w.-]/g, "_");
    py.FS.writeFile(path, new Uint8Array(await file.arrayBuffer()));
    py.globals.set("pdf_path", path);
    const html = py.runPython(`
edition = zeitung_reader.parse_pdf(pdf_path)
zeitung_reader.render_html(edition) if edition["articles"] else ""
`);
    py.FS.unlink(path);
    if (!html) {
      say("In dieser Datei wurden keine Artikel gefunden. Ist es die Süddeutsche Zeitung als PDF?", true);
      button.disabled = false;
      return;
    }
    document.open();
    document.write(html);
    document.close();
  } catch (err) {
    console.error(err);
    say("Die Zeitung konnte nicht geöffnet werden. Bitte die Seite neu laden und es noch einmal versuchen.", true);
    button.disabled = false;
  }
}

button.onclick = () => input.click();
input.onchange = () => {
  const file = input.files[0];
  input.value = "";   // allows choosing the same file again after an error
  openPdf(file);
};

window.addEventListener("dragover", e => { e.preventDefault(); document.body.classList.add("dragging"); });
window.addEventListener("dragleave", e => { if (!e.relatedTarget) document.body.classList.remove("dragging"); });
window.addEventListener("drop", e => {
  e.preventDefault();
  document.body.classList.remove("dragging");
  openPdf(e.dataTransfer.files[0]);
});
