const gatePage = document.querySelector("#gate-page");
const letterPage = document.querySelector("#letter-page");
const openingTransition = document.querySelector("#opening-transition");
const form = document.querySelector("#unlock-form");
const passwordInput = document.querySelector("#letter-password");
const unlockButton = document.querySelector("#unlock-button");
const revealButton = document.querySelector("#reveal-password");
const message = document.querySelector("#password-message");
const closeButton = document.querySelector("#close-letter");
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

passwordInput.addEventListener("input", () => {
  unlockButton.disabled = !passwordInput.value.trim();
  message.innerHTML = "&nbsp;";
  form.classList.remove("shake");
  passwordInput.removeAttribute("aria-invalid");
});

revealButton.addEventListener("click", () => {
  const showing = passwordInput.type === "text";
  passwordInput.type = showing ? "password" : "text";
  revealButton.classList.toggle("showing", !showing);
  revealButton.setAttribute("aria-label", showing ? "显示暗号" : "隐藏暗号");
  passwordInput.focus();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!passwordInput.value.trim()) return;

  setUnlocking(true);

  try {
    const response = await fetch("/thanks/unlock", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: passwordInput.value }),
    });

    if (!response.ok) {
      wrongPassword();
      return;
    }

    const loaded = await loadLetter({ animate: true });
    if (!loaded) {
      message.textContent = "信箱刚刚打了个盹，请稍后再试一次。";
    }
  } catch {
    message.textContent = "信箱刚刚打了个盹，请稍后再试一次。";
  } finally {
    setUnlocking(false);
  }
});

closeButton.addEventListener("click", () => {
  letterPage.hidden = true;
  gatePage.hidden = false;
  passwordInput.value = "";
  unlockButton.disabled = true;
  window.scrollTo({ top: 0, behavior: reduceMotion ? "auto" : "smooth" });
  window.setTimeout(() => passwordInput.focus(), reduceMotion ? 0 : 350);
});

async function loadLetter({ animate = false } = {}) {
  const response = await fetch("/thanks/letter", { cache: "no-store" });
  if (!response.ok) return false;

  const letter = await response.json();
  renderLetter(letter);

  if (animate) {
    await playOpeningTransition();
  } else {
    gatePage.hidden = true;
    letterPage.hidden = false;
    window.scrollTo({ top: 0 });
  }

  return true;
}

function renderLetter(letter) {
  document.querySelector("#letter-eyebrow").textContent = letter.eyebrow;
  document.querySelector("#letter-title").textContent = letter.title;
  document.querySelector("#letter-signoff").textContent = letter.signoff;
  document.querySelector("#letter-signature").textContent = letter.signature;

  const highlights = Array.isArray(letter.highlights) ? letter.highlights : [];
  const closingStart = Number.isInteger(letter.closing_start)
    ? letter.closing_start
    : letter.paragraphs.length;
  const asideIndex = Number.isInteger(letter.aside_index)
    ? letter.aside_index
    : -1;

  const paragraphs = letter.paragraphs.map((paragraph, index) => {
    const element = document.createElement("p");
    appendHighlightedText(element, paragraph, highlights);

    if (index === closingStart) element.classList.add("closing-start");
    if (index > closingStart) element.classList.add("closing-wish");
    if (index === asideIndex) element.classList.add("closing-aside");
    return element;
  });

  document.querySelector("#letter-copy").replaceChildren(...paragraphs);
}

function appendHighlightedText(element, text, highlights) {
  const matches = highlights
    .map((phrase) => ({ phrase, index: text.indexOf(phrase) }))
    .filter(({ phrase, index }) => phrase && index >= 0)
    .sort((a, b) => a.index - b.index);

  let cursor = 0;
  for (const match of matches) {
    if (match.index < cursor) continue;
    element.append(document.createTextNode(text.slice(cursor, match.index)));
    const emphasis = document.createElement("strong");
    emphasis.textContent = match.phrase;
    element.append(emphasis);
    cursor = match.index + match.phrase.length;
  }
  element.append(document.createTextNode(text.slice(cursor)));
}

async function playOpeningTransition() {
  openingTransition.hidden = false;
  await nextFrame();
  document.body.classList.add("letter-opening");

  if (!reduceMotion) await delay(520);
  gatePage.hidden = true;
  letterPage.hidden = false;
  window.scrollTo({ top: 0 });

  if (!reduceMotion) await delay(360);
  document.body.classList.remove("letter-opening");
  if (!reduceMotion) await delay(280);
  openingTransition.hidden = true;
}

function setUnlocking(unlocking) {
  unlockButton.disabled = unlocking || !passwordInput.value.trim();
  unlockButton.querySelector("span").textContent = unlocking
    ? "正在拆信…"
    : "打开这封信";
  form.setAttribute("aria-busy", String(unlocking));
}

function wrongPassword() {
  message.textContent = "唔，再想一下。这个暗号好像不对。";
  form.classList.remove("shake");
  void form.offsetWidth;
  form.classList.add("shake");
  passwordInput.setAttribute("aria-invalid", "true");
}

function delay(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function nextFrame() {
  return new Promise((resolve) => window.requestAnimationFrame(resolve));
}

loadLetter();
