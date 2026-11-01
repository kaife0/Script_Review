(() => {
  const { episodeId, chapters } = JSON.parse(document.getElementById("nr-data").textContent);
  const PART_BYTES = 8 * 1024 * 1024;
  const CAPTION_HOLD = 1.2;
  const CAPTION_LEAD = 0.1;
  const BADGES = { verified: "badge-ok", uploaded: "badge-info", pending: "badge-warn" };
  const players = [];
  const activeUploads = new Set();

  const fmt = t => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
  const el = (tag, cls, text) => Object.assign(document.createElement(tag), { className: cls || "", textContent: text ?? "" });
  const post = (url, data) => {
    const body = new FormData();
    Object.entries(data || {}).forEach(([k, v]) => body.set(k, v));
    return fetch(url, { method: "POST", body });
  };
  const sendPart = async (url, blob, tries = 3) => {
    try {
      const resp = await fetch(url, { method: "POST", body: blob });
      if (resp.ok || resp.status < 500 || tries <= 1) return resp;
    } catch (err) {
      if (tries <= 1) throw err;
    }
    return sendPart(url, blob, tries - 1);
  };

  window.addEventListener("beforeunload", e => { if (activeUploads.size) e.preventDefault(); });
  window.addEventListener("pagehide", () => activeUploads.forEach(url => navigator.sendBeacon(`${url}/abort`)));

  chapters.forEach(ch => {
    const root = document.getElementById(`chapter-${ch.number}`);
    const q = s => root.querySelector(s);
    const baseUrl = `/episode/${episodeId}/chapter/${ch.number}/native-review`;

    root.querySelectorAll("[data-upload]").forEach(form => {
      const status = form.querySelector(".nr-upload-status");
      const setStatus = (text, error) => { status.textContent = text; status.classList.toggle("error", !!error); };
      form.onsubmit = async e => {
        e.preventDefault();
        const file = form.querySelector("input[type=file]").files[0];
        if (!file) return;
        if ("replace" in form.dataset && !confirm(`Replace the ${form.dataset.label} audio? Its comments and the chapter's verified mark will be cleared.`)) return;
        const url = `${baseUrl}/${form.dataset.upload}/parts/${crypto.randomUUID()}`;
        const parts = Math.ceil(file.size / PART_BYTES);
        form.querySelectorAll("input, button").forEach(x => x.disabled = true);
        activeUploads.add(url);
        try {
          for (let i = 0; i < parts; i++) {
            setStatus(`Uploading… ${Math.round((i / parts) * 100)}%`);
            const resp = await sendPart(`${url}/${i}`, file.slice(i * PART_BYTES, (i + 1) * PART_BYTES));
            if (!resp.ok) throw new Error("Upload failed. Please try again.");
          }
          activeUploads.delete(url);
          setStatus("Syncing captions… this can take a minute.");
          const resp = await post(`${url}/finish`, { filename: file.name });
          if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).error || "Processing failed. Please try again.");
          location.hash = `chapter-${ch.number}`;
          location.reload();
        } catch (err) {
          if (activeUploads.delete(url)) post(`${url}/abort`);
          setStatus(err.message, true);
          form.querySelectorAll("input, button").forEach(x => x.disabled = false);
        }
      };
    });

    const verifyBtn = q("[data-verify]");
    const renderVerify = () => {
      verifyBtn.textContent = ch.verified ? "Verified ✓ (undo)" : "Mark verified";
      verifyBtn.classList.toggle("btn-secondary", ch.verified);
      root.classList.toggle("verified", ch.verified);
    };
    verifyBtn.onclick = async () => {
      verifyBtn.disabled = true;
      const resp = await post(`${baseUrl}/verify`, { verified: String(!ch.verified) });
      verifyBtn.disabled = false;
      if (!resp.ok) return alert("Could not update. Please try again.");
      const data = await resp.json();
      ch.verified = data.verified;
      renderVerify();
      const badge = document.querySelector("h1 [data-final-status]");
      badge.className = `badge ${BADGES[data.status]}`;
      badge.textContent = `final audio ${data.status}`;
    };

    const audio = q("audio");
    if (!audio) return;
    players.push(audio);
    const main = q(".nr-main"), sub = q(".nr-sub"), transcript = q(".nr-transcript"), list = q(".nr-comments");
    const form = q(".nr-comment-form form"), textarea = form.querySelector("textarea");
    let key, track, cue, draft, raf = 0;

    Object.values(ch.tracks).forEach(t => {
      t.cues.sort((a, b) => a.start - b.start);
      t.cues.forEach((c, i) => {
        const next = t.cues[i + 1];
        c.until = Math.min(c.end + CAPTION_HOLD, next ? next.start : Infinity);
      });
    });

    const line = (c, lang) => ch.lines[c.sr_no]?.[lang] ?? "";
    const label = (time, srNo) => fmt(time) + (srNo != null ? ` · Line ${srNo}` : "");
    const seek = t => { audio.currentTime = t; audio.play(); };

    const cueAt = t => {
      const cues = track.cues;
      let lo = 0, hi = cues.length - 1, found = -1;
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        if (cues[mid].start <= t) { found = mid; lo = mid + 1; } else hi = mid - 1;
      }
      return found >= 0 && t < cues[found].until ? cues[found] : null;
    };

    const showCue = c => {
      cue = c;
      main.textContent = c ? line(c, key) : "";
      sub.textContent = c && key === "native" ? line(c, "en") : "";
      transcript.querySelector(".active")?.classList.remove("active");
      const li = c && transcript.querySelector(`[data-sr="${c.sr_no}"]`);
      if (!li) return;
      li.classList.add("active");
      transcript.scrollTo({ top: li.offsetTop - transcript.clientHeight / 2 + li.clientHeight / 2, behavior: "smooth" });
    };

    const update = () => {
      const c = cueAt(audio.currentTime + CAPTION_LEAD);
      if (c !== cue) showCue(c);
    };
    const loop = () => {
      update();
      raf = audio.paused ? 0 : requestAnimationFrame(loop);
    };

    const renderTranscript = () => {
      transcript.replaceChildren(...track.cues.map(c => {
        const li = el("li");
        li.dataset.sr = c.sr_no;
        li.append(el("small", "", `${fmt(c.start)} · ${ch.lines[c.sr_no]?.speaker ?? ""}`), el("div", "", line(c, key)));
        if (key === "native") li.append(el("small", "", line(c, "en")));
        li.onclick = () => seek(c.start);
        return li;
      }));
    };

    const renderComments = () => {
      const items = [...track.comments].sort((a, b) => a.time - b.time).map(cm => {
        const li = el("li");
        const time = el("button", "nr-link", label(cm.time, cm.sr_no));
        const del = el("button", "nr-link", "Delete");
        time.onclick = () => seek(cm.time);
        del.onclick = async () => {
          if (!confirm("Delete this comment?")) return;
          const resp = await post(`${baseUrl}/${key}/comments/${cm.id}/delete`);
          if (!resp.ok) return alert("Could not delete. Please try again.");
          track.comments = track.comments.filter(x => x !== cm);
          renderComments();
        };
        li.append(time, el("span", "", cm.text), del);
        return li;
      });
      list.replaceChildren(...(items.length ? items : [el("li", "nr-empty", "No comments yet.")]));
    };

    const load = k => {
      const resumeAt = cue && ch.tracks[k].cues.find(c => c.sr_no === cue.sr_no)?.start;
      const wasPlaying = !audio.paused;
      key = k;
      track = ch.tracks[k];
      form.hidden = true;
      root.querySelectorAll("[data-track]").forEach(b => b.classList.toggle("active", b.dataset.track === k));
      renderTranscript();
      renderComments();
      showCue(null);
      audio.src = track.url;
      audio.addEventListener("loadedmetadata", () => {
        if (resumeAt != null) audio.currentTime = resumeAt;
        if (wasPlaying) audio.play();
      }, { once: true });
    };

    audio.addEventListener("play", () => {
      players.forEach(p => p !== audio && p.pause());
      if (!raf) loop();
    });
    audio.addEventListener("seeked", update);
    audio.addEventListener("timeupdate", update);
    root.querySelectorAll("[data-track]").forEach(b => b.onclick = () => b.dataset.track !== key && load(b.dataset.track));
    root.querySelectorAll("[data-speed]").forEach(b => b.onclick = () => {
      audio.defaultPlaybackRate = audio.playbackRate = Number(b.dataset.speed);
      root.querySelectorAll("[data-speed]").forEach(x => x.classList.toggle("active", x === b));
    });

    q("[data-comment]").onclick = () => {
      audio.pause();
      draft = { key, time: audio.currentTime, sr_no: cue?.sr_no };
      form.querySelector(".nr-time").textContent = label(draft.time, draft.sr_no);
      form.hidden = false;
      textarea.focus();
    };
    q("[data-cancel]").onclick = () => { form.hidden = true; };
    form.onsubmit = async e => {
      e.preventDefault();
      const data = { time: draft.time, text: textarea.value };
      if (draft.sr_no != null) data.sr_no = draft.sr_no;
      const resp = await post(`${baseUrl}/${draft.key}/comments`, data);
      if (!resp.ok) return alert("Could not save the comment. Please try again.");
      ch.tracks[draft.key].comments.push(await resp.json());
      textarea.value = "";
      form.hidden = true;
      if (draft.key === key) renderComments();
    };

    load(ch.tracks.native ? "native" : "en");
  });
})();
