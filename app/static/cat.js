// A cat walks along the bottom of the page from right to left. Now and then it
// stops to lick itself, then walks on. Its position survives page reloads
// (several pages refresh themselves), so it doesn't jump back to the start.
(function () {
  "use strict";
  var cat = document.querySelector(".cat");
  if (!cat) return;

  var SPEED = 48; // pixels per second
  var KEY = "outreach-cat";
  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function rand(min, max) { return min + Math.random() * (max - min); }
  function width() { return cat.offsetWidth || 112; }

  var state = "walking";   // walking | licking | away
  var x = window.innerWidth + 10;
  var timeLeft = 0;        // seconds left in "licking" or "away"
  var nextStopIn = rand(250, 600); // pixels to walk before the next lick

  try {
    var saved = JSON.parse(sessionStorage.getItem(KEY) || "null");
    if (saved && Date.now() - saved.savedAt < 15000) {
      x = saved.x; state = saved.state; timeLeft = saved.timeLeft; nextStopIn = saved.nextStopIn;
    }
  } catch (e) { /* storage blocked: start fresh */ }

  function save() {
    try {
      sessionStorage.setItem(KEY, JSON.stringify({
        x: x, state: state, timeLeft: timeLeft, nextStopIn: nextStopIn, savedAt: Date.now()
      }));
    } catch (e) { /* ignore */ }
  }
  window.addEventListener("pagehide", save);

  function render() {
    cat.style.transform = "translateX(" + x.toFixed(1) + "px)";
    cat.classList.toggle("is-walking", state === "walking");
    cat.classList.toggle("is-licking", state === "licking");
    cat.style.visibility = state === "away" ? "hidden" : "visible";
  }

  if (reduceMotion) {
    // Sit still in the corner instead of walking around.
    state = "idle";
    x = window.innerWidth - width() - 16;
    render();
    return;
  }

  var last = performance.now();
  function step(now) {
    var dt = Math.min((now - last) / 1000, 0.1); // a hidden tab shouldn't make it teleport
    last = now;

    if (state === "walking") {
      var moved = SPEED * dt;
      x -= moved;
      nextStopIn -= moved;
      var onScreen = x > 40 && x < window.innerWidth - width() - 40;
      if (nextStopIn <= 0 && onScreen) {
        state = "licking";
        timeLeft = rand(2.5, 4.5);
      } else if (x < -width() - 20) {
        state = "away";
        timeLeft = rand(3, 8);
      }
    } else if (state === "licking") {
      timeLeft -= dt;
      if (timeLeft <= 0) {
        state = "walking";
        nextStopIn = rand(300, 700);
      }
    } else if (state === "away") {
      timeLeft -= dt;
      if (timeLeft <= 0) {
        state = "walking";
        x = window.innerWidth + 10;
        nextStopIn = rand(150, 500);
      }
    }
    render();
    requestAnimationFrame(step);
  }
  render();
  requestAnimationFrame(step);
})();
