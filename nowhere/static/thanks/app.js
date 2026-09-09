const gatePage = document.querySelector("#gate-page");
const letterPage = document.querySelector("#letter-page");
const form = document.querySelector("#unlock-form");
const passwordInput = document.querySelector("#letter-password");
const unlockButton = document.querySelector("#unlock-button");
const revealButton = document.querySelector("#reveal-password");
const message = document.querySelector("#password-message");
const closeButton = document.querySelector("#close-letter");

passwordInput.addEventListener("input", () => {
  unlockButton.disabled = !passwordInput.value.trim();
  message.innerHTML = "&nbsp;";
  form.classList.remove("shake");
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

  unlockButton.disabled = true;
  unlockButton.querySelector("span").textContent = "正在拆信…";

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

    await loadLetter();
  } catch {
    message.textContent = "信箱刚刚打了个盹，请稍后再试一次。";
  } finally {
    unlockButton.disabled = !passwordInput.value.trim();
    unlockButton.querySelector("span").textContent = "打开这封信";
  }
});

closeButton.addEventListener("click", () => {
  letterPage.hidden = true;
  gatePage.hidden = false;
  window.scrollTo({ top: 0, behavior: "smooth" });
});

async function loadLetter() {
  const response = await fetch("/thanks/letter", { cache: "no-store" });
  if (!response.ok) return;
  const letter = await response.json();

  document.querySelector("#letter-eyebrow").textContent = letter.eyebrow;
  document.querySelector("#letter-title").textContent = letter.title;
  document.querySelector("#letter-signoff").textContent = letter.signoff;
  document.querySelector("#letter-signature").textContent = letter.signature;

  const copy = document.querySelector("#letter-copy");
  copy.replaceChildren(
    ...letter.paragraphs.map((paragraph) => {
      const element = document.createElement("p");
      element.textContent = paragraph;
      return element;
    }),
  );

  gatePage.hidden = true;
  letterPage.hidden = false;
  window.scrollTo({ top: 0 });
}

function wrongPassword() {
  message.textContent = "唔，再想一下。这个暗号好像不对。";
  form.classList.remove("shake");
  void form.offsetWidth;
  form.classList.add("shake");
  passwordInput.setAttribute("aria-invalid", "true");
}

loadLetter();
