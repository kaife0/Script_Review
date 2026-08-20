(function () {
  const config = document.getElementById("episode-config").dataset;
  const episodeId = config.episodeId;
  const audioInProgress = config.audioInProgress === "true";

  function post(url, data) {
    const body = new FormData();
    for (const [key, value] of Object.entries(data || {})) body.set(key, value);
    return fetch(url, { method: "POST", headers: { "X-Requested-With": "XMLHttpRequest" }, body })
      .then(r => r.json());
  }

  // ---- URL helpers for locator-addressed resources (row sr_no, "episode", or "chapter:N") ----
  function commentUrl(locator, target, ...tail) {
    const suffix = tail.length ? "/" + tail.join("/") : "";
    if (locator === "episode") return `/episode/${episodeId}/title/comments/${target}${suffix}`;
    if (String(locator).startsWith("chapter:")) {
      const n = String(locator).split(":")[1];
      return `/episode/${episodeId}/chapter/${n}/title/comments/${target}${suffix}`;
    }
    return `/episode/${episodeId}/row/${locator}/comments/${target}${suffix}`;
  }

  function rowOrTitleUrl(locator, suffix) {
    if (locator === "episode") return `/episode/${episodeId}/title${suffix}`;
    if (String(locator).startsWith("chapter:")) return `/episode/${episodeId}/chapter/${String(locator).split(":")[1]}/title${suffix}`;
    return `/episode/${episodeId}/row/${locator}${suffix}`;
  }

  function updateVerifyBar(verified, total) {
    const pct = total ? Math.round((verified / total) * 100) : 0;
    document.getElementById("verify-pct").textContent = pct + "%";
    document.getElementById("verify-fill").style.width = pct + "%";
    document.getElementById("verify-count").textContent = `${verified} / ${total} lines verified`;
  }

  // ---- verify toggle ----
  document.addEventListener("click", e => {
    const btn = e.target.closest(".verify-toggle");
    if (!btn) return;
    const sr = btn.dataset.srNo;
    const next = btn.dataset.verified !== "true";
    post(`/episode/${episodeId}/row/${sr}`, { human_verified: next }).then(data => {
      if (!data.ok) return;
      const card = btn.closest(".row-card");
      btn.dataset.verified = String(next);
      card.dataset.verified = String(next);
      btn.textContent = next ? "✓ Verified" : "Mark verified";
      btn.classList.toggle("btn-secondary", !next);
      card.classList.toggle("is-verified", next);
      const pill = card.querySelector(".status-pill");
      pill.textContent = next ? "Verified" : "Needs review";
      pill.classList.toggle("verified", next);
      pill.classList.toggle("pending", !next);
      updateVerifyBar(data.verified_rows, data.total_rows);
      applyFilter();
    });
  });

  // ---- reviewer text autosave (rows + titles) ----
  const saveTimers = {};
  document.addEventListener("input", e => {
    const textarea = e.target.closest('[data-role="reviewer-text"], [data-role="title-reviewer-text"]');
    if (!textarea) return;
    const isTitle = textarea.dataset.role === "title-reviewer-text";
    const locator = isTitle ? textarea.dataset.locator : textarea.dataset.srNo;
    const saveKey = isTitle ? `title-${locator}` : locator;
    const state = document.querySelector(`[data-save-state="${saveKey}"]`);
    if (state) state.textContent = "Saving…";
    clearTimeout(saveTimers[saveKey]);
    saveTimers[saveKey] = setTimeout(() => {
      const url = isTitle ? rowOrTitleUrl(locator, "/reviewer-text") : `/episode/${episodeId}/row/${locator}/reviewer-text`;
      post(url, { text: textarea.value })
        .then(() => {
          if (!state) return;
          state.textContent = "Saved";
          setTimeout(() => { state.textContent = ""; }, 1500);
        });
    }, 700);
  });

  // ---- undo / redo (rows + titles) / complete (rows only) ----
  document.addEventListener("click", e => {
    const btn = e.target.closest('[data-action="undo"], [data-action="redo"]');
    if (!btn) return;
    const isTitle = btn.dataset.role === "title";
    const locator = isTitle ? btn.dataset.locator : btn.dataset.srNo;
    const url = isTitle ? rowOrTitleUrl(locator, `/reviewer-text/${btn.dataset.action}`)
                         : `/episode/${episodeId}/row/${locator}/reviewer-text/${btn.dataset.action}`;
    post(url).then(data => {
      if (!data.ok) return;
      const selector = isTitle
        ? `[data-role="title-reviewer-text"][data-locator="${locator}"]`
        : `[data-role="reviewer-text"][data-sr-no="${locator}"]`;
      document.querySelector(selector).value = data.text;
    });
  });

  document.addEventListener("click", e => {
    const btn = e.target.closest(".complete-toggle");
    if (!btn) return;
    const sr = btn.dataset.srNo;
    const next = btn.dataset.complete !== "true";
    post(`/episode/${episodeId}/row/${sr}/complete`, { complete: next }).then(data => {
      if (!data.ok) return;
      btn.dataset.complete = String(next);
      btn.classList.toggle("done", next);
      btn.textContent = next ? "✓ Complete" : "Mark complete";
    });
  });

  // ---- comment panels ----
  document.addEventListener("click", e => {
    const btn = e.target.closest(".comment-toggle");
    if (!btn) return;
    const container = btn.closest(".box") || btn.closest(".audio-comment-wrap")
      || btn.closest(".title-reviewer-block") || btn.closest(".title-pair");
    container.querySelector(".comment-panel").classList.toggle("open");
  });

  // ---- regenerate audio (rows + titles) ----
  document.addEventListener("click", e => {
    const btn = e.target.closest('[data-action="regenerate-audio"]');
    if (!btn) return;
    const isTitle = btn.dataset.role === "title";
    const locator = btn.dataset.locator;
    const url = isTitle ? rowOrTitleUrl(locator, "/regenerate-audio") : `/episode/${episodeId}/row/${locator}/regenerate-audio`;
    btn.disabled = true;
    const originalText = btn.textContent;
    btn.textContent = "Regenerating…";
    post(url).then(data => {
      btn.disabled = false;
      btn.textContent = originalText;
      if (!data.ok) { alert(data.error || "Failed to regenerate audio."); return; }
      location.reload();
    });
  });

  // ---- audio source switch / delete reviewer audio (rows only) ----
  document.addEventListener("click", e => {
    const useReviewer = e.target.closest('[data-action="use-reviewer-audio"]');
    const useTts = e.target.closest('[data-action="use-tts-audio"]');
    const deleteReviewer = e.target.closest('[data-action="delete-reviewer-audio"]');
    if (useReviewer) {
      post(`/episode/${episodeId}/row/${useReviewer.dataset.locator}/audio-source`, { source: "reviewer" })
        .then(data => { if (data.ok) location.reload(); });
    }
    if (useTts) {
      post(`/episode/${episodeId}/row/${useTts.dataset.locator}/audio-source`, { source: "tts" })
        .then(data => { if (data.ok) location.reload(); });
    }
    if (deleteReviewer) {
      if (!confirm("Remove the reviewer-recorded audio for this line?")) return;
      post(`/episode/${episodeId}/row/${deleteReviewer.dataset.locator}/reviewer-audio/delete`)
        .then(data => { if (data.ok) location.reload(); });
    }
  });

  // ---- record / upload reviewer audio ----
  const activeRecorders = {};

  document.addEventListener("click", async e => {
    const btn = e.target.closest('[data-action="record-audio"]');
    if (!btn) return;
    const locator = btn.dataset.locator;
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      alert("Could not access the microphone.");
      return;
    }
    let recorder;
    try {
      recorder = new MediaRecorder(stream);
    } catch {
      stream.getTracks().forEach(t => t.stop());
      alert("Recording isn't supported in this browser.");
      return;
    }
    const chunks = [];
    recorder.ondataavailable = ev => chunks.push(ev.data);
    recorder.onstop = () => {
      stream.getTracks().forEach(t => t.stop());
      if (!chunks.length) return;
      uploadReviewerAudio(locator, new Blob(chunks, { type: recorder.mimeType }));
    };
    recorder.start();
    activeRecorders[locator] = recorder;
    const indicator = document.querySelector(`.recording-indicator[data-locator="${locator}"]`);
    if (indicator) indicator.hidden = false;
  });

  document.addEventListener("click", e => {
    const btn = e.target.closest('[data-action="stop-recording"]');
    if (!btn) return;
    const locator = btn.dataset.locator;
    if (activeRecorders[locator]) activeRecorders[locator].stop();
    const indicator = document.querySelector(`.recording-indicator[data-locator="${locator}"]`);
    if (indicator) indicator.hidden = true;
  });

  document.addEventListener("change", e => {
    const input = e.target.closest('[data-role="reviewer-audio-file"]');
    if (!input || !input.files.length) return;
    uploadReviewerAudio(input.dataset.locator, input.files[0]);
  });

  function uploadReviewerAudio(locator, blob) {
    const body = new FormData();
    body.set("audio", blob, "recording");
    fetch(`/episode/${episodeId}/row/${locator}/reviewer-audio`, { method: "POST", body })
      .then(async r => {
        let data;
        try { data = await r.json(); } catch { data = null; }
        if (!r.ok || !data || !data.ok) {
          alert((data && data.error) || `Failed to upload audio (status ${r.status}).`);
          return;
        }
        location.reload();
      })
      .catch(() => alert("Failed to upload audio: network error."));
  }

  function escapeHtml(s) {
    const div = document.createElement("div");
    div.textContent = s;
    return div.innerHTML;
  }

  // ---- Confluence-style anchored text comments ----
  function anchorDataEl(locator, target) {
    return document.querySelector(`.anchor-data[data-locator="${locator}"][data-target="${target}"]`);
  }

  function anchorDataFor(locator, target) {
    const el = anchorDataEl(locator, target);
    if (!el) return [];
    try { return JSON.parse(el.textContent); } catch { return []; }
  }

  function addAnchorData(locator, target, comment) {
    const el = anchorDataEl(locator, target);
    if (!el) return;
    const data = anchorDataFor(locator, target);
    data.push(comment);
    el.textContent = JSON.stringify(data);
  }

  function removeAnchorData(locator, target, commentId) {
    const el = anchorDataEl(locator, target);
    if (!el) return;
    const data = anchorDataFor(locator, target).filter(c => c.id !== commentId);
    el.textContent = JSON.stringify(data);
  }

  function setAnchorResolved(locator, target, commentId, resolved) {
    const el = anchorDataEl(locator, target);
    if (!el) return;
    const data = anchorDataFor(locator, target);
    const entry = data.find(c => c.id === commentId);
    if (entry) { entry.resolved = resolved; el.textContent = JSON.stringify(data); }
  }

  function applyHighlights(locator, target) {
    const box = document.querySelector(`.box-text.anchorable[data-locator="${locator}"][data-target="${target}"]`);
    if (!box) return;
    const rawText = box.dataset.rawText;
    const comments = anchorDataFor(locator, target)
      .filter(c => c.anchor)
      .sort((a, b) => a.anchor.start - b.anchor.start);

    if (!comments.length) { box.textContent = rawText; return; }

    let cursor = 0;
    const frag = document.createDocumentFragment();
    for (const c of comments) {
      const { start, end } = c.anchor;
      if (start < cursor || end > rawText.length || start >= end) continue;
      frag.appendChild(document.createTextNode(rawText.slice(cursor, start)));
      const mark = document.createElement("mark");
      mark.className = "anchor-mark" + (c.resolved ? " resolved-mark" : "");
      mark.dataset.commentId = c.id;
      mark.textContent = rawText.slice(start, end);
      frag.appendChild(mark);
      cursor = end;
    }
    frag.appendChild(document.createTextNode(rawText.slice(cursor)));
    box.textContent = "";
    box.appendChild(frag);
  }

  document.querySelectorAll(".box-text.anchorable").forEach(box => {
    applyHighlights(box.dataset.locator, box.dataset.target);
  });

  document.addEventListener("click", e => {
    const mark = e.target.closest(".anchor-mark");
    if (!mark) return;
    const box = mark.closest(".box");
    const panel = box.querySelector(".comment-panel");
    panel.classList.add("open");
    const thread = panel.querySelector(`.comment-thread[data-comment-id="${mark.dataset.commentId}"]`);
    if (thread) thread.scrollIntoView({ behavior: "smooth", block: "center" });
  });

  // ---- select text -> floating "Add comment" button ----
  const selectionBtn = document.createElement("button");
  selectionBtn.type = "button";
  selectionBtn.className = "selection-comment-btn";
  selectionBtn.textContent = "💬 Comment";
  document.body.appendChild(selectionBtn);
  let pendingSelection = null;

  document.addEventListener("mouseup", e => {
    if (e.target === selectionBtn) return;
    const box = e.target.closest(".box-text.anchorable");
    const sel = window.getSelection();
    if (!box || !sel || sel.isCollapsed || sel.rangeCount === 0) {
      selectionBtn.classList.remove("visible");
      return;
    }
    const selectedText = sel.toString().trim();
    if (!selectedText) { selectionBtn.classList.remove("visible"); return; }

    const rawText = box.dataset.rawText;
    const start = rawText.indexOf(selectedText);
    if (start === -1) { selectionBtn.classList.remove("visible"); return; }

    pendingSelection = {
      locator: box.dataset.locator, target: box.dataset.target,
      quote: selectedText, start, end: start + selectedText.length,
    };

    const rect = sel.getRangeAt(0).getBoundingClientRect();
    selectionBtn.style.left = (rect.left + window.scrollX) + "px";
    selectionBtn.style.top = (rect.top + window.scrollY - 38) + "px";
    selectionBtn.classList.add("visible");
  });

  selectionBtn.addEventListener("click", () => {
    if (!pendingSelection) return;
    const { locator, target, quote, start, end } = pendingSelection;
    const panel = document.querySelector(`.comment-panel[data-locator="${locator}"][data-target="${target}"]`);
    panel.classList.add("open");
    const form = panel.querySelector(".comment-new-form.top-level");
    form.querySelector('[name="anchor_quote"]').value = quote;
    form.querySelector('[name="anchor_start"]').value = start;
    form.querySelector('[name="anchor_end"]').value = end;
    form.querySelector("input[type=text]").focus();
    selectionBtn.classList.remove("visible");
    window.getSelection().removeAllRanges();
  });

  document.addEventListener("mousedown", e => {
    if (e.target !== selectionBtn && !e.target.closest(".box-text.anchorable")) {
      selectionBtn.classList.remove("visible");
    }
  });

  function replyHTML(r) {
    return `<div class="comment-item" data-reply-id="${r.id}">
      <span class="author">${escapeHtml(r.author)}</span>: ${escapeHtml(r.text)}
      <button type="button" class="comment-delete-btn" data-act="delete-reply" title="Delete reply">&times;</button>
    </div>`;
  }

  function threadHTML(c) {
    const replies = (c.replies || []).map(replyHTML).join("");
    const anchorHtml = c.anchor
      ? `<div class="anchor-quote-preview">&ldquo;${escapeHtml(c.anchor.quote)}&rdquo;</div>` : "";
    return `
      ${anchorHtml}
      <div class="comment-item"><span class="author">${escapeHtml(c.author)}</span>: ${escapeHtml(c.text)}</div>
      <div class="comment-replies">${replies}</div>
      <div class="comment-thread-actions">
        <button type="button" data-act="reply">Reply</button>
        <button type="button" data-act="resolve">${c.resolved ? "Reopen" : "Resolve"}</button>
        <button type="button" data-act="delete" class="comment-delete-link">Delete</button>
      </div>
      <form class="comment-new-form" data-act="reply-form" style="display:none">
        <input type="text" placeholder="Write a reply…" required><button type="submit">Reply</button>
      </form>`;
  }

  document.addEventListener("click", e => {
    const panel = e.target.closest(".comment-panel");
    if (!panel) return;
    const threadEl = e.target.closest(".comment-thread");
    if (!threadEl) return;
    const locator = panel.dataset.locator, target = panel.dataset.target;
    const commentId = threadEl.dataset.commentId;

    if (e.target.dataset.act === "reply") {
      const f = threadEl.querySelector('[data-act="reply-form"]');
      f.style.display = f.style.display === "none" ? "flex" : "none";
    }
    if (e.target.dataset.act === "resolve") {
      const resolved = e.target.textContent === "Resolve";
      post(commentUrl(locator, target, commentId, "resolve"), { resolved })
        .then(data => {
          if (!data.ok) return;
          threadEl.classList.toggle("resolved", data.resolved);
          e.target.textContent = data.resolved ? "Reopen" : "Resolve";
          setAnchorResolved(locator, target, commentId, data.resolved);
          const mark = document.querySelector(`.anchor-mark[data-comment-id="${commentId}"]`);
          if (mark) mark.classList.toggle("resolved-mark", data.resolved);
        });
    }
    if (e.target.dataset.act === "delete") {
      if (!confirm("Delete this comment and all its replies?")) return;
      post(commentUrl(locator, target, commentId, "delete")).then(data => {
        if (!data.ok) return;
        threadEl.remove();
        const count = panel.parentElement.querySelector(".comment-toggle .count");
        const next = Math.max(0, parseInt(count.textContent || "0") - 1);
        count.textContent = next;
        count.classList.toggle("zero", next === 0);
        removeAnchorData(locator, target, commentId);
        applyHighlights(locator, target);
      });
    }
    if (e.target.dataset.act === "delete-reply") {
      if (!confirm("Delete this reply?")) return;
      const replyEl = e.target.closest("[data-reply-id]");
      const replyId = replyEl.dataset.replyId;
      post(commentUrl(locator, target, commentId, "reply", replyId, "delete"))
        .then(data => {
          if (!data.ok) return;
          replyEl.remove();
        });
    }
  });

  document.addEventListener("submit", e => {
    const form = e.target.closest(".comment-new-form");
    if (!form) return;
    e.preventDefault();
    const panel = form.closest(".comment-panel");
    const locator = panel.dataset.locator, target = panel.dataset.target;
    const input = form.querySelector('input[type="text"]');
    if (!input.value.trim()) return;

    if (form.dataset.act === "reply-form") {
      const threadEl = form.closest(".comment-thread");
      post(commentUrl(locator, target, threadEl.dataset.commentId, "reply"), { text: input.value })
        .then(data => {
          if (!data.ok) return;
          threadEl.querySelector(".comment-replies").insertAdjacentHTML("beforeend", replyHTML(data.reply));
          input.value = "";
          form.style.display = "none";
        });
    } else {
      const anchorQuote = form.querySelector('[name="anchor_quote"]');
      const anchorStart = form.querySelector('[name="anchor_start"]');
      const anchorEnd = form.querySelector('[name="anchor_end"]');
      const payload = { text: input.value, author: "Reviewer" };
      if (anchorQuote && anchorQuote.value) {
        payload.anchor_quote = anchorQuote.value;
        payload.anchor_start = anchorStart.value;
        payload.anchor_end = anchorEnd.value;
      }
      post(commentUrl(locator, target), payload)
        .then(data => {
          if (!data.ok) return;
          const wrap = document.createElement("div");
          wrap.className = "comment-thread";
          wrap.dataset.commentId = data.comment.id;
          wrap.innerHTML = threadHTML(data.comment);
          panel.querySelector(".comment-thread-list").appendChild(wrap);
          input.value = "";
          if (anchorQuote) { anchorQuote.value = ""; anchorStart.value = ""; anchorEnd.value = ""; }
          const count = panel.parentElement.querySelector(".comment-toggle .count");
          count.textContent = parseInt(count.textContent || "0") + 1;
          count.classList.remove("zero");
          if (data.comment.anchor) {
            addAnchorData(locator, target, data.comment);
            applyHighlights(locator, target);
          }
        });
    }
  });

  // ---- filters ----
  let currentFilter = "all";
  function applyFilter() {
    document.querySelectorAll(".row-card").forEach(card => {
      const verified = card.dataset.verified === "true";
      const flagged = card.dataset.flag === "note";
      let show = true;
      if (currentFilter === "unverified") show = !verified;
      else if (currentFilter === "note") show = flagged;
      card.hidden = !show;
    });
    document.querySelectorAll(".chapter").forEach(section => {
      const anyVisible = section.querySelector(".row-card:not([hidden])");
      section.hidden = !anyVisible;
    });
  }

  document.querySelectorAll(".filter-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      currentFilter = btn.dataset.filter;
      applyFilter();
    });
  });

  // ---- jump to next unreviewed ----
  document.getElementById("jump-next").addEventListener("click", () => {
    const cards = [...document.querySelectorAll('.row-card[data-verified="false"]:not([hidden])')];
    const y = window.scrollY;
    const next = cards.find(c => c.getBoundingClientRect().top + y > y + 220) || cards[0];
    if (next) next.scrollIntoView({ behavior: "smooth", block: "center" });
  });

  updateVerifyBar(Number(config.verifiedRows), Number(config.totalRows));

  // ---- keep sticky offsets in sync with actual rendered heights ----
  function updateStickyOffsets() {
    const topbar = document.querySelector(".topbar");
    const controlbar = document.getElementById("controlbar");
    const topbarHeight = topbar ? topbar.getBoundingClientRect().height : 0;
    controlbar.style.top = topbarHeight + "px";
    const controlbarHeight = controlbar.getBoundingClientRect().height;
    document.querySelectorAll(".chapter-head").forEach(el => {
      el.style.top = (topbarHeight + controlbarHeight + 4) + "px";
    });
  }
  updateStickyOffsets();
  window.addEventListener("resize", updateStickyOffsets);

  // ---- fill in audio as it finishes generating in the background ----
  if (audioInProgress) {
    const pollAudioStatus = () => {
      fetch(`/episode/${episodeId}/audio-status`).then(r => r.json()).then(data => {
        let anyPending = false;
        for (const row of data.rows) {
          const slot = document.querySelector(`.audio-strip[data-audio-slot="${row.sr_no}"]`);
          if (!slot) continue;
          const ttsLane = slot.querySelector('.audio-lane[data-lane="tts"]') || slot;
          const card = slot.closest(".row-card");
          if (row.audio_status === "done" && row.audio_url) {
            if (!ttsLane.querySelector("audio")) {
              const placeholder = ttsLane.querySelector(".audio-generating, .no-audio");
              const audio = document.createElement("audio");
              audio.controls = true;
              audio.preload = "none";
              audio.src = row.audio_url;
              if (placeholder) placeholder.replaceWith(audio);
              else ttsLane.appendChild(audio);
              if (card) card.dataset.audioStatus = "done";
            }
          } else if (row.audio_status === "failed") {
            if (card) card.dataset.audioStatus = "failed";
          } else {
            anyPending = true;
          }
        }
        if (data.status === "done") {
          const banner = document.getElementById("audio-progress-banner");
          if (banner) banner.remove();
          return;
        }
        if (anyPending) setTimeout(pollAudioStatus, 3000);
      }).catch(() => setTimeout(pollAudioStatus, 5000));
    };
    pollAudioStatus();
  }

  // ---- queued playback: when a row's audio finishes, auto-play the next row's primary audio.
  // Pausing never auto-advances -- only a natural "ended" does. ----
  function allAudioEls() {
    return [...document.querySelectorAll(".row-card .audio-strip audio")];
  }

  function primaryAudioEl(card) {
    for (const lane of card.querySelectorAll(".audio-strip .audio-lane")) {
      if (lane.querySelector(".audio-source-badge.is-primary")) {
        const audio = lane.querySelector("audio");
        if (audio) return audio;
      }
    }
    return card.querySelector(".audio-strip audio");
  }

  document.addEventListener("ended", e => {
    const audio = e.target;
    if (!(audio instanceof HTMLAudioElement)) return;
    const card = audio.closest(".row-card");
    if (!card) return;
    const cards = [...document.querySelectorAll(".row-card:not([hidden])")];
    const idx = cards.indexOf(card);
    for (let i = idx + 1; i < cards.length; i++) {
      const next = primaryAudioEl(cards[i]);
      if (next) { next.play(); break; }
    }
  }, true);

  // Only one audio plays at a time -- starting one pauses any other still playing.
  document.addEventListener("play", e => {
    const audio = e.target;
    if (!(audio instanceof HTMLAudioElement)) return;
    for (const other of allAudioEls()) {
      if (other !== audio && !other.paused) other.pause();
    }
  }, true);
})();
