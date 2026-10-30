(() => {
  const { episodeId, chapters } = JSON.parse(document.getElementById("nr-data").textContent);
  const fmt = t => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
  const el = (tag, cls, text) => Object.assign(document.createElement(tag), { className: cls || "", textContent: text ?? "" });

  chapters.forEach(ch => {
    const root = document.getElementById(`chapter-${ch.number}`);
    const audio = root.querySelector("audio");
    if (!audio) return;
    const q = s => root.querySelector(s);
    const main = q(".nr-main"), sub = q(".nr-sub"), transcript = q(".nr-transcript"), list = q(".nr-comments");
    const form = q(".nr-comment-form form"), textarea = form.querySelector("textarea");
    const baseUrl = `/episode/${episodeId}/chapter/${ch.number}/native-review`;
    let key, track, cue, draft;

    const line = (c, lang) => ch.lines[c.sr_no]?.[lang] ?? "";
    const label = (time, srNo) => fmt(time) + (srNo != null ? ` · Line ${srNo}` : "");
    const seek = t => { audio.currentTime = t; audio.play(); };

    const showCue = c => {
      cue = c;
      main.textContent = c ? line(c, key) : "";
      sub.textContent = c && key === "native" ? line(c, "en") : "";
      transcript.querySelector(".active")?.classList.remove("active");
      const li = c && transcript.querySelector(`[data-sr="${c.sr_no}"]`);
      if (li) { li.classList.add("active"); li.scrollIntoView({ block: "nearest" }); }
    };

    const tick = () => {
      const t = audio.currentTime;
      const c = track.cues.find(c => t >= c.start && t < c.end);
      if (c !== cue) showCue(c);
      if (!audio.paused) requestAnimationFrame(tick);
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
          await fetch(`${baseUrl}/${key}/comments/${cm.id}/delete`, { method: "POST" });
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
      key = k;
      track = ch.tracks[k];
      form.hidden = true;
      root.querySelectorAll("[data-track]").forEach(b => b.classList.toggle("active", b.dataset.track === k));
      renderTranscript();
      renderComments();
      showCue(null);
      const rate = audio.playbackRate;
      audio.src = track.url;
      audio.addEventListener("loadedmetadata", () => {
        audio.defaultPlaybackRate = audio.playbackRate = rate;
        if (resumeAt != null) audio.currentTime = resumeAt;
      }, { once: true });
    };

    audio.addEventListener("play", tick);
    audio.addEventListener("seeked", tick);
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
      const body = new FormData();
      body.set("time", draft.time);
      body.set("text", textarea.value);
      if (draft.sr_no != null) body.set("sr_no", draft.sr_no);
      const resp = await fetch(`${baseUrl}/${draft.key}/comments`, { method: "POST", body });
      if (!resp.ok) return alert("Could not save the comment. Please try again.");
      ch.tracks[draft.key].comments.push(await resp.json());
      textarea.value = "";
      form.hidden = true;
      renderComments();
    };

    load(ch.tracks.native ? "native" : "en");
  });
})();
