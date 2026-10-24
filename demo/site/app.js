/* Courtside Live — demo page script for FlashTrace.

   Two jobs only, on purpose:

   1. Render the fictional match clock ONCE when the page loads. There is no
      ticking timer: a per-second update would add visual change of its own
      and muddy what FlashTrace is meant to find on this page.
   2. Wire the "Pause all motion" button. It toggles the class
      .motion-paused on <html>; styles.css turns that into
      animation-play-state: paused for every element.

   No framework, no network requests, no timers. */
(function () {
  "use strict";

  /* ---- 1. Match clock, rendered once ---------------------------------- */

  // The fixture is frozen with 6:42 left in the fourth quarter.
  var QUARTER_SECONDS = 600;     // ten-minute quarters in this league
  var PLAYED_IN_QUARTER = 198;   // seconds already played in Q4

  var remaining = QUARTER_SECONDS - PLAYED_IN_QUARTER;
  var minutes = Math.floor(remaining / 60);
  var seconds = remaining % 60;
  var two = function (n) { return (n < 10 ? "0" : "") + n; };

  var clock = document.getElementById("match-clock");
  if (clock) {
    clock.textContent = two(minutes) + ":" + two(seconds);
  }

  /* ---- 2. Pause-all-motion toggle ------------------------------------- */

  var root = document.documentElement;
  var button = document.getElementById("pause-animations");
  if (!button) { return; }

  function setPaused(paused) {
    root.classList.toggle("motion-paused", paused);
    // aria-pressed announces the state; the label text never changes.
    button.setAttribute("aria-pressed", paused ? "true" : "false");
  }

  button.addEventListener("click", function () {
    setPaused(button.getAttribute("aria-pressed") !== "true");
  });
})();
