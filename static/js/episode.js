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

  // ---- reviewer text autosave ----
  const saveTimers = {};
  document.addEventListener("input", e => {
    const textarea = e.target.closest('[data-role="reviewer-text"]');
    if (!textarea) return;
    const sr = textarea.dataset.srNo;
    const state = document.querySelector(`[data-save-state="${sr}"]`);
    state.textContent = "Saving…";
    clearTimeout(saveTimers[sr]);
    saveTimers[sr] = setTimeout(() => {
      post(`/episode/${episodeId}/row/${sr}/reviewer-text`, { text: textarea.value })
        .then(() => {
          state.textContent = "Saved";
          setTimeout(() => { state.textContent = ""; }, 1500);
        });
    }, 700);
  });

  // ---- undo / redo / complete ----
  document.addEventListener("click", e => {
    const btn = e.target.closest('[data-action="undo"], [data-action="redo"]');
    if (!btn) return;
    const sr = btn.dataset.srNo;
    post(`/episode/${episodeId}/row/${sr}/reviewer-text/${btn.dataset.action}`).then(data => {
      if (!data.ok) return;
      document.querySelector(`[data-role="reviewer-text"][data-sr-no="${sr}"]`).value = data.text;
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
    const container = btn.closest(".box") || btn.closest(".audio-comment-wrap");
    container.querySelector(".comment-panel").classList.toggle("open");
  });

  function escapeHtml(s) {
    const div = document.createElement("div");
    div.textContent = s;
    return div.innerHTML;
  }

  // ---- Confluence-style anchored text comments ----
  function anchorDataEl(sr, target) {
    return document.querySelector(`.anchor-data[data-sr-no="${sr}"][data-target="${target}"]`);
  }

  function anchorDataFor(sr, target) {
    const el = anchorDataEl(sr, target);
    if (!el) return [];
    try { return JSON.parse(el.textContent); } catch { return []; }
  }

  function addAnchorData(sr, target, comment) {
    const el = anchorDataEl(sr, target);
    if (!el) return;
    const data = anchorDataFor(sr, target);
    data.push(comment);
    el.textContent = JSON.stringify(data);
  }

  function removeAnchorData(sr, target, commentId) {
    const el = anchorDataEl(sr, target);
    if (!el) return;
    const data = anchorDataFor(sr, target).filter(c => c.id !== commentId);
    el.textContent = JSON.stringify(data);
  }

  function setAnchorResolved(sr, target, commentId, resolved) {
    const el = anchorDataEl(sr, target);
    if (!el) return;
    const data = anchorDataFor(sr, target);
    const entry = data.find(c => c.id === commentId);
    if (entry) { entry.resolved = resolved; el.textContent = JSON.stringify(data); }
  }

  function applyHighlights(sr, target) {
    const box = document.querySelector(`.box-text.anchorable[data-sr-no="${sr}"][data-target="${target}"]`);
    if (!box) return;
    const rawText = box.dataset.rawText;
    const comments = anchorDataFor(sr, target)
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
    applyHighlights(box.dataset.srNo, box.dataset.target);
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
      sr: box.dataset.srNo, target: box.dataset.target,
      quote: selectedText, start, end: start + selectedText.length,
    };

    const rect = sel.getRangeAt(0).getBoundingClientRect();
    selectionBtn.style.left = (rect.left + window.scrollX) + "px";
    selectionBtn.style.top = (rect.top + window.scrollY - 38) + "px";
    selectionBtn.classList.add("visible");
  });

  selectionBtn.addEventListener("click", () => {
    if (!pendingSelection) return;
    const { sr, target, quote, start, end } = pendingSelection;
    const panel = document.querySelector(`.comment-panel[data-sr-no="${sr}"][data-target="${target}"]`);
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
    const sr = panel.dataset.srNo, target = panel.dataset.target;
    const commentId = threadEl.dataset.commentId;

    if (e.target.dataset.act === "reply") {
      const f = threadEl.querySelector('[data-act="reply-form"]');
      f.style.display = f.style.display === "none" ? "flex" : "none";
    }
    if (e.target.dataset.act === "resolve") {
      const resolved = e.target.textContent === "Resolve";
      post(`/episode/${episodeId}/row/${sr}/comments/${target}/${commentId}/resolve`, { resolved })
        .then(data => {
          if (!data.ok) return;
          threadEl.classList.toggle("resolved", data.resolved);
          e.target.textContent = data.resolved ? "Reopen" : "Resolve";
          setAnchorResolved(sr, target, commentId, data.resolved);
          const mark = document.querySelector(`.anchor-mark[data-comment-id="${commentId}"]`);
          if (mark) mark.classList.toggle("resolved-mark", data.resolved);
        });
    }
    if (e.target.dataset.act === "delete") {
      if (!confirm("Delete this comment and all its replies?")) return;
      post(`/episode/${episodeId}/row/${sr}/comments/${target}/${commentId}/delete`).then(data => {
        if (!data.ok) return;
        threadEl.remove();
        const count = panel.parentElement.querySelector(".comment-toggle .count");
        const next = Math.max(0, parseInt(count.textContent || "0") - 1);
        count.textContent = next;
        count.classList.toggle("zero", next === 0);
        removeAnchorData(sr, target, commentId);
        applyHighlights(sr, target);
      });
    }
    if (e.target.dataset.act === "delete-reply") {
      if (!confirm("Delete this reply?")) return;
      const replyEl = e.target.closest("[data-reply-id]");
      const replyId = replyEl.dataset.replyId;
      post(`/episode/${episodeId}/row/${sr}/comments/${target}/${commentId}/reply/${replyId}/delete`)
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
    const sr = panel.dataset.srNo, target = panel.dataset.target;
    const input = form.querySelector('input[type="text"]');
    if (!input.value.trim()) return;

    if (form.dataset.act === "reply-form") {
      const threadEl = form.closest(".comment-thread");
      post(`/episode/${episodeId}/row/${sr}/comments/${target}/${threadEl.dataset.commentId}/reply`, { text: input.value })
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
      post(`/episode/${episodeId}/row/${sr}/comments/${target}`, payload)
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
            addAnchorData(sr, target, data.comment);
            applyHighlights(sr, target);
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
          const card = slot.closest(".row-card");
          if (row.audio_status === "done" && row.audio_url) {
            if (!slot.querySelector("audio")) {
              const placeholder = slot.querySelector(".audio-generating, .no-audio");
              const audio = document.createElement("audio");
              audio.controls = true;
              audio.preload = "none";
              audio.src = row.audio_url;
              if (placeholder) placeholder.replaceWith(audio);
              else slot.insertBefore(audio, slot.firstChild.nextSibling);
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
})();
